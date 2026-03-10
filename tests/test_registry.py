import os
import yaml
import pytest
from core.skill_registry import _load_registry, _save_registry

# Since REGISTRY_FILE is a module-level constant we'll patch it in tests
import core.skill_registry as sr

def test_registry_yaml_format(tmp_path):
    # Patch the registry file location to a temp dir
    test_reg_file = tmp_path / "registry.yaml"
    sr.REGISTRY_FILE = str(test_reg_file)
    sr.DOCS_FILE = str(tmp_path / "skill_docs.md")
    
    # 1. Clean load should be empty
    data = _load_registry()
    assert data == {"skills": {}, "install_candidates": {}}
    
    # 2. Add sample data and save
    sample_data = {
        "skills": {
            "123": {
                "skill_id": "123",
                "skill_name": "test_skill",
                "purpose": "just a test",
                "path": "some/path.py",
                "dependencies": []
            }
        }
    }
    _save_registry(sample_data)
    
    # 3. Verify it was written in YAML format (not JSON)
    with open(test_reg_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "'123'" in content
        assert "purpose: just a test" in content
        assert "{" not in content.split("\n")[0]  # Verify it doesn't look like JSON
        
    # 4. Load it back using the standard function
    loaded_data = _load_registry()
    assert len(loaded_data["skills"]) == 1
    assert loaded_data["skills"]["123"]["skill_name"] == "test_skill"


def test_registry_normalizes_list_form_install_candidates_source_id(tmp_path):
    test_reg_file = tmp_path / "registry.yaml"
    sr.REGISTRY_FILE = str(test_reg_file)
    sr.DOCS_FILE = str(tmp_path / "skill_docs.md")

    test_reg_file.write_text(
        yaml.safe_dump(
            {
                "skills": {},
                "install_candidates": [
                    {
                        "id": "Issue Tracker",
                        "path": "skills/_external_cache/claude/repo_alpha/issue_tracker.py",
                        "source": "Claude",
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    loaded = _load_registry()
    assert "issue_tracker" in loaded["install_candidates"]
    assert loaded["install_candidates"]["issue_tracker"]["source_id"] == "claude_repo"
    assert "source" not in loaded["install_candidates"]["issue_tracker"]
