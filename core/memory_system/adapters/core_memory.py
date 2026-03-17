"""
CoreMemoryAdapter — wraps core/memory.py (read_core_memory).

read_core_memory는 JSON 파일 기반 읽기 전용 시스템.
write는 JSON 파일에 직접 저장.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.memory_system.adapters.base import MemoryBackendAdapter
from core.memory_system.models import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
    content_hash,
)

logger = logging.getLogger(__name__)


def _memory_root() -> Path:
    """~/.agent_factory/memory or AGENT_MEMORY_DIR override."""
    base = os.environ.get(
        "AGENT_MEMORY_DIR",
        os.path.join(os.path.expanduser("~"), ".agent_factory", "memory"),
    )
    return Path(base)


class CoreMemoryAdapter(MemoryBackendAdapter):
    """Wraps core/memory.py read_core_memory + direct JSON write."""

    def __init__(self, agent_id: str | None = None) -> None:
        self._agent_id = agent_id

    @property
    def backend_name(self) -> str:
        return "core_memory"

    async def read(self, record_id: str) -> MemoryRecord | None:
        # core_memory has no ID-based lookup; scan recent entries
        for rec in await self.list_recent(limit=200):
            if rec.record_id == record_id:
                return rec
        return None

    async def write(self, record: MemoryRecord) -> bool:
        try:
            subdir = self._agent_id or "general"
            root = _memory_root() / subdir
            root.mkdir(parents=True, exist_ok=True)
            fname = f"{record.record_id}.json"
            payload = {
                "key": record.record_id,
                "value": record.content,
                "category": record.metadata.get("category", "unified"),
                "updated_at": record.updated_at.isoformat(),
                "created_at": record.created_at.isoformat(),
                "metadata": record.metadata,
            }
            (root / fname).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception as exc:
            logger.error("CoreMemoryAdapter.write failed: %s", exc)
            return False

    async def update(self, record: MemoryRecord) -> bool:
        return await self.write(record)

    async def delete(self, record_id: str) -> bool:
        subdir = self._agent_id or "general"
        p = _memory_root() / subdir / f"{record_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    async def search(self, query, *, limit=10, project_id=None):
        results = []
        q = query.lower()
        for rec in await self.list_recent(limit=200):
            if q in rec.content.lower():
                results.append(rec)
            if len(results) >= limit:
                break
        return results

    async def list_recent(self, *, limit=10, project_id=None):
        try:
            from core.memory import read_core_memory
            raw = read_core_memory(self._agent_id, max_items=limit)
            records = []
            for key, value in raw.items():
                records.append(self._tag(MemoryRecord(
                    record_id=key,
                    memory_type=MemoryType.SEMANTIC,
                    scope=MemoryScope.LOCAL,
                    content=str(value),
                    source_backend=self.backend_name,
                )))
            return records
        except Exception as exc:
            logger.error("CoreMemoryAdapter.list_recent failed: %s", exc)
            return []
