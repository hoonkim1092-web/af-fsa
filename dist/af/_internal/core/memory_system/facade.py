"""
UnifiedMemoryFacade — single entry-point for all memory operations.

Delegates to registered MemoryBackendAdapters and merges results.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.memory_system.adapters.base import MemoryBackendAdapter
from core.memory_system.config import get_config
from core.memory_system.decay import MemoryDecayManager
from core.memory_system.models import (
    EpisodeRecord,
    MemoryRecord,
    MemoryScope,
    MemoryType,
)

logger = logging.getLogger(__name__)


class UnifiedMemoryFacade:
    """Unified read/write/search across all memory backends."""

    # Module-level singleton — set via set_instance() from agent_runner bootstrap
    _shared_instance: "UnifiedMemoryFacade | None" = None

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        self._adapters: dict[str, MemoryBackendAdapter] = {}
        self._healthy: set[str] = set()
        self._initialised = False

    @classmethod
    def get_instance(cls) -> "UnifiedMemoryFacade":
        """Return the shared singleton (set via set_instance).
        Returns an uninitialised stub if no instance has been registered yet.
        """
        if cls._shared_instance is None:
            cls._shared_instance = cls()
        return cls._shared_instance

    @classmethod
    def set_instance(cls, instance: "UnifiedMemoryFacade") -> None:
        """Register the process-wide shared facade (called from agent_runner bootstrap)."""
        cls._shared_instance = instance

    # ── Registration ───────────────────────────────────────────────────

    def register_adapter(self, adapter: MemoryBackendAdapter) -> None:
        self._adapters[adapter.backend_name] = adapter

    def get_adapter(self, name: str) -> MemoryBackendAdapter | None:
        return self._adapters.get(name)

    @property
    def adapter_names(self) -> list[str]:
        return list(self._adapters.keys())

    @property
    def healthy_adapters(self) -> list[str]:
        return list(self._healthy)

    def get_all_adapters(self) -> dict[str, MemoryBackendAdapter]:
        """Public accessor for adapter registry (used by CrossProjectRecall)."""
        return dict(self._adapters)

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def initialise(self) -> None:
        """Initialise all registered adapters, tracking health."""
        if self._initialised:
            return
        timeout = get_config().timeouts.init_timeout
        for name, adapter in self._adapters.items():
            try:
                await asyncio.wait_for(adapter.initialise(), timeout=timeout)
                self._healthy.add(name)
                logger.info("Adapter '%s' initialised", name)
            except asyncio.TimeoutError:
                logger.error("Adapter '%s' init timed out (%.1fs)", name, timeout)
            except Exception as exc:
                logger.error("Adapter '%s' init failed: %s", name, exc)
        self._initialised = True

    async def shutdown(self) -> None:
        for name, adapter in self._adapters.items():
            try:
                await adapter.shutdown()
            except Exception as exc:
                logger.error("Adapter '%s' shutdown failed: %s", name, exc)

    def _ensure_initialised(self) -> None:
        if not self._initialised:
            raise RuntimeError(
                "UnifiedMemoryFacade not initialised — call `await facade.initialise()` first"
            )

    # ── Write ──────────────────────────────────────────────────────────

    async def write(
        self,
        content: str,
        *,
        memory_type: MemoryType = MemoryType.SEMANTIC,
        scope: MemoryScope = MemoryScope.LOCAL,
        metadata: dict[str, Any] | None = None,
        ttl_hours: float | None = None,
        target_backend: str | None = None,
    ) -> MemoryRecord | None:
        """Write a new memory record to one or all backends."""
        self._ensure_initialised()
        record = MemoryRecord(
            memory_type=memory_type,
            scope=scope,
            project_id=self.project_id,
            content=content,
            metadata=metadata or {},
            ttl_hours=ttl_hours,
        )
        adapters = (
            [self._adapters[target_backend]]
            if target_backend and target_backend in self._adapters
            else list(self._adapters.values())
        )
        succeeded: list[str] = []
        timeout = get_config().timeouts.write_timeout
        for adapter in adapters:
            try:
                ok = await asyncio.wait_for(adapter.write(record), timeout=timeout)
                if ok:
                    succeeded.append(adapter.backend_name)
            except asyncio.TimeoutError:
                logger.error("Write to '%s' timed out", adapter.backend_name)
            except Exception as exc:
                logger.error("Write to '%s' failed: %s", adapter.backend_name, exc)
        if succeeded:
            record.source_backend = succeeded[0]
            record.metadata["_written_to"] = succeeded
            return record
        return None

    # ── Read ───────────────────────────────────────────────────────────

    async def read(self, record_id: str) -> MemoryRecord | None:
        """Read a single record from any backend."""
        self._ensure_initialised()
        for adapter in self._adapters.values():
            try:
                rec = await adapter.read(record_id)
                if rec:
                    rec.touch()
                    return rec
            except Exception:
                continue
        return None

    # ── Search (semantic + merge) ──────────────────────────────────────

    async def search_semantic(
        self,
        query: str,
        *,
        limit: int = 10,
        memory_type: MemoryType | None = None,
        scope: MemoryScope | None = None,
    ) -> list[MemoryRecord]:
        """Search across all backends, deduplicate, return merged results ranked by relevance."""
        self._ensure_initialised()
        timeout = get_config().timeouts.search_timeout

        # Parallel search across all adapters
        tasks = [
            asyncio.wait_for(
                adapter.search(query, limit=limit, project_id=self.project_id),
                timeout=timeout,
            )
            for adapter in self._adapters.values()
        ]

        all_records: list[MemoryRecord] = []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, BaseException):
                adapter_name = list(self._adapters.values())[i].backend_name
                if isinstance(res, asyncio.TimeoutError):
                    logger.error("Search on '%s' timed out", adapter_name)
                else:
                    logger.error("Search on '%s' failed: %s", adapter_name, res)
            else:
                all_records.extend(res)  # type: ignore[union-attr]

        # Filter by type/scope
        if memory_type:
            all_records = [r for r in all_records if r.memory_type == memory_type]
        if scope:
            all_records = [r for r in all_records if r.scope == scope]

        # Evict expired records before ranking (TTL enforcement at read time)
        decay_mgr = MemoryDecayManager()
        active, expired = decay_mgr.collect_expired(all_records)
        if expired:
            logger.debug(
                "search_semantic: filtered %d expired records", len(expired)
            )

        # Deduplicate by content_hash
        deduped = _deduplicate(active)

        # Rank by relevance score (Phase 14)
        scored = decay_mgr.rank_by_relevance(deduped)

        # Return top-N by relevance score
        return [rec for rec, _ in scored[:limit]]

    async def list_recent(self, *, limit: int = 10) -> list[MemoryRecord]:
        """List most-recent records from all backends."""
        self._ensure_initialised()
        all_records: list[MemoryRecord] = []
        for adapter in self._adapters.values():
            try:
                results = await adapter.list_recent(
                    limit=limit, project_id=self.project_id,
                )
                all_records.extend(results)
            except Exception as exc:
                logger.error("list_recent on '%s' failed: %s", adapter.backend_name, exc)

        deduped = _deduplicate(all_records)
        deduped.sort(key=lambda r: r.updated_at, reverse=True)
        return deduped[:limit]

    # ── Search all backends (no project filter) ────────────────────────

    async def search_all_backends(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        """Search across all backends WITHOUT project_id filter (cross-project recall)."""
        self._ensure_initialised()
        timeout = get_config().timeouts.search_timeout

        # Parallel search across all adapters, no project_id filter
        tasks = [
            asyncio.wait_for(
                adapter.search(query, limit=limit, project_id=None),
                timeout=timeout,
            )
            for adapter in self._adapters.values()
        ]

        all_records: list[MemoryRecord] = []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, BaseException):
                adapter_name = list(self._adapters.values())[i].backend_name
                logger.error("search_all on '%s' failed: %s", adapter_name, res)
            else:
                all_records.extend(res)  # type: ignore[union-attr]

        # Rank by relevance and deduplicate
        deduped = _deduplicate(all_records)
        decay_mgr = MemoryDecayManager()
        scored = decay_mgr.rank_by_relevance(deduped)
        return [rec for rec, _ in scored[:limit]]

    # ── Episode recording ──────────────────────────────────────────────

    async def record_episode(self, episode: EpisodeRecord) -> bool:
        """Store an episode via write — converts to MemoryRecord internally."""
        self._ensure_initialised()
        record = MemoryRecord(
            record_id=episode.episode_id,
            memory_type=MemoryType.EPISODIC,
            scope=MemoryScope.LOCAL,
            project_id=episode.project_id or self.project_id,
            content=_episode_to_content(episode),
            metadata=episode.to_dict(),
            ttl_hours=90 * 24,  # 90 days default
            causal_links=episode.causal_links,
        )
        # knowledge_graph adapter must never receive raw episodes — it stores
        # only structured P→C→S nodes extracted by KnowledgeForger.  Broadcasting
        # episodes there converts them to KnowledgeNode objects and corrupts the
        # graph with unstructured episodic content.
        any_ok = False
        for name, adapter in self._adapters.items():
            if name == "knowledge_graph":
                continue  # episodes must not pollute graph memory
            try:
                if await adapter.write(record):
                    any_ok = True
            except Exception as exc:
                logger.error("record_episode on '%s' failed: %s", adapter.backend_name, exc)
        return any_ok

    # ── Delete ─────────────────────────────────────────────────────────

    async def delete(self, record_id: str) -> bool:
        self._ensure_initialised()
        ok = False
        for adapter in self._adapters.values():
            try:
                if await adapter.delete(record_id):
                    ok = True
            except Exception:
                continue
        return ok


# ── Module-level helpers ───────────────────────────────────────────────

def _deduplicate(records: list[MemoryRecord]) -> list[MemoryRecord]:
    """Remove duplicates by content_hash, keeping the one with the most accesses."""
    seen: dict[str, MemoryRecord] = {}
    for r in records:
        key = r.content_hash if r.content_hash else r.record_id
        if key in seen:
            if r.access_count > seen[key].access_count:
                seen[key] = r
        else:
            seen[key] = r
    return list(seen.values())


def _episode_to_content(ep: EpisodeRecord) -> str:
    """Human-readable summary of an episode for embedding."""
    parts = [
        f"Agent: {ep.agent_name}",
        f"Task: {ep.task_input[:500]}",
        f"Outcome: {ep.outcome}",
    ]
    if ep.error_info:
        parts.append(f"Error: {ep.error_info[:300]}")
    actions_summary = ", ".join(
        a.get("skill_name", a.get("name", "?")) for a in ep.actions[:10]
    )
    if actions_summary:
        parts.append(f"Actions: {actions_summary}")
    return " | ".join(parts)
