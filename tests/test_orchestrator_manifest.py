import importlib
import json

from core.continuity.manifest_store import OrchestratorManifestStore
from core.continuity.runtime_paths import workspace_runtime_file


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
        pass

    def run(self, _agent_data, _subtask, _run_id, _auto_approve, workspace, task_id=""):
        return {"ok": True, "workspace": workspace}


class _DummyAgentManager:
    def __init__(self, _mr):
        pass

    def get_or_create(self, role, workspace=None):
        return {"name": role, "skills": [], "workspace": workspace}


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


def test_manifest_store_converts_active_work_into_interrupted(tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir(parents=True, exist_ok=True)
    store = OrchestratorManifestStore(str(workspace))

    store.save_snapshot(
        {
            "completed_subtasks": [{"role": "pm", "subtask": "done"}],
            "failed_subtasks": [],
            "agents_status": {"dev": "working"},
            "current_status": "running",
        },
        active_assignments={
            "run_1": {"role": "dev", "subtask": "finish feature", "workspace": str(workspace)}
        },
        roles=["dev"],
        project_desc="sample",
        force=True,
    )

    loaded = store.load_resume_state()

    assert loaded["completed_subtasks"] == [{"role": "pm", "subtask": "done"}]
    assert loaded["agents_status"]["dev"] == "idle"
    assert loaded["interrupted_subtasks"] == [
        {"role": "dev", "subtask": "finish feature", "workspace": str(workspace)}
    ]


def test_dynamic_orchestrator_writes_manifest_and_restores_interruptions(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import core.config_paths
    import core.utils
    import core.agent_runner
    import core.dynamic_orchestrator as dyn

    importlib.reload(core.config_paths)
    importlib.reload(core.utils)
    importlib.reload(core.agent_runner)
    dyn = importlib.reload(dyn)

    monkeypatch.setattr(dyn, "LLMEngine", _DummyLLM)
    monkeypatch.setattr(dyn, "AgentRunner", _DummyRunner)
    monkeypatch.setattr(dyn, "AgentManager", _DummyAgentManager)
    monkeypatch.setattr(dyn, "AstMemoryHub", _DummyMemoryHub)
    monkeypatch.setattr(dyn, "StrategyEvaluator", _DummyEvaluator)

    workspace = tmp_path / "proj"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".todo.md").write_text("- t1", encoding="utf-8")

    manifest = OrchestratorManifestStore(str(workspace))
    manifest.save_snapshot(
        {
            "completed_subtasks": [],
            "failed_subtasks": [],
            "agents_status": {"dev": "working"},
            "current_status": "running",
        },
        active_assignments={
            "run_1": {"role": "dev", "subtask": "resume me", "workspace": str(workspace)}
        },
        roles=["dev"],
        project_desc="resume-test",
        force=True,
    )

    orch = dyn.DynamicOrchestrator(_DummyMR())
    result = orch.run_project("resume-test", ["dev"], workspace=str(workspace))

    assert result["current_status"] == "completed"
    assert result["interrupted_subtasks"] == [
        {"role": "dev", "subtask": "resume me", "workspace": str(workspace)}
    ]

    manifest_data = json.loads((workspace / ".af_manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["state_board"]["current_status"] == "completed"
    assert manifest_data["roles"] == ["dev"]
    assert workspace_runtime_file(workspace, "dynamic_log.txt").exists()

