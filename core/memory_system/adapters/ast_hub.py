"""
AstHubAdapter — wraps core/ast_memory_hub.py AstMemoryHub.

In-memory pub/sub AST 상태를 MemoryRecord로 변환.
종료 시 스냅샷을 JSON으로 저장하여 영속성 제공.
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

_SNAPSHOT_FILE = ".system_generated/cache/ast_hub_snapshot.json"


class AstHubAdapter(MemoryBackendAdapter):
    """Wraps AstMemoryHub singleton — exposes AST state as WORKING memories."""

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = Path(workspace)
        self._hub: Any = None

    @property
    def backend_name(self) -> str:
        return "ast_hub"

    async def initialise(self) -> None:
        try:
            from core.ast_memory_hub import AstMemoryHub
            self._hub = AstMemoryHub()
            # Restore previous snapshot if exists
            snap = self._workspace / _SNAPSHOT_FILE
            if snap.exists():
                data = json.loads(snap.read_text(encoding="utf-8"))
                self._hub.ast_state.update(data.get("ast_state", {}))
                self._hub.global_context.update(data.get("global_context", {}))
                logger.info("AstHubAdapter: restored snapshot (%d files)", len(data.get("ast_state", {})))
        except Exception as exc:
            logger.warning("AstHubAdapter init failed: %s", exc)

    async def shutdown(self) -> None:
        """Persist current state to disk."""
        if not self._hub:
            return
        try:
            snap = self._workspace / _SNAPSHOT_FILE
            snap.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "ast_state": self._hub.ast_state,
                "global_context": self._hub.global_context,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            snap.write_text(json.dumps(data, default=str, ensure_ascii=False), encoding="utf-8")
            logger.info("AstHubAdapter: snapshot saved")
        except Exception as exc:
            logger.error("AstHubAdapter shutdown failed: %s", exc)

    async def read(self, record_id: str) -> MemoryRecord | None:
        if not self._hub:
            return None
        ast = self._hub.get_file_ast(record_id)
        if ast is None:
            return None
        return self._ast_to_record(record_id, ast)

    async def write(self, record: MemoryRecord) -> bool:
        if not self._hub:
            return False
        filepath = record.metadata.get("filepath", record.record_id)
        author = record.metadata.get("author", "unified_memory")
        await self._hub.update_ast_state(filepath, author, record.content)
        return True

    async def update(self, record: MemoryRecord) -> bool:
        return await self.write(record)

    async def delete(self, record_id: str) -> bool:
        if not self._hub:
            return False
        return self._hub.ast_state.pop(record_id, None) is not None

    async def search(self, query, *, limit=10, project_id=None):
        if not self._hub:
            return []
        q = query.lower()
        results = []
        for fpath, ast_data in self._hub.ast_state.items():
            summary = ast_data.get("summary", "") if isinstance(ast_data, dict) else str(ast_data)
            if q in fpath.lower() or q in summary.lower():
                results.append(self._ast_to_record(fpath, ast_data))
            if len(results) >= limit:
                break
        return results

    async def list_recent(self, *, limit=10, project_id=None):
        if not self._hub:
            return []
        items = list(self._hub.ast_state.items())
        # Sort by timestamp if available
        def _ts(item):
            v = item[1]
            if isinstance(v, dict):
                return v.get("timestamp", 0)
            return 0
        items.sort(key=_ts, reverse=True)
        return [self._ast_to_record(k, v) for k, v in items[:limit]]

    def _ast_to_record(self, filepath: str, ast_data: Any) -> MemoryRecord:
        summary = ""
        author = ""
        if isinstance(ast_data, dict):
            summary = ast_data.get("summary", "")
            author = ast_data.get("last_modified_by", "")
        return self._tag(MemoryRecord(
            record_id=filepath,
            memory_type=MemoryType.WORKING,
            scope=MemoryScope.SESSION,
            content=f"{filepath}: {summary}",
            metadata={"filepath": filepath, "author": author, "ast_data": ast_data},
            ttl_hours=24.0,
        ))
