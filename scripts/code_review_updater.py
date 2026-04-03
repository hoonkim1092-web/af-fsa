#!/usr/bin/env python3
"""
scripts/code_review_updater.py
================================
Standalone code review updater — called from:
  1. Claude Code PostToolUse hook  (after .py edits, --no-llm fast path)
  2. git post-commit hook
  3. Manual: python scripts/code_review_updater.py [workspace] [--context "..."]

Updates docs/code-review.md — the persistent living code review document.
Always exits 0 so hooks are never blocked by review failures.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime

REVIEW_DOC_REL = os.path.join("docs", "code-review.md")
DIFF_MAX_CHARS = 8000


# ── git helpers ──────────────────────────────────────────────────────────────

def _git(args: list[str], cwd: str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(
            ["git"] + args, capture_output=True, text=True, cwd=cwd, timeout=timeout,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _detect_workspace() -> str:
    """Walk up from CWD to find git root."""
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


def _changed_files(workspace: str) -> list[str]:
    """Unstaged + staged changed files."""
    seen: list[str] = []
    for extra in [["HEAD"], ["--staged"]]:
        for line in _git(["diff", "--name-only"] + extra, workspace).splitlines():
            f = line.strip()
            if f and f not in seen:
                seen.append(f)
    return seen


def _diff_content(workspace: str) -> str:
    out = _git(["diff", "HEAD"], workspace, timeout=15)
    if len(out) > DIFF_MAX_CHARS:
        return out[:DIFF_MAX_CHARS] + "\n... (truncated)"
    return out


def _branch(workspace: str) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], workspace).strip()


def _short_commit(workspace: str) -> str:
    return _git(["rev-parse", "--short", "HEAD"], workspace).strip()


# ── LLM review (best-effort) ─────────────────────────────────────────────────

def _llm_review(changed: list[str], diff_text: str, context: str) -> str:
    """Return LLM code review string, or '' if unavailable."""
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from core.control_plane_llm import ControlPlaneLLM  # type: ignore
        llm = ControlPlaneLLM()
        if not llm.is_available():
            return ""
    except Exception:
        return ""

    files_str = ", ".join(changed[:20])
    prompt = (
        "You are a senior code reviewer. Review the following changes concisely.\n\n"
        f"[Context]\n{(context or 'Claude Code edit session')[:300]}\n\n"
        f"[Changed Files ({len(changed)})]\n{files_str}\n\n"
        f"[Diff]\n{diff_text[:5000]}\n\n"
        "Format: - [Severity] file:line — finding\n"
        "Severity: Critical | High | Medium | Low | Info\n"
        "Focus: security, bugs, error handling gaps.\n"
        "If no issues: write 'No issues found.'\n"
        "Max 15 lines."
    )
    try:
        return llm.generate(prompt) or ""
    except Exception:
        return ""


# ── doc write helpers ────────────────────────────────────────────────────────

def _atomic_write(path: str, content: str) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _atomic_append(path: str, content: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


# ── main logic ───────────────────────────────────────────────────────────────

def _last_logged_commit(doc_path: str) -> str:
    """Return the commit hash of the most recent entry in the doc, or ''."""
    if not os.path.exists(doc_path):
        return ""
    try:
        with open(doc_path, encoding="utf-8") as f:
            content = f.read()
        # Entries look like: ## 2026-04-03 22:02 — `branch` (abc1234)
        matches = re.findall(r"\(([0-9a-f]{7,40})\)", content)
        return matches[-1] if matches else ""
    except Exception:
        return ""


def update_code_review_doc(workspace: str, context: str, no_llm: bool) -> bool:
    """Update docs/code-review.md. Returns True if any entry was written."""
    changed = _changed_files(workspace)
    if not changed:
        return False

    commit = _short_commit(workspace)
    doc_path = os.path.join(os.path.abspath(workspace), REVIEW_DOC_REL)
    if commit and _last_logged_commit(doc_path) == commit:
        # Same commit already logged — skip to avoid duplicate entries per session
        return False

    diff_text = "" if no_llm else _diff_content(workspace)
    review_text = "" if no_llm else _llm_review(changed, diff_text, context)

    branch = _branch(workspace)
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    files_str = ", ".join(changed[:15])
    if len(changed) > 15:
        files_str += f" ... (+{len(changed) - 15})"

    section = (
        f"\n---\n\n"
        f"## {date_str} — `{branch}` ({commit})\n\n"
        f"**Context**: {context or 'Claude Code edit session'}\n\n"
        f"**Changed ({len(changed)})**: `{files_str}`\n\n"
    )
    if review_text:
        section += f"### Findings\n\n{review_text.strip()}\n"
    else:
        section += "_Review skipped (--no-llm or LLM unavailable)_\n"

    os.makedirs(os.path.dirname(doc_path), exist_ok=True)

    if not os.path.exists(doc_path):
        header = (
            "# Code Review — Living Document\n\n"
            "> Auto-updated on every `.py` edit and AF agent run.\n"
            "> Persistent across sessions. "
            "See `docs/YYYY-MM-DD-code-review.md` for point-in-time snapshots.\n"
        )
        _atomic_write(doc_path, header + section)
    else:
        _atomic_append(doc_path, section)

    print(f"[code_review_updater] {doc_path} updated - {len(changed)} changed file(s)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update docs/code-review.md with latest git diff review"
    )
    parser.add_argument(
        "workspace", nargs="?", default=None,
        help="Workspace/git-root directory (default: auto-detect from CWD)"
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM review — fast mode for hook use"
    )
    parser.add_argument(
        "--context", default="",
        help="Short description of current work (appended to entry header)"
    )
    args = parser.parse_args()

    workspace = args.workspace or _detect_workspace()
    if not os.path.isdir(workspace):
        print(f"[code_review_updater] workspace not found: {workspace}", file=sys.stderr)
        sys.exit(0)  # still exit 0 — don't break hooks

    update_code_review_doc(workspace, args.context, args.no_llm)
    sys.exit(0)


if __name__ == "__main__":
    main()
