#!/usr/bin/env python3
"""
scripts/design_review_trigger.py
=================================
설계 문서 변경 시 자동 교차 검증을 트리거하는 CLI wrapper.

핵심 로직은 core/design_review_utils.py에 위치.

호출 방식:
  1. Claude Code PostToolUse hook (자동)
  2. Gemini/Codex 지시사항 (수동)
  3. CLI: python scripts/design_review_trigger.py <filepath> [--source claude] [--sync] [--status]

항상 exit 0 — hook 차단 방지.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

# core/ 모듈 import를 위해 프로젝트 루트를 sys.path에 추가
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.design_review_utils import (  # noqa: E402
    enqueue,
    ensure_watcher,
    is_design_doc,
    normalize_path,
    run_sync,
    show_status,
)


def _detect_workspace() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


def main() -> None:
    parser = argparse.ArgumentParser(description="Design review trigger")
    parser.add_argument("filepath", nargs="?", help="설계 문서 경로")
    parser.add_argument("--source", default="unknown", help="트리거 소스 (claude|codex|gemini|manual)")
    parser.add_argument("--sync", action="store_true", help="동기 실행 (watcher 없이 바로 리뷰)")
    parser.add_argument("--status", action="store_true", help="큐 상태 조회")
    args = parser.parse_args()

    workspace = _detect_workspace()

    if args.status:
        show_status(workspace)
        sys.exit(0)

    if not args.filepath:
        sys.exit(0)

    filepath = os.path.abspath(args.filepath)

    if not is_design_doc(filepath, workspace):
        sys.exit(0)

    if args.sync:
        run_sync(filepath, workspace, args.source)
    else:
        enqueue(filepath, workspace, args.source)
        ensure_watcher(workspace)
        rel = normalize_path(filepath, workspace)
        print(f"[design-review] queued: {rel}")

    sys.exit(0)


if __name__ == "__main__":
    main()
