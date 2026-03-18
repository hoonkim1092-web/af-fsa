import os


def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def resolve_runtime_mode(default_mode: str = "safe") -> str:
    mode = str(os.getenv("AGENT_RUNTIME_MODE", default_mode)).strip().lower()
    if mode not in ("dev", "safe", "strict"):
        return default_mode
    return mode


def resolve_quality_gate_policy(project_policies: dict | None) -> dict:
    policies = project_policies if isinstance(project_policies, dict) else {}
    qg = policies.get("quality_gate", {}) if isinstance(policies.get("quality_gate"), dict) else {}
    return {
        "default_stage_on_build": str(qg.get("default_stage_on_build", "draft")),
        "auto_promote_sequence": [str(x).strip().lower() for x in (qg.get("auto_promote_sequence") or ["canary", "active"]) if str(x).strip()],
        "installable_statuses": [
            str(x).strip().lower()
            for x in (qg.get("installable_statuses") or ["active"])
            if str(x).strip()
        ],
        "active_runtime_min_events": max(0, _coerce_int(qg.get("active_runtime_min_events", 3), 3)),
        "active_runtime_min_success_rate": _coerce_float(qg.get("active_runtime_min_success_rate", 0.8), 0.8),
        "demote_runtime_min_events": max(0, _coerce_int(qg.get("demote_runtime_min_events", 3), 3)),
        "demote_runtime_below_success_rate": _coerce_float(qg.get("demote_runtime_below_success_rate", 0.5), 0.5),
    }
