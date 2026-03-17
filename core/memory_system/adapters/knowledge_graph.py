"""
KnowledgeGraphAdapter — 지식 그래프 노드/엣지 CRUD.

Phase 13: Problem → Cause → Solution 삼중 저장소.
로컬 JSON 파일 기반 (Supabase 사용 시 확장 가능).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from core.memory_system.adapters.base import MemoryBackendAdapter
from core.memory_system.models import (
    KnowledgeEdge,
    KnowledgeNode,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    NodeType,
)

logger = logging.getLogger(__name__)

_DEFAULT_GRAPH_FILE = ".system_generated/cache/knowledge_graph.json"


class KnowledgeGraphAdapter(MemoryBackendAdapter):
    """Local-file knowledge graph backend."""

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = Path(workspace)
        self._graph_file = self._workspace / _DEFAULT_GRAPH_FILE
        self._nodes: dict[str, KnowledgeNode] = {}
        self._edges: dict[str, KnowledgeEdge] = {}

    @property
    def backend_name(self) -> str:
        return "knowledge_graph"

    async def initialise(self) -> None:
        self._load()

    async def shutdown(self) -> None:
        self._save()

    # ── Node CRUD ──────────────────────────────────────────────────────

    def add_node(self, node: KnowledgeNode) -> None:
        self._nodes[node.node_id] = node
        self._auto_save()

    def get_node(self, node_id: str) -> KnowledgeNode | None:
        return self._nodes.get(node_id)

    def remove_node(self, node_id: str) -> bool:
        if node_id in self._nodes:
            del self._nodes[node_id]
            # Remove connected edges
            to_remove = [
                eid for eid, e in self._edges.items()
                if e.source_id == node_id or e.target_id == node_id
            ]
            for eid in to_remove:
                del self._edges[eid]
            self._auto_save()
            return True
        return False

    def list_nodes(
        self,
        node_type: NodeType | None = None,
        project_id: str | None = None,
    ) -> list[KnowledgeNode]:
        result = list(self._nodes.values())
        if node_type:
            result = [n for n in result if n.node_type == node_type]
        if project_id:
            result = [n for n in result if n.project_id == project_id or n.project_id is None]
        return result

    # ── Edge CRUD ──────────────────────────────────────────────────────

    def add_edge(self, edge: KnowledgeEdge) -> None:
        self._edges[edge.edge_id] = edge
        self._auto_save()

    def get_edge(self, edge_id: str) -> KnowledgeEdge | None:
        return self._edges.get(edge_id)

    def remove_edge(self, edge_id: str) -> bool:
        removed = self._edges.pop(edge_id, None) is not None
        if removed:
            self._auto_save()
        return removed

    def get_edges_from(self, node_id: str) -> list[KnowledgeEdge]:
        return [e for e in self._edges.values() if e.source_id == node_id]

    def get_edges_to(self, node_id: str) -> list[KnowledgeEdge]:
        return [e for e in self._edges.values() if e.target_id == node_id]

    # ── MemoryBackendAdapter interface ─────────────────────────────────

    async def read(self, record_id: str) -> MemoryRecord | None:
        node = self.get_node(record_id)
        if node:
            return self._node_to_record(node)
        return None

    async def write(self, record: MemoryRecord) -> bool:
        # Convert MemoryRecord to KnowledgeNode
        node = KnowledgeNode(
            node_id=record.record_id,
            node_type=NodeType(record.metadata.get("node_type", "fact")),
            label=record.metadata.get("label", record.content[:100]),
            description=record.content,
            project_id=record.project_id or None,
            embedding=record.embedding,
            metadata=record.metadata,
        )
        self.add_node(node)
        return True

    async def update(self, record: MemoryRecord) -> bool:
        return await self.write(record)

    async def delete(self, record_id: str) -> bool:
        return self.remove_node(record_id)

    async def search(self, query, *, limit=10, project_id=None):
        q = query.lower()
        results = []
        for node in self._nodes.values():
            if project_id and node.project_id and node.project_id != project_id:
                continue
            if q in node.label.lower() or q in node.description.lower():
                results.append(self._node_to_record(node))
            if len(results) >= limit:
                break
        return results

    async def list_recent(self, *, limit=10, project_id=None):
        nodes = sorted(self._nodes.values(), key=lambda n: n.updated_at, reverse=True)
        if project_id:
            nodes = [n for n in nodes if n.project_id == project_id or n.project_id is None]
        return [self._node_to_record(n) for n in nodes[:limit]]

    def _auto_save(self) -> None:
        """Save after every mutation to keep disk in sync."""
        try:
            self._save()
        except Exception as exc:
            logger.error("KnowledgeGraph auto-save failed: %s", exc)

    # ── Persistence ────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._graph_file.exists():
            return
        try:
            data = json.loads(self._graph_file.read_text(encoding="utf-8"))
            for nd in data.get("nodes", []):
                self._nodes[nd["node_id"]] = KnowledgeNode.from_dict(nd)
            for ed in data.get("edges", []):
                self._edges[ed["edge_id"]] = KnowledgeEdge.from_dict(ed)
            logger.info("KnowledgeGraph loaded: %d nodes, %d edges", len(self._nodes), len(self._edges))
        except Exception as exc:
            logger.error("KnowledgeGraph load error: %s", exc)

    def _save(self) -> None:
        """Atomic write: temp file → os.replace()."""
        try:
            self._graph_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "nodes": [n.to_dict() for n in self._nodes.values()],
                "edges": [e.to_dict() for e in self._edges.values()],
            }
            content = json.dumps(data, ensure_ascii=False, indent=2)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._graph_file.parent), suffix=".tmp",
            )
            try:
                os.write(fd, content.encode("utf-8"))
                os.close(fd)
                os.replace(tmp_path, str(self._graph_file))
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception as exc:
            logger.error("KnowledgeGraph save error: %s", exc)

    def _node_to_record(self, node: KnowledgeNode) -> MemoryRecord:
        return self._tag(MemoryRecord(
            record_id=node.node_id,
            memory_type=MemoryType.GRAPH,
            scope=MemoryScope.GLOBAL if node.project_id is None else MemoryScope.LOCAL,
            project_id=node.project_id or "",
            content=f"[{node.node_type.value}] {node.label}: {node.description}",
            metadata={
                "node_type": node.node_type.value,
                "label": node.label,
                "confidence": node.confidence,
                **node.metadata,
            },
            embedding=node.embedding,
        ))
