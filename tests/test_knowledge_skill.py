"""
tests/test_knowledge_skill.py
==============================
Knowledge Skill 시스템 단위 테스트.
core/knowledge_skill.py 파서 및 AgentRunner 연동 검증.
"""

import os
import sys
import tempfile
import shutil
import pytest

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.knowledge_skill import (
    KnowledgeSkill,
    parse_skill_md,
    scan_knowledge_skills,
    filter_relevant_knowledge,
    build_knowledge_prompt,
)


# =============================================================================
# Fixtures
# =============================================================================
@pytest.fixture
def temp_skills_dir():
    """테스트용 임시 스킬 디렉토리 생성"""
    tmpdir = tempfile.mkdtemp(prefix="af_test_skills_")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sample_skill_md(temp_skills_dir):
    """YAML 프론트매터가 포함된 샘플 skill.md 생성"""
    skill_dir = os.path.join(temp_skills_dir, "test_guide")
    os.makedirs(skill_dir, exist_ok=True)
    md_path = os.path.join(skill_dir, "skill.md")
    content = """\
---
name: "테스트 가이드"
description: "테스트 작성 시 반드시 따라야 할 절차"
---

# 테스트 가이드

## 절차

1. 단위 테스트를 먼저 작성합니다
2. 통합 테스트를 추가합니다
3. 엣지 케이스를 점검합니다
"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    return md_path


@pytest.fixture
def sample_no_frontmatter(temp_skills_dir):
    """프론트매터 없는 순수 마크다운 skill.md"""
    skill_dir = os.path.join(temp_skills_dir, "plain_guide")
    os.makedirs(skill_dir, exist_ok=True)
    md_path = os.path.join(skill_dir, "skill.md")
    content = "# 간단한 가이드\n\n이것은 프론트매터 없는 마크다운입니다.\n"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    return md_path


# =============================================================================
# Tests: parse_skill_md
# =============================================================================
class TestParseSkillMd:
    def test_parse_with_frontmatter(self, sample_skill_md):
        """YAML 프론트매터가 포함된 마크다운 파싱"""
        skill = parse_skill_md(sample_skill_md)
        
        assert skill is not None
        assert skill.name == "테스트 가이드"
        assert skill.description == "테스트 작성 시 반드시 따라야 할 절차"
        assert "단위 테스트를 먼저 작성합니다" in skill.content
        assert skill.id == "test_guide"  # 디렉토리명에서 추출
        assert skill.source_path == sample_skill_md

    def test_parse_without_frontmatter(self, sample_no_frontmatter):
        """프론트매터 없는 마크다운도 파싱 가능"""
        skill = parse_skill_md(sample_no_frontmatter)
        
        assert skill is not None
        assert skill.id == "plain_guide"
        assert skill.name == "plain_guide"  # 프론트매터 없으면 디렉토리명
        assert skill.description == ""
        assert "간단한 가이드" in skill.content

    def test_parse_nonexistent(self):
        """존재하지 않는 파일은 None 반환"""
        result = parse_skill_md("/nonexistent/path/skill.md")
        assert result is None


# =============================================================================
# Tests: scan_knowledge_skills
# =============================================================================
class TestScanKnowledgeSkills:
    def test_scan_finds_skills(self, temp_skills_dir, sample_skill_md):
        """디렉토리 스캔 시 skill.md 파일 탐지"""
        skills = scan_knowledge_skills(temp_skills_dir)
        
        # sample_skill_md fixture가 temp_skills_dir/test_guide/skill.md에 생성
        assert len(skills) >= 1
        skill_ids = [s.id for s in skills]
        assert "test_guide" in skill_ids

    def test_scan_empty_dir(self):
        """빈 디렉토리 스캔 시 빈 리스트 반환"""
        tmpdir = tempfile.mkdtemp(prefix="af_empty_")
        try:
            skills = scan_knowledge_skills(tmpdir)
            assert skills == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_scan_nonexistent_dir(self):
        """존재하지 않는 디렉토리는 빈 리스트 반환"""
        skills = scan_knowledge_skills("/nonexistent/path")
        assert skills == []


# =============================================================================
# Tests: filter_relevant_knowledge
# =============================================================================
class TestFilterRelevantKnowledge:
    def test_filter_by_name_match(self):
        """태스크 텍스트에 스킬 이름이 포함되면 매칭"""
        skills = [
            KnowledgeSkill(
                id="review", name="코드 리뷰",
                description="코드 리뷰 절차",
                content="...", source_path="/a/b", updated_at=0.0
            ),
            KnowledgeSkill(
                id="deploy", name="배포 가이드",
                description="배포 절차",
                content="...", source_path="/a/c", updated_at=0.0
            ),
        ]
        
        result = filter_relevant_knowledge(skills, "코드 리뷰를 진행해주세요")
        assert len(result) == 1
        assert result[0].id == "review"

    def test_filter_by_description_keyword(self):
        """설명 내 키워드가 태스크에 포함되면 매칭"""
        skills = [
            KnowledgeSkill(
                id="security", name="보안 지침",
                description="보안 취약점 점검",
                content="...", source_path="/a/d", updated_at=0.0
            ),
        ]
        
        result = filter_relevant_knowledge(skills, "보안 취약점을 확인해주세요")
        assert len(result) == 1

    def test_filter_no_match(self):
        """매칭되지 않으면 빈 리스트"""
        skills = [
            KnowledgeSkill(
                id="deploy", name="배포 가이드",
                description="배포 절차",
                content="...", source_path="/a/e", updated_at=0.0
            ),
        ]
        
        result = filter_relevant_knowledge(skills, "코드 리뷰를 해주세요")
        assert len(result) == 0


# =============================================================================
# Tests: build_knowledge_prompt
# =============================================================================
class TestBuildKnowledgePrompt:
    def test_build_prompt(self):
        """Knowledge 스킬 목록을 프롬프트 텍스트로 변환"""
        skills = [
            KnowledgeSkill(
                id="guide", name="가이드",
                description="테스트 설명",
                content="## 절차\n1. 첫 번째 단계",
                source_path="/a/f", updated_at=0.0
            ),
        ]
        
        prompt = build_knowledge_prompt(skills)
        assert "[Knowledge Skills Loaded]" in prompt
        assert "### 가이드" in prompt
        assert "첫 번째 단계" in prompt

    def test_build_prompt_empty(self):
        """빈 리스트면 빈 문자열 반환"""
        prompt = build_knowledge_prompt([])
        assert prompt == ""


# =============================================================================
# Tests: register_skill with knowledge type
# =============================================================================
class TestSkillRegistryKnowledgeType:
    def test_normalize_includes_type(self):
        """_normalize_skill_entry가 type 필드를 포함하는지 확인"""
        from core.skill_registry import _normalize_skill_entry
        
        _, entry = _normalize_skill_entry("test_skill", {
            "name": "테스트",
            "type": "knowledge",
            "purpose": "테스트용",
        })
        
        assert entry["type"] == "knowledge"

    def test_normalize_default_type(self):
        """type 미지정 시 기본값 'action'"""
        from core.skill_registry import _normalize_skill_entry
        
        _, entry = _normalize_skill_entry("test_skill", {
            "name": "테스트",
            "purpose": "테스트용",
        })
        
        assert entry["type"] == "action"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
