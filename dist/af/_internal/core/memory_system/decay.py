"""
MemoryDecayManager — TTL 적용, 접근 빈도 부스팅, Relevance 점수 계산.

Phase 14: 메모리 유지 관리 — 만료 삭제, relevance 기반 정렬.

Relevance 공식:
  relevance = semantic_similarity × 0.50
            + recency_score       × 0.25   (e^(-days/30))
            + frequency_score     × 0.15   (log(1+count) / log(1+max))
            + confidence          × 0.10

TTL 기본값:
  Working   → 24시간
  Episodic  → 90일
  Graph     → 영구
  Semantic  → 영구
"""

from __future__ import annotations

import math
import logging
from datetime import datetime, timezone

from core.memory_system.config import get_config
from core.memory_system.models import MemoryRecord, MemoryType

logger = logging.getLogger(__name__)


def _build_ttl_map() -> dict[MemoryType, float | None]:
    cfg = get_config().ttl
    return {
        MemoryType.WORKING: cfg.working_hours,
        MemoryType.EPISODIC: cfg.episodic_hours,
        MemoryType.GRAPH: cfg.graph_hours,
        MemoryType.SEMANTIC: cfg.semantic_hours,
        MemoryType.PROCEDURAL: cfg.procedural_hours,
    }


class MemoryDecayManager:
    """Manages TTL enforcement and relevance scoring."""

    def __init__(self) -> None:
        self._ttl_map: dict[MemoryType, float | None] | None = None

    @property
    def ttl_map(self) -> dict[MemoryType, float | None]:
        if self._ttl_map is None:
            self._ttl_map = _build_ttl_map()
        return self._ttl_map

    def apply_default_ttl(self, record: MemoryRecord) -> None:
        """Set TTL if not explicitly set."""
        if record.ttl_hours is None:
            default = self.ttl_map.get(record.memory_type)
            if default is not None:
                record.ttl_hours = default

    def collect_expired(
        self,
        records: list[MemoryRecord],
        now: datetime | None = None,
    ) -> tuple[list[MemoryRecord], list[MemoryRecord]]:
        """
        Partition records into (active, expired).
        """
        now = now or datetime.now(timezone.utc)
        active, expired = [], []
        for r in records:
            if r.is_expired(now):
                expired.append(r)
            else:
                active.append(r)
        return active, expired

    def score_relevance(
        self,
        record: MemoryRecord,
        *,
        semantic_similarity: float = 0.0,
        now: datetime | None = None,
        max_access_count: int = 100,
    ) -> float:
        """
        Calculate composite relevance score [0.0, ~1.3].

        Args:
            semantic_similarity: cosine similarity to query (0-1).
            now: reference time for recency.
            max_access_count: max access count across all candidates (for normalization).
        """
        now = now or datetime.now(timezone.utc)

        # Recency: e^(-days/30)
        age_days = (now - record.updated_at).total_seconds() / 86400
        recency = math.exp(-age_days / 30)

        # Frequency: log(1+count) / log(1+max), guarded against zero
        max_log = max(math.log(1 + max_access_count), 0.001)
        frequency = math.log(1 + record.access_count) / max_log

        # Confidence (from metadata or default 1.0)
        # Expected range: 0.0-2.0 (KnowledgeNode defaults to 1.0, boosts up to 2.0 via boost_confidence)
        confidence = record.metadata.get("confidence", 1.0)
        # Normalize: map 0-2 range to 0-1 for scoring
        confidence_norm = min(max(confidence, 0.0) / 2.0, 1.0)

        w = get_config().weights
        score = (
            semantic_similarity * w.semantic
            + recency * w.recency
            + frequency * w.frequency
            + confidence_norm * w.confidence
        )
        return round(score, 4)

    def rank_by_relevance(
        self,
        records: list[MemoryRecord],
        *,
        semantic_scores: dict[str, float] | None = None,
        now: datetime | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        """
        Rank records by composite relevance.

        Args:
            semantic_scores: mapping record_id → cosine similarity.
        """
        sem = semantic_scores or {}
        max_access = max((r.access_count for r in records), default=1)

        scored = []
        for r in records:
            sim = sem.get(r.record_id, 0.0)
            score = self.score_relevance(
                r,
                semantic_similarity=sim,
                now=now,
                max_access_count=max_access,
            )
            scored.append((r, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
