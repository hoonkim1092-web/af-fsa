import json
import os

import yaml

import core.utils as utils_mod
from core.skill_eval_harness import (
    EvalPhaseSummary,
    ShadowEvalSummary,
    SkillEvalHarness,
    SkillEvalReport,
    _infer_workspace_root,
)
from core.skill_promotion import SkillPromotionManager


def _write_skill(tmp_path, name: str, body: str) -> str:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "skill.py"
    skill_path.write_text(body, encoding="utf-8")
    return str(skill_path)


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


def test_skill_eval_harness_runs_contract_hidden_shadow_and_writes_report(tmp_path):
    candidate_path = _write_skill(
        tmp_path,
        "candidate_skill",
        """
def apply(ctx):
    mode = ctx.get('mode')
    return {'ok': mode in {'good', 'better'}, 'mode': mode}
""",
    )
    baseline_path = _write_skill(
        tmp_path,
        "baseline_skill",
        """
def apply(ctx):
    mode = ctx.get('mode')
    return {'ok': mode == 'good', 'mode': mode}
""",
    )
    evals_path = tmp_path / "candidate_skill" / "evals.yml"
    evals_path.write_text(
        yaml.safe_dump(
            {
                "contract": [{"name": "public-good", "ctx": {"mode": "good"}, "expect": {"mode": "good"}}],
                "hidden": [{"name": "hidden-better", "ctx": {"mode": "better"}, "expect_ok": True}],
                "shadow": {
                    "baseline_skill_path": baseline_path,
                    "cases": [{"name": "shadow-better", "ctx": {"mode": "better"}, "expect_ok": True}],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    harness = SkillEvalHarness()
    report = harness.evaluate(candidate_path, evals_path=str(evals_path))

    assert report.static_gate["ok"] is True
    assert report.contract_eval.passed == 1
    assert report.hidden_eval.passed == 1
    assert report.shadow_eval.wins == 1
    assert report.shadow_eval.losses == 0
    assert report.recommended_stage == "canary"
    assert os.path.exists(report.report_path)


def test_skill_promotion_moves_draft_only_to_candidate(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    skill_path = _write_skill(tmp_path, "draft_skill", "def apply(ctx):\n    return {'ok': True}\n")
    report = _make_report("draft-skill", skill_path, contract_rate=1.0, hidden_rate=1.0, shadow_delta=1.0, shadow_cases=3)

    manager = SkillPromotionManager(project_policies={"quality_gate": {"installable_statuses": ["active"]}})
    decision = manager.apply("draft-skill", report, current_stage="draft")

    assert decision.next_stage == "candidate"
    assert decision.installable is False
    lock = utils_mod.read_skill_lock()
    assert lock["skills"]["draft_skill"]["status"] == "candidate"


def test_skill_promotion_moves_candidate_to_canary_when_shadow_nonnegative(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    skill_path = _write_skill(tmp_path, "candidate_skill", "def apply(ctx):\n    return {'ok': True}\n")
    report = _make_report("candidate-skill", skill_path, contract_rate=1.0, hidden_rate=1.0, shadow_delta=0.0, shadow_cases=2)

    manager = SkillPromotionManager(project_policies={"quality_gate": {"installable_statuses": ["active", "canary"]}})
    decision = manager.apply("candidate-skill", report, current_stage="candidate")

    assert decision.next_stage == "canary"
    assert decision.installable is True


def test_skill_promotion_demotes_active_skill_on_negative_shadow(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    skill_path = _write_skill(tmp_path, "active_skill", "def apply(ctx):\n    return {'ok': True}\n")
    report = _make_report("active-skill", skill_path, contract_rate=1.0, hidden_rate=1.0, shadow_delta=-1.0, shadow_cases=2)

    manager = SkillPromotionManager(project_policies={"quality_gate": {"installable_statuses": ["active"]}})
    decision = manager.apply("active-skill", report, current_stage="active")

    assert decision.next_stage == "candidate"
    assert decision.installable is False


def test_skill_promotion_demotes_active_skill_on_partial_external_eval_failure(tmp_path, monkeypatch):
    lock_path = tmp_path / "skill-lock.yaml"
    monkeypatch.setattr(utils_mod, "SKILL_LOCK_PATH", str(lock_path))

    skill_path = _write_skill(tmp_path, "active_regressed_skill", "def apply(ctx):\n    return {'ok': True}\n")
    report = _make_report(
        "active-regressed-skill",
        skill_path,
        contract_rate=1.0,
        hidden_rate=0.0,
        shadow_delta=0.0,
        shadow_cases=2,
    )

    manager = SkillPromotionManager(project_policies={"quality_gate": {"installable_statuses": ["active"]}})
    decision = manager.apply("active-regressed-skill", report, current_stage="active")

    assert decision.next_stage == "candidate"
    assert decision.reason == "active_regressed_external_eval"




def test_skill_eval_harness_replays_runtime_traces_for_shadow_eval(tmp_path):
    workspace = tmp_path / "workspace"
    candidate_dir = workspace / "skills" / "candidate_skill"
    baseline_dir = workspace / "skills" / "baseline_skill"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir.mkdir(parents=True, exist_ok=True)

    candidate_path = candidate_dir / "skill.py"
    candidate_path.write_text(
        "def apply(ctx):\n"
        "    task = str(ctx.get('task_input') or ctx.get('task') or '').lower()\n"
        "    return {'ok': ('safe' in task) or ('repair' in task)}\n",
        encoding="utf-8",
    )
    baseline_path = baseline_dir / "skill.py"
    baseline_path.write_text(
        "def apply(ctx):\n"
        "    task = str(ctx.get('task_input') or ctx.get('task') or '').lower()\n"
        "    return {'ok': 'safe' in task}\n",
        encoding="utf-8",
    )

    evals_path = candidate_dir / "evals.yml"
    evals_path.write_text(
        yaml.safe_dump(
            {
                "shadow": {
                    "baseline_skill_path": str(baseline_path),
                    "replay": {
                        "enabled": True,
                        "max_runs": 5,
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    feedback_path = workspace / "data" / "skill-usage.jsonl"
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_events = [
        {
            "event_type": "skill_runtime",
            "skill_id": "baseline_skill",
            "status": "succeeded",
            "project_id": "workspace",
            "run_id": "run_safe",
            "agent_role": "Dev",
            "payload": {"workspace": str(workspace), "reason": "gemini", "approval_rejects": 0},
        },
        {
            "event_type": "skill_runtime",
            "skill_id": "baseline_skill",
            "status": "failed",
            "project_id": "workspace",
            "run_id": "run_repair",
            "agent_role": "Dev",
            "payload": {"workspace": str(workspace), "reason": "gemini", "approval_rejects": 1},
        },
    ]
    feedback_path.write_text("\n".join(json.dumps(item) for item in feedback_events), encoding="utf-8")

    runs_dir = workspace / "runs"
    (runs_dir / "run_safe").mkdir(parents=True, exist_ok=True)
    (runs_dir / "run_repair").mkdir(parents=True, exist_ok=True)
    (runs_dir / "run_safe" / "chat_trace.json").write_text(
        json.dumps(
            {
                "run_id": "run_safe",
                "agent_role": "Dev",
                "task": "safe migration fix",
                "transcript": [
                    {"kind": "tool_call", "payload": {"name": "read_file"}},
                ],
                "result": {"ok": True, "reason": "gemini", "approval_rejects": 0},
            }
        ),
        encoding="utf-8",
    )
    (runs_dir / "run_repair" / "chat_trace.json").write_text(
        json.dumps(
            {
                "run_id": "run_repair",
                "agent_role": "Dev",
                "task": "repair broken import path",
                "transcript": [
                    {"kind": "tool_call", "payload": {"name": "read_file"}},
                    {"kind": "tool_call", "payload": {"name": "write_file"}},
                ],
                "result": {"ok": False, "reason": "gemini", "approval_rejects": 1},
            }
        ),
        encoding="utf-8",
    )

    report = SkillEvalHarness().evaluate(
        str(candidate_path),
        evals_path=str(evals_path),
        feedback_path=str(feedback_path),
        runs_dir=str(runs_dir),
    )

    assert report.shadow_eval.total_cases == 2
    assert report.shadow_eval.replay_cases == 2
    assert report.shadow_eval.replay_human_overrides == 1
    assert report.shadow_eval.wins == 1
    assert report.shadow_eval.losses == 0
    assert report.recommended_stage == "canary"
    assert any(item["source"] == "replay" and item["run_id"] == "run_repair" for item in report.shadow_eval.details)




def test_skill_eval_harness_replay_compares_against_replayed_baseline_not_recorded_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    candidate_dir = workspace / "skills" / "candidate_skill"
    baseline_dir = workspace / "skills" / "baseline_skill"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir.mkdir(parents=True, exist_ok=True)

    candidate_path = candidate_dir / "skill.py"
    candidate_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    baseline_path = baseline_dir / "skill.py"
    baseline_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    evals_path = candidate_dir / "evals.yml"
    evals_path.write_text(
        yaml.safe_dump(
            {
                "shadow": {
                    "baseline_skill_path": str(baseline_path),
                    "replay": {"enabled": True, "max_runs": 5},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    feedback_path = workspace / "data" / "skill-usage.jsonl"
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(
        json.dumps(
            {
                "event_type": "skill_runtime",
                "skill_id": "baseline_skill",
                "status": "failed",
                "project_id": "workspace",
                "run_id": "run_failed_for_external_reason",
                "agent_role": "Dev",
                "payload": {"reason": "provider_timeout"},
            }
        ),
        encoding="utf-8",
    )

    runs_dir = workspace / "runs"
    (runs_dir / "run_failed_for_external_reason").mkdir(parents=True, exist_ok=True)
    (runs_dir / "run_failed_for_external_reason" / "chat_trace.json").write_text(
        json.dumps(
            {
                "run_id": "run_failed_for_external_reason",
                "agent_role": "Dev",
                "task": "safe migration fix",
                "result": {"ok": False, "reason": "provider_timeout", "approval_rejects": 0},
            }
        ),
        encoding="utf-8",
    )

    report = SkillEvalHarness().evaluate(
        str(candidate_path),
        evals_path=str(evals_path),
        feedback_path=str(feedback_path),
        runs_dir=str(runs_dir),
    )

    assert report.shadow_eval.total_cases == 1
    assert report.shadow_eval.wins == 0
    assert report.shadow_eval.ties == 1
    assert report.shadow_eval.losses == 0
    replay_detail = report.shadow_eval.details[0]
    assert replay_detail["recorded_run_ok"] is False
    assert replay_detail["baseline_passed"] is True



def test_skill_eval_harness_respects_disabled_replay(tmp_path):
    workspace = tmp_path / "workspace"
    candidate_dir = workspace / "skills" / "candidate_skill"
    baseline_dir = workspace / "skills" / "baseline_skill"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir.mkdir(parents=True, exist_ok=True)

    candidate_path = candidate_dir / "skill.py"
    candidate_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    baseline_path = baseline_dir / "skill.py"
    baseline_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    evals_path = candidate_dir / "evals.yml"
    evals_path.write_text(
        yaml.safe_dump(
            {
                "shadow": {
                    "baseline_skill_path": str(baseline_path),
                    "replay": {"enabled": False, "max_runs": 5},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    feedback_path = workspace / "data" / "skill-usage.jsonl"
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(
        json.dumps(
            {
                "event_type": "skill_runtime",
                "skill_id": "baseline_skill",
                "status": "succeeded",
                "project_id": "workspace",
                "run_id": "run_disabled",
            }
        ),
        encoding="utf-8",
    )
    runs_dir = workspace / "runs"
    (runs_dir / "run_disabled").mkdir(parents=True, exist_ok=True)
    (runs_dir / "run_disabled" / "chat_trace.json").write_text(
        json.dumps({"run_id": "run_disabled", "task": "safe migration fix", "result": {"ok": True}}),
        encoding="utf-8",
    )

    report = SkillEvalHarness().evaluate(
        str(candidate_path),
        evals_path=str(evals_path),
        feedback_path=str(feedback_path),
        runs_dir=str(runs_dir),
    )

    assert report.shadow_eval.total_cases == 0
    assert report.shadow_eval.replay_cases == 0



def test_skill_eval_harness_rejects_non_dict_return_values(tmp_path):
    candidate_path = _write_skill(
        tmp_path,
        "bad_shape_skill",
        "def apply(ctx):\n    return 'ok'\n",
    )
    evals_path = tmp_path / "bad_shape_skill" / "evals.yml"
    evals_path.write_text(
        yaml.safe_dump(
            {
                "contract": [{"name": "contract-shape", "ctx": {}, "expect_ok": True}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = SkillEvalHarness().evaluate(candidate_path, evals_path=str(evals_path))

    assert report.contract_eval.failed == 1
    assert report.contract_eval.errors == 1
    assert report.contract_eval.details[0].error == "invalid_result_type:str"
    assert report.recommended_stage == "draft"



def test_infer_workspace_root_prefers_workspace_for_runs_trace_path(tmp_path):
    trace_path = tmp_path / "workspace" / "runs" / "run_123" / "chat_trace.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("{}", encoding="utf-8")

    assert _infer_workspace_root(str(trace_path)) == str(tmp_path / "workspace")
