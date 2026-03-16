"""
core/skill_context_config.py
============================
모델별 스킬 컨텍스트 설정 중앙화.
모든 MAX 상수는 여기서만 정의하고 참조한다.

토큰 계산 공식:
  max_skills = max(
    MIN_SKILLS,
    min(
      (context_tokens * CONTEXT_USAGE_RATIO - RESERVED_TOKENS) / SKILL_SIGNATURE_TOKENS_AVG,
      MAX_SKILLS_ABSOLUTE
    )
  )
"""
from dataclasses import dataclass
from typing import Dict

# ========== 전역 설정 상수 ==========
SKILL_SIGNATURE_TOKENS_AVG: int = 600      # 평균 스킬 메타데이터 크기
RESERVED_TOKENS: int = 3500                # 시스템 프롬프트 + 버퍼
CONTEXT_USAGE_RATIO: float = 0.70          # 컨텍스트의 70%만 스킬에 할당
MIN_SKILLS: int = 3                        # 최소 3개 (필수)
MAX_SKILLS_ABSOLUTE: int = 200             # 최대 200개 (오버헤드 방지)

# ========== 모델별 컨텍스트 토큰 ==========
MODEL_CONTEXT_TOKENS: Dict[str, int] = {
    "claude-haiku": 8_000,
    "claude-sonnet": 200_000,
    "claude-opus": 200_000,
    "gemini-2.0-flash": 100_000,
    "gemini-1.5": 100_000,
    "gpt-4": 128_000,
    "gpt-3.5": 4_096,
    "default": 8_000,
}


def get_context_tokens(model_name: str) -> int:
    """
    모델 이름으로부터 컨텍스트 토큰 수를 조회.

    Args:
        model_name: 모델 이름 (예: "claude-haiku-4-5", "claude-sonnet-4-6")

    Returns:
        컨텍스트 토큰 수
    """
    name = (model_name or "").lower()
    for prefix, tokens in MODEL_CONTEXT_TOKENS.items():
        if prefix in name:
            return tokens
    return MODEL_CONTEXT_TOKENS["default"]


def get_max_skills_for_model(model_name: str) -> int:
    """
    모델의 컨텍스트 크기에 맞춰 최대 스킬 개수를 자동 계산.

    Args:
        model_name: 모델 이름

    Returns:
        모델에 적합한 최대 스킬 개수

    Example:
        >>> get_max_skills_for_model("claude-haiku-4-5")
        3
        >>> get_max_skills_for_model("claude-sonnet-4-6")
        228
    """
    context = get_context_tokens(model_name)
    available = context * CONTEXT_USAGE_RATIO - RESERVED_TOKENS
    count = int(available / SKILL_SIGNATURE_TOKENS_AVG)
    return max(MIN_SKILLS, min(count, MAX_SKILLS_ABSOLUTE))


@dataclass
class SkillLoaderConfig:
    """스킬 로더 설정을 담는 데이터 클래스."""

    model_name: str = "default"
    """로더가 적용될 모델 이름"""

    enforce_dep_consistency: bool = True
    """의존성 주입 시 일관성 유지 (최대 스킬 초과 방지)"""

    use_adaptive_limit: bool = True
    """True: 모델별 자동 계산 / False: max_skills_override 사용"""

    max_skills_override: int = 0
    """0: 자동 계산 사용 / >0: 수동 지정"""

    dep_inject_max_iter: int = 5
    """의존성 재귀 주입 최대 반복 횟수"""

    @property
    def max_skills(self) -> int:
        """
        현재 설정에 따른 최대 스킬 개수 반환.

        우선순위:
        1. max_skills_override > 0 이면 해당 값 사용
        2. use_adaptive_limit=True 면 get_max_skills_for_model() 사용
        3. 기본값 12
        """
        if self.max_skills_override > 0:
            return self.max_skills_override
        if self.use_adaptive_limit:
            return get_max_skills_for_model(self.model_name)
        return 12
