"""
core/design_review_utils.py
=============================
설계 문서 교차 검증 — shared 로직.

사용처:
  1. core/hooks/design_review_hook.py (af.exe 런타임)
  2. scripts/design_review_trigger.py (Claude Code hook / CLI)
  3. scripts/design_review_watcher.py (백그라운드 데몬)

이 모듈은 frozen 빌드(af.exe)에서도 동작해야 하므로 core/에 위치한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from fnmatch import fnmatch
from pathlib import Path

# ── 트리거 대상 패턴 ─────────────────────────────────────────────────────────

INCLUDE_PATTERNS = [
    "docs/features/**/*.md",
    "docs/features/*.md",
    "docs/plans/**/*.md",
    "docs/plans/*.md",
    "docs/**/*design*.md",
    "docs/**/*feature*.md",
    "docs/*design*.md",
    "docs/*feature*.md",
]

EXCLUDE_PATTERNS = [
    "docs/code_review/**",
    "docs/code_review/*",
    "docs/reviews/**",
    "docs/reviews/*",
]

QUEUE_DIR = ".af_review_queue"
PENDING_DIR = os.path.join(QUEUE_DIR, "pending")
NOTIFICATIONS_DIR = os.path.join(QUEUE_DIR, "notifications")
PID_FILE = os.path.join(QUEUE_DIR, ".watcher.pid")


# ── 경로 매칭 ────────────────────────────────────────────────────────────────

def normalize_path(filepath: str, workspace: str) -> str:
    """절대경로를 workspace 기준 상대경로로 변환. 슬래시 통일."""
    try:
        rel = os.path.relpath(filepath, workspace)
    except ValueError:
        rel = filepath
    return rel.replace("\\", "/")


def _matches_glob(path: str, pattern: str) -> bool:
    """간단한 glob 매칭. ** = 임의 디렉토리, * = 임의 파일명."""
    if "**" in pattern:
        prefix, _, suffix = pattern.partition("**")
        prefix = prefix.rstrip("/")
        suffix = suffix.lstrip("/")
        if prefix and not path.startswith(prefix + "/") and path != prefix:
            return False
        remainder = path[len(prefix):].lstrip("/") if prefix else path
        if suffix:
            parts = remainder.split("/")
            for i in range(len(parts)):
                candidate = "/".join(parts[i:])
                if fnmatch(candidate, suffix):
                    return True
            return False
        return True
    return fnmatch(path, pattern)


def is_design_doc(filepath: str, workspace: str) -> bool:
    """파일이 설계 문서 트리거 대상인지 판정."""
    rel = normalize_path(filepath, workspace)

    for pattern in EXCLUDE_PATTERNS:
        if _matches_glob(rel, pattern):
            return False

    for pattern in INCLUDE_PATTERNS:
        if _matches_glob(rel, pattern):
            return True

    return False


# ── 큐 관리 ──────────────────────────────────────────────────────────────────

def pathhash(rel_path: str) -> str:
    """상대경로의 deterministic hash (앞 12자리)."""
    return hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:12]


def enqueue(filepath: str, workspace: str, source: str = "unknown") -> str | None:
    """pending 큐에 리뷰 요청 추가. 이미 있으면 timestamp만 갱신."""
    rel = normalize_path(filepath, workspace)
    ph = pathhash(rel)
    pending_dir = os.path.join(workspace, PENDING_DIR)
    os.makedirs(pending_dir, exist_ok=True)

    queue_file = os.path.join(pending_dir, f"{ph}.json")
    entry = {
        "file_path": rel,
        "timestamp": time.time(),
        "trigger_source": source,
        "workspace": workspace,
    }
    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)

    return queue_file


# ── watcher 관리 ──────────────────────────────────────────────────────────────

def _is_watcher_alive(workspace: str) -> bool:
    """PID 파일 기반 watcher 생존 확인."""
    pid_path = os.path.join(workspace, PID_FILE)
    if not os.path.exists(pid_path):
        return False
    try:
        with open(pid_path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError, ProcessLookupError):
        try:
            os.remove(pid_path)
        except OSError:
            pass
        return False


def _start_watcher(workspace: str) -> None:
    """watcher를 detached 백그라운드 프로세스로 시작."""
    watcher_script = os.path.join(workspace, "scripts", "design_review_watcher.py")
    if not os.path.exists(watcher_script):
        return

    kwargs: dict = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(
            [sys.executable, watcher_script, workspace],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=workspace,
            **kwargs,
        )
    except Exception:
        pass


def ensure_watcher(workspace: str) -> None:
    """watcher가 없으면 시작."""
    if not _is_watcher_alive(workspace):
        _start_watcher(workspace)


# ── 상태 조회 ─────────────────────────────────────────────────────────────────

def show_status(workspace: str) -> None:
    """큐 상태 출력."""
    pending_dir = os.path.join(workspace, PENDING_DIR)
    notif_dir = os.path.join(workspace, NOTIFICATIONS_DIR)

    pending = []
    if os.path.isdir(pending_dir):
        pending = [f for f in os.listdir(pending_dir) if f.endswith(".json")]

    notifs = []
    if os.path.isdir(notif_dir):
        notifs = [f for f in os.listdir(notif_dir) if f.endswith(".txt")]

    alive = _is_watcher_alive(workspace)

    print(f"[design-review] Watcher: {'running' if alive else 'stopped'}")
    print(f"[design-review] Pending: {len(pending)}")
    for p in pending:
        try:
            with open(os.path.join(pending_dir, p)) as f:
                data = json.load(f)
            print(f"  - {data.get('file_path', '?')}")
        except Exception:
            print(f"  - {p} (unreadable)")
    print(f"[design-review] Notifications: {len(notifs)}")


# ── 동기 실행 ─────────────────────────────────────────────────────────────────

def run_sync(filepath: str, workspace: str, source: str) -> None:
    """watcher 없이 동기적으로 리뷰 실행."""
    watcher_script = os.path.join(workspace, "scripts", "design_review_watcher.py")
    if not os.path.exists(watcher_script):
        print("[design-review] watcher script not found", file=sys.stderr)
        return

    rel = normalize_path(filepath, workspace)
    try:
        subprocess.run(
            [sys.executable, watcher_script, workspace, "--sync", rel],
            cwd=workspace,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        print("[design-review] sync review timed out (900s)", file=sys.stderr)
    except Exception as e:
        print(f"[design-review] sync review failed: {e}", file=sys.stderr)
