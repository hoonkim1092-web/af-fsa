"""
MemoryRouter — 쿼리 유형별 메모리 라우팅.

Phase 16: 자연어 쿼리를 분류하여 적절한 메모리 검색 전략으로 라우팅.

분류:
  "지난번에 ~했을 때"       → EPISODIC_RECALL
  "~는 어떻게 해결해?"      → GRAPH_TRAVERSE
  "~와 비슷한 거 있어?"     → SEMANTIC_RECALL
  "현재 상태가 뭐야?"       → WORKING_STATE
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.memory_system.facade import UnifiedMemoryFacade
from core.memory_system.models import MemoryRecord, MemoryScope, MemoryType, NodeType

logger = logging.getLogger(__name__)


class MemoryQueryType(str, Enum):
    EPISODIC_RECALL = "episodic_recall"
    GRAPH_TRAVERSE = "graph_traverse"
    SEMANTIC_RECALL = "semantic_recall"
    WORKING_STATE = "working_state"


@dataclass
class MemoryQueryPlan:
    query_type: MemoryQueryType
    confidence: float
    original_query: str
    parameters: dict[str, Any]


# ── Classification patterns ───────────────────────────────────────────

_EPISODIC_PATTERNS = [
    r"지난번|이전에|last\s+time|previously|before|전에|했을\s*때",
    r"history|기록|로그|trace|실행\s*이력",
]

_GRAPH_PATTERNS = [
    r"어떻게\s*해결|how\s+to\s+(fix|solve|resolve)|원인|cause|solution|해결책",
    r"왜\s*(실패|에러|오류)|why\s+(fail|error)|문제.*원인",
]

_WORKING_PATTERNS = [
    r"현재\s*상태|current\s+state|status|지금|now|checkpoint|진행\s*상황",
    r"what.*working\s+on|뭐\s*하고\s*있",
]

_SEMANTIC_PATTERNS = [
    r"비슷한|similar|관련|related|like|같은|찾아|search|recall",
]


def _match_score(text: str, patterns: list[str]) -> float:
    text_lower = text.lower()
    hits = sum(1 for p in patterns if re.search(p, text_lower, re.IGNORECASE))
    return hits / len(patterns) if patterns else 0.0


class MemoryRouter:
    """Routes memory queries to appropriate retrieval strategy."""

    def __init__(self, facade: UnifiedMemoryFacade) -> None:
        self._facade = facade

    def classify(self, query: str) -> MemoryQueryPlan:
        """Classify a natural-language query into a memory query type."""
        scores = {
            MemoryQueryType.EPISODIC_RECALL: _match_score(query, _EPISODIC_PATTERNS),
            MemoryQueryType.GRAPH_TRAVERSE: _match_score(query, _GRAPH_PATTERNS),
            MemoryQueryType.WORKING_STATE: _match_score(query, _WORKING_PATTERNS),
            MemoryQueryType.SEMANTIC_RECALL: _match_score(query, _SEMANTIC_PATTERNS),
        }

        # Find best type with explicit handling for ties
        best_type: MemoryQueryType | None = None
        best_score = 0.0
        for query_type, score in scores.items():
            if score > best_score:
                best_score = score
                best_type = query_type

        # Default to semantic recall if no clear winner
        if best_type is None or best_score < 0.1:
            best_type = MemoryQueryType.SEMANTIC_RECALL
            best_score = 0.1
            logger.debug("No pattern matched, defaulting to SEMANTIC_RECALL")

        return MemoryQueryPlan(
            query_type=best_type,
            confidence=min(best_score, 1.0),
            original_query=query,
            parameters={},
        )

    async def route(self, query: str, *, limit: int = 5) -> list[MemoryRecord]:
        """Classify and execute the appropriate memory retrieval."""
        plan = self.classify(query)
        logger.info("MemoryRouter: %s (conf=%.2f) for: %s", plan.query_type.value, plan.confidence, query[:80])

        if plan.query_type == MemoryQueryType.EPISODIC_RECALL:
            return await self._recall_episodic(query, limit)
        elif plan.query_type == MemoryQueryType.GRAPH_TRAVERSE:
            return await self._recall_graph(query, limit)
        elif plan.query_type == MemoryQueryType.WORKING_STATE:
            return await self._recall_working(query, limit)
        else:
            return await self._recall_semantic(query, limit)

    async def _recall_episodic(self, query: str, limit: int) -> list[MemoryRecord]:
        """Recall past episodes (timeline-based, most recent first)."""
        results = await self._facade.search_semantic(
            query, limit=limit, memory_type=MemoryType.EPISODIC,
        )
        # Sort by created_at descending (most recent first)
        results.sort(key=lambda r: r.created_at, reverse=True)
        return results[:limit]

    async def _recall_graph(self, query: str, limit: int) -> list[MemoryRecord]:
        """Traverse Knowledge Graph using BFS from matching problem nodes.

        Searches for Problem nodes matching the query keyword, then does a
        BFS traversal to collect connected Cause and Solution nodes.
        Falls back to semantic search if the knowledge_graph adapter is absent.
        """
        raw_adapter = self._facade.get_adapter("knowledge_graph")
        if raw_adapter is None:
            # Graceful fallback — no graph adapter registered
            return await self._facade.search_semantic(
                query, limit=limit, memory_type=MemoryType.GRAPH,
            )

        try:
            from core.memory_system.adapters.knowledge_graph import KnowledgeGraphAdapter
            from core.memory_system.graph_query import GraphQuery
            from core.memory_system.episode_matcher import keyword_similarity

            graph_adapter = raw_adapter  # type: KnowledgeGraphAdapter
            gq = GraphQuery(graph_adapter)  # type: ignore[arg-type]

            # 1. Score all problem nodes against query
            problem_nodes = graph_adapter.list_nodes(node_type=NodeType.PROBLEM)
            scored = [
                (n, keyword_similarity(query, f"{n.label} {n.description}"))
                for n in problem_nodes
            ]
            scored = [(n, s) for n, s in scored if s > 0.15]
            scored.sort(key=lambda x: x[1] * x[0].confidence, reverse=True)

            # 2. BFS from top matching problems → collect full subgraphs
            results: list[MemoryRecord] = []
            seen_ids: set[str] = set()

            for problem_node, match_score in scored[:3]:  # top 3 seeds
                traversed = gq.bfs(problem_node.node_id, max_depth=2)
                for node in traversed:
                    if node.node_id in seen_ids:
                        continue
                    seen_ids.add(node.node_id)
                    meta = dict(node.metadata)
                    meta.update({
                        "node_type": node.node_type.value,
                        "label": node.label,
                        "confidence": node.confidence,
                        "graph_match_score": round(match_score, 4),
                    })
                    results.append(MemoryRecord(
                        record_id=node.node_id,
                        memory_type=MemoryType.GRAPH,
                        scope=MemoryScope.LOCAL,
                        project_id=node.project_id or "",
                        content=node.description,
                        metadata=meta,
                    ))
                if len(results) >= limit:
                    break

            # 3. Sort: highest-confidence solutions / causes first
            results.sort(
                key=lambda r: (
                    r.metadata.get("confidence", 1.0),
                    r.metadata.get("graph_match_score", 0.0),
                ),
                reverse=True,
            )
            return results[:limit]

        except Exception as exc:
            logger.error("_recall_graph BFS failed, falling back: %s", exc)
            return await self._facade.search_semantic(
                query, limit=limit, memory_type=MemoryType.GRAPH,
            )

    async def _recall_working(self, query: str, limit: int) -> list[MemoryRecord]:
        """Recall current working context (session-scoped, recent first)."""
        results = await self._facade.search_semantic(
            query, limit=limit, memory_type=MemoryType.WORKING,
        )
        # Working memory: most recent is most relevant
        results.sort(key=lambda r: r.accessed_at, reverse=True)
        return results[:limit]

    async def _recall_semantic(self, query: str, limit: int) -> list[MemoryRecord]:
        """Semantic search across all memory types (relevance-ranked)."""
        return await self._facade.search_semantic(query, limit=limit)
