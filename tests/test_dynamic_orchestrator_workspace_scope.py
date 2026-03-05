import asyncio

import core.dynamic_orchestrator as dyn


class _DummyMR:
    def pick(self, _name):
        return "models/gemini-2.0-flash"


class _DummyLLM:
    def __init__(self, model_name=None):
        self.model_name = model_name

    def generate_json(self, _prompt):
        return {"next_tasks": []}


class _DummyRunner:
    def __init__(self, _mr):
        self.last_workspace = None

    def run(self, _agent_data, _subtask, _run_id, _auto_approve, workspace):
        self.last_workspace = workspace
        return {"ok": True}


class _DummyAgentManager:
    def __init__(self, _mr):
        self.last_workspace = None

    def get_or_create(self, role, workspace=None):
        self.last_workspace = workspace
        return {"name": role, "skills": []}


class _DummyMemoryHub:
    def get_summary(self):
        return "ok"

    async def update_ast_state(self, **_kwargs):
        return None


class _DummyEvaluator:
    def __init__(self, model_name=None):
        self.model_name = model_name

    def evaluate_failure(self, **_kwargs):
        return {"action": "retry", "new_instruction": ""}


def test_workspace_is_propagated_without_rebinding(monkeypatch, tmp_path):
    monkeypatch.setattr(dyn, "LLMEngine", _DummyLLM)
    monkeypatch.setattr(dyn, "AgentRunner", _DummyRunner)
    monkeypatch.setattr(dyn, "AgentManager", _DummyAgentManager)
    monkeypatch.setattr(dyn, "AstMemoryHub", _DummyMemoryHub)
    monkeypatch.setattr(dyn, "StrategyEvaluator", _DummyEvaluator)

    workspace = tmp_path / "proj"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".todo.md").write_text("- t1", encoding="utf-8")

    orch = dyn.DynamicOrchestrator(_DummyMR())

    tasks = asyncio.run(orch._lilith_decide_next("test", ["dev"], workspace=str(workspace)))
    assert tasks == []

    asyncio.run(orch._execute_agent_task("dev", "do x", "run_1", workspace=str(workspace)))
    assert orch.agent_mgr.last_workspace == str(workspace)
    assert orch.runner.last_workspace == str(workspace)
