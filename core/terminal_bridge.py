"""
core/terminal_bridge.py
=======================
(V1) Terminal Bridge — 에이전트의 터미널 실시간 연동 모듈.

OMOC의 Tmux 에이전트처럼 에이전트가 직접 명령을 실행하고,
결과를 피드백 루프에 반영할 수 있게 합니다.

주요 기능:
  1. run_command: 명령어 실행 + 표준 출력/에러 캡처
  2. run_test: pytest/jest 등 테스트 러너 전용 래퍼
  3. run_repl: REPL(python, node) 대화형 실행
  4. 안전 장치: 화이트리스트 + 타임아웃 + 출력 크기 제한
"""

import os
import subprocess
import time
import re
import shlex
from typing import Optional


# ── 안전 장치 상수 ──
DEFAULT_TIMEOUT_SEC = 30
MAX_OUTPUT_CHARS = 8000
MAX_REPL_ROUNDS = 10

# 허용된 명령어 접두사 (화이트리스트)
_ALLOWED_PREFIXES = [
    "python", "python3", "py",
    "pytest", "unittest",
    "node", "npm", "npx", "bun",
    "pip", "pip3",
    "git", "cat", "head", "tail", "find", "grep",
    "ls", "dir", "echo", "type",
    "cargo", "rustc",
    "go", "javac", "java",
    "tsc", "eslint", "prettier",
    "curl", "wget",
]

# 절대 차단 패턴 (블랙리스트)
_BLOCKED_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bformat\s+[a-z]:\b",
    r"\bdel\s+/[sS]\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\breg\s+delete\b",
]


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """출력을 제한 내로 잘라냅니다."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return (
        text[:half]
        + f"\n\n... [TRUNCATED: {len(text) - limit} chars omitted] ...\n\n"
        + text[-half:]
    )


def _is_command_safe(cmd: str) -> tuple[bool, str]:
    """명령어 안전성 검사: 화이트리스트 + 블랙리스트"""
    stripped = cmd.strip()
    if not stripped:
        return False, "empty_command"

    # 블랙리스트 검사
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, stripped, re.IGNORECASE):
            return False, f"blocked_pattern: {pattern}"

    # 화이트리스트 검사
    first_token = stripped.split()[0].lower()
    # Windows: python.exe, pytest.exe 등 허용
    first_token_base = os.path.splitext(os.path.basename(first_token))[0]
    if first_token_base in _ALLOWED_PREFIXES:
        return True, "allowed"

    # 절대 경로 내 허용 명령어 체크
    for prefix in _ALLOWED_PREFIXES:
        if first_token_base == prefix or first_token.endswith(f"/{prefix}"):
            return True, "allowed"

    return False, f"not_in_whitelist: {first_token_base}"


class TerminalBridge:
    """
    에이전트용 터미널 브릿지.
    subprocess 기반으로 명령을 실행하고 결과를 구조화하여 반환합니다.
    """

    def __init__(self, cwd: str | None = None, env: dict | None = None):
        self.cwd = cwd or os.getenv("AGENT_PROJECT_ROOT") or os.getcwd()
        self.env = {**os.environ, **(env or {})}
        self._command_log: list[dict] = []

    # =========================================================================
    # run_command: 범용 명령어 실행
    # =========================================================================
    def run_command(
        self,
        cmd: str,
        timeout: int = DEFAULT_TIMEOUT_SEC,
        cwd: str | None = None,
        safe_check: bool = True,
    ) -> dict:
        """
        명령어를 실행하고 결과를 반환합니다.
        
        Args:
            cmd: 실행할 명령어 문자열
            timeout: 최대 실행 시간 (초)
            cwd: 작업 디렉토리 (기본: 프로젝트 루트)
            safe_check: 안전성 검사 수행 여부
        
        Returns: {ok, returncode, stdout, stderr, elapsed_ms, command}
        """
        if safe_check:
            is_safe, reason = _is_command_safe(cmd)
            if not is_safe:
                result = {
                    "ok": False,
                    "error": f"[TerminalBridge] Command blocked: {reason}",
                    "command": cmd,
                }
                self._log_command(cmd, result)
                return result

        work_dir = cwd or self.cwd
        started = time.time()

        try:
            # Windows에서는 shell=True 필요, Unix에서는 shlex.split 사용
            if os.name == "nt":
                proc = subprocess.run(
                    ["cmd.exe", "/c", cmd],
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=max(5, timeout),
                    cwd=work_dir,
                    env=self.env,
                    check=False,
                )
            else:
                proc = subprocess.run(
                    shlex.split(cmd),
                    capture_output=True,
                    text=True,
                    timeout=max(5, timeout),
                    cwd=work_dir,
                    env=self.env,
                    check=False,
                )

            elapsed = int((time.time() - started) * 1000)
            result = {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": _truncate(proc.stdout or ""),
                "stderr": _truncate(proc.stderr or ""),
                "elapsed_ms": elapsed,
                "command": cmd,
            }
        except subprocess.TimeoutExpired:
            result = {
                "ok": False,
                "error": f"[TerminalBridge] Timeout after {timeout}s",
                "command": cmd,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except Exception as e:
            result = {
                "ok": False,
                "error": f"[TerminalBridge] {type(e).__name__}: {str(e)[:300]}",
                "command": cmd,
                "elapsed_ms": int((time.time() - started) * 1000),
            }

        self._log_command(cmd, result)
        return result

    # =========================================================================
    # run_test: 테스트 러너 전용 래퍼
    # =========================================================================
    def run_test(
        self,
        test_cmd: str = "pytest",
        args: str = "-v --tb=short",
        timeout: int = 60,
        cwd: str | None = None,
    ) -> dict:
        """
        pytest/jest/cargo test 등 테스트 러너를 실행합니다.
        결과를 파싱하여 passed/failed/error 카운트를 반환합니다.
        """
        full_cmd = f"{test_cmd} {args}".strip()
        result = self.run_command(full_cmd, timeout=timeout, cwd=cwd)

        # 테스트 결과 파싱 시도
        stdout = result.get("stdout", "")
        parsed = self._parse_test_output(stdout, test_cmd)
        result["test_summary"] = parsed

        return result

    def _parse_test_output(self, stdout: str, runner: str) -> dict:
        """테스트 러너 출력에서 결과 요약을 파싱합니다."""
        summary: dict = {"runner": runner, "parsed": False}

        # pytest 형식: "X passed, Y failed, Z errors"
        pytest_match = re.search(
            r"(\d+)\s+passed(?:,\s*(\d+)\s+failed)?(?:,\s*(\d+)\s+error)?",
            stdout,
        )
        if pytest_match:
            summary["parsed"] = True
            summary["passed"] = int(pytest_match.group(1) or 0)
            summary["failed"] = int(pytest_match.group(2) or 0)
            summary["errors"] = int(pytest_match.group(3) or 0)
            return summary

        # jest 형식: "Tests: X passed, Y failed"
        jest_match = re.search(
            r"Tests:\s*(?:(\d+)\s+failed,\s*)?(\d+)\s+passed",
            stdout,
        )
        if jest_match:
            summary["parsed"] = True
            summary["failed"] = int(jest_match.group(1) or 0)
            summary["passed"] = int(jest_match.group(2) or 0)
            return summary

        # 일반: 성공/실패 여부만 판단
        if "PASSED" in stdout.upper() or "OK" in stdout.upper():
            summary["parsed"] = True
            summary["status"] = "likely_passed"
        elif "FAILED" in stdout.upper() or "ERROR" in stdout.upper():
            summary["parsed"] = True
            summary["status"] = "likely_failed"

        return summary

    # =========================================================================
    # run_repl: 대화형 REPL 실행
    # =========================================================================
    def run_repl(
        self,
        runtime: str = "python",
        commands: list[str] | None = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> dict:
        """
        Python/Node REPL에 명령어를 순차 전송하고 결과를 수집합니다.
        
        Args:
            runtime: "python" or "node"
            commands: REPL에 보낼 명령어 리스트
            timeout: 전체 타임아웃
        
        Returns: {ok, outputs: [{command, output}], elapsed_ms}
        """
        if not commands:
            return {"ok": False, "error": "no_commands"}

        if len(commands) > MAX_REPL_ROUNDS:
            commands = commands[:MAX_REPL_ROUNDS]

        # 명령어들을 하나의 스크립트로 결합하여 실행 (비대화형)
        if runtime == "python":
            script = "\n".join(commands)
            cmd = f'python -c "{script}"' if os.name == "nt" else f"python3 -c '{script}'"
            # 복잡한 스크립트는 임시 파일로 실행
            return self._run_repl_via_tempfile(runtime, commands, timeout)
        elif runtime == "node":
            return self._run_repl_via_tempfile(runtime, commands, timeout)
        else:
            return {"ok": False, "error": f"unsupported_runtime: {runtime}"}

    def _run_repl_via_tempfile(
        self, runtime: str, commands: list[str], timeout: int
    ) -> dict:
        """임시 파일을 통한 REPL 실행 (안전)."""
        import tempfile

        ext = ".py" if runtime == "python" else ".js"
        exe = "python" if runtime == "python" else "node"
        
        # 각 명령의 출력을 구분 가능하게 마커 삽입
        wrapped_lines = []
        for i, cmd in enumerate(commands):
            marker = f"__REPL_MARKER_{i}__"
            if runtime == "python":
                wrapped_lines.append(f"print('{marker}')")
                wrapped_lines.append(cmd)
            else:
                wrapped_lines.append(f"console.log('{marker}')")
                wrapped_lines.append(cmd)

        script = "\n".join(wrapped_lines)

        try:
            tf = tempfile.NamedTemporaryFile(
                mode="w", suffix=ext, delete=False, encoding="utf-8"
            )
            tf.write(script)
            tf.close()
            temp_path = tf.name

            result = self.run_command(
                f"{exe} {temp_path}", timeout=timeout, safe_check=False
            )

            # 마커로 출력 분리
            outputs = []
            stdout = result.get("stdout", "")
            for i, cmd in enumerate(commands):
                marker = f"__REPL_MARKER_{i}__"
                next_marker = f"__REPL_MARKER_{i+1}__"
                start = stdout.find(marker)
                if start == -1:
                    outputs.append({"command": cmd, "output": ""})
                    continue
                start += len(marker) + 1  # skip newline
                end = stdout.find(next_marker, start)
                if end == -1:
                    end = len(stdout)
                outputs.append({
                    "command": cmd,
                    "output": stdout[start:end].strip(),
                })

            result["outputs"] = outputs
            return result

        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    # =========================================================================
    # Command Log
    # =========================================================================
    def _log_command(self, cmd: str, result: dict):
        """명령 실행 이력을 기록합니다."""
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "command": cmd[:200],
            "ok": result.get("ok", False),
            "returncode": result.get("returncode"),
            "elapsed_ms": result.get("elapsed_ms"),
        }
        self._command_log.append(entry)
        if len(self._command_log) > 50:
            self._command_log = self._command_log[-50:]

    def get_command_log(self, last_n: int = 10) -> list[dict]:
        """최근 명령 실행 이력을 반환합니다."""
        return self._command_log[-last_n:]
