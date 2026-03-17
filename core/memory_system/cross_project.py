"""
CrossProjectRecall — 크로스 프로젝트 메모리 검색.

Phase 14: 다른 프로젝트의 knowledge graph와 semantic 메모리를
현재 프로젝트에서 활용.
"""

from __future__ import annotations

import logging
from typing import Any

from core.memory_system.facade import UnifiedMemoryFacade
from core.memory_system.models import MemoryRecord, MemoryScope, MemoryType

logger = logging.getLogger(__name__)


class CrossProjectRecall:
    """Cross-project memory search via UnifiedMemoryFacade."""

    def __init__(self, facade: UnifiedMemoryFacade) -> None:
        self._facade = facade

    async def recall_global(
        self,
        query: str,
        *,
        limit: int = 5,
        memory_type: MemoryType | None = None,
    ) -> list[MemoryRecord]:
        """Search only GLOBAL scope memories (cross-project knowledge)."""
        results = await self._facade.search_semantic(
            query, limit=limit * 2, scope=MemoryScope.GLOBAL,
        )
        if memory_type:
            results = [r for r in results if r.memory_type == memory_type]
        return results[:limit]

    async def recall_all_projects(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        """
        Search across all backends ignoring project_id filter.
        Useful for finding solutions from other projects.
        """
        return await self._facade.search_all_backends(query, limit=limit)

    async def find_similar_solutions(
        self,
        error_description: str,
        *,
        limit: int = 3,
    ) -> list[MemoryRecord]:
        """Find graph-type solution nodes matching an error description."""
        return await self._facade.search_semantic(
            error_description,
            limit=limit,
            memory_type=MemoryType.GRAPH,
        )
