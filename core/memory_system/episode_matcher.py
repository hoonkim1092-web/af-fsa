"""
EpisodeMatcher — 실패→성공 에피소드 쌍 자동 매칭.

Stage 4: 저장된 에피소드를 스캔하여 같은/유사한 task의
failure→success 쌍을 찾고, graph_builder.extract_triple()을 호출할
재료를 제공한다.

매칭 전략:
  1. causal_links 기반 (FSALoop 재시도 → 가장 정확)
  2. task_input 키워드 유사도 (같은 task 표현)
  3. SemanticEmbedder 유사도 (의미적으로 유사한 task)
"""

from __future__ import annotations

import logging
from typing import Any

from core.memory_system.models import EpisodeRecord, MemoryRecord, MemoryType

logger = logging.getLogger(__name__)

# task_input 키워드 유사도 임계치 (0-1)
_KEYWORD_THRESHOLD = 0.4


def _keyword_similarity(a: str, b: str) -> float:
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


class EpisodeMatcher:
    """Find failure→success episode pairs for knowledge extraction."""

    def __init__(self, facade: Any) -> None:
        self._facade = facade

    async def find_pairs(
        self,
        recent_success: EpisodeRecord,
        *,
        max_candidates: int = 50,
    ) -> list[tuple[EpisodeRecord, EpisodeRecord]]:
        """Given a recent *success* episode, find matching failure episodes.

        Returns list of (failure, success) pairs, best match first.
        """
        pairs: list[tuple[EpisodeRecord, float]] = []

        # ── Strategy 1: causal_links (최고 신뢰도) ──────────────
        for linked_id in recent_success.causal_links:
            failure_ep = await self._load_episode(linked_id)
            if failure_ep and failure_ep.outcome == "failure":
                pairs.append((failure_ep, 1.0))
                logger.info(
                    "EpisodeMatcher: causal link %s → %s",
                    linked_id[:12],
                    recent_success.episode_id[:12],
                )

        # causal_links로 충분하면 바로 반환
        if pairs:
            return [(f, recent_success) for f, _ in pairs]

        # ── Strategy 2: 같은 프로젝트의 최근 실패 에피소드 검색 ──
        failure_records = await self._search_failures(
            project_id=recent_success.project_id,
            limit=max_candidates,
        )

        for record in failure_records:
            failure_ep = self._record_to_episode(record)
            if not failure_ep or failure_ep.outcome != "failure":
                continue

            # 키워드 유사도
            sim = _keyword_similarity(
                recent_success.task_input,
                failure_ep.task_input,
            )
            if sim >= _KEYWORD_THRESHOLD:
                pairs.append((failure_ep, sim))

        # 유사도 높은 순 정렬
        pairs.sort(key=lambda x: x[1], reverse=True)

        # 상위 3개만 반환 (노이즈 방지)
        return [(f, recent_success) for f, _ in pairs[:3]]

    # ── Internal helpers ───────────────────────────────────────────

    async def _load_episode(self, episode_id: str) -> EpisodeRecord | None:
        """Load a single episode by ID from the facade."""
        record = await self._facade.read(episode_id)
        if not record:
            return None
        return self._record_to_episode(record)

    async def _search_failures(
        self,
        project_id: str,  # noqa: ARG002 — reserved for future project-scoped filtering
        limit: int,
    ) -> list[MemoryRecord]:
        """Search for recent failure episodes in the same project."""
        records = await self._facade.search_semantic(
            "failure error exception failed",
            limit=limit,
            memory_type=MemoryType.EPISODIC,
        )
        # Filter to actual failures
        return [
            r for r in records
            if r.metadata.get("outcome") == "failure"
            or "failure" in r.content.lower()
        ]

    @staticmethod
    def _record_to_episode(record: MemoryRecord) -> EpisodeRecord | None:
        """Reconstruct an EpisodeRecord from a MemoryRecord's metadata."""
        meta = record.metadata
        if not meta or "episode_id" not in meta:
            return None
        try:
            return EpisodeRecord.from_dict(meta)
        except Exception:
            return None
