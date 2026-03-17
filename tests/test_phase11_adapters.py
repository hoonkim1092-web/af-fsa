"""
Phase 11 — Backend Adapters 테스트.

5개 어댑터(core_memory, ast_hub, continuity, sync_compyne, trace_log)를
mock/stub으로 단위 테스트.
"""

import asyncio
import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.memory_system.models import MemoryRecord, MemoryType, MemoryScope


def run(coro):
    return asyncio.run(coro)


# ── CoreMemoryAdapter ─────────────────────────────────────────────────

class TestCoreMemoryAdapter:
    def test_write_and_read_via_file(self, tmp_path):
        from core.memory_system.adapters.core_memory import CoreMemoryAdapter

        with patch("core.memory_system.adapters.core_memory._memory_root", return_value=tmp_path):
            adapter = CoreMemoryAdapter(agent_id="test_agent")
            rec = MemoryRecord(content="test value", record_id="rec1")
            assert run(adapter.write(rec))

            # Verify file was created
            f = tmp_path / "test_agent" / "rec1.json"
            assert f.exists()
            data = json.loads(f.read_text(encoding="utf-8"))
            assert data["value"] == "test value"

    def test_delete(self, tmp_path):
        from core.memory_system.adapters.core_memory import CoreMemoryAdapter

        with patch("core.memory_system.adapters.core_memory._memory_root", return_value=tmp_path):
            adapter = CoreMemoryAdapter(agent_id="test_agent")
            rec = MemoryRecord(content="del me", record_id="del1")
            run(adapter.write(rec))
            assert run(adapter.delete("del1"))
            assert not (tmp_path / "test_agent" / "del1.json").exists()

    def test_search_keyword(self, tmp_path):
        from core.memory_system.adapters.core_memory import CoreMemoryAdapter

        with patch("core.memory_system.adapters.core_memory._memory_root", return_value=tmp_path):
            adapter = CoreMemoryAdapter(agent_id=None)
            # Mock read_core_memory — imported lazily inside list_recent
            with patch("core.memory.read_core_memory", return_value={
                "k1": "python error fix",
                "k2": "java null pointer",
            }):
                results = run(adapter.search("python"))
                assert len(results) == 1
                assert "python" in results[0].content

    def test_backend_name(self):
        from core.memory_system.adapters.core_memory import CoreMemoryAdapter
        assert CoreMemoryAdapter().backend_name == "core_memory"


# ── AstHubAdapter ─────────────────────────────────────────────────────

class TestAstHubAdapter:
    def test_read_write(self):
        from core.memory_system.adapters.ast_hub import AstHubAdapter

        mock_hub = MagicMock()
        mock_hub.ast_state = {}
        mock_hub.global_context = {}
        mock_hub.get_file_ast = lambda fp: mock_hub.ast_state.get(fp)
        mock_hub.update_ast_state = AsyncMock()

        adapter = AstHubAdapter(workspace=tempfile.mkdtemp())
        adapter._hub = mock_hub
        rec = MemoryRecord(
            content="modified login.py",
            metadata={"filepath": "src/login.py", "author": "Architect"},
        )
        assert run(adapter.write(rec))
        mock_hub.update_ast_state.assert_called_once()

    def test_search(self):
        from core.memory_system.adapters.ast_hub import AstHubAdapter

        adapter = AstHubAdapter()
        adapter._hub = MagicMock()
        adapter._hub.ast_state = {
            "src/auth.py": {"summary": "auth module refactored", "last_modified_by": "Dev"},
            "src/db.py": {"summary": "database connection pool", "last_modified_by": "Dev"},
        }
        results = run(adapter.search("auth"))
        assert len(results) == 1
        assert "auth" in results[0].content

    def test_list_recent(self):
        from core.memory_system.adapters.ast_hub import AstHubAdapter

        adapter = AstHubAdapter()
        adapter._hub = MagicMock()
        adapter._hub.ast_state = {
            "a.py": {"summary": "a", "timestamp": 100},
            "b.py": {"summary": "b", "timestamp": 200},
        }
        results = run(adapter.list_recent(limit=1))
        assert len(results) == 1
        assert "b.py" in results[0].content

    def test_snapshot_save(self, tmp_path):
        from core.memory_system.adapters.ast_hub import AstHubAdapter

        adapter = AstHubAdapter(workspace=str(tmp_path))
        adapter._hub = MagicMock()
        adapter._hub.ast_state = {"test.py": {"summary": "test"}}
        adapter._hub.global_context = {"ctx": "val"}
        run(adapter.shutdown())

        snap = tmp_path / ".system_generated" / "cache" / "ast_hub_snapshot.json"
        assert snap.exists()
        data = json.loads(snap.read_text(encoding="utf-8"))
        assert "test.py" in data["ast_state"]

    def test_backend_name(self):
        from core.memory_system.adapters.ast_hub import AstHubAdapter
        assert AstHubAdapter().backend_name == "ast_hub"


# ── ContinuityAdapter ─────────────────────────────────────────────────

class TestContinuityAdapter:
    def test_read_manifest(self):
        from core.memory_system.adapters.continuity import ContinuityAdapter

        mock_store = MagicMock()
        mock_store.load_raw.return_value = {
            "project_desc": "test project",
            "state_board": {
                "completed_subtasks": [{"id": 1}],
                "failed_subtasks": [],
                "interrupted_subtasks": [],
                "current_status": "running",
            },
        }
        adapter = ContinuityAdapter()
        adapter._manifest_store = mock_store

        rec = run(adapter.read("manifest"))
        assert rec is not None
        assert "Completed: 1" in rec.content
        assert rec.memory_type == MemoryType.WORKING

    def test_read_resume_brief(self):
        from core.memory_system.adapters.continuity import ContinuityAdapter

        adapter = ContinuityAdapter()
        with patch(
            "core.continuity.resume_brief.read_resume_brief_excerpt",
            return_value="## Resume Brief\n- 3 tasks completed",
        ):
            rec = run(adapter.read("resume_brief"))
            assert rec is not None
            assert "Resume Brief" in rec.content

    def test_write_not_supported(self):
        from core.memory_system.adapters.continuity import ContinuityAdapter
        adapter = ContinuityAdapter()
        assert run(adapter.write(MemoryRecord(content="x"))) is False

    def test_search(self):
        from core.memory_system.adapters.continuity import ContinuityAdapter

        mock_store = MagicMock()
        mock_store.load_raw.return_value = {
            "project_desc": "search test",
            "state_board": {"completed_subtasks": [], "failed_subtasks": [], "interrupted_subtasks": [], "current_status": "idle"},
        }
        adapter = ContinuityAdapter()
        adapter._manifest_store = mock_store

        results = run(adapter.search("search"))
        assert len(results) >= 1

    def test_backend_name(self):
        from core.memory_system.adapters.continuity import ContinuityAdapter
        assert ContinuityAdapter().backend_name == "continuity"


# ── SyncCompyneAdapter ────────────────────────────────────────────────

class TestSyncCompyneAdapter:
    def test_write(self):
        from core.memory_system.adapters.sync_compyne import SyncCompyneAdapter

        adapter = SyncCompyneAdapter()
        adapter._available = True

        with patch("syncCompyne.memory_store.add_entry") as mock_add:
            rec = MemoryRecord(content="log entry", metadata={"entry_type": "WORK"})
            assert run(adapter.write(rec))
            mock_add.assert_called_once()

    def test_list_recent(self):
        from core.memory_system.adapters.sync_compyne import SyncCompyneAdapter

        mock_entry = MagicMock()
        mock_entry.entry_time = "14:30"
        mock_entry.entry_type = "WORK"
        mock_entry.message = "did some work"
        mock_entry.source = "cli"

        adapter = SyncCompyneAdapter()
        adapter._available = True

        with patch("syncCompyne.memory_store.pick_latest_session_day", return_value=date(2026, 3, 17)):
            with patch("syncCompyne.memory_store.read_entries_for_day", return_value=[mock_entry]):
                results = run(adapter.list_recent(limit=5))
                assert len(results) == 1
                assert "did some work" in results[0].content
                assert results[0].source_backend == "sync_compyne"

    def test_unavailable(self):
        from core.memory_system.adapters.sync_compyne import SyncCompyneAdapter
        adapter = SyncCompyneAdapter()
        # Not initialised → not available
        assert run(adapter.write(MemoryRecord(content="x"))) is False
        assert run(adapter.list_recent()) == []

    def test_backend_name(self):
        from core.memory_system.adapters.sync_compyne import SyncCompyneAdapter
        assert SyncCompyneAdapter().backend_name == "sync_compyne"


# ── TraceLogAdapter ───────────────────────────────────────────────────

class TestTraceLogAdapter:
    def _write_trace(self, logs_dir: Path, run_id: str, events: list[dict]):
        logs_dir.mkdir(parents=True, exist_ok=True)
        f = logs_dir / f"trace_{run_id}.jsonl"
        lines = [json.dumps(e) for e in events]
        f.write_text("\n".join(lines), encoding="utf-8")

    def test_read_by_run_id(self, tmp_path):
        from core.memory_system.adapters.trace_log import TraceLogAdapter

        events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00", "run_id": "r1", "agent_name": "Arch", "task_input": "build X"},
            {"event_type": "skill_call_start", "run_id": "r1", "skill_name": "read_file"},
            {"event_type": "skill_call_end", "run_id": "r1", "skill_name": "read_file"},
            {"event_type": "run_end", "run_id": "r1", "ok": True, "duration_ms": 5000},
        ]
        self._write_trace(tmp_path, "r1", events)

        adapter = TraceLogAdapter(logs_dir=tmp_path)
        rec = run(adapter.read("r1"))
        assert rec is not None
        assert "Arch" in rec.content
        assert "success" in rec.content
        assert rec.memory_type == MemoryType.EPISODIC

    def test_list_recent(self, tmp_path):
        from core.memory_system.adapters.trace_log import TraceLogAdapter

        for rid in ("r1", "r2"):
            self._write_trace(tmp_path, rid, [
                {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00", "run_id": rid, "agent_name": "A", "task_input": f"task {rid}"},
                {"event_type": "run_end", "run_id": rid, "ok": True, "duration_ms": 100},
            ])

        adapter = TraceLogAdapter(logs_dir=tmp_path)
        results = run(adapter.list_recent(limit=5))
        assert len(results) == 2

    def test_search(self, tmp_path):
        from core.memory_system.adapters.trace_log import TraceLogAdapter

        self._write_trace(tmp_path, "r1", [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00", "run_id": "r1", "agent_name": "Dev", "task_input": "fix python import"},
            {"event_type": "run_end", "run_id": "r1", "ok": False, "reason": "ImportError", "duration_ms": 300},
        ])
        adapter = TraceLogAdapter(logs_dir=tmp_path)
        results = run(adapter.search("python"))
        assert len(results) == 1
        assert "failure" in results[0].content

    def test_nonexistent_run(self, tmp_path):
        from core.memory_system.adapters.trace_log import TraceLogAdapter
        adapter = TraceLogAdapter(logs_dir=tmp_path)
        assert run(adapter.read("nonexistent")) is None

    def test_write_not_supported(self, tmp_path):
        from core.memory_system.adapters.trace_log import TraceLogAdapter
        adapter = TraceLogAdapter(logs_dir=tmp_path)
        assert run(adapter.write(MemoryRecord(content="x"))) is False

    def test_backend_name(self, tmp_path):
        from core.memory_system.adapters.trace_log import TraceLogAdapter
        assert TraceLogAdapter(logs_dir=tmp_path).backend_name == "trace_log"


# ── Facade 통합 (multi-adapter merge) ─────────────────────────────────

class TestFacadeMultiAdapter:
    def test_merge_results_from_multiple_adapters(self, tmp_path):
        """Facade가 여러 어댑터 결과를 병합하고 중복 제거하는지 검증."""
        from core.memory_system.facade import UnifiedMemoryFacade
        from tests.test_phase10_memory_foundation import InMemoryAdapter

        a1 = InMemoryAdapter("backend_a")
        a2 = InMemoryAdapter("backend_b")

        facade = UnifiedMemoryFacade(project_id="test")
        facade.register_adapter(a1)
        facade.register_adapter(a2)
        run(facade.initialise())

        # Write different content to each
        run(facade.write("shared keyword data", target_backend="backend_a"))
        run(facade.write("other keyword info", target_backend="backend_b"))

        results = run(facade.search_semantic("keyword"))
        assert len(results) == 2  # Different content, no dedup

    def test_dedup_across_backends(self):
        from core.memory_system.facade import UnifiedMemoryFacade
        from tests.test_phase10_memory_foundation import InMemoryAdapter

        a1 = InMemoryAdapter("a1")
        a2 = InMemoryAdapter("a2")
        facade = UnifiedMemoryFacade(project_id="test")
        facade.register_adapter(a1)
        facade.register_adapter(a2)
        run(facade.initialise())

        # Same content to both
        run(facade.write("identical content"))
        results = run(facade.search_semantic("identical"))
        assert len(results) == 1
