from __future__ import annotations

from typing import Any

from core.hooks.base import ToolCallDecision
from core.hooks.guardrails import IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator
from core.hooks.langsmith_tracing import LangSmithTracingHook


class HookEventBus:
    """
    Priority-ordered dispatch for agent and tool lifecycle hooks.
    """

    def __init__(self):
        self._pre_hooks: list[Any] = []
        self._post_hooks: list[Any] = []
        self._pre_tool_hooks: list[Any] = []
        self._post_tool_hooks: list[Any] = []

        # Auto-register LangSmith tracing when API key is present
        import os
        if os.getenv("LANGSMITH_API_KEY"):
            self.register(LangSmithTracingHook())

    def register(self, hook: Any):
        if hasattr(hook, "pre_execute"):
            self._pre_hooks.append(hook)
        if hasattr(hook, "post_execute"):
            self._post_hooks.append(hook)
        if hasattr(hook, "pre_tool_call"):
            self._pre_tool_hooks.append(hook)
        if hasattr(hook, "post_tool_call"):
            self._post_tool_hooks.append(hook)

        ordered = (
            self._pre_hooks,
            self._post_hooks,
            self._pre_tool_hooks,
            self._post_tool_hooks,
        )
        for collection in ordered:
            collection.sort(key=lambda item: getattr(item, "PRIORITY", 50))

    def run_pre_execute(self, agent_state: dict) -> bool:
        for hook in self._pre_hooks:
            if not hook.pre_execute(agent_state):
                print(f"[HookEventBus] Execution blocked by {hook.__class__.__name__}")
                return False
        return True

    def run_post_execute(self, agent_state: dict, result: Any) -> Any:
        current = result
        for hook in self._post_hooks:
            current = hook.post_execute(agent_state, current)
        return current

    def run_pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict[str, Any]) -> ToolCallDecision:
        current_args = dict(tool_args)
        for hook in self._pre_tool_hooks:
            decision = self._normalize_decision(hook.pre_tool_call(agent_state, tool_name, current_args), current_args)
            current_args = dict(decision.tool_args or {})
            if not decision.allowed:
                return ToolCallDecision(allowed=False, tool_args=current_args, reason=decision.reason)
        return ToolCallDecision(allowed=True, tool_args=current_args)

    def run_post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        current = result
        for hook in self._post_tool_hooks:
            current = hook.post_tool_call(agent_state, tool_name, current)
        return current

    @staticmethod
    def _normalize_decision(raw: Any, tool_args: dict[str, Any]) -> ToolCallDecision:
        if isinstance(raw, ToolCallDecision):
            return ToolCallDecision(
                allowed=raw.allowed,
                tool_args=dict(raw.tool_args or tool_args),
                reason=raw.reason,
            )
        if raw is None:
            return ToolCallDecision(allowed=True, tool_args=dict(tool_args))
        if isinstance(raw, bool):
            return ToolCallDecision(allowed=raw, tool_args=dict(tool_args))
        if isinstance(raw, dict):
            return ToolCallDecision(
                allowed=bool(raw.get("allowed", True)),
                tool_args=dict(raw.get("tool_args", tool_args)),
                reason=str(raw.get("reason", "")),
            )
        raise TypeError(f"unsupported_tool_call_decision:{type(raw).__name__}")


__all__ = [
    "HookEventBus",
    "IntentGateHook",
    "TodoContinuationEnforcer",
    "ToolOutputTruncator",
    "LangSmithTracingHook",
]
