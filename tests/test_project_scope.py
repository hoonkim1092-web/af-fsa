import importlib
import os


def _load_launcher(monkeypatch, project_root):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_test")
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    importlib.reload(core.utils)
    import core.agent_runner
    importlib.reload(core.agent_runner)
    import agent_launcher
    return importlib.reload(agent_launcher)


def test_project_scaffold_files_created(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch, tmp_path / "proj")
    assert os.path.exists(al.POLICIES_PATH)
    assert os.path.exists(al.CONTEXT_SCHEMA_PATH)
    assert os.path.exists(al.SKILL_LOCK_PATH)
    assert os.path.exists(al.DASHBOARD_PATH)
    assert al.DASHBOARD_PATH == os.path.join(al.PROJECT_ROOT, "dashboard.json")


def test_register_built_updates_skill_lock(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch, tmp_path / "proj2")
    reg = al.RegistryManager()
    skill_dir = tmp_path / "skills" / "abc"
    skill_dir.mkdir(parents=True, exist_ok=True)
    reg.register_built(
        {
            "id": "abc",
            "name": "abc",
            "status": "active",
            "version": "0.1.0",
            "capabilities": ["abc"],
            "last_test_ok": True,
        },
        str(skill_dir),
    )
    lock = al.read_skill_lock()
    assert "abc" in lock["skills"]
    assert lock["skills"]["abc"]["status"] in ("active", "canary", "candidate")


def test_context_schema_validation_blocks_run(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch, tmp_path / "proj3")
    al.write_yaml(al.CONTEXT_SCHEMA_PATH, {"required_keys": ["agent", "data_dir", "artifacts_dir", "missing_key"]})
    runner = al.AgentRunner(al.ModelRouter())
    res = runner.run({"name": "a", "role": "r", "skills": []}, "do task")
    assert res["ok"] is False
    assert str(res["reason"]).startswith("context_schema:")
