import os


def resolve_runtime_mode(default_mode: str = "safe") -> str:
    mode = str(os.getenv("AGENT_RUNTIME_MODE", default_mode)).strip().lower()
    if mode not in ("dev", "safe", "strict"):
        return default_mode
    return mode


def resolve_quality_gate_policy(project_policies: dict | None) -> dict:
    policies = project_policies if isinstance(project_policies, dict) else {}
    qg = policies.get("quality_gate", {}) if isinstance(policies.get("quality_gate"), dict) else {}
    return {
        "default_stage_on_build": str(qg.get("default_stage_on_build", "candidate")),
        "auto_promote_sequence": [str(x).strip().lower() for x in (qg.get("auto_promote_sequence") or ["canary", "active"]) if str(x).strip()],
        "installable_statuses": [
            str(x).strip().lower()
            for x in (qg.get("installable_statuses") or ["active"])
            if str(x).strip()
        ],
    }
