"""
GraphQuery — BFS/DFS 순회 + 시맨틱 노드 검색.

Phase 13: 지식 그래프를 순회하여 관련 노드를 찾고,
Problem→Cause→Solution 경로를 추출.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from core.memory_system.adapters.knowledge_graph import KnowledgeGraphAdapter
from core.memory_system.models import (
    EdgeType,
    KnowledgeEdge,
    KnowledgeNode,
    NodeType,
)

logger = logging.getLogger(__name__)


class GraphQuery:
    """Query engine for the knowledge graph."""

    def __init__(self, adapter: KnowledgeGraphAdapter) -> None:
        self._adapter = adapter

    def bfs(
        self,
        start_id: str,
        max_depth: int = 3,
        edge_types: list[EdgeType] | None = None,
    ) -> list[KnowledgeNode]:
        """BFS from start node, returning all reachable nodes."""
        visited: set[str] = set()
        result: list[KnowledgeNode] = []
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])

        while queue:
            node_id, depth = queue.popleft()
            if node_id in visited or depth > max_depth:
                continue
            visited.add(node_id)

            node = self._adapter.get_node(node_id)
            if node:
                result.append(node)

            for edge in self._adapter.get_edges_from(node_id):
                if edge_types and edge.edge_type not in edge_types:
                    continue
                if edge.target_id not in visited:
                    queue.append((edge.target_id, depth + 1))

        return result

    def dfs(
        self,
        start_id: str,
        max_depth: int = 3,
        edge_types: list[EdgeType] | None = None,
    ) -> list[KnowledgeNode]:
        """DFS from start node, returning all reachable nodes."""
        visited: set[str] = set()
        result: list[KnowledgeNode] = []
        self._dfs_visit(start_id, 0, max_depth, edge_types, visited, result)
        return result

    def _dfs_visit(
        self,
        node_id: str,
        depth: int,
        max_depth: int,
        edge_types: list[EdgeType] | None,
        visited: set[str],
        result: list[KnowledgeNode],
    ) -> None:
        if node_id in visited or depth > max_depth:
            return
        visited.add(node_id)

        node = self._adapter.get_node(node_id)
        if node:
            result.append(node)

        for edge in self._adapter.get_edges_from(node_id):
            if edge_types and edge.edge_type not in edge_types:
                continue
            self._dfs_visit(edge.target_id, depth + 1, max_depth, edge_types, visited, result)

    def find_solutions_for_problem(self, problem_id: str) -> list[KnowledgeNode]:
        """Find all solution nodes connected to a problem via SOLVED_BY."""
        solutions = []
        for edge in self._adapter.get_edges_from(problem_id):
            if edge.edge_type == EdgeType.SOLVED_BY:
                node = self._adapter.get_node(edge.target_id)
                if node:
                    solutions.append(node)
        return solutions

    def find_causes_for_problem(self, problem_id: str) -> list[KnowledgeNode]:
        """Find all cause nodes connected to a problem via CAUSED_BY."""
        causes = []
        for edge in self._adapter.get_edges_from(problem_id):
            if edge.edge_type == EdgeType.CAUSED_BY:
                node = self._adapter.get_node(edge.target_id)
                if node:
                    causes.append(node)
        return causes

    def search_nodes_by_keyword(
        self,
        keyword: str,
        node_type: NodeType | None = None,
        project_id: str | None = None,
    ) -> list[KnowledgeNode]:
        """Keyword search across all nodes."""
        q = keyword.lower()
        results = []
        for node in self._adapter.list_nodes(node_type=node_type, project_id=project_id):
            if q in node.label.lower() or q in node.description.lower():
                results.append(node)
        return results

    def get_full_triple(self, problem_id: str) -> dict[str, Any]:
        """Get complete Problem→Cause→Solution triple."""
        problem = self._adapter.get_node(problem_id)
        if not problem:
            return {}

        causes = self.find_causes_for_problem(problem_id)
        solutions = self.find_solutions_for_problem(problem_id)

        return {
            "problem": problem,
            "causes": causes,
            "solutions": solutions,
        }
