import os
from datetime import datetime

SKILL_ID = "zero_integration_parsing_spec"


def propose(ctx):
    return {
        "skill_id": SKILL_ID,
        "mode": "himari_bridge",
        "description": "Bridge skill for declared-but-missing implementation.",
        "required_keys": ["task_input"],
        "optional_keys": ["topic", "constraints"],
    }


def apply(ctx):
    artifacts_dir = ctx.get("artifacts_dir", ".")
    os.makedirs(artifacts_dir, exist_ok=True)

    topic = str(ctx.get("topic") or ctx.get("task_input") or SKILL_ID)
    constraints = ctx.get("constraints")
    if isinstance(constraints, (list, tuple)):
        constraints_text = ", ".join([str(x) for x in constraints if str(x).strip()])
    else:
        constraints_text = str(constraints or "none")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(artifacts_dir, f"{SKILL_ID}_{ts}.md")

    body = (
        f"# Himari Bridge Output: {SKILL_ID}\\n"
        f"timestamp: {datetime.now().isoformat()}\\n"
        f"topic: {topic}\\n"
        f"constraints: {constraints_text}\\n\\n"
        "## Planning Frame\\n"
        "1. clarify intent\\n"
        "2. define constraints\\n"
        "3. draft executable checklist\\n"
        "4. produce verifiable output\\n"
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)

    return {
        "ok": True,
        "skill_id": SKILL_ID,
        "bridge": "himari",
        "artifact_path": out_path,
        "message": "Bridge skill executed successfully.",
    }


def test(ctx):
    res = apply(dict(ctx or {}))
    path = res.get("artifact_path")
    if not res.get("ok") or not path or not os.path.exists(path):
        return {"ok": False, "reason": "artifact_not_created", "detail": res}
    return {"ok": True, "artifact_path": path}
