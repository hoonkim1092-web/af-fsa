
import pytest
from core.skill_metadata import SkillMetadata, SkillCategory
from core.skill_registry import SkillRegistry
from core.skill_loader import DynamicSkillLoader, MAX_SKILLS_IN_CONTEXT

def test_skill_loader_initialization():
    loader = DynamicSkillLoader()
    assert loader is not None
    assert loader.relevance is not None

def test_12_cap_enforcement():
    registry = SkillRegistry()
    registry.clear()
    
    for i in range(50):
        metadata = SkillMetadata(
            skill_id=f"skill-{i:02d}",
            category=SkillCategory.CODING if i % 2 == 0 else SkillCategory.RESEARCH,
            when_to_use_keywords=["test"] * (i % 5 + 1)
        )
        registry.register(metadata)
    
    loader = DynamicSkillLoader()
    task = "test code test"
    
    selected, scores = loader.load_skills_for_task(task)
    
    assert len(selected) <= MAX_SKILLS_IN_CONTEXT
    assert len(selected) <= 12

def test_conflict_resolution():
    registry = SkillRegistry()
    registry.clear()
    
    skill1 = SkillMetadata(
        skill_id="web-search",
        category=SkillCategory.RESEARCH,
        when_to_use_keywords=["search"],
        incompatible_with=["local-search"]
    )
    
    skill2 = SkillMetadata(
        skill_id="local-search",
        category=SkillCategory.RESEARCH,
        when_to_use_keywords=["search"]
    )
    
    registry.register(skill1)
    registry.register(skill2)
    
    loader = DynamicSkillLoader()
    task = "search"
    
    selected, _ = loader.load_skills_for_task(task)
    
    skill_ids = [s.skill_id for s in selected]
    if len(skill_ids) > 1:
        assert "web-search" in skill_ids and "local-search" not in skill_ids

def test_keyword_matching():
    registry = SkillRegistry()
    registry.clear()
    
    skill = SkillMetadata(
        skill_id="code-gen",
        name="Code Generator",
        description="generates code",
        category=SkillCategory.CODING,
        when_to_use_keywords=["python", "code", "function"]
    )
    registry.register(skill)
    
    loader = DynamicSkillLoader()
    
    task_good = "python function code"
    selected_good, _ = loader.load_skills_for_task(task_good, min_score=0.1)
    assert len(selected_good) > 0
    
    task_bad = "weather today"
    selected_bad, _ = loader.load_skills_for_task(task_bad, min_score=0.2)
    assert len(selected_bad) == 0


def test_auto_invocable_skips_archived_and_manual_skills():
    registry = SkillRegistry()
    registry.clear()

    manual_only = SkillMetadata(
        skill_id="manual-only",
        category=SkillCategory.CODING,
        when_to_use_keywords=["python"],
        auto_invocable=False,
    )
    archived = SkillMetadata(
        skill_id="archived-skill",
        category=SkillCategory.CODING,
        when_to_use_keywords=["python"],
        lifecycle_stage="archived",
    )
    active = SkillMetadata(
        skill_id="active-skill",
        category=SkillCategory.CODING,
        when_to_use_keywords=["python"],
    )

    registry.register(manual_only)
    registry.register(archived)
    registry.register(active)

    loader = DynamicSkillLoader()
    selected, _ = loader.load_skills_for_task("python code", min_score=0.1)
    selected_ids = {skill.skill_id for skill in selected}

    assert "active-skill" in selected_ids
    assert "manual-only" not in selected_ids
    assert "archived-skill" not in selected_ids

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

