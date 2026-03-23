"""
ContinuityAdapter — wraps core/continuity/ (manifest_store + resume_brief).

체크포인트 상태를 WORKING 타입 MemoryRecord로 노출.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.memory_system.adapters.base import MemoryBackendAdapter
from core.memory_system.models import (
    MemoryRecord,
    MemoryScope,
    MemoryType,
)

logger = logging.getLogger(__name__)


class ContinuityAdapter(MemoryBackendAdapter):
    """Wraps OrchestratorManifestStore and resume_brief."""

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = Path(workspace)
        self._manifest_store: Any = None

    @property
    def backend_name(self) -> str:
        return "continuity"

    async def initialise(self) -> None:
        try:
            from core.continuity.manifest_store import OrchestratorManifestStore
            self._manifest_store = OrchestratorManifestStore(self._workspace)
        except Exception as exc:
            logger.warning("ContinuityAdapter init failed: %s", exc)

    async def read(self, record_id: str) -> MemoryRecord | None:
        if record_id == "manifest":
            return await self._load_manifest_record()
        if record_id == "resume_brief":
            return await self._load_resume_record()
        return None

    async def write(self, record: MemoryRecord) -> bool:
        # Continuity is managed by orchestrator; external writes not supported
        logger.debug("ContinuityAdapter: write not supported (managed by orchestrator)")
        return False

    async def update(self, record: MemoryRecord) -> bool:
        return False

    async def delete(self, record_id: str) -> bool:
        return False

    async def search(self, query, *, limit=10, project_id=None):
        results = []
        q = query.lower()
        for rid in ("manifest", "resume_brief"):
            rec = await self.read(rid)
            if rec and q in rec.content.lower():
                results.append(rec)
        return results[:limit]

    async def list_recent(self, *, limit=10, project_id=None):
        results = []
        for rid in ("manifest", "resume_brief"):
            rec = await self.read(rid)
            if rec:
                results.append(rec)
        return results[:limit]

    async def _load_manifest_record(self) -> MemoryRecord | None:
        if not self._manifest_store:
            return None
        try:
            raw = self._manifest_store.load_raw()
            if not raw:
                return None
            sb = raw.get("state_board", {})
            completed = len(sb.get("completed_subtasks", []))
            failed = len(sb.get("failed_subtasks", []))
            interrupted = len(sb.get("interrupted_subtasks", []))
            content = (
                f"Project: {raw.get('project_desc', '?')} | "
                f"Completed: {completed}, Failed: {failed}, Interrupted: {interrupted} | "
                f"Status: {sb.get('current_status', '?')}"
            )
            return self._tag(MemoryRecord(
                record_id="manifest",
                memory_type=MemoryType.WORKING,
                scope=MemoryScope.LOCAL,
                content=content,
                metadata=raw,
                ttl_hours=24.0,
            ))
        except Exception as exc:
            logger.error("ContinuityAdapter manifest load: %s", exc)
            return None

    async def _load_resume_record(self) -> MemoryRecord | None:
        try:
            from core.continuity.resume_brief import read_resume_brief_excerpt
            text = read_resume_brief_excerpt(str(self._workspace), max_chars=2000)
            if not text:
                return None
            return self._tag(MemoryRecord(
                record_id="resume_brief",
                memory_type=MemoryType.WORKING,
                scope=MemoryScope.LOCAL,
                content=text,
                ttl_hours=24.0,
            ))
        except Exception as exc:
            logger.error("ContinuityAdapter resume load: %s", exc)
            return None
