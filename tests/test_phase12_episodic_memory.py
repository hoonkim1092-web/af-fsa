"""
Phase 12 — Episodic Memory + Consolidation Hook 테스트.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.memory_system.models import EpisodeRecord, MemoryType
from core.memory_system.episode_extractor import (
    extract_episode_from_events,
    extract_episode_from_jsonl,
)
from core.hooks.memory_consolidation import MemoryConsolidationHook


def run(coro):
    return asyncio.run(coro)


# ── Episode Extractor ─────────────────────────────────────────────────

class TestEpisodeExtractor:
    def _sample_events(self, ok=True, reason=""):
        events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00",
             "run_id": "r1", "agent_name": "Architect", "task_input": "build auth module"},
            {"event_type": "skill_call_start", "run_id": "r1",
             "skill_name": "read_file", "skill_args": {"path": "auth.py"}},
            {"event_type": "skill_call_end", "run_id": "r1",
             "skill_name": "read_file", "skill_result": "file content here"},
            {"event_type": "skill_call_start", "run_id": "r1",
             "skill_name": "write_file", "skill_args": {"path": "auth.py"}},
            {"event_type": "skill_call_end", "run_id": "r1",
             "skill_name": "write_file", "skill_result": "ok"},
        ]
        end = {"event_type": "run_end", "run_id": "r1", "ok": ok, "duration_ms": 5000}
        if reason:
            end["reason"] = reason
        events.append(end)
        return events

    def test_extract_success(self):
        ep = extract_episode_from_events(self._sample_events(ok=True))
        assert ep is not None
        assert ep.outcome == "success"
        assert ep.agent_name == "Architect"
        assert ep.run_id == "r1"
        assert len(ep.actions) == 2
        assert ep.actions[0]["skill_name"] == "read_file"
        assert ep.actions[1]["skill_name"] == "write_file"
        assert ep.duration_ms == 5000

    def test_extract_failure(self):
        ep = extract_episode_from_events(self._sample_events(ok=False, reason="ImportError"))
        assert ep is not None
        assert ep.outcome == "failure"
        assert ep.error_info == "ImportError"

    def test_extract_failure_from_captured_output(self):
        events = self._sample_events(ok=False)
        events.append({
            "event_type": "captured_output",
            "run_id": "r1",
            "content": "line1\nTraceback (most recent call last):\nTypeError: bad arg\nline4",
        })
        ep = extract_episode_from_events(events)
        assert ep is not None
        assert "Traceback" in ep.error_info

    def test_extract_empty_events(self):
        assert extract_episode_from_events([]) is None

    def test_extract_from_jsonl(self, tmp_path):
        events = self._sample_events(ok=True)
        jsonl = tmp_path / "trace_test.jsonl"
        jsonl.write_text(
            "\n".join(json.dumps(e) for e in events),
            encoding="utf-8",
        )
        ep = extract_episode_from_jsonl(jsonl)
        assert ep is not None
        assert ep.outcome == "success"

    def test_extract_nonexistent_file(self):
        assert extract_episode_from_jsonl("/nonexistent/path.jsonl") is None

    def test_skill_result_truncation(self):
        events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00",
             "run_id": "r1", "agent_name": "A", "task_input": "t"},
            {"event_type": "skill_call_start", "run_id": "r1",
             "skill_name": "big_result", "skill_args": {}},
            {"event_type": "skill_call_end", "run_id": "r1",
             "skill_name": "big_result", "skill_result": "x" * 1000},
            {"event_type": "run_end", "run_id": "r1", "ok": True, "duration_ms": 100},
        ]
        ep = extract_episode_from_events(events)
        assert ep is not None
        assert len(ep.actions[0]["result"]) <= 504  # 500 + "..."

    def test_nested_same_skill_calls_match_lifo(self):
        events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00",
             "run_id": "r1", "agent_name": "A", "task_input": "t"},
            {"event_type": "skill_call_start", "run_id": "r1",
             "skill_name": "retryable_tool", "skill_args": {"depth": 1}},
            {"event_type": "skill_call_start", "run_id": "r1",
             "skill_name": "retryable_tool", "skill_args": {"depth": 2}},
            {"event_type": "skill_call_end", "run_id": "r1",
             "skill_name": "retryable_tool", "skill_result": "inner result"},
            {"event_type": "skill_call_end", "run_id": "r1",
             "skill_name": "retryable_tool", "skill_result": "outer result"},
            {"event_type": "run_end", "run_id": "r1", "ok": True, "duration_ms": 100},
        ]
        ep = extract_episode_from_events(events)
        assert ep is not None
        assert ep.actions[0]["result"] == "outer result"
        assert ep.actions[1]["result"] == "inner result"

    def test_partial_outcome(self):
        events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00",
             "run_id": "r1", "agent_name": "A", "task_input": "t"},
            {"event_type": "run_end", "run_id": "r1", "ok": None, "duration_ms": 100},
        ]
        ep = extract_episode_from_events(events)
        assert ep is not None
        assert ep.outcome == "partial"


# ── MemoryConsolidationHook ───────────────────────────────────────────

class TestConsolidationHook:
    def test_priority(self):
        hook = MemoryConsolidationHook()
        assert hook.PRIORITY == 95

    def test_pre_execute_allows(self):
        hook = MemoryConsolidationHook()
        assert hook.pre_execute({}) is True

    def test_post_execute_no_facade(self):
        hook = MemoryConsolidationHook()
        result = hook.post_execute({"run_id": "r1"}, {"ok": True})
        assert result == {"ok": True}

    def test_post_execute_records_episode(self, tmp_path):
        hook = MemoryConsolidationHook()
        mock_facade = MagicMock()
        mock_facade.record_episode = AsyncMock(return_value=True)
        hook.set_facade(mock_facade)

        # Create JSONL trace
        logs_dir = tmp_path / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00",
             "run_id": "r1", "agent_name": "Dev", "task_input": "fix bug"},
            {"event_type": "run_end", "run_id": "r1", "ok": True, "duration_ms": 1000},
        ]
        (logs_dir / "trace_r1.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events), encoding="utf-8"
        )

        agent_state = {
            "run_id": "r1",
            "workspace": str(tmp_path),
            "project_id": "test_proj",
        }
        result = hook.post_execute(agent_state, {"ok": True})
        assert "_episode_id" in result
        mock_facade.record_episode.assert_called_once()

    def test_post_execute_no_jsonl(self, tmp_path):
        hook = MemoryConsolidationHook()
        mock_facade = MagicMock()
        hook.set_facade(mock_facade)

        agent_state = {"run_id": "missing", "workspace": str(tmp_path)}
        result = hook.post_execute(agent_state, {"ok": True})
        assert result == {"ok": True}

    def test_causal_link_on_retry_success(self, tmp_path):
        hook = MemoryConsolidationHook()
        mock_facade = MagicMock()
        mock_facade.record_episode = AsyncMock(return_value=True)
        hook.set_facade(mock_facade)

        logs_dir = tmp_path / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00",
             "run_id": "r2", "agent_name": "Dev", "task_input": "retry fix"},
            {"event_type": "run_end", "run_id": "r2", "ok": True, "duration_ms": 500},
        ]
        (logs_dir / "trace_r2.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events), encoding="utf-8"
        )

        agent_state = {
            "run_id": "r2",
            "workspace": str(tmp_path),
            "previous_episode_id": "ep_failed_001",
        }
        hook.post_execute(agent_state, {"ok": True})

        call_args = mock_facade.record_episode.call_args
        episode = call_args[0][0]
        assert "ep_failed_001" in episode.causal_links
