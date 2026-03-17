"""
Phase 10 — Foundation Layer 테스트.

데이터 모델, Adapter ABC, CortexVectorAdapter(mock), UnifiedMemoryFacade.
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from core.memory_system.models import (
    MemoryType,
    MemoryScope,
    MemoryRecord,
    EpisodeRecord,
    KnowledgeNode,
    KnowledgeEdge,
    NodeType,
    EdgeType,
    content_hash,
)
from core.memory_system.adapters.base import MemoryBackendAdapter
from core.memory_system.facade import UnifiedMemoryFacade


# ── Helpers ────────────────────────────────────────────────────────────

class InMemoryAdapter(MemoryBackendAdapter):
    """In-memory stub adapter for testing."""

    def __init__(self, name: str = "in_memory"):
        self._name = name
        self._store: dict[str, MemoryRecord] = {}

    @property
    def backend_name(self) -> str:
        return self._name

    async def read(self, record_id: str) -> MemoryRecord | None:
        return self._store.get(record_id)

    async def write(self, record: MemoryRecord) -> bool:
        self._store[record.record_id] = record
        record.source_backend = self._name
        return True

    async def update(self, record: MemoryRecord) -> bool:
        self._store[record.record_id] = record
        return True

    async def delete(self, record_id: str) -> bool:
        return self._store.pop(record_id, None) is not None

    async def search(self, query, *, limit=10, project_id=None):
        results = []
        for r in self._store.values():
            if query.lower() in r.content.lower():
                if project_id and r.project_id != project_id:
                    continue
                results.append(r)
        return results[:limit]

    async def list_recent(self, *, limit=10, project_id=None):
        recs = sorted(self._store.values(), key=lambda r: r.updated_at, reverse=True)
        if project_id:
            recs = [r for r in recs if r.project_id == project_id]
        return recs[:limit]


def run(coro):
    return asyncio.run(coro)


# ── 1. MemoryRecord 기본 ──────────────────────────────────────────────

class TestMemoryRecord:
    def test_default_values(self):
        r = MemoryRecord(content="hello")
        assert r.memory_type == MemoryType.SEMANTIC
        assert r.scope == MemoryScope.LOCAL
        assert r.access_count == 0
        assert r.content_hash != ""
        assert len(r.record_id) == 32  # uuid hex

    def test_content_hash_deterministic(self):
        h1 = content_hash("test content")
        h2 = content_hash("test content")
        assert h1 == h2
        assert h1 != content_hash("different")

    def test_touch(self):
        r = MemoryRecord(content="x")
        old = r.accessed_at
        r.touch()
        assert r.access_count == 1
        assert r.accessed_at >= old

    def test_is_expired_no_ttl(self):
        r = MemoryRecord(content="x")
        assert not r.is_expired()

    def test_is_expired_yes(self):
        r = MemoryRecord(
            content="x",
            ttl_hours=1.0,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        assert r.is_expired()

    def test_is_expired_no(self):
        r = MemoryRecord(content="x", ttl_hours=24.0)
        assert not r.is_expired()

    def test_to_dict_from_dict_roundtrip(self):
        r = MemoryRecord(
            content="round trip",
            memory_type=MemoryType.EPISODIC,
            scope=MemoryScope.GLOBAL,
            metadata={"key": "val"},
            causal_links=["link1"],
        )
        d = r.to_dict()
        r2 = MemoryRecord.from_dict(d)
        assert r2.content == "round trip"
        assert r2.memory_type == MemoryType.EPISODIC
        assert r2.scope == MemoryScope.GLOBAL
        assert r2.causal_links == ["link1"]


# ── 2. EpisodeRecord ──────────────────────────────────────────────────

class TestEpisodeRecord:
    def test_defaults(self):
        e = EpisodeRecord(task_input="build feature")
        assert e.outcome == ""
        assert e.actions == []

    def test_to_dict_from_dict(self):
        e = EpisodeRecord(
            run_id="run1",
            agent_name="Architect",
            task_input="fix bug",
            outcome="success",
            actions=[{"name": "read_file", "args": {}}],
        )
        d = e.to_dict()
        e2 = EpisodeRecord.from_dict(d)
        assert e2.run_id == "run1"
        assert e2.outcome == "success"
        assert len(e2.actions) == 1


# ── 3. KnowledgeNode / KnowledgeEdge ─────────────────────────────────

class TestKnowledgeGraph:
    def test_node_defaults(self):
        n = KnowledgeNode(label="test")
        assert n.node_type == NodeType.FACT
        assert n.confidence == 1.0

    def test_boost_confidence(self):
        n = KnowledgeNode(label="x", confidence=1.8)
        n.boost_confidence(0.3, cap=2.0)
        assert n.confidence == 2.0

    def test_node_roundtrip(self):
        n = KnowledgeNode(node_type=NodeType.PROBLEM, label="import error")
        d = n.to_dict()
        n2 = KnowledgeNode.from_dict(d)
        assert n2.node_type == NodeType.PROBLEM
        assert n2.label == "import error"

    def test_edge_roundtrip(self):
        e = KnowledgeEdge(
            edge_type=EdgeType.CAUSED_BY,
            source_id="a",
            target_id="b",
            weight=0.9,
        )
        d = e.to_dict()
        e2 = KnowledgeEdge.from_dict(d)
        assert e2.edge_type == EdgeType.CAUSED_BY
        assert e2.source_id == "a"
        assert e2.weight == 0.9


# ── 4. Adapter ABC 준수 ──────────────────────────────────────────────

class TestAdapterABC:
    def test_in_memory_adapter_implements_abc(self):
        adapter = InMemoryAdapter()
        assert isinstance(adapter, MemoryBackendAdapter)
        assert adapter.backend_name == "in_memory"

    def test_tag_helper(self):
        adapter = InMemoryAdapter("test_be")
        r = MemoryRecord(content="x")
        tagged = adapter._tag(r)
        assert tagged.source_backend == "test_be"


# ── 5. UnifiedMemoryFacade ────────────────────────────────────────────

class TestFacade:
    def _make_facade(self, *adapters):
        f = UnifiedMemoryFacade(project_id="test_proj")
        for a in adapters:
            f.register_adapter(a)
        run(f.initialise())
        return f

    def test_register_and_list(self):
        f = self._make_facade(InMemoryAdapter("a"), InMemoryAdapter("b"))
        assert set(f.adapter_names) == {"a", "b"}

    def test_write_and_read(self):
        a = InMemoryAdapter()
        f = self._make_facade(a)
        rec = run(f.write("hello world"))
        assert rec is not None
        assert rec.project_id == "test_proj"
        fetched = run(f.read(rec.record_id))
        assert fetched is not None
        assert fetched.content == "hello world"

    def test_search_semantic(self):
        a = InMemoryAdapter()
        f = self._make_facade(a)
        run(f.write("python import error fix"))
        run(f.write("java null pointer"))
        results = run(f.search_semantic("python"))
        assert len(results) == 1
        assert "python" in results[0].content

    def test_search_deduplication(self):
        a1 = InMemoryAdapter("a1")
        a2 = InMemoryAdapter("a2")
        f = self._make_facade(a1, a2)
        # Write same content to both backends
        run(f.write("duplicate content"))
        results = run(f.search_semantic("duplicate"))
        assert len(results) == 1

    def test_list_recent(self):
        a = InMemoryAdapter()
        f = self._make_facade(a)
        run(f.write("first"))
        run(f.write("second"))
        recent = run(f.list_recent(limit=5))
        assert len(recent) == 2

    def test_delete(self):
        a = InMemoryAdapter()
        f = self._make_facade(a)
        rec = run(f.write("to delete"))
        assert run(f.delete(rec.record_id))
        assert run(f.read(rec.record_id)) is None

    def test_record_episode(self):
        a = InMemoryAdapter()
        f = self._make_facade(a)
        ep = EpisodeRecord(
            run_id="r1",
            agent_name="Arch",
            task_input="build X",
            outcome="success",
        )
        assert run(f.record_episode(ep))
        # Episode stored as MemoryRecord with EPISODIC type
        rec = run(f.read(ep.episode_id))
        assert rec is not None
        assert rec.memory_type == MemoryType.EPISODIC

    def test_write_with_target_backend(self):
        a1 = InMemoryAdapter("a1")
        a2 = InMemoryAdapter("a2")
        f = self._make_facade(a1, a2)
        run(f.write("only a1", target_backend="a1"))
        assert len(a1._store) == 1
        assert len(a2._store) == 0

    def test_write_with_ttl(self):
        a = InMemoryAdapter()
        f = self._make_facade(a)
        rec = run(f.write("temp", ttl_hours=24.0))
        assert rec.ttl_hours == 24.0

    def test_search_filter_by_type(self):
        a = InMemoryAdapter()
        f = self._make_facade(a)
        run(f.write("fact info", memory_type=MemoryType.SEMANTIC))
        run(f.write("episode info", memory_type=MemoryType.EPISODIC))
        results = run(f.search_semantic("info", memory_type=MemoryType.EPISODIC))
        assert all(r.memory_type == MemoryType.EPISODIC for r in results)


# ── 6. CortexVectorAdapter (mock) ────────────────────────────────────

class TestCortexVectorAdapter:
    def test_unavailable_gracefully(self):
        from core.memory_system.adapters.cortex_vector import CortexVectorAdapter
        adapter = CortexVectorAdapter("test")
        # Without initialise, should return empty/False gracefully
        assert run(adapter.read("x")) is None
        assert run(adapter.write(MemoryRecord(content="x"))) is False
        assert run(adapter.search("x")) == []

    @patch("core.memory_system.adapters.cortex_vector._try_import_cortex")
    def test_write_with_mock_client(self, mock_import):
        from core.memory_system.adapters.cortex_vector import CortexVectorAdapter

        mock_client_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.save_memory = MagicMock(return_value={"ok": True})
        mock_client.sb_url = "https://test.supabase.co"
        mock_client.headers = {"Authorization": "Bearer test"}
        mock_client_cls.return_value = mock_client
        mock_import.return_value = mock_client_cls

        adapter = CortexVectorAdapter("proj")
        run(adapter.initialise())
        assert adapter._available

        rec = MemoryRecord(content="test memory", project_id="proj")
        # Mock the dedup check
        with patch.object(adapter, "_search_by_hash", new_callable=AsyncMock, return_value=None):
            result = run(adapter.write(rec))
        assert result is True
        mock_client.save_memory.assert_called_once()

    @patch("core.memory_system.adapters.cortex_vector._try_import_cortex")
    def test_search_with_mock_client(self, mock_import):
        from core.memory_system.adapters.cortex_vector import CortexVectorAdapter

        mock_client_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.recall = MagicMock(return_value={
            "results": [
                {
                    "content": "found it",
                    "metadata": {"project_id": "proj", "record_id": "r1"},
                }
            ]
        })
        mock_client_cls.return_value = mock_client
        mock_import.return_value = mock_client_cls

        adapter = CortexVectorAdapter("proj")
        run(adapter.initialise())
        results = run(adapter.search("find", project_id="proj"))
        assert len(results) == 1
        assert results[0].content == "found it"
        assert results[0].source_backend == "cortex_vector"
