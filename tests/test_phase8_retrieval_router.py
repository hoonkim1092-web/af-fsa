"""
Phase 8: Retrieval Router + Evidence Schema + SemanticEmbedder 연구 통합 테스트.
"""
import pytest
from core.retrieval_router import RetrievalRouter, RetrievalStrategy, RetrievalPlan


class TestRetrievalRouter:
    """RetrievalRouter 분류 정확도 테스트."""

    def setup_method(self):
        self.router = RetrievalRouter()

    # ── 코드/설정 요청 → DIRECT_LOCAL ──
    def test_code_query_routes_to_direct_local(self):
        plan = self.router.classify("core/skill_loader.py 파일의 함수 구현을 확인해줘")
        assert plan.primary == RetrievalStrategy.DIRECT_LOCAL

    def test_config_query_routes_to_direct_local(self):
        plan = self.router.classify("config.yaml 설정 파일을 수정해줘")
        assert plan.primary == RetrievalStrategy.DIRECT_LOCAL

    def test_import_query_routes_to_direct_local(self):
        plan = self.router.classify("import 경로를 변경해야 하는 모듈을 찾아줘")
        assert plan.primary == RetrievalStrategy.DIRECT_LOCAL

    # ── 문서/가이드 요청 → DOCUMENT_RAG ──
    def test_doc_query_routes_to_document_rag(self):
        plan = self.router.classify("README 문서에서 사용법 가이드를 찾아줘")
        assert plan.primary == RetrievalStrategy.DOCUMENT_RAG

    def test_architecture_query_routes_to_document_rag(self):
        plan = self.router.classify("시스템 아키텍처 설계 문서를 참고해줘")
        assert plan.primary == RetrievalStrategy.DOCUMENT_RAG

    # ── 스킬 요청 → SEMANTIC_SKILL ──
    def test_skill_query_routes_to_semantic_skill(self):
        plan = self.router.classify("web-search 스킬을 설치해줘")
        assert plan.primary == RetrievalStrategy.SEMANTIC_SKILL

    def test_skill_query_with_research_context(self):
        plan = self.router.classify(
            "데이터 분석 기능이 필요해",
            context={"phase": "research"},
        )
        assert plan.primary == RetrievalStrategy.SEMANTIC_SKILL

    # ── 웹 요청 → LIVE_WEB ──
    def test_web_query_routes_to_live_web(self):
        plan = self.router.classify("2026년 최신 트렌드를 알려줘")
        assert plan.primary == RetrievalStrategy.LIVE_WEB

    # ── 기본값 ──
    def test_ambiguous_query_defaults_to_semantic_skill(self):
        plan = self.router.classify("뭔가 해줘")
        assert plan.primary == RetrievalStrategy.SEMANTIC_SKILL

    # ── 전략 목록 ──
    def test_plan_contains_multiple_strategies(self):
        plan = self.router.classify("스킬 문서를 참고해서 설치해줘")
        assert len(plan.strategies) >= 2

    # ── confidence ──
    def test_confidence_range(self):
        plan = self.router.classify("core/utils.py 코드를 봐줘")
        assert 0.0 <= plan.confidence <= 1.0

    # ── 필터 ──
    def test_filters_from_context(self):
        plan = self.router.classify(
            "검색해줘",
            context={"phase": "research", "role": "developer"},
        )
        assert plan.filters.get("role") == "developer"


class TestEvidencePackExtension:
    """확장된 evidence_pack 필드 검증."""

    def test_researcher_evidence_pack_has_new_fields(self):
        """evidence_pack에 Phase 1 확장 필드가 존재하는지 확인."""
        from unittest.mock import patch, MagicMock

        from core.researcher import HimariResearchAgent

        agent = HimariResearchAgent(mr=MagicMock())

        # LLM 호출을 모킹하여 연구 실행
        mock_reqs = {"goal": "테스트", "missing_skills": ["web_search"]}
        mock_agent = {"role": "developer", "name": "test"}

        with patch.object(agent, "_registry_skill_index", return_value={
            "web_search": {
                "id": "web_search",
                "name": "Web Search",
                "capabilities": ["search", "web"],
                "meta": {},
            }
        }):
            with patch("core.researcher.execute_requirement_prompt", return_value={"ok": False}):
                with patch("core.researcher.query_notebooklm", return_value=""):
                    result = agent.research(mock_agent, mock_reqs)

        ep = result["evidence_pack"]

        # 기존 필드
        assert "generated_at" in ep
        assert "agent_role" in ep
        assert "goal" in ep
        assert "targets" in ep

        # Phase 1 확장 필드
        assert "retrieval_strategy" in ep
        assert "retrieval_confidence" in ep
        assert "semantic_available" in ep
        assert "feedback_history" in ep
        assert isinstance(ep["feedback_history"], list)

    def test_target_has_provenance_fields(self):
        """각 target에 matching_rationale, source_type 필드가 있는지 확인."""
        from unittest.mock import patch, MagicMock

        from core.researcher import HimariResearchAgent

        agent = HimariResearchAgent(mr=MagicMock())

        mock_reqs = {"goal": "테스트", "missing_skills": ["git_master"]}
        mock_agent = {"role": "developer", "name": "test"}

        with patch.object(agent, "_registry_skill_index", return_value={
            "git_master": {
                "id": "git_master",
                "name": "Git Master",
                "capabilities": ["git", "version_control"],
                "meta": {"last_test_ok": True},
            }
        }):
            with patch("core.researcher.execute_requirement_prompt", return_value={"ok": False}):
                with patch("core.researcher.query_notebooklm", return_value=""):
                    result = agent.research(mock_agent, mock_reqs)

        target = result["evidence_pack"]["targets"].get("git_master", {})
        assert "matching_rationale" in target
        assert "source_type" in target
        assert target["source_type"] == "local_registry"
        assert "feedback_history" in target

    def test_score_candidate_includes_semantic(self):
        """_score_candidate가 semantic_score를 verify에 포함하는지 확인."""
        from unittest.mock import MagicMock

        from core.researcher import HimariResearchAgent

        agent = HimariResearchAgent(mr=MagicMock())

        item = {
            "id": "web_search",
            "name": "Web Search",
            "capabilities": ["search"],
            "meta": {},
        }
        score, verify = agent._score_candidate("web_search", item)

        assert "semantic_score" in verify
        assert isinstance(verify["semantic_score"], float)
        assert score >= 0
        assert score <= 100
