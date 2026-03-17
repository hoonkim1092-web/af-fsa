"""
Memory System 중앙 설정.

모든 경로, 가중치, TTL 기본값을 한 곳에서 관리.
환경 변수로 오버라이드 가능.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw else default


@dataclass
class MemoryPaths:
    """파일시스템 경로 — workspace 기준 상대경로."""
    knowledge_graph: str = _env("MEMORY_GRAPH_PATH", ".system_generated/cache/knowledge_graph.json")
    ast_hub_snapshot: str = _env("MEMORY_AST_SNAPSHOT_PATH", ".system_generated/cache/ast_hub_snapshot.json")
    lifecycle_state: str = _env("MEMORY_LIFECYCLE_PATH", ".system_generated/project_lifecycle.json")
    trace_logs_dir: str = _env("MEMORY_TRACE_LOGS_DIR", ".system_generated/logs")
    core_memory_root: str = _env(
        "AGENT_MEMORY_DIR",
        os.path.join(os.path.expanduser("~"), ".agent_factory", "memory"),
    )
    sync_compyne_db: str = _env(
        "SYNCCOMPYNE_DB_PATH",
        os.path.join(os.path.expanduser("~"), ".agent_factory", "syncCompyne.db"),
    )


@dataclass
class RelevanceWeights:
    """Relevance 점수 가중치 — 합계 1.0 권장."""
    semantic: float = _env_float("DECAY_WEIGHT_SEMANTIC", 0.50)
    recency: float = _env_float("DECAY_WEIGHT_RECENCY", 0.25)
    frequency: float = _env_float("DECAY_WEIGHT_FREQUENCY", 0.15)
    confidence: float = _env_float("DECAY_WEIGHT_CONFIDENCE", 0.10)


@dataclass
class TTLDefaults:
    """메모리 유형별 TTL (시간 단위). None = 영구."""
    working_hours: float | None = 24.0
    episodic_hours: float | None = 90 * 24.0   # 90일
    graph_hours: float | None = None
    semantic_hours: float | None = None
    procedural_hours: float | None = None


@dataclass
class AdapterTimeouts:
    """어댑터별 타임아웃 (초)."""
    search_timeout: float = _env_float("MEMORY_SEARCH_TIMEOUT", 5.0)
    write_timeout: float = _env_float("MEMORY_WRITE_TIMEOUT", 10.0)
    init_timeout: float = _env_float("MEMORY_INIT_TIMEOUT", 15.0)


@dataclass
class MemorySystemConfig:
    """최상위 설정 객체."""
    paths: MemoryPaths = field(default_factory=MemoryPaths)
    weights: RelevanceWeights = field(default_factory=RelevanceWeights)
    ttl: TTLDefaults = field(default_factory=TTLDefaults)
    timeouts: AdapterTimeouts = field(default_factory=AdapterTimeouts)


# 모듈 레벨 싱글톤 — 필요 시 교체 가능
_config: MemorySystemConfig | None = None


def get_config() -> MemorySystemConfig:
    global _config
    if _config is None:
        _config = MemorySystemConfig()
    return _config


def set_config(config: MemorySystemConfig) -> None:
    global _config
    _config = config
