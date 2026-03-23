"""
Unified data models for the Next-Generation Memory System.

Phase 10 — MemoryRecord, EpisodeRecord, KnowledgeNode, KnowledgeEdge.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Enums ──────────────────────────────────────────────────────────────

class MemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"
    GRAPH = "graph"


class MemoryScope(str, Enum):
    LOCAL = "local"
    GLOBAL = "global"
    SESSION = "session"


class NodeType(str, Enum):
    PROBLEM = "problem"
    CAUSE = "cause"
    SOLUTION = "solution"
    FACT = "fact"
    PATTERN = "pattern"


class EdgeType(str, Enum):
    CAUSED_BY = "caused_by"
    SOLVED_BY = "solved_by"
    RELATED_TO = "related_to"
    DEPENDS_ON = "depends_on"
    EVOLVED_FROM = "evolved_from"


# ── Helpers ────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


def content_hash(text: str) -> str:
    """SHA-256 digest of text — used for dedup."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Core record ────────────────────────────────────────────────────────

@dataclass
class MemoryRecord:
    """Normalised record that every adapter returns."""

    record_id: str = field(default_factory=_new_id)
    memory_type: MemoryType = MemoryType.SEMANTIC
    scope: MemoryScope = MemoryScope.LOCAL
    project_id: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    accessed_at: datetime = field(default_factory=_utcnow)
    access_count: int = 0
    ttl_hours: float | None = None
    source_backend: str = ""
    causal_links: list[str] = field(default_factory=list)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash and self.content:
            self.content_hash = content_hash(self.content)

    def touch(self) -> None:
        """Record an access — bumps accessed_at and counter."""
        self.accessed_at = _utcnow()
        self.access_count += 1

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.ttl_hours is None:
            return False
        now = now or _utcnow()
        age_hours = (now - self.created_at).total_seconds() / 3600
        return age_hours > self.ttl_hours

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "memory_type": self.memory_type.value,
            "scope": self.scope.value,
            "project_id": self.project_id,
            "content": self.content,
            "metadata": self.metadata,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "accessed_at": self.accessed_at.isoformat(),
            "access_count": self.access_count,
            "ttl_hours": self.ttl_hours,
            "source_backend": self.source_backend,
            "causal_links": self.causal_links,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MemoryRecord:
        d = dict(d)
        if "memory_type" in d and isinstance(d["memory_type"], str):
            d["memory_type"] = MemoryType(d["memory_type"])
        if "scope" in d and isinstance(d["scope"], str):
            d["scope"] = MemoryScope(d["scope"])
        for ts_key in ("created_at", "updated_at", "accessed_at"):
            if ts_key in d and isinstance(d[ts_key], str):
                d[ts_key] = datetime.fromisoformat(d[ts_key])
        return cls(**d)


# ── Episode ────────────────────────────────────────────────────────────

@dataclass
class EpisodeRecord:
    """One execution episode: task → actions → outcome."""

    episode_id: str = field(default_factory=_new_id)
    run_id: str = ""
    project_id: str = ""
    agent_name: str = ""
    task_input: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = ""  # "success" | "failure" | "partial"
    error_info: str = ""
    duration_ms: int = 0
    created_at: datetime = field(default_factory=_utcnow)
    causal_links: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "agent_name": self.agent_name,
            "task_input": self.task_input,
            "actions": self.actions,
            "outcome": self.outcome,
            "error_info": self.error_info,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat(),
            "causal_links": self.causal_links,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EpisodeRecord:
        d = dict(d)
        if "created_at" in d and isinstance(d["created_at"], str):
            d["created_at"] = datetime.fromisoformat(d["created_at"])
        return cls(**d)


# ── Knowledge Graph ────────────────────────────────────────────────────

@dataclass
class KnowledgeNode:
    node_id: str = field(default_factory=_new_id)
    node_type: NodeType = NodeType.FACT
    label: str = ""
    description: str = ""
    project_id: str | None = None  # None = global
    confidence: float = 1.0
    embedding: list[float] | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def boost_confidence(self, delta: float = 0.1, cap: float = 2.0) -> None:
        self.confidence = min(self.confidence + delta, cap)
        self.updated_at = _utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "label": self.label,
            "description": self.description,
            "project_id": self.project_id,
            "confidence": self.confidence,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KnowledgeNode:
        d = dict(d)
        if "node_type" in d and isinstance(d["node_type"], str):
            d["node_type"] = NodeType(d["node_type"])
        for ts_key in ("created_at", "updated_at"):
            if ts_key in d and isinstance(d[ts_key], str):
                d[ts_key] = datetime.fromisoformat(d[ts_key])
        return cls(**d)


@dataclass
class KnowledgeEdge:
    edge_id: str = field(default_factory=_new_id)
    edge_type: EdgeType = EdgeType.RELATED_TO
    source_id: str = ""
    target_id: str = ""
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type.value,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "weight": self.weight,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KnowledgeEdge:
        d = dict(d)
        if "edge_type" in d and isinstance(d["edge_type"], str):
            d["edge_type"] = EdgeType(d["edge_type"])
        if "created_at" in d and isinstance(d["created_at"], str):
            d["created_at"] = datetime.fromisoformat(d["created_at"])
        return cls(**d)
