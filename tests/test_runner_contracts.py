import importlib
import types


def _load_launcher(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    importlib.reload(core.utils)
    import core.agent_runner
    importlib.reload(core.agent_runner)
    import agent_launcher
    return importlib.reload(agent_launcher)


def _module_from_code(name: str, code: str, skill_id: str):
    mod = types.ModuleType(name)
    exec(code, mod.__dict__)
    mod.__skill_id__ = skill_id
    return mod


def test_tool_filter_and_ctx_merge(monkeypatch):
    al = _load_launcher(monkeypatch)
    runner = al.AgentRunner(al.ModelRouter())
    mod = _module_from_code(
        "skills.issue_tracker",
        """
from datetime import datetime
def apply(ctx):
    return {"ok": True, "command": ctx.get("command"), "has_datetime": bool(datetime)}
""",
        "issue_tracker",
    )
    ctx = {"data_dir": ".", "artifacts_dir": ".", "agent": {}}
    policy = runner._build_policy({"runtime_rules": {}}, ["issue_tracker"])
    registry = runner.build_tool_registry([mod], ctx, policy)
    tools = registry.get_active_tools()
    names = [t.__name__ for t in tools]

    # imported callable(datetime) must not be exposed as a tool
    assert "issue_tracker_apply" in names
    assert "datetime" not in names

    apply_tool = next(t for t in tools if t.__name__ == "issue_tracker_apply")
    res = apply_tool(command="list")
    assert res["ok"] is True
    assert res["command"] == "list"


def test_runtime_rule_default_deny(monkeypatch):
    al = _load_launcher(monkeypatch)
    runner = al.AgentRunner(al.ModelRouter())
    m1 = _module_from_code("skills.alpha", "def apply(ctx): return {'ok': True}", "alpha")
    m2 = _module_from_code("skills.beta", "def apply(ctx): return {'ok': True}", "beta")
    ctx = {"data_dir": ".", "artifacts_dir": ".", "agent": {}}
    agent = {"runtime_rules": {"default_deny": True, "allowed_skills": ["alpha"]}}
    policy = runner._build_policy(agent, ["alpha", "beta"])
    registry = runner.build_tool_registry([m1, m2], ctx, policy)
    tools = registry.get_active_tools()
    skill_ids = [getattr(t, "_skill_id", "") for t in tools]
    assert "alpha" in skill_ids
    assert "beta" not in skill_ids


def test_system_prompt_and_signature_resolution(monkeypatch):
    al = _load_launcher(monkeypatch)
    runner = al.AgentRunner(al.ModelRouter())
    agent = {
        "prompt": {"system_ko": "nested-prompt"},
        "persona": {"signature_lines": ["sig-a", "sig-b"]},
    }
    assert runner._resolve_system_prompt(agent) == "nested-prompt"
    sigs = runner._resolve_signature_lines(agent)
    assert sigs == ["sig-a", "sig-b"]
