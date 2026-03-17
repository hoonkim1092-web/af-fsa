"""
CortexVectorAdapter — wraps skills/core/cortex.py CortexClient.

Adds update, delete, TTL, and dedup capabilities on top of the
append-only CortexClient.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from core.memory_system.adapters.base import MemoryBackendAdapter
from core.memory_system.models import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
    content_hash,
)

logger = logging.getLogger(__name__)


def _try_import_cortex():
    """Lazy import — CortexClient may be unavailable (missing env vars)."""
    try:
        from skills.core.cortex import CortexClient
        return CortexClient
    except Exception:
        return None


class CortexVectorAdapter(MemoryBackendAdapter):
    """Adapter for the Supabase pgvector cortex backend."""

    def __init__(self, project_id: str = "") -> None:
        self._project_id = project_id or os.path.basename(os.getcwd())
        self._client: Any = None
        self._available = False

    @property
    def backend_name(self) -> str:
        return "cortex_vector"

    async def initialise(self) -> None:
        CortexClient = _try_import_cortex()
        if CortexClient is None:
            logger.warning("CortexClient unavailable — adapter disabled")
            return
        try:
            self._client = CortexClient()
            self._available = True
            logger.info("CortexVectorAdapter initialised")
        except Exception as exc:
            logger.warning("CortexClient init failed: %s", exc)

    # ── CRUD ───────────────────────────────────────────────────────────

    async def read(self, record_id: str) -> MemoryRecord | None:
        if not self._available:
            return None
        try:
            import requests
            url = f"{self._client.sb_url}/rest/v1/cortex_memory"
            params = {
                "metadata->>record_id": f"eq.{record_id}",
                "limit": "1",
            }
            resp = requests.get(url, headers=self._client.headers, params=params, timeout=10)
            rows = resp.json() if resp.status_code == 200 else []
            if rows:
                return self._cortex_to_record(rows[0])
        except Exception as exc:
            logger.error("CortexVectorAdapter.read failed: %s", exc)
        return None

    async def write(self, record: MemoryRecord) -> bool:
        if not self._available:
            return False
        try:
            # Dedup: check content hash in metadata
            existing = await self._search_by_hash(record.content_hash)
            if existing:
                logger.debug("Dedup hit — skipping write for hash %s", record.content_hash[:12])
                return True

            meta = {
                **record.metadata,
                "project_id": record.project_id or self._project_id,
                "record_id": record.record_id,
                "content_hash": record.content_hash,
                "memory_type": record.memory_type.value,
                "scope": record.scope.value,
                "ttl_hours": record.ttl_hours,
            }
            self._client.save_memory(record.content, meta)
            return True
        except Exception as exc:
            logger.error("CortexVectorAdapter.write failed: %s", exc)
            return False

    async def update(self, record: MemoryRecord) -> bool:
        if not self._available:
            return False
        # Cortex is append-only; emulate update via delete + write.
        await self.delete(record.record_id)
        return await self.write(record)

    async def delete(self, record_id: str) -> bool:
        if not self._available:
            return False
        try:
            import requests
            url = f"{self._client.sb_url}/rest/v1/cortex_memory"
            params = {"metadata->>record_id": f"eq.{record_id}"}
            resp = requests.delete(url, headers=self._client.headers, params=params, timeout=10)
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.error("CortexVectorAdapter.delete failed: %s", exc)
            return False

    # ── Search ─────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[MemoryRecord]:
        if not self._available:
            return []
        try:
            result = self._client.recall(query, threshold=0.5, limit=limit)
            matches = result.get("results", [])
            records: list[MemoryRecord] = []
            for m in matches:
                rec = self._cortex_to_record(m)
                pid = project_id or self._project_id
                if pid and rec.project_id and rec.project_id != pid:
                    continue
                records.append(rec)
            return records
        except Exception as exc:
            logger.error("CortexVectorAdapter.search failed: %s", exc)
            return []

    async def list_recent(
        self,
        *,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[MemoryRecord]:
        # Cortex doesn't support list_recent natively; return empty.
        return []

    # ── Internal ───────────────────────────────────────────────────────

    async def _search_by_hash(self, chash: str) -> MemoryRecord | None:
        """Quick duplicate check via metadata query."""
        if not self._available or not chash:
            return None
        try:
            import requests
            url = f"{self._client.sb_url}/rest/v1/cortex_memory"
            params = {
                "metadata->>content_hash": f"eq.{chash}",
                "limit": "1",
            }
            resp = requests.get(url, headers=self._client.headers, params=params, timeout=10)
            rows = resp.json() if resp.status_code == 200 else []
            if rows:
                return self._cortex_to_record(rows[0])
        except Exception:
            pass
        return None

    def _cortex_to_record(self, row: dict[str, Any]) -> MemoryRecord:
        """Convert a cortex row/match result to MemoryRecord."""
        meta = row.get("metadata", {})
        content_text = row.get("content", "")
        return self._tag(MemoryRecord(
            record_id=meta.get("record_id", row.get("id", "")),
            memory_type=MemoryType(meta.get("memory_type", "semantic")),
            scope=MemoryScope(meta.get("scope", "local")),
            project_id=meta.get("project_id", self._project_id),
            content=content_text,
            metadata=meta,
            embedding=row.get("embedding"),
            content_hash=meta.get("content_hash", content_hash(content_text)),
            ttl_hours=meta.get("ttl_hours"),
        ))
