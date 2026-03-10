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
    def resolve_and_install_external_detailed(self, needs, reqs=None, evidence_pack=None):
        del reqs, evidence_pack
        need_id = needs[0]
        return {
            "installed": {},
            "results": {
                need_id: {
                    "need_id": need_id,
                    "installed_skill_id": "",
                    "installed_from": "",
                    "attempts": [
                        {"source_id": "claude_repo", "status": "miss", "reason": "no_candidate"},
                        {"source_id": "codex_repo", "status": "install_failed", "reason": "syntax_error"},
                    ],
                }
            },
        }

    def is_installable(self, _skill_id):
        return True


class _AgentMgr:
    def install_skills(self, _role_spec, _skill_ids, workspace=None):
        raise AssertionError("install_skills should not run when external install fails")


def test_procure_multiple_passes_external_attempts_to_builder(monkeypatch):
    sp = _load_skill_procurer()
    build_calls = []
    logs = []
    monkeypatch.setattr(sp, "log", lambda step, msg: logs.append((step, msg)))
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda _sid: (None, None))

    class _Builder:
        def build_skill(self, **kwargs):
            build_calls.append(kwargs)
            return False, None, {"last_test_detail": {"reason": "no_api_key"}}

    orchestrator = sp.SkillOrchestrator(_Registry(), _Research(), _Builder(), _AgentMgr())
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["new_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_ext_builder",
    )

    assert installed == []
    assert len(build_calls) == 1
    target = build_calls[0]["evidence_pack"]["targets"]["new_skill"]
    assert target["external_attempts"][0]["source_id"] == "claude_repo"
    assert target["external_attempts"][1]["source_id"] == "codex_repo"
    assert any(step == "EXTERNAL" and "new_skill" in msg for step, msg in logs)


def test_procure_multiple_denied_external_install_can_still_build(monkeypatch):
    sp = _load_skill_procurer()
    build_calls = []
    approval_calls = []
    logs = []
    monkeypatch.setattr(sp, "log", lambda step, msg: logs.append((step, msg)))
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda _sid: (None, None))

    class _RegistryNoExternal:
        def resolve_and_install_external_detailed(self, needs, reqs=None, evidence_pack=None):
            raise AssertionError("external install should not run when install approval is denied")

        def is_installable(self, _skill_id):
            return True

    class _Builder:
        def build_skill(self, **kwargs):
            build_calls.append(kwargs)
            return False, None, {"last_test_detail": {"reason": "no_api_key"}}

    def _approval_gate(role, skill_ids, action, auto_approve):
        approval_calls.append((role, tuple(skill_ids), action, auto_approve))
        return action == "build"

    orchestrator = sp.SkillOrchestrator(_RegistryNoExternal(), _Research(), _Builder(), _AgentMgr())
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["new_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_external_denied",
        approval_gate=_approval_gate,
    )

    assert installed == []
    assert len(build_calls) == 1
    assert approval_calls == [
        ("General", ("new_skill",), "install", False),
        ("General", ("new_skill",), "build", False),
    ]
    target = build_calls[0]["evidence_pack"]["targets"]["new_skill"]
    assert target["external_attempts"][0]["source_id"] == "approval_gate"
    assert target["external_attempts"][0]["status"] == "approval_rejected"
    assert any(step == "EXTERNAL" and "install_denied" in msg for step, msg in logs)


def test_procure_multiple_legacy_external_miss_stays_as_miss(monkeypatch):
    sp = _load_skill_procurer()
    build_calls = []
    logs = []
    monkeypatch.setattr(sp, "log", lambda step, msg: logs.append((step, msg)))
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda _sid: (None, None))

    class _LegacyRegistry:
        def resolve_and_install_external(self, needs, reqs=None, evidence_pack=None):
            del needs, reqs, evidence_pack
            return {}

        def is_installable(self, _skill_id):
            return True

    class _Builder:
        def build_skill(self, **kwargs):
            build_calls.append(kwargs)
            return False, None, {"last_test_detail": {"reason": "no_api_key"}}

    orchestrator = sp.SkillOrchestrator(_LegacyRegistry(), _Research(), _Builder(), _AgentMgr())
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["legacy_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_legacy_external",
    )

    assert installed == []
    assert len(build_calls) == 1
    target = build_calls[0]["evidence_pack"]["targets"]["legacy_skill"]
    assert target["external_attempts"][0]["source_id"] == "external"
    assert target["external_attempts"][0]["status"] == "miss"
    assert target["external_attempts"][0]["reason"] == "not_installed"
    assert not any(step == "EXTERNAL" and "Installed external skill" in msg for step, msg in logs)
