import os

try:
    from core.decorators import skill_metadata
except ImportError:
    # Fallback if decorator doesn't exist yet
    def skill_metadata(**kwargs):
        def decorator(func):
            func.__skill_metadata__ = kwargs
            return func
        return decorator

SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.agents/skills"))

def _read_skill_md(skill_name: str) -> str:
    path = os.path.join(SKILL_DIR, skill_name, "SKILL.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return f"[Error] Guideline {skill_name} not found."

@skill_metadata(category="domain", max_tokens=2000)
def get_framework_selection_guideline(ctx) -> str:
    """[When to use] 프로젝트 초기에 LangChain, LangGraph, Deep Agents 중 어떤 프레임워크를 선택할지 기준이 필요할 때 호출하라."""
    return _read_skill_md("framework-selection")

@skill_metadata(category="domain", max_tokens=2000)
def get_langchain_dependencies_guideline(ctx) -> str:
    """[When to use] 패키지 버전 관리, 의존성 오류 해결, 또는 프로젝트 초기 버전 설정이 필요할 때 호출하라."""
    return _read_skill_md("langchain-dependencies")

@skill_metadata(category="domain", max_tokens=3000)
def get_deep_agents_core_guideline(ctx) -> str:
    """[When to use] Deep Agents 아키텍처 및 코어 셋업을 구성할 때 호출하라."""
    return _read_skill_md("deep-agents-core")

@skill_metadata(category="domain", max_tokens=3000)
def get_deep_agents_memory_guideline(ctx) -> str:
    """[When to use] Deep Agents 환경에서 메모리 및 영속성 레이어를 구현할 때 호출하라."""
    return _read_skill_md("deep-agents-memory")

@skill_metadata(category="domain", max_tokens=3000)
def get_deep_agents_orchestration_guideline(ctx) -> str:
    """[When to use] Deep Agents의 서브에이전트, 오케스트레이션 및 파이프라인 구성을 살펴볼 때 호출하라."""
    return _read_skill_md("deep-agents-orchestration")

@skill_metadata(category="domain", max_tokens=3000)
def get_langchain_fundamentals_guideline(ctx) -> str:
    """[When to use] LangChain의 기초 구문, 에이전트 생성 및 툴 정의 방법을 확인할 때 호출하라."""
    return _read_skill_md("langchain-fundamentals")

@skill_metadata(category="domain", max_tokens=3000)
def get_langchain_middleware_guideline(ctx) -> str:
    """[When to use] 미들웨어를 이용한 요청/응답 가로채기, 로깅, 커스텀 훅 구현 시 호출하라."""
    return _read_skill_md("langchain-middleware")

@skill_metadata(category="domain", max_tokens=3000)
def get_langchain_rag_guideline(ctx) -> str:
    """[When to use] 문서 로드, 임베딩, 벡터 스토어 구축 등 RAG 파이프라인 설계 시 호출하라."""
    return _read_skill_md("langchain-rag")

@skill_metadata(category="domain", max_tokens=3000)
def get_langgraph_fundamentals_guideline(ctx) -> str:
    """[When to use] LangGraph의 기초 상태 관리, 노드 에지 라우팅 구조를 파악할 때 호출하라."""
    return _read_skill_md("langgraph-fundamentals")

@skill_metadata(category="domain", max_tokens=3000)
def get_langgraph_human_in_the_loop_guideline(ctx) -> str:
    """[When to use] 사람의 승인이 필요한 인터럽트 흐름(HITL)을 LangGraph에 구현할 때 호출하라."""
    return _read_skill_md("langgraph-human-in-the-loop")

@skill_metadata(category="domain", max_tokens=3000)
def get_langgraph_persistence_guideline(ctx) -> str:
    """[When to use] LangGraph의 체크포인터 기반 영속성(Persistence)과 과거 상태 복원 기능을 다룰 때 호출하라."""
    return _read_skill_md("langgraph-persistence")
