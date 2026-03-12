"""
Human-in-the-Loop interrupt hook.

Pauses execution for user approval on specific tool calls
when auto_approve is not set.
"""
from __future__ import annotations

from typing import Any

from core.hooks.base import ContinuationHook, ToolCallDecision


class HumanInterruptHook(ContinuationHook):
    """
    Requires human approval for specified tool names.
    Integrates with the existing auto_approve flag in agent_state.
    """

    PRIORITY = 10

    def __init__(self, require_approval_tools: list[str] | None = None):
        self._require_approval = set(require_approval_tools or [])

    def pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict[str, Any]) -> ToolCallDecision:
        # Skip if auto_approve is enabled
        if agent_state.get("auto_approve", False):
            return ToolCallDecision(allowed=True, tool_args=dict(tool_args))

        # Skip if tool is not in the approval list (empty list = no restrictions)
        if self._require_approval and tool_name not in self._require_approval:
            return ToolCallDecision(allowed=True, tool_args=dict(tool_args))

        # Request human approval
        print(f"\n🔒 [HumanInterrupt] Tool '{tool_name}' requires approval.")
        print(f"   Args: {tool_args}")
        try:
            response = input("   Approve? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = "n"

        if response in ("y", "yes"):
            return ToolCallDecision(allowed=True, tool_args=dict(tool_args))

        return ToolCallDecision(
            allowed=False,
            tool_args=dict(tool_args),
            reason=f"Human denied tool call: {tool_name}",
        )
