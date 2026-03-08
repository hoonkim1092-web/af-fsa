import importlib


def _load_launcher(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    importlib.reload(core.utils)
    import core.agent_runner
    importlib.reload(core.agent_runner)
    import agent_launcher
    return importlib.reload(agent_launcher)


def test_run_default_skips_build(monkeypatch):
    al = _load_launcher(monkeypatch)
    factory = al.AgentFactory()

    factory.agent_mgr.get_or_create = lambda role_spec: {"name": "t", "role": "r", "skills": []}
    factory.req.analyze = lambda _agent, _task: {"goal": "g", "missing_skills": ["new_skill"], "constraints": []}
    factory._missing_local_skill_files = lambda _agent: []

    called = {"build": 0}
    factory.builder.build_skill = lambda **kwargs: called.__setitem__("build", called["build"] + 1)  # should not run
    factory.runner.run = lambda run_agent, task_input, run_id=None: {"ok": True, "reason": "gemini", "latency_ms": 1}

    res = factory.run(task_input="x", role_spec="General")
    assert res["build_enabled"] is False
    assert res["missing_skills_detected"] == ["new_skill"]
    assert called["build"] == 0


def test_run_with_build_executes_builder(monkeypatch):
    al = _load_launcher(monkeypatch)
    factory = al.AgentFactory()
    import core.skill_procurer as sp

    factory.agent_mgr.get_or_create = lambda role_spec: {"name": "t", "role": "r", "skills": []}
    factory.req.analyze = lambda _agent, _task: {"goal": "g", "missing_skills": ["new_skill"], "constraints": []}
    factory._missing_local_skill_files = lambda _agent: []
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda sid: (None, None))
    factory.research.research = lambda _agent, _reqs, build_targets=None: {
        "evidence_pack": {
            "targets": {
                "new_skill": {"top_candidate": None, "verified": False, "candidates": []}
            }
        }
    }

    called = {"build": 0}

    def _build_skill(**kwargs):
        called["build"] += 1
        return False, None, {"last_test_detail": {"reason": "forced"}}

    factory.builder.build_skill = _build_skill
    factory.runner.run = lambda run_agent, task_input, run_id=None: {"ok": True, "reason": "gemini", "latency_ms": 1}

    res = factory.run(task_input="x", role_spec="General", enable_build=True)
    assert res["build_enabled"] is True
    assert called["build"] == 1
