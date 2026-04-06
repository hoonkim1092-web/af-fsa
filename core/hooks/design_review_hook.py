"""
core/hooks/design_review_hook.py
=================================
설계 문서 자동 교차 검증 훅 — af.exe 런타임 통합.

에이전트가 설계 문서(docs/features/, docs/plans/, *design*.md 등)를
작성/수정하면 자동으로 교차 검증 큐에 등록한다.

동작 환경:
  - af.exe (네이티브 API): post_tool_call → 즉시 감지
  - af.exe (CLI 프로바이더): pre_execute baseline → post_execute delta 비교

PRIORITY = 87 (CodeReviewDocHook=85 이후, CheckpointHook=90 이전)
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from core.design_review_utils import (
    NOTIFICATIONS_DIR,
    enqueue,
    ensure_watcher,
    is_design_doc,
)
from core.hooks.base import ContinuationHook, ToolCallDecision

logger = logging.getLogger(__name__)

# 파일 쓰기 tool 이름 (lsp_check.py와 동일)
_WRITE_TOOLS = frozenset({
    "write_file",
    "create_file",
    "save_file",
    "write_text",
    "edit_file",
    "patch_file",
    "overwrite_file",
})


def _extract_file_path(tool_args: dict) -> str | None:
    """tool_args에서 파일 경로를 추출한다."""
    for key in ("path", "file_path", "filename", "filepath", "target"):
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _git_changed_files(workspace: str) -> set[str]:
    """git diff로 변경된 파일 목록 반환 (상대경로 set)."""
    files: set[str] = set()
    for extra in [["HEAD"], ["--staged"]]:
        try:
            r = subprocess.run(
                ["git", "diff", "--name-only"] + extra,
                capture_output=True, text=True, cwd=workspace, timeout=10,
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    f = line.strip()
                    if f:
                        files.add(f)
        except subprocess.TimeoutExpired:
            logger.warning("[DesignReviewHook] git diff timeout (workspace=%s)", workspace)
        except FileNotFoundError:
            logger.debug("[DesignReviewHook] git not found")
        except Exception as exc:
            logger.warning("[DesignReviewHook] git diff failed: %s", exc)
    return files


class DesignReviewHook(ContinuationHook):
    """설계 문서 변경 시 자동으로 교차 검증 큐에 등록하는 훅."""

    PRIORITY = 87

    # ── pre_execute: baseline 스냅샷 ──────────────────────────────

    def pre_execute(self, agent_state: dict) -> bool:
        workspace = agent_state.get("workspace", "")
        agent_state["_dr_baseline"] = _git_changed_files(workspace) if workspace else set()
        return True  # 차단하지 않음

    # ── pre_tool_call: tool_args 저장 (post_tool_call에서 사용) ────

    def pre_tool_call(
        self, agent_state: dict, tool_name: str, tool_args: dict[str, Any],
    ) -> ToolCallDecision:
        if tool_name in _WRITE_TOOLS:
            agent_state["_dr_last_tool_args"] = tool_args
        return ToolCallDecision(allowed=True, tool_args=dict(tool_args))

    # ── post_tool_call: 네이티브 API 경로 (즉시 감지) ──────────────

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        if tool_name not in _WRITE_TOOLS:
            return result

        workspace = agent_state.get("workspace", "")
        if not workspace:
            return result

        tool_args = agent_state.get("_dr_last_tool_args", {}) or {}
        file_path = _extract_file_path(tool_args)
        if not file_path:
            return result

        if not os.path.isabs(file_path):
            file_path = os.path.join(workspace, file_path)

        try:
            if is_design_doc(file_path, workspace):
                enqueue(file_path, workspace, source="af_hook")
                ensure_watcher(workspace)
                logger.info("[DesignReviewHook] queued (tool_call): %s", file_path)
        except Exception as exc:
            logger.warning("[DesignReviewHook] post_tool_call failed: %s", exc)

        return result

    # ── post_execute: CLI 프로바이더 catch-all (baseline-delta) ────

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        workspace = agent_state.get("workspace", "")
        if not workspace:
            return result

        try:
            self._scan_delta(agent_state, workspace)
        except Exception as exc:
            logger.warning("[DesignReviewHook] scan failed: %s", exc)

        try:
            self._deliver_notifications(workspace)
        except Exception as exc:
            logger.warning("[DesignReviewHook] notification delivery failed: %s", exc)

        return result

    # ── 내부 메서드 ─────────────────────────────────────────────────

    def _scan_delta(self, agent_state: dict, workspace: str) -> None:
        """baseline과 비교해서 이번 실행에서 새로 변경된 설계 문서만 enqueue."""
        baseline = agent_state.get("_dr_baseline", set())
        current = _git_changed_files(workspace)
        new_changes = current - baseline

        enqueued = False
        for rel_path in new_changes:
            abs_path = os.path.join(workspace, rel_path)
            if not os.path.exists(abs_path):
                continue
            if is_design_doc(abs_path, workspace):
                enqueue(abs_path, workspace, source="af_hook")
                enqueued = True
                logger.info("[DesignReviewHook] queued (delta): %s", rel_path)

        if enqueued:
            ensure_watcher(workspace)

    def _deliver_notifications(self, workspace: str) -> None:
        """알림 파일을 읽어서 출력하고 삭제."""
        notif_dir = os.path.join(workspace, NOTIFICATIONS_DIR)
        if not os.path.isdir(notif_dir):
            return

        for fname in os.listdir(notif_dir):
            if not fname.endswith(".txt"):
                continue
            fpath = os.path.join(notif_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    print(f"[DesignReview] {content}")
                os.remove(fpath)
            except Exception:
                pass
