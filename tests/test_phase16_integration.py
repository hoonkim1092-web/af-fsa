"""
Phase 16 — Memory Router + Full E2E Integration 테스트.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from core.memory_system.models import (
    EdgeType,
    EpisodeRecord,
    KnowledgeEdge,
    KnowledgeNode,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    NodeType,
)
from core.memory_system.facade import UnifiedMemoryFacade
from core.memory_system.router import MemoryRouter, MemoryQueryType
from core.memory_system.decay import MemoryDecayManager
from core.memory_system.cross_project import CrossProjectRecall
from core.memory_system.adapters.knowledge_graph import KnowledgeGraphAdapter
from core.memory_system.graph_builder import extract_triple
from core.memory_system.graph_query import GraphQuery
from core.memory_system.episode_extractor import extract_episode_from_events
from core.hooks.memory_consolidation import MemoryConsolidationHook
from tests.test_phase10_memory_foundation import InMemoryAdapter


def run(coro):
    return asyncio.run(coro)


# ── MemoryRouter Classification ───────────────────────────────────────

class TestMemoryRouterClassification:
    def _router(self):
        f = UnifiedMemoryFacade(project_id="test")
        return MemoryRouter(f)

    def test_episodic_korean(self):
        plan = self._router().classify("지난번에 로그인 모듈 작업했을 때 뭐가 문제였지?")
        assert plan.query_type == MemoryQueryType.EPISODIC_RECALL

    def test_episodic_english(self):
        plan = self._router().classify("last time we fixed the auth module")
        assert plan.query_type == MemoryQueryType.EPISODIC_RECALL

    def test_graph_korean(self):
        plan = self._router().classify("ImportError는 어떻게 해결해?")
        assert plan.query_type == MemoryQueryType.GRAPH_TRAVERSE

    def test_graph_english(self):
        plan = self._router().classify("how to fix the null pointer exception")
        assert plan.query_type == MemoryQueryType.GRAPH_TRAVERSE

    def test_working_state(self):
        plan = self._router().classify("현재 상태가 뭐야?")
        assert plan.query_type == MemoryQueryType.WORKING_STATE

    def test_semantic_default(self):
        plan = self._router().classify("비슷한 패턴 찾아줘")
        assert plan.query_type == MemoryQueryType.SEMANTIC_RECALL

    def test_unknown_defaults_to_semantic(self):
        plan = self._router().classify("random unrelated query xyz")
        assert plan.query_type == MemoryQueryType.SEMANTIC_RECALL

    def test_confidence_range(self):
        plan = self._router().classify("지난번에 에러 났을 때")
        assert 0.0 < plan.confidence <= 1.0


# ── MemoryRouter Routing ──────────────────────────────────────────────

class TestMemoryRouterRouting:
    def test_route_episodic(self):
        a = InMemoryAdapter()
        f = UnifiedMemoryFacade(project_id="test")
        f.register_adapter(a)
        run(f.initialise())

        # Write episodic record with searchable content
        run(f.write("로그인 모듈 수정 이력 에피소드", memory_type=MemoryType.EPISODIC))
        run(f.write("semantic fact unrelated", memory_type=MemoryType.SEMANTIC))

        router = MemoryRouter(f)
        results = run(router.route("지난번에 로그인 에피소드 기록"))
        # Should only return episodic since route filters by type
        assert all(r.memory_type == MemoryType.EPISODIC for r in results if results)

    def test_route_graph(self):
        a = InMemoryAdapter()
        f = UnifiedMemoryFacade(project_id="test")
        f.register_adapter(a)
        run(f.initialise())

        run(f.write("[solution] add null check", memory_type=MemoryType.GRAPH))

        router = MemoryRouter(f)
        results = run(router.route("어떻게 해결해?"))
        # Only GRAPH type should return
        assert all(r.memory_type == MemoryType.GRAPH for r in results)


# ── E2E: Failure → Retry Success → Episode → Graph → Recall ──────────

class TestE2EScenario:
    def test_failure_retry_success_pipeline(self, tmp_path):
        """
        E2E: 에이전트 실패 → 재시도 성공 → 에피소드 캡처 → 그래프 생성
        → 다른 쿼리에서 그래프가 해결책 제공.
        """
        # Step 1: Create failure episode
        fail_events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00",
             "run_id": "r1", "agent_name": "Dev", "task_input": "fix database connection timeout"},
            {"event_type": "skill_call_start", "run_id": "r1", "skill_name": "read_file"},
            {"event_type": "skill_call_end", "run_id": "r1", "skill_name": "read_file"},
            {"event_type": "run_end", "run_id": "r1", "ok": False,
             "reason": "ConnectionTimeoutError: pool exhausted", "duration_ms": 3000},
        ]
        fail_ep = extract_episode_from_events(fail_events)
        assert fail_ep is not None
        assert fail_ep.outcome == "failure"

        # Step 2: Create success episode (retry)
        success_events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:05:00+00:00",
             "run_id": "r2", "agent_name": "Dev", "task_input": "fix database connection timeout"},
            {"event_type": "skill_call_start", "run_id": "r2", "skill_name": "read_file"},
            {"event_type": "skill_call_end", "run_id": "r2", "skill_name": "read_file"},
            {"event_type": "skill_call_start", "run_id": "r2", "skill_name": "analyze_error"},
            {"event_type": "skill_call_end", "run_id": "r2", "skill_name": "analyze_error"},
            {"event_type": "skill_call_start", "run_id": "r2", "skill_name": "write_file"},
            {"event_type": "skill_call_end", "run_id": "r2", "skill_name": "write_file"},
            {"event_type": "run_end", "run_id": "r2", "ok": True, "duration_ms": 5000},
        ]
        success_ep = extract_episode_from_events(success_events)
        assert success_ep is not None
        assert success_ep.outcome == "success"

        # Step 3: Extract knowledge triple
        problem, cause, solution, edges = extract_triple(fail_ep, success_ep)
        assert problem.node_type == NodeType.PROBLEM
        assert "ConnectionTimeout" in cause.description
        assert "analyze_error" in solution.description

        # Step 4: Store in knowledge graph
        kg = KnowledgeGraphAdapter(workspace=str(tmp_path))
        kg.add_node(problem)
        kg.add_node(cause)
        kg.add_node(solution)
        for e in edges:
            kg.add_edge(e)

        # Step 5: Query graph for solutions
        gq = GraphQuery(kg)
        solutions = gq.find_solutions_for_problem(problem.node_id)
        assert len(solutions) == 1
        assert "analyze_error" in solutions[0].description

        # Step 6: Full triple retrieval
        triple = gq.get_full_triple(problem.node_id)
        assert triple["problem"] is not None
        assert len(triple["causes"]) == 1
        assert len(triple["solutions"]) == 1

    def test_ttl_expiry(self):
        """TTL 만료된 메모리가 정리되는지 검증."""
        mgr = MemoryDecayManager()
        now = datetime.now(timezone.utc)
        records = [
            MemoryRecord(content="fresh", ttl_hours=24.0, created_at=now),
            MemoryRecord(content="old", ttl_hours=1.0, created_at=now - timedelta(hours=2)),
            MemoryRecord(content="permanent", ttl_hours=None),
        ]
        active, expired = mgr.collect_expired(records, now=now)
        assert len(active) == 2
        assert len(expired) == 1
        assert expired[0].content == "old"

    def test_cross_project_graph_recall(self, tmp_path):
        """다른 프로젝트의 그래프 지식이 검색되는지 검증."""
        a = InMemoryAdapter()
        f = UnifiedMemoryFacade(project_id="proj_b")
        f.register_adapter(a)
        run(f.initialise())

        # Write a solution from proj_a — use recall_all_projects (no scope filter)
        rec = MemoryRecord(
            content="database timeout fix: increase pool size",
            memory_type=MemoryType.GRAPH,
            project_id="proj_a",
        )
        run(a.write(rec))

        xp = CrossProjectRecall(f)
        results = run(xp.recall_all_projects("database timeout"))
        assert len(results) == 1

    def test_facade_full_lifecycle(self):
        """Facade: write → search → touch → delete 전체 흐름."""
        a = InMemoryAdapter()
        f = UnifiedMemoryFacade(project_id="test")
        f.register_adapter(a)
        run(f.initialise())

        # Write
        rec = run(f.write("test lifecycle content"))
        assert rec is not None

        # Search
        results = run(f.search_semantic("lifecycle"))
        assert len(results) == 1

        # Read (touches)
        fetched = run(f.read(rec.record_id))
        assert fetched is not None
        assert fetched.access_count == 1

        # Delete
        assert run(f.delete(rec.record_id))
        assert run(f.read(rec.record_id)) is None

    def test_episode_recording_via_facade(self):
        """Facade를 통한 에피소드 기록."""
        a = InMemoryAdapter()
        f = UnifiedMemoryFacade(project_id="test")
        f.register_adapter(a)
        run(f.initialise())

        ep = EpisodeRecord(
            run_id="r1",
            agent_name="Architect",
            task_input="design authentication system",
            actions=[{"skill_name": "analyze", "args": {}}],
            outcome="success",
            duration_ms=3000,
        )
        assert run(f.record_episode(ep))

        # Verify stored
        rec = run(f.read(ep.episode_id))
        assert rec is not None
        assert rec.memory_type == MemoryType.EPISODIC

    def test_multi_adapter_integration(self, tmp_path):
        """여러 어댑터가 동시에 작동하는 통합 테스트."""
        mem = InMemoryAdapter("mem")
        kg = KnowledgeGraphAdapter(workspace=str(tmp_path))

        f = UnifiedMemoryFacade(project_id="test")
        f.register_adapter(mem)
        f.register_adapter(kg)
        run(f.initialise())

        # Write semantic to mem
        run(f.write("semantic fact", memory_type=MemoryType.SEMANTIC, target_backend="mem"))

        # Write graph node to kg
        run(f.write(
            "solution: fix import",
            memory_type=MemoryType.GRAPH,
            metadata={"node_type": "solution", "label": "fix import"},
            target_backend="knowledge_graph",
        ))

        # Search across both
        all_results = run(f.search_semantic("fix"))
        # Should find the graph node
        assert any("fix import" in r.content for r in all_results)

    def test_router_with_populated_memory(self):
        """라우터가 채워진 메모리에서 올바르게 쿼리하는지 검증."""
        a = InMemoryAdapter()
        f = UnifiedMemoryFacade(project_id="test")
        f.register_adapter(a)
        run(f.initialise())

        # Populate
        run(f.write("이전 로그인 작업 결과", memory_type=MemoryType.EPISODIC))
        run(f.write("null pointer 해결책", memory_type=MemoryType.GRAPH))
        run(f.write("현재 빌드 상태: passing", memory_type=MemoryType.WORKING))

        router = MemoryRouter(f)

        # Episodic query — keyword "로그인" matches episodic content
        results = run(router.route("지난번에 로그인 작업 기록"))
        assert all(r.memory_type == MemoryType.EPISODIC for r in results if results)

        # Graph query — keyword "해결책" matches graph content
        results = run(router.route("null pointer 어떻게 해결해? 해결책"))
        assert all(r.memory_type == MemoryType.GRAPH for r in results if results)

        # Working state — keyword "상태" matches working content
        results = run(router.route("현재 상태 빌드"))
        assert all(r.memory_type == MemoryType.WORKING for r in results if results)

    def test_consolidation_hook_to_graph_pipeline(self, tmp_path):
        """ConsolidationHook → Episode → Graph 파이프라인 E2E."""
        # Setup facade with in-memory adapter
        a = InMemoryAdapter()
        f = UnifiedMemoryFacade(project_id="test")
        f.register_adapter(a)
        run(f.initialise())

        # Setup consolidation hook
        hook = MemoryConsolidationHook()
        hook.set_facade(f)

        # Create JSONL trace
        logs_dir = tmp_path / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        events = [
            {"event_type": "run_start", "timestamp": "2026-03-17T10:00:00+00:00",
             "run_id": "r1", "agent_name": "Dev", "task_input": "fix auth bug"},
            {"event_type": "skill_call_start", "run_id": "r1", "skill_name": "read_file"},
            {"event_type": "skill_call_end", "run_id": "r1", "skill_name": "read_file"},
            {"event_type": "run_end", "run_id": "r1", "ok": True, "duration_ms": 2000},
        ]
        (logs_dir / "trace_r1.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events), encoding="utf-8"
        )

        # Run hook
        agent_state = {"run_id": "r1", "workspace": str(tmp_path), "project_id": "test"}
        result = hook.post_execute(agent_state, {"ok": True})

        # Verify episode was recorded
        assert "_episode_id" in result
        episode_id = result["_episode_id"]

        # Search for it
        results = run(f.search_semantic("auth bug"))
        assert len(results) >= 1
