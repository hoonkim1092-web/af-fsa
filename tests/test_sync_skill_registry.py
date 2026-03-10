import importlib.util
from pathlib import Path


def _load_sync_skill_registry():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "sync_skill_registry.py"
    spec = importlib.util.spec_from_file_location("sync_skill_registry_test", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sync_normalize_install_candidates_preserves_legacy_list_form():
    mod = _load_sync_skill_registry()

    normalized = mod.normalize_install_candidates(
        [
            {
                "id": "Issue Tracker",
                "path": "skills/_external_cache/claude/repo_alpha/skills/issue_tracker/skill.py",
                "source": "Claude",
            }
        ]
    )

    assert "claude_repo_issue_tracker" in normalized
    assert normalized["claude_repo_issue_tracker"]["source_id"] == "claude_repo"


def test_sync_normalize_install_candidates_canonicalizes_legacy_external_keys():
    mod = _load_sync_skill_registry()

    normalized = mod.normalize_install_candidates(
        {
            "claude_issue_tracker": {
                "id": "Issue Tracker",
                "path": "skills/_external_cache/claude/repo_alpha/skills/issue_tracker/skill.py",
                "source": "Claude",
            }
        }
    )

    assert "claude_repo_issue_tracker" in normalized
    assert "claude_issue_tracker" not in normalized
    assert normalized["claude_repo_issue_tracker"]["source_id"] == "claude_repo"
