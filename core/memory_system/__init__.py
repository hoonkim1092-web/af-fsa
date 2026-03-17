"""
Next-Generation Unified Memory System (Phase 10-16).

단일 진입점(UnifiedMemoryFacade)을 통해 6개 기존 메모리 서브시스템을
통합하고, Knowledge Graph 백엔드를 추가.
"""

from core.memory_system.models import (
    MemoryType,
    MemoryScope,
    MemoryRecord,
    EpisodeRecord,
    KnowledgeNode,
    KnowledgeEdge,
    NodeType,
    EdgeType,
)
from core.memory_system.facade import UnifiedMemoryFacade

__all__ = [
    "MemoryType",
    "MemoryScope",
    "MemoryRecord",
    "EpisodeRecord",
    "KnowledgeNode",
    "KnowledgeEdge",
    "NodeType",
    "EdgeType",
    "UnifiedMemoryFacade",
]
