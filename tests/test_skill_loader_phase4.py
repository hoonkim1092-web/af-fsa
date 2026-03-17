"""
Phase 4 ENHANCEMENT 테스트
- SkillContextConfig: 모델별 토큰/스킬 계산, 입력값 검증
- AdaptiveSkillLoader: 모델별 로더 생성
"""
import pytest
from core.skill_context_config import (
    get_context_tokens,
    get_max_skills_for_model,
    SkillLoaderConfig,
    MODEL_CONTEXT_TOKENS,
)
from core.skill_loader import AdaptiveSkillLoader


class TestSkillContextConfig:
    """스킬 컨텍스트 설정 테스트"""

    def test_get_context_tokens_haiku(self):
        """Haiku 컨텍스트 토큰 확인"""
        assert get_context_tokens("claude-haiku-4-5") == 200_000

    def test_get_context_tokens_sonnet(self):
        """Sonnet 컨텍스트 토큰 확인"""
        assert get_context_tokens("claude-sonnet-4-6") == 200_000

    def test_get_context_tokens_default(self):
        """알 수 없는 모델은 기본값 반환"""
        assert get_context_tokens("unknown-model") == 8_000

    def test_get_context_tokens_none(self):
        """None 입력 시 기본값 반환"""
        assert get_context_tokens(None) == MODEL_CONTEXT_TOKENS["default"]

    def test_get_context_tokens_empty(self):
        """빈 문자열 입력 시 기본값 반환"""
        assert get_context_tokens("") == MODEL_CONTEXT_TOKENS["default"]

    def test_get_context_tokens_non_string(self):
        """비문자열 입력 시 기본값 반환"""
        assert get_context_tokens(123) == MODEL_CONTEXT_TOKENS["default"]

    def test_get_max_skills_haiku(self):
        """Haiku 최대 스킬 개수 확인 (200K 컨텍스트 → MAX_SKILLS_ABSOLUTE)"""
        result = get_max_skills_for_model("claude-haiku-4-5")
        assert result == 200  # MAX_SKILLS_ABSOLUTE

    def test_get_max_skills_sonnet(self):
        """Sonnet 최대 스킬 개수 확인"""
        result = get_max_skills_for_model("claude-sonnet-4-6")
        assert result == 200  # MAX_SKILLS_ABSOLUTE

    def test_skill_loader_config_auto(self):
        """SkillLoaderConfig 자동 계산"""
        config = SkillLoaderConfig(model_name="claude-sonnet-4-6")
        assert config.max_skills == 200

    def test_skill_loader_config_override(self):
        """SkillLoaderConfig 수동 지정"""
        config = SkillLoaderConfig(max_skills_override=50)
        assert config.max_skills == 50


class TestAdaptiveSkillLoader:
    """적응형 스킬 로더 테스트"""

    def test_for_model_haiku(self):
        """Haiku 로더 생성 확인"""
        loader = AdaptiveSkillLoader.for_model("claude-haiku-4-5")
        assert loader.config.model_name == "claude-haiku-4-5"
        assert loader.config.max_skills == 200

    def test_for_model_sonnet(self):
        """Sonnet 로더 생성 확인"""
        loader = AdaptiveSkillLoader.for_model("claude-sonnet-4-6")
        assert loader.config.model_name == "claude-sonnet-4-6"
        assert loader.config.max_skills == 200
