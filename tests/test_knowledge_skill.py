"""
tests/test_knowledge_skill.py
=============================
Knowledge skill parser and prompt assembly tests.
"""

import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.knowledge_skill import (
    KnowledgeSkill,
    build_knowledge_prompt,
    filter_relevant_knowledge,
    parse_skill_md,
    scan_knowledge_skills,
)


@pytest.fixture
def temp_skills_dir(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


@pytest.fixture
def sample_skill_md(temp_skills_dir):
    skill_dir = temp_skills_dir / "test_guide"
    skill_dir.mkdir(parents=True, exist_ok=True)
    md_path = skill_dir / "skill.md"
    md_path.write_text(
        "---\n"
        "name: Test Guide\n"
        "description: Testing workflow guide\n"
        "---\n\n"
        "# Test Guide\n\n"
        "1. Write unit tests.\n"
        "2. Add integration tests.\n"
        "3. Cover edge cases.\n",
        encoding="utf-8",
    )
    return str(md_path)


@pytest.fixture
def sample_no_frontmatter(temp_skills_dir):
    skill_dir = temp_skills_dir / "plain_guide"
    skill_dir.mkdir(parents=True, exist_ok=True)
    md_path = skill_dir / "skill.md"
    md_path.write_text("# Plain Guide\n\nMarkdown without frontmatter.\n", encoding="utf-8")
    return str(md_path)


class TestParseSkillMd:
    def test_parse_with_frontmatter(self, sample_skill_md):
        skill = parse_skill_md(sample_skill_md)

        assert skill is not None
        assert skill.name == "Test Guide"
        assert skill.description == "Testing workflow guide"
        assert "Write unit tests." in skill.content
        assert skill.id == "test_guide"
        assert skill.source_path == sample_skill_md

    def test_parse_without_frontmatter(self, sample_no_frontmatter):
        skill = parse_skill_md(sample_no_frontmatter)

        assert skill is not None
        assert skill.id == "plain_guide"
        assert skill.name == "plain_guide"
        assert skill.description == ""
        assert "Plain Guide" in skill.content

    def test_parse_nonexistent(self):
        assert parse_skill_md("/nonexistent/path/skill.md") is None


class TestScanKnowledgeSkills:
    def test_scan_finds_skills(self, temp_skills_dir, sample_skill_md):
        del sample_skill_md
        skills = scan_knowledge_skills(str(temp_skills_dir))

        assert len(skills) >= 1
        skill_ids = [skill.id for skill in skills]
        assert "test_guide" in skill_ids

    def test_scan_empty_dir(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir(parents=True, exist_ok=True)

        assert scan_knowledge_skills(str(empty_dir)) == []

    def test_scan_nonexistent_dir(self):
        assert scan_knowledge_skills("/nonexistent/path") == []


class TestFilterRelevantKnowledge:
    def test_filter_by_name_match(self):
        skills = [
            KnowledgeSkill(
                id="review",
                name="Code Review",
                description="Review workflow",
                content="...",
                source_path="/a/b",
                updated_at=0.0,
            ),
            KnowledgeSkill(
                id="deploy",
                name="Deploy Guide",
                description="Deployment workflow",
                content="...",
                source_path="/a/c",
                updated_at=0.0,
            ),
        ]

        result = filter_relevant_knowledge(skills, "Please do a Code Review for this change")
        assert len(result) == 1
        assert result[0].id == "review"

    def test_filter_by_description_keyword(self):
        skills = [
            KnowledgeSkill(
                id="security",
                name="Security Guide",
                description="security vulnerability checklist",
                content="...",
                source_path="/a/d",
                updated_at=0.0,
            ),
        ]

        result = filter_relevant_knowledge(skills, "Check for vulnerability issues")
        assert len(result) == 1

    def test_filter_no_match(self):
        skills = [
            KnowledgeSkill(
                id="deploy",
                name="Deploy Guide",
                description="Deployment workflow",
                content="...",
                source_path="/a/e",
                updated_at=0.0,
            ),
        ]

        result = filter_relevant_knowledge(skills, "Please review the code")
        assert len(result) == 0


class TestBuildKnowledgePrompt:
    def test_build_prompt(self):
        skills = [
            KnowledgeSkill(
                id="guide",
                name="Guide",
                description="Prompt assembly test",
                content="## Steps\n1. First step",
                source_path="/a/f",
                updated_at=0.0,
            ),
        ]

        prompt = build_knowledge_prompt(skills)
        assert "[Knowledge Skills Loaded]" in prompt
        assert "### Guide" in prompt
        assert "First step" in prompt

    def test_build_prompt_empty(self):
        assert build_knowledge_prompt([]) == ""


class TestSkillRegistryKnowledgeType:
    def test_normalize_includes_type(self):
        from core.skill_registry import _normalize_skill_entry

        _, entry = _normalize_skill_entry(
            "test_skill",
            {
                "name": "Test Skill",
                "type": "knowledge",
                "purpose": "Test purpose",
            },
        )

        assert entry["type"] == "knowledge"

    def test_normalize_default_type(self):
        from core.skill_registry import _normalize_skill_entry

        _, entry = _normalize_skill_entry(
            "test_skill",
            {
                "name": "Test Skill",
                "purpose": "Test purpose",
            },
        )

        assert entry["type"] == "action"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
