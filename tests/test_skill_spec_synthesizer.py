import os

import yaml

from core.skill_spec_synthesizer import SkillSpecSynthesizer



def test_skill_spec_synthesizer_writes_spec_and_shadow_eval(tmp_path, monkeypatch):
    baseline_dir = tmp_path / "skills" / "existing_skill"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baseline_dir / "skill.py"
    baseline_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    import core.skill_spec_synthesizer as synth_mod

    monkeypatch.setattr(
        synth_mod,
        "resolve_skill_paths",
        lambda skill_id: (str(baseline_path), None) if skill_id == "existing_skill" else (None, None),
    )

    artifacts = SkillSpecSynthesizer().synthesize(
        agent={"role": "Backend Architect"},
        skill_name="pytest_regression_guard",
        reqs={
            "goal": "Add pytest regression coverage and safe file updates",
            "constraints": ["offline_only", "safe_file_edit"],
            "risk_level": "elevated",
            "capabilities": [{"id": "pytest_regression", "required": True}],
        },
        target_evidence={"top_candidate": "existing_skill", "verified": True, "score": 0.91},
    )
    spec_path, evals_path = artifacts.write_to_dir(str(tmp_path / "generated_skill"))

    assert os.path.exists(spec_path)
    assert os.path.exists(evals_path)

    spec = yaml.safe_load((tmp_path / "generated_skill" / "skill-spec.yaml").read_text(encoding="utf-8"))
    evals = yaml.safe_load((tmp_path / "generated_skill" / "evals.yml").read_text(encoding="utf-8"))

    assert spec["id"] == "pytest_regression_guard"
    assert spec["distribution"]["maturity"] == "draft"
    assert "pytest_regression" in spec["capabilities"]
    assert "run_pytest" in spec["policy"]["allowed_tools"]
    assert evals["shadow"]["baseline_skill_path"].endswith("skill.py")
    assert evals["shadow"]["replay"]["enabled"] is True
    assert artifacts.capability_intent["evidence"]["verified"] is True

