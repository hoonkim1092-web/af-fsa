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
    import core.skill_procurer
    importlib.reload(core.skill_procurer)
    import agent_launcher
    return importlib.reload(agent_launcher)


def test_procure_multiple_prefers_exact_skill_reuse(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch)
    factory = al.AgentFactory()

    import core.skill_procurer as sp

    resolved = tmp_path / "issue_tracker.py"
    resolved.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    monkeypatch.setattr(
        sp,
        "resolve_skill_paths",
        lambda sid: (str(resolved), None) if sid == "issue_tracker" else (None, None),
    )

    called = {"research": 0, "build": 0, "install": []}

    class _Research:
        def research(self, *args, **kwargs):
            called["research"] += 1
            return {"evidence_pack": {"targets": {}}}

    factory.procurer.research = _Research()
    factory.procurer.builder.build_skill = lambda **kwargs: (
        called.__setitem__("build", called["build"] + 1),
        False,
        None,
        {},
    )[1:]
    factory.procurer.registry.ensure_lock_for_existing_skill = lambda skill_id: None
    factory.procurer.registry.is_installable = lambda skill_id: True
    factory.procurer.agent_mgr.install_skills = lambda role_spec, skill_ids, workspace=None: (
        called["install"].append(list(skill_ids)) or list(skill_ids)
    )

    res = factory.procurer.procure_multiple(
        agent={"role": "General"},
        skill_names=["issue_tracker"],
        reqs={"goal": "track issues", "constraints": [], "missing_skills": ["issue_tracker"]},
        run_id="r1",
        workspace=str(tmp_path),
    )

    assert res == ["issue_tracker"]
    assert called["research"] == 0
    assert called["build"] == 0
    assert called["install"] == [["issue_tracker"]]
