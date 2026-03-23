"""
skills/domain/langchain/langchain_guidelines.py
================================================
LangChain/LangGraph/Deep Agents Knowledge 스킬.

.agents/skills/{스킬명}/SKILL.md를 읽어 에이전트에 가이드라인을 제공합니다.
Phase 1 SkillMetadata 시스템과 연동하여 12-Cap 동적 로더에서 자동 감지됩니다.
"""

import os

from core.skill_metadata import SkillMetadata, SkillCategory, SkillType, skill_metadata

SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.agents/skills"))


def _read_skill_md(skill_name: str) -> str:
    path = os.path.join(SKILL_DIR, skill_name, "SKILL.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return f"[Error] Guideline '{skill_name}' not found at {path}"


# =============================================================================
# Knowledge 스킬 함수 (Phase 1 SkillMetadata 연동)
# =============================================================================

@skill_metadata(SkillMetadata(
    skill_id="framework-selection",
    name="Framework Selection Guide",
    description="LangChain, LangGraph, Deep Agents 중 프레임워크 선택 기준 가이드",
    when_to_use="프로젝트 초기에 어떤 프레임워크를 사용할지 결정이 필요할 때",
    when_to_use_keywords=["프레임워크", "선택", "langchain", "langgraph", "deep agents", "framework"],
    semantic_tags=["framework-selection", "architecture", "decision"],
    category=SkillCategory.PLAN,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=2000,
))
def get_framework_selection_guideline(ctx) -> str:
    """프로젝트 초기에 LangChain, LangGraph, Deep Agents 중 어떤 프레임워크를 선택할지 기준을 제공합니다."""
    return _read_skill_md("framework-selection")


@skill_metadata(SkillMetadata(
    skill_id="langchain-dependencies",
    name="LangChain Dependencies Guide",
    description="LangChain/LangGraph/LangSmith 패키지 버전 관리 및 의존성 가이드",
    when_to_use="패키지 버전 관리, 의존성 오류 해결, 프로젝트 초기 버전 설정이 필요할 때",
    when_to_use_keywords=["패키지", "버전", "의존성", "설치", "pip", "dependency", "install"],
    semantic_tags=["dependency-management", "package-version", "setup"],
    category=SkillCategory.PLAN,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=2000,
))
def get_langchain_dependencies_guideline(ctx) -> str:
    """LangChain/LangGraph/LangSmith 패키지 버전 관리 및 의존성 해결 가이드를 제공합니다."""
    return _read_skill_md("langchain-dependencies")


@skill_metadata(SkillMetadata(
    skill_id="langchain-fundamentals",
    name="LangChain Fundamentals Guide",
    description="create_agent(), @tool 데코레이터, 에이전트 기본 구문 가이드",
    when_to_use="LangChain 에이전트 생성, 툴 정의, 기본 구문을 확인할 때",
    when_to_use_keywords=["에이전트", "create_agent", "tool", "langchain", "agent", "도구"],
    semantic_tags=["langchain", "agent-creation", "tool-definition", "fundamentals"],
    category=SkillCategory.CODING,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_langchain_fundamentals_guideline(ctx) -> str:
    """LangChain create_agent(), @tool, 에이전트 기본 구문 가이드를 제공합니다."""
    return _read_skill_md("langchain-fundamentals")


@skill_metadata(SkillMetadata(
    skill_id="langchain-middleware",
    name="LangChain Middleware Guide",
    description="미들웨어를 이용한 HITL, 커스텀 훅, 요청/응답 가로채기 가이드",
    when_to_use="미들웨어를 이용한 요청/응답 가로채기, 로깅, 커스텀 훅 구현 시",
    when_to_use_keywords=["미들웨어", "middleware", "hook", "HITL", "human-in-the-loop", "승인"],
    semantic_tags=["middleware", "hook", "human-in-the-loop", "interceptor"],
    category=SkillCategory.CODING,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_langchain_middleware_guideline(ctx) -> str:
    """LangChain 미들웨어 패턴 (HITL, 커스텀 훅) 가이드를 제공합니다."""
    return _read_skill_md("langchain-middleware")


@skill_metadata(SkillMetadata(
    skill_id="langchain-rag",
    name="LangChain RAG Guide",
    description="문서 로드, 임베딩, 벡터 스토어 구축 등 RAG 파이프라인 가이드",
    when_to_use="문서 로드, 임베딩, 벡터 스토어 구축 등 RAG 파이프라인 설계 시",
    when_to_use_keywords=["RAG", "벡터", "임베딩", "문서", "검색", "vector", "embedding", "retrieval"],
    semantic_tags=["rag", "retrieval", "embedding", "vector-store", "document-loader"],
    category=SkillCategory.RESEARCH,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_langchain_rag_guideline(ctx) -> str:
    """LangChain RAG 파이프라인 설계 가이드를 제공합니다."""
    return _read_skill_md("langchain-rag")


@skill_metadata(SkillMetadata(
    skill_id="langgraph-fundamentals",
    name="LangGraph Fundamentals Guide",
    description="StateGraph, 노드/에지 라우팅, 상태 관리 기초 가이드",
    when_to_use="LangGraph의 기초 상태 관리, 노드 에지 라우팅 구조를 파악할 때",
    when_to_use_keywords=["langgraph", "StateGraph", "노드", "에지", "그래프", "상태", "node", "edge"],
    semantic_tags=["langgraph", "state-graph", "node-edge", "routing"],
    category=SkillCategory.CODING,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_langgraph_fundamentals_guideline(ctx) -> str:
    """LangGraph StateGraph, 노드/에지 라우팅, 상태 관리 가이드를 제공합니다."""
    return _read_skill_md("langgraph-fundamentals")


@skill_metadata(SkillMetadata(
    skill_id="langgraph-human-in-the-loop",
    name="LangGraph HITL Guide",
    description="interrupt/resume 기반 사람 승인 워크플로우 가이드",
    when_to_use="사람의 승인이 필요한 인터럽트 흐름(HITL)을 LangGraph에 구현할 때",
    when_to_use_keywords=["interrupt", "resume", "승인", "HITL", "human", "approval"],
    semantic_tags=["human-in-the-loop", "interrupt", "approval", "workflow"],
    category=SkillCategory.CODING,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_langgraph_human_in_the_loop_guideline(ctx) -> str:
    """LangGraph interrupt/resume 기반 HITL 가이드를 제공합니다."""
    return _read_skill_md("langgraph-human-in-the-loop")


@skill_metadata(SkillMetadata(
    skill_id="langgraph-persistence",
    name="LangGraph Persistence Guide",
    description="체크포인터 기반 영속성, Store, 과거 상태 복원 가이드",
    when_to_use="LangGraph의 체크포인터 기반 영속성과 과거 상태 복원 기능을 다룰 때",
    when_to_use_keywords=["체크포인터", "checkpointer", "persistence", "영속", "store", "thread"],
    semantic_tags=["persistence", "checkpointer", "state-recovery", "store"],
    category=SkillCategory.CODING,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_langgraph_persistence_guideline(ctx) -> str:
    """LangGraph 체크포인터 영속성, Store, 과거 상태 복원 가이드를 제공합니다."""
    return _read_skill_md("langgraph-persistence")


@skill_metadata(SkillMetadata(
    skill_id="deep-agents-core",
    name="Deep Agents Core Guide",
    description="Deep Agents 아키텍처, create_deep_agent(), SKILL.md 포맷 가이드",
    when_to_use="Deep Agents 아키텍처 및 코어 셋업을 구성할 때",
    when_to_use_keywords=["deep agents", "deep_agent", "harness", "create_deep_agent"],
    semantic_tags=["deep-agents", "architecture", "harness", "core-setup"],
    category=SkillCategory.CODING,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_deep_agents_core_guideline(ctx) -> str:
    """Deep Agents 코어 아키텍처 가이드를 제공합니다."""
    return _read_skill_md("deep-agents-core")


@skill_metadata(SkillMetadata(
    skill_id="deep-agents-memory",
    name="Deep Agents Memory Guide",
    description="Deep Agents 메모리/영속성 레이어 (StateBackend, StoreBackend) 가이드",
    when_to_use="Deep Agents 환경에서 메모리 및 영속성 레이어를 구현할 때",
    when_to_use_keywords=["메모리", "영속성", "StateBackend", "StoreBackend", "memory", "persistence"],
    semantic_tags=["deep-agents", "memory", "persistence", "state-backend"],
    category=SkillCategory.CODING,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_deep_agents_memory_guideline(ctx) -> str:
    """Deep Agents 메모리/영속성 레이어 가이드를 제공합니다."""
    return _read_skill_md("deep-agents-memory")


@skill_metadata(SkillMetadata(
    skill_id="deep-agents-orchestration",
    name="Deep Agents Orchestration Guide",
    description="서브에이전트, 오케스트레이션, TodoList 기반 태스크 플래닝 가이드",
    when_to_use="Deep Agents의 서브에이전트, 오케스트레이션 및 파이프라인 구성 시",
    when_to_use_keywords=["서브에이전트", "오케스트레이션", "SubAgent", "TodoList", "orchestration"],
    semantic_tags=["deep-agents", "orchestration", "sub-agent", "task-planning"],
    category=SkillCategory.PLAN,
    skill_type=SkillType.KNOWLEDGE,
    max_tokens=3000,
))
def get_deep_agents_orchestration_guideline(ctx) -> str:
    """Deep Agents 서브에이전트/오케스트레이션 가이드를 제공합니다."""
    return _read_skill_md("deep-agents-orchestration")
