from __future__ import annotations

import os
from typing import Any

from core.hooks.base import ContinuationHook


class IntentGateHook(ContinuationHook):
    PRIORITY = 40

    def pre_execute(self, agent_state: dict) -> bool:
        task_input = str(agent_state.get("task_input", "")).strip()
        if "Goal:\n" in task_input and "Constraints:\n" in task_input:
            return True

        # "해줘"는 한국어 일반 요청 어미 — 단독 사용 시만 모호함으로 판단
        vague_keywords = ["아이디어", "뭐할까", "아무거나", "대충", "알아서"]
        sole_haejwo = task_input in {"해줘", "해 줘"}
        is_vague = sole_haejwo or any(kw in task_input for kw in vague_keywords) or len(task_input) < 10
        if is_vague:
            print("\n[IntentGateHook] ERROR: task intent is too vague.")
            print("[IntentGateHook] Please clarify the scope or provide a structured task.\n")
            return False
        return True


class TodoContinuationEnforcer(ContinuationHook):
    PRIORITY = 45

    @staticmethod
    def requires_plan(task_input: str, intent: str = "trivial", risk_level: str = "normal") -> bool:
        text = str(task_input or "")
        normalized_intent = str(intent or "trivial")
        normalized_risk = str(risk_level or "normal")
        is_complex = len(text) > 30 or any(
            token in text.lower() for token in ["refactor", "build", "create", "implement"]
        )
        return normalized_intent in ["refactoring", "greenfield", "complex_feature"] or normalized_risk in [
            "elevated",
            "strict",
        ] or is_complex

    def pre_execute(self, agent_state: dict) -> bool:
        # 오케스트레이터 워커 서브프로세스: 이미 보드에서 배정된 태스크 → 훅 통과
        if os.environ.get("AGENT_WORKER_SUBPROCESS"):
            return True

        task_input = str(agent_state.get("task_input", ""))
        intent = agent_state.get("intent", "trivial")
        risk_level = agent_state.get("risk_level", "normal")
        workspace = agent_state.get("workspace", os.getcwd())

        requires_plan = self.requires_plan(task_input, intent=intent, risk_level=risk_level)

        if requires_plan:
            has_todo_file = os.path.exists(os.path.join(workspace, ".todo.md"))
            has_board = os.path.exists(os.path.join(workspace, "project_board_state.json"))
            if not (has_todo_file or has_board):
                print("\n[TodoContinuationEnforcer] ERROR: complex tasks require a structured plan.")
                print("[TodoContinuationEnforcer] Add `.todo.md` or a project board state first.\n")
                return False
        return True


class ToolOutputTruncator(ContinuationHook):
    PRIORITY = 10
    MAX_TRUNC_LENGTH = 16000

    def _truncate(self, result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        mutated = None
        for field in ("stdout", "stderr"):
            if field in result:
                output = str(result[field])
                if len(output) > self.MAX_TRUNC_LENGTH:
                    if mutated is None:
                        mutated = dict(result)
                    mutated[field] = output[: self.MAX_TRUNC_LENGTH].rstrip() + "\n[...truncated...]"
        return mutated if mutated is not None else result

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        return self._truncate(result)

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        return self._truncate(result)
