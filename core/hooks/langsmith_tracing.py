"""
LangSmith Tracing Hook for Agent Factory.

Automatically traces agent execution and tool calls to LangSmith
when LANGSMITH_API_KEY is set and LangChain is available.
All methods are no-op when dependencies are missing.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

from core.hooks.base import ContinuationHook, ToolCallDecision
from core.langchain_adapter import LANGCHAIN_AVAILABLE

# Conditional LangSmith import
_langsmith_client = None
_RunTree = None

if LANGCHAIN_AVAILABLE:
    try:
        from langsmith import Client as _LangSmithClient  # type: ignore
        from langsmith.run_trees import RunTree as _RunTree  # type: ignore
        _langsmith_client = _LangSmithClient
    except ImportError:
        pass


def _is_enabled() -> bool:
    return bool(
        _langsmith_client is not None
        and os.getenv("LANGSMITH_API_KEY")
    )


class LangSmithTracingHook(ContinuationHook):
    """
    Priority-5 hook that traces agent runs and tool calls to LangSmith.
    Fire-and-forget: tracing errors never block execution.
    """

    PRIORITY = 5

    def __init__(self):
        self._enabled = _is_enabled()
        self._client = _langsmith_client() if self._enabled else None
        self._run_tree: Any = None
        self._tool_spans: dict[str, Any] = {}

    def pre_execute(self, agent_state: dict) -> bool:
        if not self._enabled or _RunTree is None:
            return True
        try:
            run_id = agent_state.get("run_id", str(uuid.uuid4()))
            agent_name = agent_state.get("agent", {}).get("name", "unknown")
            self._run_tree = _RunTree(
                name=f"agent:{agent_name}",
                run_type="chain",
                inputs={"task": agent_state.get("task_input", "")},
                project_name=os.getenv("LANGSMITH_PROJECT", "agent-factory"),
            )
            self._run_tree.post()
            # Inject run_id into agent_state for downstream consumers
            agent_state["langsmith_run_id"] = str(self._run_tree.id)
        except Exception:
            pass  # fire-and-forget
        return True

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        if not self._enabled or self._run_tree is None:
            return result
        try:
            ok = result.get("ok", False) if isinstance(result, dict) else bool(result)
            self._run_tree.end(
                outputs={"ok": ok, "reason": result.get("reason", "") if isinstance(result, dict) else ""},
                error=None if ok else str(result.get("reason", "")) if isinstance(result, dict) else None,
            )
            self._run_tree.patch()
        except Exception:
            pass
        return result

    def pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict[str, Any]) -> ToolCallDecision:
        if not self._enabled or self._run_tree is None or _RunTree is None:
            return ToolCallDecision(allowed=True, tool_args=dict(tool_args))
        try:
            span = self._run_tree.create_child(
                name=f"tool:{tool_name}",
                run_type="tool",
                inputs=tool_args,
            )
            span.post()
            span_key = f"{tool_name}_{id(tool_args)}"
            self._tool_spans[span_key] = span
            # Store span key in tool_args for post_tool_call lookup
            tool_args = dict(tool_args)
            tool_args["_langsmith_span_key"] = span_key
        except Exception:
            pass
        return ToolCallDecision(allowed=True, tool_args=tool_args)

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        if not self._enabled:
            return result
        try:
            # Find the matching span
            span_key = None
            if isinstance(result, dict):
                span_key = result.pop("_langsmith_span_key", None)

            # Fallback: find any span starting with this tool_name
            if span_key is None:
                for k in list(self._tool_spans.keys()):
                    if k.startswith(f"{tool_name}_"):
                        span_key = k
                        break

            span = self._tool_spans.pop(span_key, None) if span_key else None
            if span is not None:
                span.end(outputs={"result": str(result)[:2000]})
                span.patch()
        except Exception:
            pass
        return result
