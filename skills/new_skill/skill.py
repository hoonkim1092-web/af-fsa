from datetime import datetime

def propose(ctx):
    return {"description": "external demo skill", "required_keys": ["task_input"]}

def apply(ctx):
    return {"ok": True, "source": "external_repo", "ts": datetime.now().isoformat(), "task": ctx.get("task_input", "")}

def test(ctx):
    r = apply(ctx or {})
    return {"ok": bool(r.get("ok")), "result": r}
