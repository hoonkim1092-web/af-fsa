"""
SyncCompyneAdapter — wraps syncCompyne/memory_store.py SQLite backend.

타임라인 로그를 MemoryRecord로 변환.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.memory_system.adapters.base import MemoryBackendAdapter
from core.memory_system.models import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
)

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path.home() / ".agent_factory" / "syncCompyne.db"


class SyncCompyneAdapter(MemoryBackendAdapter):
    """Wraps syncCompyne/memory_store.py SQLite session log."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        project_path: str | Path = ".",
    ) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._project_path = Path(project_path)
        self._available = False

    @property
    def backend_name(self) -> str:
        return "sync_compyne"

    async def initialise(self) -> None:
        try:
            from syncCompyne.memory_store import ensure_db
            ensure_db(self._db_path)
            self._available = True
        except Exception as exc:
            logger.warning("SyncCompyneAdapter init failed: %s", exc)

    async def read(self, record_id: str) -> MemoryRecord | None:
        return None  # No ID-based lookup in SQLite store

    async def write(self, record: MemoryRecord) -> bool:
        if not self._available:
            return False
        try:
            from syncCompyne.memory_store import add_entry
            now = datetime.now(timezone.utc)
            add_entry(
                db_path=self._db_path,
                project_path=self._project_path,
                session_date=now.date(),
                entry_time=now.strftime("%H:%M"),
                entry_type=record.metadata.get("entry_type", "MEMORY"),
                message=record.content[:2000],
                source="unified_memory",
            )
            return True
        except Exception as exc:
            logger.error("SyncCompyneAdapter.write failed: %s", exc)
            return False

    async def update(self, record: MemoryRecord) -> bool:
        return False

    async def delete(self, record_id: str) -> bool:
        return False

    async def search(self, query, *, limit=10, project_id=None):
        entries = await self.list_recent(limit=100)
        q = query.lower()
        return [e for e in entries if q in e.content.lower()][:limit]

    async def list_recent(self, *, limit=10, project_id=None):
        if not self._available:
            return []
        try:
            from syncCompyne.memory_store import pick_latest_session_day, read_entries_for_day
            today = date.today()
            day = pick_latest_session_day(
                self._db_path, self._project_path,
                explicit_day=None, period=None,
                from_day=None, to_day=None, today=today,
            )
            if not day:
                return []
            entries = read_entries_for_day(
                self._db_path, self._project_path, day, limit=limit,
            )
            return [self._entry_to_record(e, day) for e in entries]
        except Exception as exc:
            logger.error("SyncCompyneAdapter.list_recent failed: %s", exc)
            return []

    def _entry_to_record(self, entry: Any, day: date) -> MemoryRecord:
        entry_time = getattr(entry, "entry_time", "00:00") or "00:00"
        entry_type = getattr(entry, "entry_type", "UNKNOWN") or "UNKNOWN"
        message = getattr(entry, "message", "") or ""
        source = getattr(entry, "source", "unknown") or "unknown"
        return self._tag(MemoryRecord(
            record_id=f"{day}_{entry_time}_{entry_type}",
            memory_type=MemoryType.EPISODIC,
            scope=MemoryScope.LOCAL,
            content=f"[{entry_type}] {message}",
            metadata={
                "entry_type": entry_type,
                "source": source,
                "session_date": str(day),
                "entry_time": entry_time,
            },
        ))
