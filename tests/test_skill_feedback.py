import pytest
import json
import os

import core.skill_feedback as feedback_mod
import core.utils as utils_mod
from core.skill_eval_harness import EvalPhaseSummary, ShadowEvalSummary, SkillEvalReport
from core.skill_feedback import SkillFeedbackLoop
from core.skill_promotion import SkillPromotionManager



def _read_events(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle.read().splitlines() if line.strip()]



def _make_report(skill_id: str, skill_path: str, *, contract_rate: float = 1.0, hidden_rate: float = 1.0, shadow_delta: float = 0.0, shadow_cases: int = 0) -> SkillEvalReport:
    contract_eval = EvalPhaseSummary(
        name="contract",
        total_cases=1,
        passed=1 if contract_rate >= 1.0 else 0,
        pass_rate=contract_rate,
    )
    hidden_eval = EvalPhaseSummary(
        name="hidden",
        total_cases=1,
        passed=1 if hidden_rate >= 1.0 else 0,
        pass_rate=hidden_rate,
    )
    shadow_eval = ShadowEvalSummary(
        total_cases=shadow_cases,
        wins=1 if shadow_delta > 0 else 0,
        losses=1 if shadow_delta < 0 else 0,
        ties=shadow_cases if shadow_delta == 0 else 0,
        delta=shadow_delta,
    )
    return SkillEvalReport(
        skill_id=skill_id,
        skill_path=skill_path,
        static_gate={"ok": True, "entrypoint": "apply", "error": ""},
        contract_eval=contract_eval,
        hidden_eval=hidden_eval,
        shadow_eval=shadow_eval,
        recommended_stage="candidate",
        report_path=os.path.join(os.path.dirname(skill_path), "skill-eval-report.json"),
    )



def test_skill_feedback_loop_records_selection_build_runtime(tmp_path):
    events_path = tmp_path / "skill-usage.jsonl"
    loop = SkillFeedbackLoop(str(events_path), project_id="demo_project")

    loop.record_selection(
        skill_id="demo_skill",
        decision_mode="shadow_reuse",
        status="selected_for_build",
        run_id="run_demo",
        agent_role="Architect",
        candidate_skill_id="candidate_skill",
        confidence=0.7,
        score=70,
    )
    loop.record_build(
        skill_id="demo_skill",
        ok=True,
        run_id="run_demo",
        agent_role="Architect",
        lifecycle_stage="draft",
        payload={"code_path": "D:/demo/skill.py"},
    )
    loop.record_runtime_result(
        skill_id="demo_skill",
        ok=True,
        run_id="run_demo",
        agent_role="Architect",
        payload={"reason": "codex_cli"},
    )

    events = _read_events(events_path)

    assert [event["event_type"] for event in events] == ["skill_selection", "skill_build", "skill_runtime"]
    assert events[0]["payload"]["decision_mode"] == "shadow_reuse"
    assert events[1]["status"] == "passed"
    assert events[2]["status"] == "succeeded"
    assert events[2]["project_id"] == "demo_project"



def test_skill_feedback_loop_summarizes_historical_score(tmp_path):
    events_path = tmp_path / "skill-usage.jsonl"
    loop = SkillFeedbackLoop(str(events_path), project_id="demo_project")

    loop.record_build(skill_id="demo_skill", ok=True, run_id="run_demo")
    loop.record_runtime_result(skill_id="demo_skill", ok=True, run_id="run_demo")
    loop.record_runtime_result(skill_id="demo_skill", ok=True, run_id="run_demo")
    loop.record_promotion(skill_id="demo_skill", status="changed", from_stage="candidate", to_stage="active")

    summary = loop.summarize_skill("demo_skill")

    assert summary.total_events == 4
    assert summary.build_passed == 1
    assert summary.runtime_succeeded == 2
    assert summary.current_stage == "active"
    assert summary.runtime_success_rate == 1.0
    assert summary.historical_score > 70



def test_skill_promotion_emits_feedback_event(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    feedback_dir = tmp_path / "data"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))
    monkeypatch.setattr(feedback_mod, "DATA_DIR", str(feedback_dir))

    skill_dir = tmp_path / "candidate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "skill.py"
    skill_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    report = _make_report("candidate-skill", str(skill_path), contract_rate=1.0, hidden_rate=1.0, shadow_delta=0.0, shadow_cases=2)
    decision = SkillPromotionManager(project_policies={"quality_gate": {"installable_statuses": ["canary", "active"]}}).apply(
        "candidate-skill",
        report,
        current_stage="candidate",
    )

    events = _read_events(feedback_dir / "skill-usage.jsonl")
    promotion_events = [event for event in events if event["event_type"] == "skill_promotion"]

    assert decision.next_stage == "canary"
    assert promotion_events
    assert promotion_events[0]["status"] == "changed"
    assert promotion_events[0]["payload"]["from_stage"] == "candidate"
    assert promotion_events[0]["payload"]["to_stage"] == "canary"


def test_skill_promotion_prefers_workspace_feedback_log(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "candidate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "skill.py"
    skill_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    report = _make_report("candidate-skill", str(skill_path), contract_rate=1.0, hidden_rate=1.0, shadow_delta=0.0, shadow_cases=2)
    decision = SkillPromotionManager(project_policies={"quality_gate": {"installable_statuses": ["canary", "active"]}}).apply(
        "candidate-skill",
        report,
        current_stage="candidate",
    )

    events = _read_events(workspace / "data" / "skill-usage.jsonl")
    promotion_events = [event for event in events if event["event_type"] == "skill_promotion"]

    assert decision.next_stage == "canary"
    assert promotion_events
    assert promotion_events[0]["project_id"] == "workspace"
    assert promotion_events[0]["payload"]["to_stage"] == "canary"



def test_skill_feedback_loop_batches_multiple_summaries(tmp_path):
    events_path = tmp_path / "skill-usage.jsonl"
    loop = SkillFeedbackLoop(str(events_path), project_id="demo_project")

    loop.record_runtime_result(skill_id="alpha_skill", ok=True, run_id="run_demo")
    loop.record_runtime_result(skill_id="beta_skill", ok=False, run_id="run_demo")

    summaries = loop.summarize_skills(["alpha_skill", "beta_skill", "gamma_skill"])

    assert summaries["alpha_skill"].runtime_succeeded == 1
    assert summaries["beta_skill"].runtime_failed == 1
    assert summaries["gamma_skill"].total_events == 0


def test_skill_promotion_keeps_canary_without_runtime_evidence(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    events_path = tmp_path / "data" / "skill-usage.jsonl"
    loop = SkillFeedbackLoop(str(events_path), project_id="demo_project")

    skill_dir = tmp_path / "canary_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "skill.py"
    skill_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    report = _make_report("canary-skill", str(skill_path), contract_rate=1.0, hidden_rate=1.0, shadow_delta=1.0, shadow_cases=3)
    decision = SkillPromotionManager(
        project_policies={
            "quality_gate": {
                "installable_statuses": ["canary", "active"],
                "active_runtime_min_events": 2,
                "active_runtime_min_success_rate": 1.0,
            }
        }
    ).apply(
        "canary-skill",
        report,
        current_stage="canary",
        feedback_loop=loop,
    )

    assert decision.next_stage == "canary"
    assert decision.reason == "awaiting_runtime_evidence"
    assert decision.evidence["runtime_total"] == 0



def test_skill_promotion_promotes_canary_with_runtime_evidence(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    events_path = tmp_path / "data" / "skill-usage.jsonl"
    loop = SkillFeedbackLoop(str(events_path), project_id="demo_project")
    loop.record_runtime_result(skill_id="canary-skill", ok=True, run_id="run_1")
    loop.record_runtime_result(skill_id="canary-skill", ok=True, run_id="run_2")

    skill_dir = tmp_path / "canary_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "skill.py"
    skill_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    report = _make_report("canary-skill", str(skill_path), contract_rate=1.0, hidden_rate=1.0, shadow_delta=1.0, shadow_cases=3)
    decision = SkillPromotionManager(
        project_policies={
            "quality_gate": {
                "installable_statuses": ["canary", "active"],
                "active_runtime_min_events": 2,
                "active_runtime_min_success_rate": 0.9,
            }
        }
    ).apply(
        "canary-skill",
        report,
        current_stage="canary",
        feedback_loop=loop,
    )

    assert decision.next_stage == "active"
    assert decision.reason == "canary_promoted_with_runtime_evidence"
    assert decision.evidence["runtime_total"] == 2
    assert decision.evidence["runtime_success_rate"] == 1.0



def test_skill_promotion_demotes_active_skill_on_runtime_regression(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    events_path = tmp_path / "data" / "skill-usage.jsonl"
    loop = SkillFeedbackLoop(str(events_path), project_id="demo_project")
    loop.record_runtime_result(skill_id="active-skill", ok=False, run_id="run_1")
    loop.record_runtime_result(skill_id="active-skill", ok=False, run_id="run_2")
    loop.record_runtime_result(skill_id="active-skill", ok=True, run_id="run_3")

    skill_dir = tmp_path / "active_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "skill.py"
    skill_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    report = _make_report("active-skill", str(skill_path), contract_rate=1.0, hidden_rate=1.0, shadow_delta=0.0, shadow_cases=2)
    decision = SkillPromotionManager(
        project_policies={
            "quality_gate": {
                "installable_statuses": ["active"],
                "demote_runtime_min_events": 3,
                "demote_runtime_below_success_rate": 0.5,
            }
        }
    ).apply(
        "active-skill",
        report,
        current_stage="active",
        feedback_loop=loop,
    )

    assert decision.next_stage == "candidate"
    assert decision.reason == "active_runtime_regressed"
    assert decision.evidence["runtime_total"] == 3
    assert decision.evidence["runtime_regressed"] is True



def test_skill_promotion_rejects_skill_id_mismatch(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    skill_dir = tmp_path / "candidate_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "skill.py"
    skill_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    report = _make_report("candidate-skill", str(skill_path), contract_rate=1.0, hidden_rate=1.0, shadow_delta=0.0, shadow_cases=2)

    with pytest.raises(ValueError, match="skill_id_mismatch"):
        SkillPromotionManager(project_policies={"quality_gate": {"installable_statuses": ["canary", "active"]}}).apply(
            "different-skill",
            report,
            current_stage="candidate",
        )
