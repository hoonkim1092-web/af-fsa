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


def test_factory_reuses_verified_skill(monkeypatch):
    al = _load_launcher(monkeypatch)
    factory = al.AgentFactory()

    agent = {"name": "t", "role": "r", "skills": []}
    factory.agent_mgr.get_or_create = lambda role_spec: agent
    factory.req.analyze = lambda _agent, _task: {"goal": "g", "missing_skills": ["needs_issue"], "constraints": []}
    factory._missing_local_skill_files = lambda _agent: []
    factory.research.research = lambda _agent, _reqs, build_targets=None: {
        "evidence_pack": {
            "targets": {
                "needs_issue": {
                    "top_candidate": "issue_tracker",
                    "verified": True,
                    "top_score": 90,
                }
            }
        }
    }

    install_calls = []
    build_calls = []
    runner_calls = []

    factory.agent_mgr.install_skills = lambda _role, skill_ids: install_calls.append(list(skill_ids)) or list(skill_ids)
    factory.builder.build_skill = lambda **kwargs: build_calls.append(kwargs) or (False, None, {"last_test_detail": {"reason": "should_not_build"}})
    factory.runner.run = lambda run_agent, task_input: runner_calls.append((run_agent, task_input))

    factory.run(task_input="track issues", role_spec="General", enable_build=True)

    assert install_calls == [["issue_tracker"]]
    assert build_calls == []
    assert len(runner_calls) == 1


def test_factory_builds_unresolved_skill_and_registers(monkeypatch):
    al = _load_launcher(monkeypatch)
    factory = al.AgentFactory()

    agent = {"name": "t", "role": "r", "skills": []}
    factory.agent_mgr.get_or_create = lambda role_spec: agent
    factory.req.analyze = lambda _agent, _task: {"goal": "g", "missing_skills": ["new_skill"], "constraints": []}
    factory._missing_local_skill_files = lambda _agent: []
    factory.research.research = lambda _agent, _reqs, build_targets=None: {
        "evidence_pack": {
            "targets": {
                "new_skill": {
                    "top_candidate": None,
                    "verified": False,
                    "top_score": 0,
                    "candidates": [],
                }
            }
        }
    }

    install_calls = []
    register_calls = []
    workflow_calls = []
    runner_calls = []

    factory.agent_mgr.install_skills = lambda _role, skill_ids: install_calls.append(list(skill_ids)) or list(skill_ids)
    factory.builder.build_skill = lambda **kwargs: (
        True,
        "dummy",
        {
            "id": "new_skill",
            "name": "new_skill",
            "status": "active",
            "version": "0.1.0",
            "capabilities": ["new_skill"],
            "last_test_ok": True,
        },
    )
    factory.registry.register_built = lambda meta, skill_dir: register_calls.append((meta["id"], skill_dir))
    factory.registry.workflow_apply = lambda metas: workflow_calls.append([m["id"] for m in metas])
    factory.registry.is_installable = lambda _sid: True
    factory.runner.run = lambda run_agent, task_input: runner_calls.append((run_agent, task_input))

    factory.run(task_input="do something new", role_spec="General", enable_build=True)

    assert register_calls and register_calls[0][0] == "new_skill"
    assert workflow_calls == [["new_skill"]]
    assert install_calls == [["new_skill"]]
    assert len(runner_calls) == 1


def test_factory_run_workflow_sequences_roles(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch)
    factory = al.AgentFactory()
    wf = tmp_path / "wf.yaml"
    wf.write_text(
        "owner_agent: Lilith\n"
        "stages:\n"
        "  - id: D0\n"
        "    name: Decision Lock\n"
        "    objective: lock decisions\n"
        "  - id: D1\n"
        "    name: Risks First\n"
        "    objective: handle risks\n",
        encoding="utf-8",
    )

    calls = []
    factory.run = lambda task_input, role_spec="General": (calls.append((role_spec, task_input)), {"ok": True})[1]

    factory.run_workflow(task_input="deliver demo", workflow_path=str(wf), role_specs=["Lilith", "Himari"])

    assert len(calls) == 4
    assert calls[0][0] == "Lilith"
    assert calls[1][0] == "Himari"
