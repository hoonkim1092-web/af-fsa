"""
Phase 14 — Decay, Relevance, Cross-Project 테스트.
"""

import asyncio
import math
from datetime import datetime, timedelta, timezone

from core.memory_system.models import MemoryRecord, MemoryScope, MemoryType
from core.memory_system.decay import MemoryDecayManager
from core.memory_system.cross_project import CrossProjectRecall
from core.memory_system.facade import UnifiedMemoryFacade


def run(coro):
    return asyncio.run(coro)


# ── InMemoryAdapter (재사용) ──────────────────────────────────────────

from tests.test_phase10_memory_foundation import InMemoryAdapter


# ── MemoryDecayManager ────────────────────────────────────────────────

class TestDecayManager:
    def test_default_ttl_working(self):
        mgr = MemoryDecayManager()
        r = MemoryRecord(content="x", memory_type=MemoryType.WORKING)
        mgr.apply_default_ttl(r)
        assert r.ttl_hours == 24.0

    def test_default_ttl_episodic(self):
        mgr = MemoryDecayManager()
        r = MemoryRecord(content="x", memory_type=MemoryType.EPISODIC)
        mgr.apply_default_ttl(r)
        assert r.ttl_hours == 90 * 24.0

    def test_default_ttl_graph_permanent(self):
        mgr = MemoryDecayManager()
        r = MemoryRecord(content="x", memory_type=MemoryType.GRAPH)
        mgr.apply_default_ttl(r)
        assert r.ttl_hours is None

    def test_explicit_ttl_not_overridden(self):
        mgr = MemoryDecayManager()
        r = MemoryRecord(content="x", memory_type=MemoryType.WORKING, ttl_hours=48.0)
        mgr.apply_default_ttl(r)
        assert r.ttl_hours == 48.0

    def test_collect_expired(self):
        mgr = MemoryDecayManager()
        now = datetime.now(timezone.utc)
        r_active = MemoryRecord(content="active", ttl_hours=24.0, created_at=now)
        r_expired = MemoryRecord(
            content="expired", ttl_hours=1.0,
            created_at=now - timedelta(hours=2),
        )
        r_permanent = MemoryRecord(content="permanent")

        active, expired = mgr.collect_expired([r_active, r_expired, r_permanent], now=now)
        assert len(active) == 2
        assert len(expired) == 1
        assert expired[0].content == "expired"

    def test_relevance_score_high_similarity(self):
        mgr = MemoryDecayManager()
        r = MemoryRecord(content="x", access_count=10)
        score = mgr.score_relevance(r, semantic_similarity=0.95)
        assert score > 0.5

    def test_relevance_score_zero_similarity(self):
        mgr = MemoryDecayManager()
        r = MemoryRecord(content="x")
        score = mgr.score_relevance(r, semantic_similarity=0.0)
        assert score < 0.5  # Only recency + frequency contribute

    def test_relevance_recency_matters(self):
        mgr = MemoryDecayManager()
        now = datetime.now(timezone.utc)
        r_recent = MemoryRecord(content="a", updated_at=now)
        r_old = MemoryRecord(content="b", updated_at=now - timedelta(days=60))

        score_recent = mgr.score_relevance(r_recent, semantic_similarity=0.5, now=now)
        score_old = mgr.score_relevance(r_old, semantic_similarity=0.5, now=now)
        assert score_recent > score_old

    def test_relevance_frequency_matters(self):
        mgr = MemoryDecayManager()
        r_freq = MemoryRecord(content="a", access_count=50)
        r_rare = MemoryRecord(content="b", access_count=1)

        score_freq = mgr.score_relevance(r_freq, semantic_similarity=0.5, max_access_count=50)
        score_rare = mgr.score_relevance(r_rare, semantic_similarity=0.5, max_access_count=50)
        assert score_freq > score_rare

    def test_rank_by_relevance(self):
        mgr = MemoryDecayManager()
        now = datetime.now(timezone.utc)
        r1 = MemoryRecord(record_id="r1", content="a", access_count=10, updated_at=now)
        r2 = MemoryRecord(record_id="r2", content="b", access_count=1, updated_at=now - timedelta(days=30))

        ranked = mgr.rank_by_relevance(
            [r1, r2],
            semantic_scores={"r1": 0.8, "r2": 0.3},
            now=now,
        )
        assert ranked[0][0].record_id == "r1"
        assert ranked[0][1] > ranked[1][1]

    def test_confidence_boosts_score(self):
        mgr = MemoryDecayManager()
        r_high = MemoryRecord(content="a", metadata={"confidence": 2.0})
        r_low = MemoryRecord(content="b", metadata={"confidence": 0.5})

        score_high = mgr.score_relevance(r_high, semantic_similarity=0.5)
        score_low = mgr.score_relevance(r_low, semantic_similarity=0.5)
        assert score_high > score_low


# ── CrossProjectRecall ────────────────────────────────────────────────

class TestCrossProjectRecall:
    def _make_facade(self):
        a = InMemoryAdapter("mem")
        f = UnifiedMemoryFacade(project_id="proj_a")
        f.register_adapter(a)
        run(f.initialise())
        return f, a

    def test_recall_global(self):
        f, a = self._make_facade()
        # Write a global memory
        rec = MemoryRecord(
            content="global knowledge about error handling",
            scope=MemoryScope.GLOBAL,
            project_id="proj_a",
        )
        run(a.write(rec))

        xp = CrossProjectRecall(f)
        results = run(xp.recall_global("error"))
        assert len(results) == 1
        assert results[0].scope == MemoryScope.GLOBAL

    def test_recall_all_projects(self):
        f, a = self._make_facade()
        run(a.write(MemoryRecord(content="from proj_a", project_id="proj_a")))
        run(a.write(MemoryRecord(content="from proj_b", project_id="proj_b")))

        xp = CrossProjectRecall(f)
        results = run(xp.recall_all_projects("from"))
        assert len(results) == 2

    def test_find_similar_solutions(self):
        f, a = self._make_facade()
        rec = MemoryRecord(
            content="import error solution: add missing dependency",
            memory_type=MemoryType.GRAPH,
            scope=MemoryScope.LOCAL,
            project_id="proj_a",
        )
        run(a.write(rec))

        xp = CrossProjectRecall(f)
        results = run(xp.find_similar_solutions("import error"))
        assert len(results) == 1
        assert results[0].memory_type == MemoryType.GRAPH

    def test_empty_results(self):
        f, _ = self._make_facade()
        xp = CrossProjectRecall(f)
        results = run(xp.recall_global("nonexistent query"))
        assert results == []
