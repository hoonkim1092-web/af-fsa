import importlib


def _load_skill_procurer():
    import core.skill_procurer

    return importlib.reload(core.skill_procurer)


class _Research:
    def research(self, _agent, _reqs, build_targets=None):
        targets = {}
        for name in build_targets or []:
            targets[name] = {
                "top_candidate": None,
                "verified": False,
                "top_score": 0,
                "candidates": [],
            }
        return {"evidence_pack": {"targets": targets}}


class _Registry:
    def is_installable(self, _skill_id):
        return True


class _AgentMgr:
    def install_skills(self, _role_spec, _skill_ids, workspace=None):
        raise AssertionError("install_skills should not run on failed build")


def test_procure_multiple_logs_no_key_builder_guidance(monkeypatch):
    sp = _load_skill_procurer()
    logs = []
    monkeypatch.setattr(sp, "log", lambda step, msg: logs.append((step, msg)))
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda _sid: (None, None))

    class _Builder:
        def build_skill(self, **kwargs):
            return False, None, {"last_test_detail": {"reason": "no_api_key"}}

    orchestrator = sp.SkillOrchestrator(_Registry(), _Research(), _Builder(), _AgentMgr())
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["new_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_no_key",
    )

    assert installed == []
    assert any(
        step == "BUILD" and "AGENT_CHAT_PROVIDER" in msg and "new_skill" in msg
        for step, msg in logs
    )


def test_procure_multiple_logs_cli_builder_failure_detail(monkeypatch):
    sp = _load_skill_procurer()
    logs = []
    monkeypatch.setattr(sp, "log", lambda step, msg: logs.append((step, msg)))
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda _sid: (None, None))

    class _Builder:
        def build_skill(self, **kwargs):
            return False, None, {"last_test_detail": {"reason": "gemini_cli", "detail": "timeout"}}

    orchestrator = sp.SkillOrchestrator(_Registry(), _Research(), _Builder(), _AgentMgr())
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["new_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_cli_fail",
    )

    assert installed == []
    assert any(
        step == "BUILD" and "CLI build failed" in msg and "gemini_cli" in msg and "timeout" in msg
        for step, msg in logs
    )
