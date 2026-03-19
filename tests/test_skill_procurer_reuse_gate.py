import importlib



def _load_skill_procurer():
    import core.skill_procurer

    return importlib.reload(core.skill_procurer)


class _AgentMgr:
    def __init__(self):
        self.calls = []

    def install_skills(self, role_spec, skill_ids, workspace=None):
        self.calls.append((role_spec, list(skill_ids), workspace))



def test_procure_multiple_reuses_high_confidence_candidate(monkeypatch, tmp_path):
    sp = _load_skill_procurer()
    candidate_path = tmp_path / "candidate_skill.py"
    candidate_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda sid: (str(candidate_path), None) if sid == "candidate_skill" else (None, None))

    class _Research:
        def research(self, _agent, _reqs, build_targets=None):
            del build_targets
            return {
                "evidence_pack": {
                    "targets": {
                        "new_skill": {
                            "top_candidate": "candidate_skill",
                            "verified": True,
                            "top_score": 92,
                            "candidates": [],
                        }
                    }
                }
            }

    class _Registry:
        def __init__(self):
            self.lock_calls = []

        def ensure_lock_for_existing_skill(self, skill_id):
            self.lock_calls.append(skill_id)

        def is_installable(self, _skill_id):
            return True

    class _Builder:
        def build_skill(self, **kwargs):
            raise AssertionError("builder should not run for high-confidence reuse")

    agent_mgr = _AgentMgr()
    registry = _Registry()
    orchestrator = sp.SkillOrchestrator(registry, _Research(), _Builder(), agent_mgr)
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["new_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_reuse_high",
    )

    assert installed == ["candidate_skill"]
    assert registry.lock_calls == ["candidate_skill"]
    assert agent_mgr.calls == [("General", ["candidate_skill"], None)]



def test_procure_multiple_medium_confidence_candidate_prefers_adaptation_build(monkeypatch):
    sp = _load_skill_procurer()
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda sid: (f"/tmp/{sid}.py", None) if sid == "candidate_skill" else (None, None))

    class _Research:
        def research(self, _agent, _reqs, build_targets=None):
            del build_targets
            return {
                "evidence_pack": {
                    "targets": {
                        "new_skill": {
                            "top_candidate": "candidate_skill",
                            "verified": True,
                            "top_score": 70,
                            "candidates": [],
                        }
                    }
                }
            }

    class _Registry:
        def __init__(self):
            self.registered = []
            self.workflow_calls = []

        def resolve_and_install_external_detailed(self, needs, reqs=None, evidence_pack=None):
            raise AssertionError("external install should be skipped for shadow_reuse adaptation")

        def register_built(self, meta, skill_dir):
            self.registered.append((meta["id"], skill_dir))

        def workflow_apply(self, metas):
            self.workflow_calls.append([item["id"] for item in metas])

        def is_installable(self, _skill_id):
            return True

    class _Builder:
        def __init__(self):
            self.calls = []

        def build_skill(self, **kwargs):
            self.calls.append(kwargs)
            return True, "/tmp/new_skill/skill.py", {"id": "new_skill", "status": "draft"}

    agent_mgr = _AgentMgr()
    builder = _Builder()
    registry = _Registry()
    orchestrator = sp.SkillOrchestrator(registry, _Research(), builder, agent_mgr)
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["new_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_reuse_medium",
    )

    assert installed == ["new_skill"]
    assert len(builder.calls) == 1
    target = builder.calls[0]["evidence_pack"]["targets"]["new_skill"]
    assert target["reuse_decision"]["mode"] == "shadow_reuse"
    assert target["reuse_decision"]["candidate_skill_id"] == "candidate_skill"
    assert registry.registered == [("new_skill", "/tmp/new_skill")]
    assert agent_mgr.calls == [("General", ["new_skill"], None)]

import json



def test_procure_multiple_writes_feedback_events_for_shadow_reuse(monkeypatch, tmp_path):
    sp = _load_skill_procurer()
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda sid: (str(tmp_path / "candidate_skill.py"), None) if sid == "candidate_skill" else (None, None))
    (tmp_path / "candidate_skill.py").write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    class _Research:
        def research(self, _agent, _reqs, build_targets=None):
            del build_targets
            return {
                "evidence_pack": {
                    "targets": {
                        "new_skill": {
                            "top_candidate": "candidate_skill",
                            "verified": True,
                            "top_score": 70,
                            "candidates": [],
                        }
                    }
                }
            }

    class _Registry:
        def __init__(self):
            self.registered = []

        def register_built(self, meta, skill_dir):
            self.registered.append((meta["id"], skill_dir))

        def workflow_apply(self, metas):
            self.last_workflow = [item["id"] for item in metas]

        def is_installable(self, _skill_id):
            return True

    class _Builder:
        def build_skill(self, **kwargs):
            del kwargs
            skill_dir = tmp_path / "new_skill"
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path = skill_dir / "skill.py"
            skill_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
            return True, str(skill_path), {"id": "new_skill", "status": "draft", "lifecycle_stage": "draft"}

    project_root = tmp_path / "proj"
    project_root.mkdir(parents=True, exist_ok=True)

    orchestrator = sp.SkillOrchestrator(_Registry(), _Research(), _Builder(), _AgentMgr())
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["new_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_feedback_shadow",
        workspace=str(project_root),
    )

    events_path = project_root / "data" / "skill-usage.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert installed == ["new_skill"]
    assert [event["event_type"] for event in events] == ["skill_selection", "skill_promotion", "skill_build"]
    assert events[0]["payload"]["decision_mode"] == "shadow_reuse"
    assert events[1]["payload"]["to_stage"] == "candidate"
    assert events[2]["status"] == "passed"

import types


def test_procure_multiple_promotes_built_skill_before_install(monkeypatch, tmp_path):
    sp = _load_skill_procurer()
    candidate_path = tmp_path / "candidate_skill.py"
    candidate_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    monkeypatch.setattr(sp, "resolve_skill_paths", lambda sid: (str(candidate_path), None) if sid == "candidate_skill" else (None, None))

    class _Research:
        def research(self, _agent, _reqs, build_targets=None):
            del build_targets
            return {
                "evidence_pack": {
                    "targets": {
                        "new_skill": {
                            "top_candidate": "candidate_skill",
                            "verified": True,
                            "top_score": 70,
                            "candidates": [],
                        }
                    }
                }
            }

    class _Registry:
        def __init__(self):
            self.registered = []
            self.last_status = ""

        def register_built(self, meta, skill_dir):
            stage = meta.get("status") or meta.get("lifecycle_stage") or ""
            self.registered.append((meta["id"], skill_dir, stage))
            self.last_status = stage

        def workflow_apply(self, metas):
            self.last_workflow = [item["id"] for item in metas]

        def is_installable(self, _skill_id):
            return self.last_status in {"canary", "active"}

    class _Builder:
        def build_skill(self, **kwargs):
            del kwargs
            skill_dir = tmp_path / "new_skill"
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path = skill_dir / "skill.py"
            skill_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
            evals_path = skill_dir / "evals.yml"
            evals_path.write_text("contract: []\nhidden: []\n", encoding="utf-8")
            return True, str(skill_path), {
                "id": "new_skill",
                "status": "draft",
                "lifecycle_stage": "draft",
                "evals_path": str(evals_path),
            }

    class _FakeHarness:
        def evaluate(self, skill_path, *, evals_path=None, baseline_skill_path=None, report_path=None, feedback_path=None, runs_dir=None):
            del baseline_skill_path, report_path, feedback_path, runs_dir
            return types.SimpleNamespace(
                skill_id="new_skill",
                skill_path=skill_path,
                evals_path=evals_path or "",
                report_path=str(tmp_path / "new_skill" / "skill-eval-report.json"),
                static_gate={"ok": True},
                contract_eval=types.SimpleNamespace(total_cases=1, pass_rate=1.0),
                hidden_eval=types.SimpleNamespace(total_cases=1, pass_rate=1.0),
                shadow_eval=types.SimpleNamespace(total_cases=1, delta=0.0),
                recommended_stage="canary",
            )

    class _FakePromotionManager:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def apply(self, skill_id, report, *, current_stage=None, promotion_path=None, feedback_loop=None):
            del skill_id, report, promotion_path, feedback_loop
            assert current_stage in {"draft", "candidate"}
            return types.SimpleNamespace(
                current_stage=current_stage or "candidate",
                next_stage="canary",
                changed=True,
                installable=True,
                reason="shadow_eval_ready",
                evidence={},
                promotion_path=str(tmp_path / "new_skill" / "skill-promotion.json"),
            )

    monkeypatch.setattr(sp, "SkillEvalHarness", _FakeHarness, raising=False)
    monkeypatch.setattr(sp, "SkillPromotionManager", _FakePromotionManager, raising=False)

    agent_mgr = _AgentMgr()
    registry = _Registry()
    orchestrator = sp.SkillOrchestrator(registry, _Research(), _Builder(), agent_mgr)
    installed = orchestrator.procure_multiple(
        agent={"role": "General"},
        skill_names=["new_skill"],
        reqs={"goal": "g", "constraints": []},
        run_id="run_reuse_promote",
    )

    assert installed == ["new_skill"]
    assert registry.registered == [("new_skill", str(tmp_path / "new_skill"), "canary")]
    assert agent_mgr.calls == [("General", ["new_skill"], None)]

