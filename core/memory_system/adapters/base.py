"""
Abstract base for all memory backend adapters.

Each legacy subsystem gets an adapter that normalises its data to MemoryRecord.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.memory_system.models import MemoryRecord


class MemoryBackendAdapter(ABC):
    """Protocol that every memory adapter must implement."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Unique identifier, e.g. 'cortex_vector', 'core_memory'."""

    # ── CRUD ───────────────────────────────────────────────────────────

    @abstractmethod
    async def read(self, record_id: str) -> MemoryRecord | None:
        """Fetch a single record by ID."""

    @abstractmethod
    async def write(self, record: MemoryRecord) -> bool:
        """Persist a record. Returns True on success."""

    @abstractmethod
    async def update(self, record: MemoryRecord) -> bool:
        """Update an existing record. Returns True on success."""

    @abstractmethod
    async def delete(self, record_id: str) -> bool:
        """Delete a record by ID. Returns True on success."""

    # ── Search / List ──────────────────────────────────────────────────

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[MemoryRecord]:
        """Free-text or semantic search. Results sorted by relevance."""

    @abstractmethod
    async def list_recent(
        self,
        *,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[MemoryRecord]:
        """Most-recent records (by updated_at)."""

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def initialise(self) -> None:
        """Called once when the adapter is registered with the Facade."""

    async def shutdown(self) -> None:
        """Graceful teardown (flush caches, close connections)."""

    # ── Helpers ────────────────────────────────────────────────────────

    def _tag(self, record: MemoryRecord) -> MemoryRecord:
        """Stamp source_backend onto a record."""
        record.source_backend = self.backend_name
        return record
