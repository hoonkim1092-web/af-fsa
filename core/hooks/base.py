from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCallDecision:
    allowed: bool = True
    tool_args: dict[str, Any] | None = None
    reason: str = ""


class ContinuationHook:
    """
    Base hook contract for agent and tool lifecycle interception.
    """

    PRIORITY = 50

    def pre_execute(self, agent_state: dict) -> bool:
        return True

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        return result

    def pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict[str, Any]) -> ToolCallDecision:
        return ToolCallDecision(allowed=True, tool_args=dict(tool_args))

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        return result
