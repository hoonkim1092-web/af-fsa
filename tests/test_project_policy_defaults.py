import importlib

import yaml


def _load_launcher(monkeypatch, project_root):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_policy_defaults")
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    importlib.reload(core.utils)
    import core.agent_runner
    importlib.reload(core.agent_runner)
    import agent_launcher
    return importlib.reload(agent_launcher)


def test_project_scaffold_policies_include_external_skill_defaults(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch, tmp_path / "proj")
    with open(al.POLICIES_PATH, "r", encoding="utf-8") as f:
        policies = yaml.safe_load(f) or {}

    assert policies["external_skill_source_priority"] == ["claude_repo", "codex_repo", "registry", "external_cache"]
    assert policies["external_skill_sources"] == []
