import core.policy_runtime as pr


class _ModelDumpOnlyConfig:
    def model_dump(self):
        return {
            "quality_gate": {
                "fallback": "auto",
                "timeout_sec": 15,
            },
            "global_policy": {
                "readonly": True,
            },
        }

    def dict(self):
        raise AssertionError("legacy dict() should not be called when model_dump() exists")


def test_resolve_quality_gate_policy_prefers_model_dump():
    policy = pr.resolve_quality_gate_policy("skill_a", config=_ModelDumpOnlyConfig())

    assert policy["fallback"] == "auto"
    assert policy["timeout_sec"] == 15
    assert policy["resolved_for"] == "skill_a"


def test_policy_runtime_resolve_agent_policy_prefers_model_dump(monkeypatch):
    monkeypatch.setattr(pr, "factory_config", _ModelDumpOnlyConfig())

    merged = pr.PolicyRuntime(base_dir=".").resolve_agent_policy(
        {"runtime_rules": {"shell": "blocked"}}
    )

    assert merged["readonly"] is True
    assert merged["shell"] == "blocked"
