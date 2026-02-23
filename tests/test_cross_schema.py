import os
import yaml
import pytest
import core.skill_registry as sr
from agent_launcher import AgentRunner

def test_registry_schema_cross_validation(tmp_path):
    """
    Validates that core/skill_registry.py schema writes data
    that agent_launcher.py expects (dict of dicts, no lists).
    """
    test_reg_file = tmp_path / "registry.yaml"
    sr.REGISTRY_FILE = str(test_reg_file)
    sr.DOCS_FILE = str(tmp_path / "skill_docs.md")
    
    # 1. Register a skill using the factory/core logic
    sr.register_skill(
        skill_name="test_schema_skill",
        purpose="Testing dictionary mappings",
        path="d:/fake/path.py",
        dependencies=["sys"]
    )
    
    # 2. Verify schema structure from the file directly
    with open(test_reg_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    assert "skills" in data
    assert isinstance(data["skills"], dict)  # CRITICAL: MUST BE A DICT
    
    # Check that our skill was added
    keys = list(data["skills"].keys())
    assert len(keys) == 1
    skill_id = keys[0]

    skill_entry = data["skills"][skill_id]
    assert skill_entry["skill_name"] == "test_schema_skill"
    assert skill_entry["skill_id"] == skill_id
    
    # 3. Emulate agent_launcher.py's expected read behavior
    # agent_launcher expects reg["skills"][id] = meta
    launcher_reg = data.get("skills", {})
    assert skill_id in launcher_reg
    assert launcher_reg[skill_id]["path"] == "d:/fake/path.py"
