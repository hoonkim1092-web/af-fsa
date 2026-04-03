"""
core/hooks/code_review_doc.py
==============================
코드 리뷰 + 문서 자동 업데이트 훅.

에이전트 실행 성공 후 자동으로:
  1. git diff로 변경 파일 감지
  2. ControlPlaneLLM으로 코드 리뷰 생성 (LLM 없으면 skip)
  3. docs/code_review.md에 리뷰 결과 append
  4. docs/change_history.md에 변경 이력 append

PRIORITY = 85 (SkillSelfEvolutionHook=80 이후, CheckpointHook=90 이전)
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import datetime
from typing import Any

from core.hooks.base import ContinuationHook

logger = logging.getLogger(__name__)

CODE_REVIEW_REL_PATH = os.path.join("docs", "code_review.md")


class CodeReviewDocHook(ContinuationHook):
    """에이전트 실행 성공 후 코드 리뷰 + 문서 자동 업데이트."""

    PRIORITY = 85

    def __init__(self, min_files: int = 1, diff_max_chars: int = 8000):
        self._min_files = min_files
        self._diff_max_chars = diff_max_chars
        self._lock = threading.Lock()

    # ── Hook 인터페이스 ──────────────────────────────────────

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        """성공한 최종 결과에서만 트리거."""
        if not (isinstance(result, dict) and result.get("ok") and "reason" in result):
            return result
        workspace = agent_state.get("workspace", "")
        if not workspace:
            return result
        threading.Thread(
            target=self._run_review,
            args=(
                workspace,
                agent_state.get("run_id", ""),
                agent_state.get("task_input", ""),
            ),
            daemon=True,
        ).start()
        return result

    # ── 내부 로직 ────────────────────────────────────────────

    def _run_review(self, workspace: str, run_id: str, task_input: str) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._do_review(workspace, run_id, task_input)
        except Exception as exc:
            logger.warning("[CodeReviewDoc] review failed: %s", exc)
        finally:
            self._lock.release()

    def _do_review(self, workspace: str, run_id: str, task_input: str) -> None:
        changed = self._get_changed_files(workspace)
        if len(changed) < self._min_files:
            return

        diff_text = self._get_diff_content(workspace)
        review_text = self._llm_review(changed, diff_text, task_input)

        self._append_code_review(workspace, run_id, changed, review_text)
        self._append_change_history(workspace, run_id, changed, task_input)
        logger.info("[CodeReviewDoc] review completed — %d files, run=%s", len(changed), run_id)

    # ── git diff ─────────────────────────────────────────────

    def _get_changed_files(self, workspace: str) -> list[str]:
        """Unstaged + staged 변경 파일 목록."""
        files: list[str] = []
        for cmd_extra in [["HEAD"], ["--staged"]]:
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only"] + cmd_extra,
                    capture_output=True, text=True, cwd=workspace, timeout=10,
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        f = line.strip()
                        if f and f not in files:
                            files.append(f)
            except Exception:
                pass
        return files

    def _get_diff_content(self, workspace: str) -> str:
        """git diff HEAD 내용. diff_max_chars로 절단."""
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True, text=True, cwd=workspace, timeout=15,
            )
            if result.returncode == 0:
                text = result.stdout
                if len(text) > self._diff_max_chars:
                    return text[: self._diff_max_chars] + "\n... (truncated)"
                return text
        except Exception:
            pass
        return ""

    # ── LLM 리뷰 ────────────────────────────────────────────

    def _llm_review(self, changed: list[str], diff_text: str, task_input: str) -> str:
        """ControlPlaneLLM으로 코드 리뷰. LLM 없으면 빈 문자열."""
        try:
            from core.control_plane_llm import ControlPlaneLLM
            llm = ControlPlaneLLM()
            if not llm.is_available():
                return ""
        except Exception:
            return ""

        files_str = ", ".join(changed[:20])
        prompt = f"""You are a senior code reviewer. Review the following changes concisely.

[Task Context]
{task_input[:500]}

[Changed Files ({len(changed)})]
{files_str}

[Diff]
{diff_text[:6000]}

Provide a brief review in this format:
- [Severity] file:line — finding (one line each)

Severity levels: Critical, High, Medium, Low, Info
Focus on: security issues, bugs, error handling gaps, performance problems.
If no issues found, write "No issues found."
Keep the review under 20 lines."""

        try:
            return llm.generate(prompt) or ""
        except Exception as exc:
            logger.warning("[CodeReviewDoc] LLM review failed: %s", exc)
            return ""

    # ── 문서 쓰기 ────────────────────────────────────────────

    def _append_code_review(
        self, workspace: str, run_id: str, changed: list[str], review_text: str,
    ) -> None:
        """docs/code_review.md에 리뷰 섹션 append."""
        path = os.path.join(os.path.abspath(workspace), CODE_REVIEW_REL_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        files_str = ", ".join(changed[:15])
        if len(changed) > 15:
            files_str += f" ... (+{len(changed) - 15})"

        section = (
            f"\n---\n\n"
            f"## Review — {run_id} ({date_str})\n\n"
            f"**Changed files ({len(changed)})**: {files_str}\n\n"
        )
        if review_text:
            section += f"### Findings\n\n{review_text.strip()}\n"
        else:
            section += "*LLM unavailable — file list only.*\n"

        if not os.path.exists(path):
            header = "# Code Review Log\n\n> Auto-generated by agent-factory CodeReviewDocHook.\n"
            self._atomic_write(path, header + section)
        else:
            self._atomic_append(path, section)

    def _append_change_history(
        self, workspace: str, run_id: str, changed: list[str], task_input: str,
    ) -> None:
        """docs/change_history.md에 엔트리 append."""
        try:
            from core.documentation_policy import ensure_documentation_files, CHANGE_HISTORY_REL_PATH
            ensure_documentation_files(workspace)
        except Exception:
            pass

        try:
            from core.documentation_policy import CHANGE_HISTORY_REL_PATH
        except ImportError:
            return

        path = os.path.join(os.path.abspath(workspace), CHANGE_HISTORY_REL_PATH)
        if not os.path.exists(path):
            return

        date_str = datetime.now().strftime("%Y-%m-%d")
        summary = task_input[:80].replace("|", "/").replace("\n", " ")
        files_short = ", ".join(f[:30] for f in changed[:5])
        if len(changed) > 5:
            files_short += f" +{len(changed) - 5}"

        entry = f"| {date_str} | {run_id[:24]} | {summary} | {files_short} |\n"
        self._atomic_append(path, entry)

    # ── 파일 I/O 유틸 ────────────────────────────────────────

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    @staticmethod
    def _atomic_append(path: str, content: str) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
