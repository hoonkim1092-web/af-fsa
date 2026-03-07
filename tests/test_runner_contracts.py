import importlib
import os
import time
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


def test_load_skills_uses_cache_and_invalidates_on_file_change(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch)
    runner = al.AgentRunner(al.ModelRouter())

    skill_path = tmp_path / "cache_skill.py"
    skill_path.write_text("COUNTER = 1\ndef apply(ctx):\n    return {'counter': COUNTER}\n", encoding="utf-8")

    import core.agent_runner as ar

    def _resolve(sid: str):
        if sid == "cache_skill":
            return str(skill_path), None
        return None, None

    monkeypatch.setattr(ar, "resolve_skill_paths", _resolve)
    agent = {"skills": ["cache_skill"]}

    loaded_first = runner.load_skills(agent)
    loaded_second = runner.load_skills(agent)
    assert loaded_first and loaded_second
    assert loaded_first[0] is loaded_second[0]
    assert int(getattr(loaded_second[0], "COUNTER", 0)) == 1

    time.sleep(1.1)
    skill_path.write_text("COUNTER = 2\ndef apply(ctx):\n    return {'counter': COUNTER}\n", encoding="utf-8")
    os.utime(skill_path, None)

    loaded_third = runner.load_skills(agent)
    assert loaded_third
    assert loaded_third[0] is not loaded_second[0]
    assert int(getattr(loaded_third[0], "COUNTER", 0)) == 2


def test_load_skills_collects_knowledge_markdown(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch)
    runner = al.AgentRunner(al.ModelRouter())

    skill_dir = tmp_path / "knowledge_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.md").write_text(
        "---\nname: Knowledge Skill\ndescription: prompt injection\n---\n\n# Steps\n1. test\n",
        encoding="utf-8",
    )

    import core.agent_runner as ar

    monkeypatch.setattr(ar, "PROJECT_SKILLS_DIR", str(tmp_path))
    monkeypatch.setattr(ar, "SKILLS_DIR", str(tmp_path))

    loaded = runner.load_skills({"skills": ["knowledge_skill"]})

    assert loaded == []
    assert len(runner._knowledge_skills) == 1
    assert runner._knowledge_skills[0].id == "knowledge_skill"
