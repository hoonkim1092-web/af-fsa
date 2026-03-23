from __future__ import annotations

import logging
from typing import Any

from core.hooks.base import ToolCallDecision
from core.hooks.guardrails import IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator
from core.hooks.langsmith_tracing import LangSmithTracingHook

logger = logging.getLogger(__name__)


class HookEventBus:
    """
    Priority-ordered dispatch for agent and tool lifecycle hooks.

    라이프사이클:
      pre_execute / post_execute       — 에이전트 실행 전후
      pre_tool_call / post_tool_call   — 도구/스킬 호출 전후
      on_skill_evolved                 — 스킬 코드/메타 변경 후 (자가 진화)
      on_skill_quality_checked         — 품질 감사 결과
    """

    def __init__(self):
        self._pre_hooks: list[Any] = []
        self._post_hooks: list[Any] = []
        self._pre_tool_hooks: list[Any] = []
        self._post_tool_hooks: list[Any] = []

        # 스킬 라이프사이클 훅
        self._skill_evolved_hooks: list[Any] = []
        self._skill_quality_hooks: list[Any] = []

        # Always register tracing hook: JSONL local logging works without API key,
        # LangSmith API tracing activates only when LANGSMITH_API_KEY is set.
        self.register(LangSmithTracingHook())

    def register(self, hook: Any):
        # 개선 6: 같은 인스턴스의 중복 등록 방지 (이중 실행 방어)
        all_hooks = (
            self._pre_hooks + self._post_hooks
            + self._pre_tool_hooks + self._post_tool_hooks
            + self._skill_evolved_hooks + self._skill_quality_hooks
        )
        if any(h is hook for h in all_hooks):
            return

        if hasattr(hook, "pre_execute"):
            self._pre_hooks.append(hook)
        if hasattr(hook, "post_execute"):
            self._post_hooks.append(hook)
        if hasattr(hook, "pre_tool_call"):
            self._pre_tool_hooks.append(hook)
        if hasattr(hook, "post_tool_call"):
            self._post_tool_hooks.append(hook)
        if hasattr(hook, "on_skill_evolved"):
            self._skill_evolved_hooks.append(hook)
        if hasattr(hook, "on_skill_quality_checked"):
            self._skill_quality_hooks.append(hook)

        ordered = (
            self._pre_hooks,
            self._post_hooks,
            self._pre_tool_hooks,
            self._post_tool_hooks,
            self._skill_evolved_hooks,
            self._skill_quality_hooks,
        )
        for collection in ordered:
            collection.sort(key=lambda item: getattr(item, "PRIORITY", 50))

    def run_pre_execute(self, agent_state: dict) -> bool:
        for hook in self._pre_hooks:
            if not hook.pre_execute(agent_state):
                logger.warning("Execution blocked by %s", hook.__class__.__name__)
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

    def run_skill_evolved(
        self,
        skill_id: str,
        old_version: str = "",
        new_version: str = "",
        trigger: str = "manual",
    ) -> None:
        """스킬 진화 완료 이벤트 브로드캐스트."""
        for hook in self._skill_evolved_hooks:
            try:
                hook.on_skill_evolved(skill_id, old_version, new_version, trigger)
            except Exception as exc:
                logger.error(
                    "Hook %s.on_skill_evolved failed: %s",
                    hook.__class__.__name__, exc,
                )

    def run_skill_quality_checked(self, skill_id: str, quality_report: dict) -> None:
        """품질 감사 결과 이벤트 브로드캐스트."""
        for hook in self._skill_quality_hooks:
            try:
                hook.on_skill_quality_checked(skill_id, quality_report)
            except Exception as exc:
                logger.error(
                    "Hook %s.on_skill_quality_checked failed: %s",
                    hook.__class__.__name__, exc,
                )

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
