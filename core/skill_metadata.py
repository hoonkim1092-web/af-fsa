"""
core/skill_metadata.py
======================
스킬 메타데이터 스키마 및 데코레이터.

구조화된 스킬 등록 시스템으로, 스킬의 "언제 사용할 것인가"를
명시적으로 정의하여 Dynamic Skill Loader(12-Cap)에서 활용.
"""

from dataclasses import dataclass, asdict, field
from typing import Literal, Optional, List
from enum import Enum


# =============================================================================
# Enum 정의
# =============================================================================
class SkillCategory(str, Enum):
    """스킬 카테고리 분류"""
    CODING = "coding"              # 코드 작성, 리팩토링, 수정
    RESEARCH = "research"          # 웹 검색, 문서 분석, 조사
    IO = "io"                      # 파일 I/O, 메모리, 저장소 접근
    TESTING = "testing"            # 테스트 작성, 테스트 실행, 검증
    EVAL = "eval"                  # 실행 평가, 에러 분석, 피드백 수집
    PLAN = "plan"                  # 계획 수립, 설계, 아키텍처
    REVIEW = "review"              # 코드 리뷰, 품질 검증
    DEBUG = "debug"                # 디버깅, 문제 진단


class SkillType(str, Enum):
    """스킬 타입"""
    ACTION = "action"              # skill.py + meta.yaml (격리 실행 가능)
    KNOWLEDGE = "knowledge"        # SKILL.md (LLM용 프롬프트/가이드)
    TOOL = "tool"                  # 외부 도구 래퍼 (웹 검색, API 등)


# =============================================================================
# 메타데이터 스키마
# =============================================================================
@dataclass
class SkillMetadata:
    """
    스킬의 구조화된 메타데이터.

    이 클래스는 스킬이 "언제", "어떤 상황에서", "어떻게" 사용되는지를
    Dynamic Skill Loader가 이해할 수 있도록 정의합니다.
    """

    # === 기본 정보 ===
    skill_id: str
    """스킬 고유 식별자 (hyphen-case, 예: 'web-search', 'code-gen')"""

    name: str = ""
    """스킬 표시명 (예: 'Web Search', 'Code Generator')"""

    version: str = "0.1.0"
    """스킬 버전"""

    # === 설명 및 사용 시점 ===
    description: str = ""
    """스킬의 기능을 한두 문장으로 설명"""

    when_to_use: str = ""
    """
    스킬을 언제 사용할 것인가? (예: '최신 정보가 필요하거나,
    구글 검색이 필요할 때 사용. 오래된 API 문서라면 local-search 사용.')
    """

    when_NOT_to_use: str = ""
    """스킬을 사용하면 안 되는 경우"""

    use_case_examples: List[str] = field(default_factory=list)
    """
    구체적 사용 예시 (예: [
        '사용자가 "최신 뉴스를 찾아줘"라고 했을 때',
        '문제 설명에 "실시간", "현재", "최신" 키워드가 포함될 때'
    ])
    """

    # === 분류 ===
    category: SkillCategory = SkillCategory.CODING
    """스킬 카테고리"""

    skill_type: SkillType = SkillType.ACTION
    """스킬 타입 (action 또는 knowledge)"""

    # === 라우팅 신호 (Dynamic Loader용) ===
    when_to_use_keywords: List[str] = field(default_factory=list)
    """
    키워드 기반 매칭 (Fast Signal).
    예: ['최신', '실시간', '웹', '구글', '검색']
    """

    semantic_tags: List[str] = field(default_factory=list)
    """
    시맨틱 분류 태그 (Semantic Similarity 계산용).
    예: ['information-retrieval', 'search', 'internet']
    """

    # === 제약사항 ===
    max_tokens: int = 4000
    """스킬이 소비할 수 있는 최대 토큰 수 (컨텍스트 관리용)"""

    timeout_sec: int = 30
    """스킬 실행 타임아웃 (초)"""

    dependencies: List[str] = field(default_factory=list)
    """
    선행 스킬 (이 스킬을 실행하기 전에 먼저 필요한 스킬).
    예: ['web-search'] (이 스킬이 웹 검색 결과를 입력받으려면)
    """

    incompatible_with: List[str] = field(default_factory=list)
    """
    충돌 스킬 (같은 턴에서 함께 사용할 수 없는 스킬).
    예: ['local-search'] (같은 질의에 두 가지 검색 사용 X)
    """

    # === 지원 정보 ===
    requires_auth: bool = False
    """인증(API 키 등)이 필요한가?"""

    network_required: bool = False
    """네트워크 접근이 필요한가?"""

    stateful: bool = False
    """상태 저장이 필요한가? (세션 유지 등)"""

    # === 메타 ===
    author: str = "agent-factory"
    """스킬 작성자"""

    tags: List[str] = field(default_factory=list)
    """임의 태그 (추가 분류용)"""

    experimental: bool = False
    """실험 단계 스킬인가?"""

    @property
    def can_run_isolated(self) -> bool:
        """격리 실행(sandbox) 가능 여부"""
        return self.skill_type == SkillType.ACTION

    def to_dict(self) -> dict:
        """딕셔너리로 변환 (YAML 저장용)"""
        return asdict(self)


# =============================================================================
# 데코레이터
# =============================================================================
def skill_metadata(metadata: SkillMetadata):
    """
    스킬 함수/클래스에 메타데이터를 부착하는 데코레이터.

    기존 스킬과의 호환성을 유지하면서, 필요한 곳에서만 적용.

    사용 예시:
        @skill_metadata(SkillMetadata(
            skill_id="web-search",
            name="Web Search",
            description="구글/Tavily API로 웹 검색",
            category=SkillCategory.RESEARCH,
            when_to_use="사용자가 최신 정보를 요청했거나 현재 날짜 기준 정보가 필요할 때",
            when_to_use_keywords=["최신", "실시간", "웹", "구글"],
            max_tokens=2000,
            timeout_sec=20,
            incompatible_with=["local-search"]
        ))
        def web_search(query: str) -> dict:
            pass
    """
    def decorator(func_or_class):
        # 메타데이터를 함수/클래스의 특수 속성으로 부착
        func_or_class.__skill_metadata__ = metadata
        return func_or_class

    return decorator


# =============================================================================
# 메타데이터 조회 헬퍼
# =============================================================================
def get_skill_metadata(func_or_class) -> Optional[SkillMetadata]:
    """함수/클래스에서 메타데이터 추출 (있으면 반환, 없으면 None)"""
    return getattr(func_or_class, "__skill_metadata__", None)


def has_skill_metadata(func_or_class) -> bool:
    """함수/클래스에 메타데이터가 있는지 확인"""
    return hasattr(func_or_class, "__skill_metadata__")


def require_skill_metadata(func_or_class) -> SkillMetadata:
    """
    함수/클래스에서 메타데이터를 강제로 추출.
    없으면 ValueError 발생.
    """
    metadata = get_skill_metadata(func_or_class)
    if metadata is None:
        raise ValueError(
            f"{func_or_class} 에 @skill_metadata 데코레이터가 없습니다. "
            "Dynamic Skill Loader 사용 시 필수입니다."
        )
    return metadata
