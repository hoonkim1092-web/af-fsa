"""
core/control/work_kind.py
=========================
WorkKindClassifier — 2차 작업 유형 분류기.

IntentGate의 1차 분류(trivial/question/refactoring/greenfield/debugging)를
변경하지 않고, 그 결과와 workspace 상태를 조합해 2차 분류한다.

work_kind:  "new_project" | "maintenance" | "bugfix" | "feature_update" | "refactor"
issue_kind: "incident" | "planned_update" | "regression" | "backlog_item" | "research_task"
"""
from __future__ import annotations

import os
import subprocess

# ──────────────────────────────────────────────────────────────
# 분류 규칙 테이블
# ──────────────────────────────────────────────────────────────

# intent × board_exists → work_kind
# 우선순위: 위에서 아래로 (첫 번째 매칭 사용)
_WORK_KIND_PRIORITY: list[tuple[str, bool | None, str]] = [
    # (intent, board_exists, work_kind)
    # board_exists=None 이면 무관
    ("debugging",   None,  "bugfix"),
    ("refactoring", True,  "refactor"),
    ("refactoring", False, "refactor"),
    ("greenfield",  False, "new_project"),
    ("greenfield",  True,  "feature_update"),
    ("trivial",     True,  "maintenance"),
    ("trivial",     False, "maintenance"),
    ("question",    None,  "maintenance"),
]

ISSUE_KIND_MAP: dict[str, str] = {
    "bugfix":         "incident",
    "refactor":       "planned_update",
    "feature_update": "planned_update",
    "maintenance":    "planned_update",
    "new_project":    "backlog_item",
}

# 작업 유형별 키워드 힌트 (task_input에서 보조 신호로 사용)
_MAINTENANCE_KEYWORDS = [
    "수정", "업데이트", "개선", "변경", "보완", "정리",
    "update", "improve", "refine", "adjust", "change", "modify",
]
_BUGFIX_KEYWORDS = [
    "버그", "오류", "에러", "수정", "고쳐", "fix", "bug", "error", "crash", "broken",
]
_FEATURE_KEYWORDS = [
    "추가", "기능", "신규", "add", "feature", "new", "implement",
]


class WorkKindClassifier:
    """IntentGate 결과를 기반으로 work_kind / issue_kind를 2차 분류한다."""

    def classify(self, route: dict, workspace: str) -> tuple[str, str]:
        """
        Returns: (work_kind, issue_kind)

        판단 신호 (우선순위 순):
          1. route["intent"] — IntentGate 1차 분류 결과
          2. workspace에 project_board_state.json 존재 여부
          3. task_input 키워드 힌트 (보조)
        """
        intent = str(route.get("intent") or "question")
        task_input = str(route.get("task_input") or route.get("reasoning") or "").lower()
        board_exists = self._board_exists(workspace)

        work_kind = self._classify_work_kind(intent, board_exists, task_input)
        issue_kind = ISSUE_KIND_MAP.get(work_kind, "planned_update")

        # regression 감지: bugfix + 이전 bugfix 이력 있으면 regression으로 조정
        if work_kind == "bugfix" and self._has_prior_bugfix(workspace):
            issue_kind = "regression"

        return work_kind, issue_kind

    # ── 내부 메서드 ──

    def _classify_work_kind(self, intent: str, board_exists: bool, task_input: str) -> str:
        """우선순위 테이블로 work_kind를 결정한다."""
        for rule_intent, rule_board, result in _WORK_KIND_PRIORITY:
            if rule_intent != intent:
                continue
            if rule_board is None or rule_board == board_exists:
                return result

        # fallback: board 있으면 maintenance, 없으면 new_project
        return "maintenance" if board_exists else "new_project"

    def _board_exists(self, workspace: str) -> bool:
        """workspace에 project_board_state.json이 있는지 확인한다."""
        return os.path.isfile(os.path.join(workspace, "project_board_state.json"))

    def _has_prior_bugfix(self, workspace: str) -> bool:
        """run_ledger에서 이전 bugfix 이력을 확인한다."""
        ledger_path = os.path.join(workspace, ".af_runtime", "control", "run_ledger.jsonl")
        if not os.path.isfile(ledger_path):
            return False
        try:
            with open(ledger_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    import json
                    entry = json.loads(line)
                    if entry.get("work_kind") == "bugfix" and entry.get("outcome") == "success":
                        return True
        except Exception:
            pass
        return False


__all__ = ["WorkKindClassifier", "ISSUE_KIND_MAP"]
