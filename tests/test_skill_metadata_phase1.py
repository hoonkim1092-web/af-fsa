"""
tests/test_skill_metadata_phase1.py
===================================
Phase 1: 메타데이터 데코레이터 및 레지스트리 단위 테스트
"""

import pytest
from core.skill_metadata import (
    SkillMetadata, SkillCategory, SkillType, 
    skill_metadata, get_skill_metadata, has_skill_metadata
)
from core.skill_registry import SkillRegistry


class TestSkillMetadataSchema:
    def test_create_metadata(self):
        metadata = SkillMetadata(skill_id="test-skill", name="Test Skill")
        assert metadata.skill_id == "test-skill"

    def test_metadata_defaults(self):
        metadata = SkillMetadata(skill_id="test")
        assert metadata.version == "0.1.0"
        assert metadata.max_tokens == 4000


class TestSkillMetadataDecorator:
    def test_decorator_attaches_metadata(self):
        metadata = SkillMetadata(skill_id="my-skill")
        @skill_metadata(metadata)
        def my_func():
            pass
        assert has_skill_metadata(my_func)


class TestSkillRegistry:
    def test_register_and_get(self):
        registry = SkillRegistry()
        registry.clear()
        metadata = SkillMetadata(skill_id="test-skill")
        registry.register(metadata)
        retrieved = registry.get("test-skill")
        assert retrieved is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
