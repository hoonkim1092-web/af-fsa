"""
core/synergy/process.py
=======================
프로세스 관리 및 IO 유틸리티.

단일 책임:
  - _truncate     → 긴 문자열 자르기
  - _kill_tree    → 프로세스 트리 강제 종료 (Windows/Unix 통합)
  - OmoDetector   → OmO 경로/커맨드 자동 탐지 및 argv 조립
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path


# =============================================================================
# IO 유틸
# =============================================================================

def _truncate(text: str, limit: int) -> str:
    """텍스트를 limit 글자로 자르고 '...'를 붙인다."""
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)].rstrip() + "..."


# =============================================================================
# 프로세스 트리 종료
#
# ⚠️ Windows: Popen.terminate()/kill()은 직계 자식만 종료.
#             taskkill /F /T /PID로 손자(npm→node 등) 고아 방지.
# ⚠️ Unix: os.killpg로 프로세스 그룹 전체 시그널.
# =============================================================================

def _kill_tree(pid: int) -> None:
    """프로세스 트리 전체를 강제 종료한다 (Windows/Unix 플랫폼 호환)."""
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        # 이미 종료된 프로세스 등 예외는 무시 (best-effort)
        pass


# =============================================================================
# OmO 경로 및 커맨드 자동 탐지
# =============================================================================

class OmoDetector:
    """OmO(Oh My OpenCode) 런타임 위치와 실행 커맨드를 자동 탐지한다.

    탐지 우선순위:
      1. OMO_PATH 환경변수
      2. 인접 디렉토리 (oh-my-opencode-dev / oh-my-opencode)
      3. agent-factory 내부 임시 경로

    커맨드 우선순위:
      1. OMO_ULTRAWORK_CMD 환경변수 (shell 문자열)
      2. ultrawork.py Python 스크립트
      3. npm/bun scripts.ultrawork (package.json 기반)
    """

    def __init__(self, base_dir: str):
        self._base_dir = base_dir

    # ------------------------------------------------------------------
    # 경로 탐지
    # ------------------------------------------------------------------

    def detect_path(self) -> str | None:
        """OmO 설치 경로를 탐지한다. 없으면 None."""
        raw = str(os.getenv("OMO_PATH", "")).strip()
        candidates: list[str] = []
        if raw:
            candidates.append(raw)
        parent = os.path.dirname(self._base_dir)
        candidates.extend([
            os.path.join(parent, "oh-my-opencode-dev"),
            os.path.join(parent, "oh-my-opencode"),
            os.path.join(self._base_dir, "tmp_oh_my_opencode_review"),
        ])
        for c in candidates:
            if c and os.path.isdir(c):
                return os.path.abspath(c)
        return None

    # ------------------------------------------------------------------
    # 커맨드 탐지
    # ------------------------------------------------------------------

    def resolve_cmd(self, omo_path: str) -> dict | None:
        """OmO 실행 커맨드를 탐지한다. 없으면 None.

        Returns:
            {"kind": "shell", "cmd": "..."} 또는 {"kind": "argv", "cmd": [...]}
        """
        env_cmd = str(os.getenv("OMO_ULTRAWORK_CMD", "")).strip()
        if env_cmd:
            return {"kind": "shell", "cmd": env_cmd}

        py_candidates = [
            os.path.join(omo_path, "scripts", "ultrawork.py"),
            os.path.join(omo_path, "ultrawork.py"),
            os.path.join(omo_path, "bin", "ultrawork.py"),
        ]
        for p in py_candidates:
            if os.path.isfile(p):
                return {"kind": "argv", "cmd": [sys.executable, p]}

        package_json = os.path.join(omo_path, "package.json")
        try:
            if os.path.isfile(package_json):
                pkg = json.loads(Path(package_json).read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
                if isinstance(scripts, dict) and "ultrawork" in scripts:
                    if shutil.which("bun"):
                        return {"kind": "argv", "cmd": ["bun", "run", "ultrawork", "--"]}
                    if shutil.which("npm"):
                        return {"kind": "argv", "cmd": ["npm", "run", "ultrawork", "--"]}
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # argv 조립
    # ------------------------------------------------------------------

    _ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(.*)$")

    @staticmethod
    def build_argv(cmd: dict, task: str) -> tuple[list[str], dict[str, str]]:
        """Build argv plus env overrides with cross-platform env-assignment parsing."""
        text = str(task or "").strip()
        env_overrides: dict[str, str] = {}
        if cmd.get("kind") == "shell":
            raw = str(cmd.get("cmd") or "").strip()
            posix_mode = platform.system() != "Windows"
            tokens = shlex.split(raw, posix=posix_mode)
            argv: list[str] = []
            for token in tokens:
                if not argv:
                    m = OmoDetector._ENV_ASSIGN_RE.match(token)
                    if m:
                        key = token.split("=", 1)[0]
                        env_overrides[key] = m.group(1)
                        continue
                argv.append(token)
        else:
            argv = list(cmd.get("cmd") or [])
        if not argv:
            raise ValueError("empty_command_after_env_parse")
        argv.append(text)
        return argv, env_overrides

    # ------------------------------------------------------------------
    # OS별 Popen 키워드 인자
    # ------------------------------------------------------------------

    @staticmethod
    def popen_platform_kwargs() -> dict:
        """새 프로세스 그룹 격리를 위한 OS별 Popen 키워드 인자 반환."""
        if platform.system() == "Windows":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}
