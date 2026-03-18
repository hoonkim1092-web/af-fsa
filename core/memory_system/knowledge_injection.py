"""
KnowledgeInjectionHook — 실행 전 Knowledge Graph에서 관련 지식 주입.

Stage 7: pre_execute()에서 현재 task_input과 유사한 Problem 노드를 검색하고,
해당 P→C→S 삼중을 agent_state의 시스템 프롬프트에 주입하여
에이전트가 과거 실패/성공 경험을 참고할 수 있게 한다.

PRIORITY=10: 다른 pre_execute 훅보다 먼저 실행되어 컨텍스트를 풍부하게 만든다.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 주입할 최대 트리플 수 (컨텍스트 오염 방지)
_MAX_INJECTED_TRIPLES = 3
# 키워드 매칭 최소 Jaccard 유사도
_MIN_SIMILARITY = 0.3


class KnowledgeInjectionHook:
    """Injects relevant P→C→S knowledge into agent context before execution.

    This hook queries the Knowledge Graph for problems similar to the
    current task_input and injects the associated causes and solutions
    into the agent's system prompt context.
    """

    PRIORITY = 10  # Early — enrich context before other hooks

    def __init__(self) -> None:
        self._graph_adapter: Any = None
        self._graph_query: Any = None

    def set_graph_adapter(self, adapter: Any) -> None:
        """Inject KnowledgeGraphAdapter after construction."""
        self._graph_adapter = adapter
        if adapter is not None:
            from core.memory_system.graph_query import GraphQuery
            self._graph_query = GraphQuery(adapter)

    def pre_execute(self, agent_state: dict) -> bool:
        """Search Knowledge Graph and inject relevant triples.

        Adds a ``_knowledge_context`` key to agent_state containing
        formatted P→C→S lessons from past episodes.

        Returns True always (never blocks execution).
        """
        if not self._graph_adapter or not self._graph_query:
            return True

        task_input = agent_state.get("task_input", "") or agent_state.get("prompt", "")
        if not task_input:
            return True

        try:
            triples = self._find_relevant_triples(task_input)
            if triples:
                injection_text = self._format_injection(triples)
                agent_state["_knowledge_context"] = injection_text
                logger.info(
                    "KnowledgeInjection: injected %d triples for task",
                    len(triples),
                )
        except Exception as exc:
            logger.debug("KnowledgeInjection skipped: %s", exc)

        return True

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        """No-op — passthrough."""
        return result

    def pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict) -> Any:
        return None

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        return result

    # ── Internal ──────────────────────────────────────────────────────

    def _find_relevant_triples(
        self,
        task_input: str,
    ) -> list[dict[str, Any]]:
        """Find P→C→S triples relevant to the current task."""
        from core.memory_system.models import NodeType

        # Search for similar problem nodes
        problem_nodes = self._graph_adapter.list_nodes(node_type=NodeType.PROBLEM)
        if not problem_nodes:
            return []

        # Score and rank by keyword similarity
        scored: list[tuple[Any, float]] = []
        for node in problem_nodes:
            sim = _jaccard_similarity(task_input, node.description)
            if sim >= _MIN_SIMILARITY:
                scored.append((node, sim))

        # Sort by similarity * confidence (higher is better)
        scored.sort(key=lambda x: x[1] * x[0].confidence, reverse=True)

        # Get full triples for top matches
        triples = []
        for node, score in scored[:_MAX_INJECTED_TRIPLES]:
            triple = self._graph_query.get_full_triple(node.node_id)
            if triple and triple.get("problem"):
                triple["_score"] = score
                triples.append(triple)

        return triples

    @staticmethod
    def _format_injection(triples: list[dict[str, Any]]) -> str:
        """Format triples into a concise text block for prompt injection."""
        lines = ["## Past Lessons (from Knowledge Graph)"]
        lines.append("")

        for i, triple in enumerate(triples, 1):
            problem = triple["problem"]
            causes = triple.get("causes", [])
            solutions = triple.get("solutions", [])

            lines.append(f"### Lesson {i}")
            lines.append(f"- **Problem**: {problem.label}")

            if causes:
                cause_text = causes[0].label
                lines.append(f"- **Cause**: {cause_text}")

            if solutions:
                sol = solutions[0]
                insight = sol.metadata.get("insight", sol.label)
                lines.append(f"- **Solution**: {insight}")
                tags = sol.metadata.get("tags", [])
                if tags:
                    lines.append(f"- **Tags**: {', '.join(tags)}")

            lines.append("")

        return "\n".join(lines)


def _jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity on whitespace-split tokens."""
    if not a or not b:
        return 0.0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)
