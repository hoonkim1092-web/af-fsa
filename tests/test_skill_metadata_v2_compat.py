import yaml

from core.skill_metadata import SkillCategory, SkillType
from core.skill_metadata_adapter import (
    auto_detect_and_convert,
    convert_skill_md_to_metadata,
    convert_skill_yaml_to_metadata,
)



def test_convert_skill_md_supports_claude_style_frontmatter(tmp_path):
    skill_dir = tmp_path / "research-posts"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: research-posts
description: Generate platform-ready research posts
disable-model-invocation: true
user-invocable: true
allowed-tools: Read, Grep
context: fork
agent: Explore
model: claude-sonnet
argument-hint: [topic]
hooks:
  pre_run:
    - echo preparing
---

Build concise research-backed social posts.
""",
        encoding="utf-8",
    )

    metadata = convert_skill_md_to_metadata(str(skill_file), "research-posts")

    assert metadata is not None
    assert metadata.skill_id == "research-posts"
    assert metadata.skill_type == SkillType.KNOWLEDGE
    assert metadata.category == SkillCategory.PLAN
    assert metadata.auto_invocable is False
    assert metadata.user_invocable is True
    assert metadata.context_mode == "fork"
    assert metadata.preferred_agent == "Explore"
    assert metadata.preferred_model == "claude-sonnet"
    assert metadata.argument_hint == "[topic]"
    assert metadata.allowed_tools == ["Read", "Grep"]
    assert metadata.hooks == {"pre_run": ["echo preparing"]}



def test_convert_skill_yaml_supports_v2_package_fields(tmp_path):
    skill_dir = tmp_path / "file-ops"
    skill_dir.mkdir()
    generated_spec_path = skill_dir / "skill-spec.yaml"
    generated_spec_path.write_text("id: file-ops\n", encoding="utf-8")
    evals_path = skill_dir / "evals.yml"
    evals_path.write_text("cases: []\n", encoding="utf-8")
    spec_path = skill_dir / "skill.yaml"
    spec_path.write_text(
        yaml.safe_dump(
            {
                "id": "file-ops",
                "name": "File Ops",
                "kind": "action",
                "description": "File operations package",
                "when_to_use": ["Need to read or write files"],
                "category": "io",
                "dependencies": ["path-utils"],
                "invocation": {
                    "auto": False,
                    "user_invocable": True,
                    "planner_invocable": False,
                    "context_mode": "isolated",
                },
                "policy": {
                    "allowed_tools": ["read_file", "write_file"],
                    "approval_required_tools": ["delete_file"],
                },
                "distribution": {
                    "maturity": "candidate",
                    "source": "project",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    metadata = convert_skill_yaml_to_metadata(str(spec_path))

    assert metadata is not None
    assert metadata.skill_id == "file-ops"
    assert metadata.skill_type == SkillType.ACTION
    assert metadata.category == SkillCategory.IO
    assert metadata.auto_invocable is False
    assert metadata.user_invocable is True
    assert metadata.planner_invocable is False
    assert metadata.context_mode == "isolated"
    assert metadata.allowed_tools == ["read_file", "write_file"]
    assert metadata.approval_required_tools == ["delete_file"]
    assert metadata.lifecycle_stage == "candidate"
    assert metadata.distribution_source == "project"
    assert metadata.has_spec is True
    assert metadata.spec_path.endswith("skill-spec.yaml")
    assert metadata.has_evals is True
    assert metadata.evals_path.endswith("evals.yml")



def test_auto_detect_prefers_skill_yaml_over_skill_md(tmp_path):
    skill_dir = tmp_path / "hybrid-skill"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "hybrid-skill",
                "description": "Action package description",
                "kind": "action",
                "policy": {"allowed_tools": ["run_pytest"]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        """---
name: hybrid-skill
description: Knowledge description
---

Knowledge body.
""",
        encoding="utf-8",
    )

    metadata = auto_detect_and_convert(str(skill_dir), "hybrid-skill")

    assert metadata is not None
    assert metadata.skill_type == SkillType.ACTION
    assert metadata.description == "Action package description"
    assert metadata.allowed_tools == ["run_pytest"]
