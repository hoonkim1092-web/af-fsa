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

        vague_keywords = ["아이디어", "해줘", "뭐할까", "아무거나", "대충", "알아서"]
        is_vague = any(kw in task_input for kw in vague_keywords) or len(task_input) < 10
        if is_vague:
            print("\n[IntentGateHook] ERROR: task intent is too vague.")
            print("[IntentGateHook] Please clarify the scope or provide a structured task.\n")
            return False
        return True


class TodoContinuationEnforcer(ContinuationHook):
    PRIORITY = 45

    def pre_execute(self, agent_state: dict) -> bool:
        task_input = str(agent_state.get("task_input", ""))
        intent = agent_state.get("intent", "trivial")
        risk_level = agent_state.get("risk_level", "normal")
        workspace = agent_state.get("workspace", os.getcwd())

        is_complex = len(task_input) > 30 or any(
            token in task_input.lower() for token in ["refactor", "build", "create", "implement"]
        )
        requires_plan = intent in ["refactoring", "greenfield", "complex_feature"] or risk_level in [
            "elevated",
            "strict",
        ] or is_complex

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
        if isinstance(result, dict) and "stdout" in result:
            output = str(result["stdout"])
            if len(output) > self.MAX_TRUNC_LENGTH:
                mutated = dict(result)
                mutated["stdout"] = output[: self.MAX_TRUNC_LENGTH].rstrip() + "\n[...truncated...]"
                return mutated
        return result

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        return self._truncate(result)

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        return self._truncate(result)
