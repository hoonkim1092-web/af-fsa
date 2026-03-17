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
from core.memory_system.models import MemoryRecord, MemoryScope, MemoryType

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

        best_type = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best_type]

        # Default to semantic recall if no pattern matches
        if best_score < 0.1:
            best_type = MemoryQueryType.SEMANTIC_RECALL
            best_score = 0.3

        # Confidence = raw pattern score, no artificial inflation
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
        return await self._facade.search_semantic(
            query, limit=limit, memory_type=MemoryType.EPISODIC,
        )

    async def _recall_graph(self, query: str, limit: int) -> list[MemoryRecord]:
        return await self._facade.search_semantic(
            query, limit=limit, memory_type=MemoryType.GRAPH,
        )

    async def _recall_working(self, query: str, limit: int) -> list[MemoryRecord]:
        return await self._facade.search_semantic(
            query, limit=limit, memory_type=MemoryType.WORKING,
        )

    async def _recall_semantic(self, query: str, limit: int) -> list[MemoryRecord]:
        return await self._facade.search_semantic(query, limit=limit)
