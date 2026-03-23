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
            # Embed all round-trip fields into metadata so list_recent can reconstruct fully
            meta = dict(record.metadata)
            meta.setdefault("memory_type", record.memory_type.value)
            meta.setdefault("scope", record.scope.value)
            meta.setdefault("project_id", record.project_id)
            meta["causal_links"] = record.causal_links
            meta["ttl_hours"] = record.ttl_hours
            meta["access_count"] = record.access_count
            payload = {
                "key": record.record_id,
                "value": record.content,
                "category": meta.get("category", "unified"),
                "updated_at": record.updated_at.isoformat(),
                "created_at": record.created_at.isoformat(),
                "metadata": meta,
            }
            (root / fname).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception as exc:
            logger.error("CoreMemoryAdapter.write failed: %s", exc)
            return False

    def _payload_to_record(self, data: dict[str, Any]) -> MemoryRecord:
        """Reconstruct a full MemoryRecord from a stored JSON payload."""
        meta = data.get("metadata", {})
        try:
            mem_type = MemoryType(meta.get("memory_type", MemoryType.SEMANTIC.value))
        except ValueError:
            mem_type = MemoryType.SEMANTIC
        try:
            scope = MemoryScope(meta.get("scope", MemoryScope.LOCAL.value))
        except ValueError:
            scope = MemoryScope.LOCAL

        def _parse_dt(s: Any) -> datetime:
            if isinstance(s, str):
                try:
                    return datetime.fromisoformat(s)
                except ValueError:
                    pass
            return datetime.now(timezone.utc)

        return MemoryRecord(
            record_id=data.get("key", ""),
            memory_type=mem_type,
            scope=scope,
            project_id=meta.get("project_id", ""),
            content=data.get("value", ""),
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
            source_backend=self.backend_name,
            metadata=meta,
            causal_links=meta.get("causal_links", []),
            ttl_hours=meta.get("ttl_hours"),
            access_count=int(meta.get("access_count", 0)),
        )

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
        """Return recent records.

        Prefers reading from JSON files written by write() to preserve all fields.
        Falls back to legacy read_core_memory() when no files exist yet.
        """
        subdir = self._agent_id or "general"
        root = _memory_root() / subdir

        # Primary path: scan JSON files (preserves full round-trip fields)
        if root.exists():
            try:
                files = sorted(
                    root.glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                records: list[MemoryRecord] = []
                for fpath in files:
                    if len(records) >= limit:
                        break
                    try:
                        data = json.loads(fpath.read_text(encoding="utf-8"))
                        rec = self._payload_to_record(data)
                        if project_id and rec.project_id and rec.project_id != project_id:
                            continue
                        records.append(self._tag(rec))
                    except Exception:
                        continue
                if records:
                    return records
            except Exception as exc:
                logger.error("CoreMemoryAdapter.list_recent file-scan failed: %s", exc)

        # Legacy fallback: read_core_memory (flat key→value, no structured fields)
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
            logger.error("CoreMemoryAdapter.list_recent fallback failed: %s", exc)
            return []
