import importlib
import os


def _load_launcher(monkeypatch, project_root):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_wf")
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    importlib.reload(core.utils)
    import core.agent_runner
    importlib.reload(core.agent_runner)
    import agent_launcher
    return importlib.reload(agent_launcher)


def test_workflow_state_failed_and_stops_next_stage(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch, tmp_path / "proj")

    wf_path = tmp_path / "wf.yaml"
    al.write_yaml(
        str(wf_path),
        {
            "owner_agent": "General",
            "stages": [
                {"id": "PLAN", "name": "Plan", "objective": "plan"},
                {"id": "BUILD", "name": "Build", "objective": "build"},
            ],
        },
    )

    factory = al.AgentFactory()
    calls = []

    def fake_run(task_input: str, role_spec: str = "General"):
        calls.append((task_input, role_spec))
        return {"ok": False, "reason": "forced_fail", "latency_ms": 1, "approval_rejects": 0}

    factory.run = fake_run
    factory.run_workflow("test task", workflow_path=str(wf_path), role_specs=["General"])

    # max_stage_retries 기본 2회 * 첫 stage만 실행되고 중단되어야 한다.
    assert len(calls) == 2

    run_dirs = [d for d in os.listdir(al.RUNS_DIR) if d.startswith("wf_")]
    assert run_dirs
    latest = sorted(run_dirs)[-1]
    with open(os.path.join(al.RUNS_DIR, latest, "state.json"), "r", encoding="utf-8") as f:
        state = al.json.load(f)

    assert state["status"] == "failed"
    assert state["stages"][0]["status"] == "failed"
    assert state["stages"][1]["status"] == "pending"
