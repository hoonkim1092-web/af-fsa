"""
Tests for Stage 4-7: Knowledge Pipeline (EpisodeMatcher → KnowledgeForger → KnowledgeInjection).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.memory_system.models import (
    EpisodeRecord,
    KnowledgeNode,
    KnowledgeEdge,
    MemoryRecord,
    MemoryType,
    NodeType,
    EdgeType,
)
from core.memory_system.episode_matcher import EpisodeMatcher, _keyword_similarity
from core.memory_system.knowledge_forger import KnowledgeForger, _extract_tags
from core.memory_system.knowledge_injection import KnowledgeInjectionHook, _jaccard_similarity
from core.hooks.memory_consolidation import MemoryConsolidationHook


# ── Fixtures ──────────────────────────────────────────────────────────

def _make_episode(
    outcome: str = "success",
    task_input: str = "deploy web app",
    error_info: str = "",
    causal_links: list[str] | None = None,
    episode_id: str = "",
    project_id: str = "proj-1",
    actions: list[dict] | None = None,
) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode_id or f"ep-{outcome}-{id(task_input)}",
        project_id=project_id,
        agent_name="test-agent",
        task_input=task_input,
        actions=actions or [{"skill_name": "build"}, {"skill_name": "test"}],
        outcome=outcome,
        error_info=error_info,
        causal_links=causal_links or [],
    )


def _make_graph_adapter():
    """Create a mock KnowledgeGraphAdapter with in-memory storage."""
    adapter = MagicMock()
    adapter.backend_name = "knowledge_graph"
    nodes: dict[str, KnowledgeNode] = {}
    edges: dict[str, KnowledgeEdge] = {}

    def add_node(node):
        nodes[node.node_id] = node
    def add_edge(edge):
        edges[edge.edge_id] = edge
    def get_node(node_id):
        return nodes.get(node_id)
    def list_nodes(node_type=None, project_id=None):
        result = list(nodes.values())
        if node_type:
            result = [n for n in result if n.node_type == node_type]
        return result
    def get_edges_from(node_id):
        return [e for e in edges.values() if e.source_id == node_id]

    adapter.add_node = add_node
    adapter.add_edge = add_edge
    adapter.get_node = get_node
    adapter.list_nodes = list_nodes
    adapter.get_edges_from = get_edges_from
    adapter._nodes = nodes
    adapter._edges = edges
    return adapter


# ── Stage 4: EpisodeMatcher ──────────────────────────────────────────

class TestKeywordSimilarity:
    def test_identical_strings(self):
        assert _keyword_similarity("hello world", "hello world") == 1.0

    def test_no_overlap(self):
        assert _keyword_similarity("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        sim = _keyword_similarity("deploy web app", "deploy api app")
        assert 0.3 < sim < 0.8

    def test_empty_strings(self):
        assert _keyword_similarity("", "hello") == 0.0
        assert _keyword_similarity("hello", "") == 0.0


class TestEpisodeMatcher:
    def test_causal_link_match(self):
        """Strategy 1: causal_links should return exact match."""
        facade = AsyncMock()
        failure_ep = _make_episode(
            outcome="failure",
            episode_id="ep-fail-1",
            error_info="import error",
        )
        facade.read.return_value = MemoryRecord(
            record_id="ep-fail-1",
            memory_type=MemoryType.EPISODIC,
            content="failure episode",
            metadata=failure_ep.to_dict(),
        )

        success_ep = _make_episode(
            outcome="success",
            episode_id="ep-success-1",
            causal_links=["ep-fail-1"],
        )

        matcher = EpisodeMatcher(facade)
        pairs = asyncio.run(matcher.find_pairs(success_ep))

        assert len(pairs) == 1
        assert pairs[0][0].episode_id == "ep-fail-1"
        assert pairs[0][1].episode_id == "ep-success-1"

    def test_keyword_similarity_match(self):
        """Strategy 2: keyword similarity should match similar tasks."""
        facade = AsyncMock()
        facade.read.return_value = None

        failure_ep = _make_episode(
            outcome="failure",
            task_input="deploy web application to production",
            episode_id="ep-fail-kw",
            error_info="timeout",
        )
        failure_record = MemoryRecord(
            record_id="ep-fail-kw",
            memory_type=MemoryType.EPISODIC,
            content="failure: deploy web application to production",
            metadata=failure_ep.to_dict(),
        )
        facade.search_semantic.return_value = [failure_record]

        success_ep = _make_episode(
            outcome="success",
            task_input="deploy web application to staging",
            episode_id="ep-success-kw",
        )

        matcher = EpisodeMatcher(facade)
        pairs = asyncio.run(matcher.find_pairs(success_ep))

        assert len(pairs) >= 1
        assert pairs[0][0].outcome == "failure"

    def test_no_match_when_tasks_differ(self):
        """Dissimilar tasks should not match."""
        facade = AsyncMock()
        facade.read.return_value = None

        failure_ep = _make_episode(
            outcome="failure",
            task_input="run database migration",
            episode_id="ep-fail-diff",
        )
        facade.search_semantic.return_value = [
            MemoryRecord(
                record_id="ep-fail-diff",
                memory_type=MemoryType.EPISODIC,
                content="failure: run database migration",
                metadata=failure_ep.to_dict(),
            )
        ]

        success_ep = _make_episode(
            outcome="success",
            task_input="deploy frontend assets to CDN",
            episode_id="ep-success-diff",
        )

        matcher = EpisodeMatcher(facade)
        pairs = asyncio.run(matcher.find_pairs(success_ep))

        assert len(pairs) == 0


# ── Stage 6: KnowledgeForger ─────────────────────────────────────────

class TestKnowledgeForger:
    def test_forge_stores_triple(self):
        """Forger should store P, C, S nodes and 2 edges."""
        adapter = _make_graph_adapter()
        forger = KnowledgeForger(adapter)

        failure = _make_episode(
            outcome="failure",
            task_input="build project",
            error_info="ModuleNotFoundError: no module named 'foo'",
            actions=[{"skill_name": "install"}, {"skill_name": "build"}],
        )
        success = _make_episode(
            outcome="success",
            task_input="build project",
            actions=[{"skill_name": "install"}, {"skill_name": "fix_deps"}, {"skill_name": "build"}],
        )

        result = asyncio.run(forger.forge(failure, success))

        assert result is not None
        assert "problem_id" in result
        assert "cause_id" in result
        assert "solution_id" in result
        assert len(result["edge_ids"]) == 2
        assert len(adapter._nodes) == 3
        assert len(adapter._edges) == 2

    def test_forge_fallback_insight(self):
        """Without LLM, fallback should produce a readable insight."""
        adapter = _make_graph_adapter()
        forger = KnowledgeForger(adapter)

        failure = _make_episode(outcome="failure", error_info="timeout")
        success = _make_episode(outcome="success")

        result = asyncio.run(forger.forge(failure, success))
        assert result is not None
        assert len(result["insight"]) > 10


class TestExtractTags:
    def test_api_tag(self):
        p = KnowledgeNode(node_type=NodeType.PROBLEM, label="API call failed")
        c = KnowledgeNode(node_type=NodeType.CAUSE, label="timeout error")
        s = KnowledgeNode(node_type=NodeType.SOLUTION, label="add retry")
        tags = _extract_tags(p, c, s)
        assert "api" in tags
        assert "timeout" in tags

    def test_default_general_tag(self):
        p = KnowledgeNode(label="xyz")
        c = KnowledgeNode(label="abc")
        s = KnowledgeNode(label="def")
        tags = _extract_tags(p, c, s)
        assert tags == ["general"]


# ── Stage 7: KnowledgeInjectionHook ──────────────────────────────────

class TestKnowledgeInjectionHook:
    def test_no_injection_without_adapter(self):
        hook = KnowledgeInjectionHook()
        state = {"task_input": "build project"}
        assert hook.pre_execute(state) is True
        assert "_knowledge_context" not in state

    def test_injection_with_matching_knowledge(self):
        adapter = _make_graph_adapter()
        hook = KnowledgeInjectionHook()
        hook.set_graph_adapter(adapter)

        # Populate graph with a triple
        problem = KnowledgeNode(
            node_type=NodeType.PROBLEM,
            label="build project fails",
            description="build project fails due to missing deps",
            confidence=1.0,
        )
        cause = KnowledgeNode(
            node_type=NodeType.CAUSE,
            label="missing dependencies",
        )
        solution = KnowledgeNode(
            node_type=NodeType.SOLUTION,
            label="run pip install first",
            metadata={"insight": "Always install dependencies before building"},
        )
        adapter.add_node(problem)
        adapter.add_node(cause)
        adapter.add_node(solution)
        adapter.add_edge(KnowledgeEdge(
            edge_type=EdgeType.CAUSED_BY,
            source_id=problem.node_id,
            target_id=cause.node_id,
        ))
        adapter.add_edge(KnowledgeEdge(
            edge_type=EdgeType.SOLVED_BY,
            source_id=problem.node_id,
            target_id=solution.node_id,
        ))

        # task_input must have enough overlap with problem.description for Jaccard > 0.3
        state = {"task_input": "build project fails due to deps"}
        result = hook.pre_execute(state)

        assert result is True
        assert "_knowledge_context" in state
        ctx = state["_knowledge_context"]
        assert "Past Lessons" in ctx
        assert "build project fails" in ctx

    def test_no_injection_for_unrelated_task(self):
        adapter = _make_graph_adapter()
        hook = KnowledgeInjectionHook()
        hook.set_graph_adapter(adapter)

        problem = KnowledgeNode(
            node_type=NodeType.PROBLEM,
            label="database migration fails",
            description="database migration fails on schema change",
        )
        adapter.add_node(problem)

        state = {"task_input": "deploy frontend assets"}
        hook.pre_execute(state)

        assert "_knowledge_context" not in state


class TestJaccardSimilarity:
    def test_identical(self):
        assert _jaccard_similarity("a b c", "a b c") == 1.0

    def test_empty(self):
        assert _jaccard_similarity("", "a") == 0.0


# ── Integration: MemoryConsolidationHook with forge pipeline ─────────

class TestMemoryConsolidationForge:
    def test_set_graph_adapter(self):
        hook = MemoryConsolidationHook()
        adapter = _make_graph_adapter()
        hook.set_graph_adapter(adapter)
        assert hook._graph_adapter is adapter

    def test_forge_not_triggered_on_failure(self):
        """Forge pipeline should NOT trigger on failure episodes."""
        hook = MemoryConsolidationHook()
        hook._facade = AsyncMock()
        hook._graph_adapter = _make_graph_adapter()

        with patch.object(hook, '_run_forge_pipeline') as mock_forge:
            state = {
                "run_id": "test-run",
                "workspace": "/nonexistent",
            }
            hook.post_execute(state, {})
            mock_forge.assert_not_called()
