"""
TraceLogAdapter — wraps core/hooks/langsmith_tracing.py JSONL output.

JSONL 실행 로그를 파싱하여 EPISODIC MemoryRecord로 변환.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.memory_system.adapters.base import MemoryBackendAdapter
from core.memory_system.models import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
)

logger = logging.getLogger(__name__)


class TraceLogAdapter(MemoryBackendAdapter):
    """Read-only adapter that parses JSONL trace files."""

    def __init__(self, logs_dir: str | Path | None = None) -> None:
        self._logs_dir = Path(logs_dir) if logs_dir else Path(".system_generated/logs")

    @property
    def backend_name(self) -> str:
        return "trace_log"

    async def read(self, record_id: str) -> MemoryRecord | None:
        # record_id = run_id; find trace file
        trace_file = self._logs_dir / f"trace_{record_id}.jsonl"
        if not trace_file.exists():
            return None
        events = self._parse_jsonl(trace_file)
        if not events:
            return None
        return self._events_to_record(record_id, events)

    async def write(self, record: MemoryRecord) -> bool:
        # Trace logs are written by LangSmithTracingHook, not this adapter
        return False

    async def update(self, record: MemoryRecord) -> bool:
        return False

    async def delete(self, record_id: str) -> bool:
        return False

    async def search(self, query, *, limit=10, project_id=None):
        q = query.lower()
        results = []
        for rec in await self.list_recent(limit=50):
            if q in rec.content.lower():
                results.append(rec)
            if len(results) >= limit:
                break
        return results

    async def list_recent(self, *, limit=10, project_id=None):
        if not self._logs_dir.exists():
            return []
        files = sorted(
            self._logs_dir.glob("trace_*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:limit]
        results = []
        for f in files:
            run_id = f.stem.replace("trace_", "")
            events = self._parse_jsonl(f)
            if events:
                results.append(self._events_to_record(run_id, events))
        return results

    def _parse_jsonl(self, path: Path) -> list[dict]:
        events = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        except Exception as exc:
            logger.error("TraceLogAdapter parse error %s: %s", path, exc)
        return events

    def _events_to_record(self, run_id: str, events: list[dict]) -> MemoryRecord:
        start_ev = next((e for e in events if e.get("event_type") == "run_start"), {})
        end_ev = next((e for e in events if e.get("event_type") == "run_end"), {})
        skill_calls = [e for e in events if e.get("event_type") == "skill_call_start"]

        agent_name = start_ev.get("agent_name", "?")
        task_input = start_ev.get("task_input", "")[:500]
        ok = end_ev.get("ok", None)
        reason = end_ev.get("reason", "")
        duration = end_ev.get("duration_ms", 0)
        skills_used = ", ".join(e.get("skill_name", "?") for e in skill_calls[:10])

        content = (
            f"Run {run_id} | Agent: {agent_name} | "
            f"Task: {task_input} | "
            f"Outcome: {'success' if ok else 'failure'} | "
            f"Skills: {skills_used} | Duration: {duration}ms"
        )
        if reason:
            content += f" | Reason: {reason}"

        ts = start_ev.get("timestamp")
        created = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)

        return self._tag(MemoryRecord(
            record_id=run_id,
            memory_type=MemoryType.EPISODIC,
            scope=MemoryScope.LOCAL,
            content=content,
            metadata={
                "run_id": run_id,
                "agent_name": agent_name,
                "ok": ok,
                "duration_ms": duration,
                "skill_count": len(skill_calls),
                "event_count": len(events),
            },
            created_at=created,
        ))
