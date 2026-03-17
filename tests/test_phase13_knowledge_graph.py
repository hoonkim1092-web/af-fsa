"""
Phase 13 — Knowledge Graph 테스트.

KnowledgeGraphAdapter CRUD, GraphBuilder 삼중 추출, GraphQuery 순회.
"""

import asyncio
import json
from pathlib import Path

from core.memory_system.models import (
    EdgeType,
    EpisodeRecord,
    KnowledgeEdge,
    KnowledgeNode,
    MemoryRecord,
    MemoryType,
    NodeType,
)
from core.memory_system.adapters.knowledge_graph import KnowledgeGraphAdapter
from core.memory_system.graph_builder import extract_triple, promote_to_global
from core.memory_system.graph_query import GraphQuery


def run(coro):
    return asyncio.run(coro)


# ── KnowledgeGraphAdapter ─────────────────────────────────────────────

class TestKnowledgeGraphAdapter:
    def test_add_and_get_node(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        node = KnowledgeNode(node_id="n1", node_type=NodeType.FACT, label="test fact")
        adapter.add_node(node)
        assert adapter.get_node("n1") is not None
        assert adapter.get_node("n1").label == "test fact"

    def test_remove_node_cascades_edges(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        adapter.add_node(KnowledgeNode(node_id="a"))
        adapter.add_node(KnowledgeNode(node_id="b"))
        adapter.add_edge(KnowledgeEdge(edge_id="e1", source_id="a", target_id="b"))
        adapter.remove_node("a")
        assert adapter.get_edge("e1") is None

    def test_add_and_get_edge(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        adapter.add_node(KnowledgeNode(node_id="a"))
        adapter.add_node(KnowledgeNode(node_id="b"))
        edge = KnowledgeEdge(edge_id="e1", edge_type=EdgeType.CAUSED_BY, source_id="a", target_id="b")
        adapter.add_edge(edge)
        assert adapter.get_edge("e1") is not None
        assert adapter.get_edge("e1").edge_type == EdgeType.CAUSED_BY

    def test_get_edges_from(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        adapter.add_node(KnowledgeNode(node_id="a"))
        adapter.add_node(KnowledgeNode(node_id="b"))
        adapter.add_node(KnowledgeNode(node_id="c"))
        adapter.add_edge(KnowledgeEdge(edge_id="e1", source_id="a", target_id="b"))
        adapter.add_edge(KnowledgeEdge(edge_id="e2", source_id="a", target_id="c"))
        assert len(adapter.get_edges_from("a")) == 2
        assert len(adapter.get_edges_to("a")) == 0

    def test_list_nodes_by_type(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        adapter.add_node(KnowledgeNode(node_id="p1", node_type=NodeType.PROBLEM))
        adapter.add_node(KnowledgeNode(node_id="f1", node_type=NodeType.FACT))
        adapter.add_node(KnowledgeNode(node_id="p2", node_type=NodeType.PROBLEM))
        problems = adapter.list_nodes(node_type=NodeType.PROBLEM)
        assert len(problems) == 2

    def test_persistence(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        adapter.add_node(KnowledgeNode(node_id="n1", label="persist me"))
        adapter.add_edge(KnowledgeEdge(edge_id="e1", source_id="n1", target_id="n1"))
        run(adapter.shutdown())  # Save

        adapter2 = KnowledgeGraphAdapter(workspace=str(tmp_path))
        run(adapter2.initialise())  # Load
        assert adapter2.get_node("n1") is not None
        assert adapter2.get_node("n1").label == "persist me"
        assert adapter2.get_edge("e1") is not None

    def test_backend_name(self, tmp_path):
        assert KnowledgeGraphAdapter(workspace=str(tmp_path)).backend_name == "knowledge_graph"

    def test_adapter_search(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        adapter.add_node(KnowledgeNode(node_id="n1", label="import error", description="ModuleNotFoundError"))
        adapter.add_node(KnowledgeNode(node_id="n2", label="timeout issue", description="Connection timed out"))
        results = run(adapter.search("import"))
        assert len(results) == 1
        assert results[0].record_id == "n1"

    def test_adapter_write_read(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        rec = MemoryRecord(
            record_id="mr1",
            content="test node",
            metadata={"node_type": "fact", "label": "test"},
        )
        assert run(adapter.write(rec))
        fetched = run(adapter.read("mr1"))
        assert fetched is not None
        assert fetched.memory_type == MemoryType.GRAPH


# ── GraphBuilder ──────────────────────────────────────────────────────

class TestGraphBuilder:
    def _make_episodes(self):
        failure = EpisodeRecord(
            episode_id="fail1",
            run_id="r1",
            project_id="proj",
            agent_name="Dev",
            task_input="Fix the login module authentication",
            actions=[
                {"skill_name": "read_file", "args": {"path": "auth.py"}},
                {"skill_name": "write_file", "args": {"path": "auth.py"}},
            ],
            outcome="failure",
            error_info="TypeError: cannot read property 'token' of undefined",
        )
        success = EpisodeRecord(
            episode_id="succ1",
            run_id="r2",
            project_id="proj",
            agent_name="Dev",
            task_input="Fix the login module authentication",
            actions=[
                {"skill_name": "read_file", "args": {"path": "auth.py"}},
                {"skill_name": "analyze_error", "args": {}},
                {"skill_name": "write_file", "args": {"path": "auth.py"}},
            ],
            outcome="success",
        )
        return failure, success

    def test_extract_triple(self):
        fail, succ = self._make_episodes()
        problem, cause, solution, edges = extract_triple(fail, succ)

        assert problem.node_type == NodeType.PROBLEM
        assert "login" in problem.label.lower() or "login" in problem.description.lower()

        assert cause.node_type == NodeType.CAUSE
        assert "TypeError" in cause.description

        assert solution.node_type == NodeType.SOLUTION
        assert "analyze_error" in solution.description

        assert len(edges) == 2
        edge_types = {e.edge_type for e in edges}
        assert EdgeType.CAUSED_BY in edge_types
        assert EdgeType.SOLVED_BY in edge_types

    def test_triple_edge_connectivity(self):
        fail, succ = self._make_episodes()
        problem, cause, solution, edges = extract_triple(fail, succ)

        caused_by = next(e for e in edges if e.edge_type == EdgeType.CAUSED_BY)
        solved_by = next(e for e in edges if e.edge_type == EdgeType.SOLVED_BY)

        assert caused_by.source_id == problem.node_id
        assert caused_by.target_id == cause.node_id
        assert solved_by.source_id == problem.node_id
        assert solved_by.target_id == solution.node_id

    def test_promote_to_global(self):
        node = KnowledgeNode(node_id="n1", project_id="proj_a")
        assert not promote_to_global(node, min_projects=2, project_hits=["proj_a"])
        assert promote_to_global(node, min_projects=2, project_hits=["proj_a", "proj_b"])
        assert node.project_id is None
        assert node.confidence > 1.0

    def test_promote_already_global(self):
        node = KnowledgeNode(node_id="n1", project_id=None)
        assert not promote_to_global(node, min_projects=2, project_hits=["a", "b"])

    def test_diff_same_skills(self):
        fail = EpisodeRecord(
            episode_id="f1", task_input="task",
            actions=[{"skill_name": "read_file"}, {"skill_name": "write_file"}],
            outcome="failure",
        )
        succ = EpisodeRecord(
            episode_id="s1", task_input="task",
            actions=[{"skill_name": "read_file"}, {"skill_name": "write_file"}],
            outcome="success",
        )
        _, _, solution, _ = extract_triple(fail, succ)
        assert "Same skills" in solution.description or "different args" in solution.description


# ── GraphQuery ────────────────────────────────────────────────────────

class TestGraphQuery:
    def _build_graph(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        # Build: P --CAUSED_BY--> C, P --SOLVED_BY--> S
        adapter.add_node(KnowledgeNode(node_id="P", node_type=NodeType.PROBLEM, label="login fails"))
        adapter.add_node(KnowledgeNode(node_id="C", node_type=NodeType.CAUSE, label="null token"))
        adapter.add_node(KnowledgeNode(node_id="S", node_type=NodeType.SOLUTION, label="add null check"))
        adapter.add_edge(KnowledgeEdge(edge_id="e1", edge_type=EdgeType.CAUSED_BY, source_id="P", target_id="C"))
        adapter.add_edge(KnowledgeEdge(edge_id="e2", edge_type=EdgeType.SOLVED_BY, source_id="P", target_id="S"))
        return adapter

    def test_bfs(self, tmp_path):
        adapter = self._build_graph(tmp_path)
        query = GraphQuery(adapter)
        nodes = query.bfs("P", max_depth=2)
        assert len(nodes) == 3
        ids = {n.node_id for n in nodes}
        assert ids == {"P", "C", "S"}

    def test_bfs_with_edge_filter(self, tmp_path):
        adapter = self._build_graph(tmp_path)
        query = GraphQuery(adapter)
        nodes = query.bfs("P", max_depth=2, edge_types=[EdgeType.SOLVED_BY])
        ids = {n.node_id for n in nodes}
        assert "S" in ids
        assert "C" not in ids

    def test_dfs(self, tmp_path):
        adapter = self._build_graph(tmp_path)
        query = GraphQuery(adapter)
        nodes = query.dfs("P", max_depth=2)
        assert len(nodes) == 3

    def test_find_solutions(self, tmp_path):
        adapter = self._build_graph(tmp_path)
        query = GraphQuery(adapter)
        solutions = query.find_solutions_for_problem("P")
        assert len(solutions) == 1
        assert solutions[0].label == "add null check"

    def test_find_causes(self, tmp_path):
        adapter = self._build_graph(tmp_path)
        query = GraphQuery(adapter)
        causes = query.find_causes_for_problem("P")
        assert len(causes) == 1
        assert causes[0].label == "null token"

    def test_keyword_search(self, tmp_path):
        adapter = self._build_graph(tmp_path)
        query = GraphQuery(adapter)
        results = query.search_nodes_by_keyword("login")
        assert len(results) == 1
        assert results[0].node_id == "P"

    def test_keyword_search_with_type_filter(self, tmp_path):
        adapter = self._build_graph(tmp_path)
        query = GraphQuery(adapter)
        results = query.search_nodes_by_keyword("null", node_type=NodeType.CAUSE)
        assert len(results) == 1
        assert results[0].node_type == NodeType.CAUSE

    def test_get_full_triple(self, tmp_path):
        adapter = self._build_graph(tmp_path)
        query = GraphQuery(adapter)
        triple = query.get_full_triple("P")
        assert triple["problem"].node_id == "P"
        assert len(triple["causes"]) == 1
        assert len(triple["solutions"]) == 1

    def test_get_full_triple_nonexistent(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        query = GraphQuery(adapter)
        assert query.get_full_triple("nonexistent") == {}

    def test_max_depth_limiting(self, tmp_path):
        adapter = KnowledgeGraphAdapter(workspace=str(tmp_path))
        # Chain: A → B → C → D
        for nid in "ABCD":
            adapter.add_node(KnowledgeNode(node_id=nid, label=nid))
        adapter.add_edge(KnowledgeEdge(edge_id="e1", source_id="A", target_id="B"))
        adapter.add_edge(KnowledgeEdge(edge_id="e2", source_id="B", target_id="C"))
        adapter.add_edge(KnowledgeEdge(edge_id="e3", source_id="C", target_id="D"))

        query = GraphQuery(adapter)
        nodes = query.bfs("A", max_depth=1)
        ids = {n.node_id for n in nodes}
        assert "A" in ids
        assert "B" in ids
        assert "C" not in ids
