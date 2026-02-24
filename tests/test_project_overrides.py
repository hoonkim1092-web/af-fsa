import importlib
import os


def _load_launcher(monkeypatch, project_root):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_override")
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    importlib.reload(core.utils)
    import core.agent_runner
    importlib.reload(core.agent_runner)
    import agent_launcher
    return importlib.reload(agent_launcher)


def test_project_skill_override_priority(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch, tmp_path / "proj")
    sid = "ovr"
    proj_skill_dir = tmp_path / "proj" / "skills" / sid
    proj_skill_dir.mkdir(parents=True, exist_ok=True)
    (proj_skill_dir / "skill.py").write_text(
        "def propose(ctx): return {'ok': True}\n"
        "def apply(ctx): return {'source': 'project'}\n"
        "def test(ctx): return {'ok': True}\n",
        encoding="utf-8",
    )

    py_path, _ = al.resolve_skill_paths(sid)
    assert py_path is not None
    assert os.path.normpath(str(py_path)).endswith(os.path.normpath(f"skills\\{sid}\\skill.py"))
    assert os.path.normpath(str(py_path)).startswith(os.path.normpath(str(tmp_path / "proj")))


def test_agent_override_merge(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch, tmp_path / "proj2")
    al.write_yaml(
        al.PROJECT_SETTINGS_PATH,
        {
            "agent_overrides": {
                "backend_architect": {
                    "runtime_rules": {"default_deny": True},
                    "skills_add": ["core_memory"],
                }
            }
        },
    )
    merged = al.apply_agent_overrides({"skills": ["issue_tracker"]}, "Backend Architect")
    assert merged.get("runtime_rules", {}).get("default_deny") is True
    assert "core_memory" in merged.get("skills", [])
