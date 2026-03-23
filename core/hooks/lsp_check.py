"""
core/hooks/lsp_check.py
=======================
LSP(Language Server Protocol) 기반 실시간 정적 분석 훅.

에이전트가 파일을 작성한 직후 Pyright(Python)로 정적 분석을 수행하고
진단 결과를 tool 결과에 추가합니다. LLM은 다음 턴에 이 피드백을 보고
실행 전에 에러를 스스로 수정할 수 있습니다.

활성화: AGENT_LSP_CHECK=1 환경변수 설정 (기본 비활성)

지원 언어:
  - Python (.py) → pyright --outputjson
  - 기타 → 분석 스킵

비용: 파일 저장 시에만 실행 (pre_tool_call 아닌 post_tool_call)
      pyright는 온디맨드 방식으로 실행 (상시 프로세스 없음)
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from core.hooks.base import ContinuationHook

# 파일 쓰기/생성으로 판단할 tool 이름 목록
_WRITE_TOOLS = frozenset({
    "write_file",
    "create_file",
    "save_file",
    "write_text",
    "edit_file",
    "patch_file",
    "overwrite_file",
})

# pyright 타임아웃 (초) — 너무 크면 ReAct 루프가 막힘
_PYRIGHT_TIMEOUT = 15

# 최대 출력할 진단 수 — LLM 컨텍스트 절약
_MAX_DIAG = 10


def _is_enabled() -> bool:
    return bool(os.getenv("AGENT_LSP_CHECK"))


# 캐싱: pyright 경로를 매 호출마다 재탐색하지 않음
_pyright_path_cache: str | None | bool = False  # False = 미탐색, None = 없음, str = 경로


def _find_pyright() -> str | None:
    """pyright 실행 파일 경로 탐색. 없으면 None. 결과를 캐싱."""
    global _pyright_path_cache
    if _pyright_path_cache is not False:
        return _pyright_path_cache  # type: ignore[return-value]

    # PATH에서 찾기
    for name in ("pyright", "pyright.cmd"):
        try:
            result = subprocess.run(
                [name, "--version"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                _pyright_path_cache = name
                return name
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    # node_modules/.bin에서 찾기 (npm local 설치)
    local = os.path.join("node_modules", ".bin", "pyright")
    if os.path.isfile(local):
        _pyright_path_cache = local
        return local

    # Bug fix: AGENT_LSP_CHECK=1인데 pyright가 없으면 경고 출력
    if _is_enabled():
        print(
            "[LSPCheckHook] 경고: AGENT_LSP_CHECK=1이지만 pyright를 찾을 수 없습니다. "
            "설치: npm install -g pyright"
        )
    _pyright_path_cache = None
    return None


def _validate_file_path(file_path: str) -> bool:
    """분석 대상 파일 경로의 유효성을 검증한다.
    - 절대/상대 경로 정규화
    - 프로젝트 외부 경로 접근 차단 (path traversal 방지)
    """
    cwd = os.path.abspath(os.getcwd())
    abs_path = os.path.abspath(file_path)
    # 현재 작업 디렉토리 하위인지 확인
    try:
        abs_path.replace("\\", "/").startswith(cwd.replace("\\", "/"))
        return abs_path.startswith(cwd)
    except Exception:
        return False


def _run_pyright(file_path: str) -> list[dict]:
    """pyright --outputjson으로 진단 결과를 반환한다.
    실패하거나 pyright 없으면 빈 리스트 반환.
    """
    pyright = _find_pyright()
    if not pyright:
        return []

    try:
        proc = subprocess.run(
            [pyright, "--outputjson", file_path],
            capture_output=True,
            timeout=_PYRIGHT_TIMEOUT,
            cwd=os.getcwd(),
        )
        raw = proc.stdout.decode("utf-8", errors="replace")
        if not raw.strip():
            return []
        data = json.loads(raw)
        diags = []
        for summary in data.get("generalDiagnostics", []):
            sev = str(summary.get("severity", "")).lower()
            if sev not in ("error", "warning"):
                continue
            diags.append({
                "severity": sev,
                "message": summary.get("message", ""),
                "line": summary.get("range", {}).get("start", {}).get("line", 0) + 1,
                "rule": summary.get("rule", ""),
            })
        return diags[:_MAX_DIAG]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return []


def _extract_file_path(tool_args: dict) -> str | None:
    """tool_args에서 파일 경로를 추출한다."""
    for key in ("path", "file_path", "filename", "filepath", "target"):
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _is_python_file(path: str) -> bool:
    return path.endswith(".py")


class LSPCheckHook(ContinuationHook):
    """파일 쓰기 tool 호출 후 Pyright로 정적 분석을 수행하는 훅.

    AGENT_LSP_CHECK=1 환경변수가 설정된 경우에만 활성화된다.
    진단 결과는 tool 결과 딕셔너리의 "lsp_diagnostics" 키로 추가된다.
    """
    PRIORITY = 8  # ToolOutputTruncator(10)보다 먼저 실행

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        if not _is_enabled():
            return result
        if tool_name not in _WRITE_TOOLS:
            return result

        # tool_args는 pre_tool_call에서 agent_state에 저장되어 있어야 함
        # 없으면 스킵
        tool_args = agent_state.get("_last_tool_args", {}) or {}
        file_path = _extract_file_path(tool_args)
        if not file_path:
            return result
        if not _is_python_file(file_path):
            return result
        # Bug fix: path traversal 방지 — 프로젝트 외부 파일 분석 차단
        if not _validate_file_path(file_path):
            return result
        if not os.path.isfile(file_path):
            return result

        diags = _run_pyright(file_path)
        if not diags:
            return result

        # 결과에 진단 정보 추가
        if isinstance(result, dict):
            result = dict(result)
            result["lsp_diagnostics"] = diags
            result["lsp_summary"] = (
                f"[LSP] {file_path}: {len(diags)} issue(s) found — "
                + "; ".join(
                    f"L{d['line']} [{d['severity']}] {d['message'][:80]}"
                    for d in diags
                )
            )
        else:
            # 문자열 결과면 LSP 요약을 뒤에 붙임
            result = (
                str(result) + "\n\n"
                + f"[LSP Diagnostics] {file_path}:\n"
                + "\n".join(
                    f"  L{d['line']} [{d['severity']}] {d['message']}"
                    for d in diags
                )
            )

        print(f"[LSPCheckHook] {file_path}: {len(diags)} diagnostic(s) injected into result")
        return result

    def pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict) -> Any:
        """post_tool_call에서 file_path를 꺼낼 수 있도록 args를 agent_state에 저장."""
        if tool_name in _WRITE_TOOLS:
            agent_state["_last_tool_args"] = tool_args
        from core.hooks.base import ToolCallDecision
        return ToolCallDecision(allowed=True, tool_args=dict(tool_args))
