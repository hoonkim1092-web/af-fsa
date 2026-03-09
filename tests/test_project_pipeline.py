import importlib
import os


def _load_launcher(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    importlib.reload(core.utils)
    import core.agent_runner
    importlib.reload(core.agent_runner)
    import core.project_pipeline
    importlib.reload(core.project_pipeline)
    import agent_launcher
    return importlib.reload(agent_launcher)


def test_factory_routes_complex_task_to_project_pipeline(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch)
    factory = al.AgentFactory()

    routed = []
    factory.request_router.route = lambda **kwargs: {
        "pipeline": "project",
        "intent": "greenfield",
        "confidence": 99,
        "reasoning": "forced-test",
    }
    factory.project_pipeline.run = lambda **kwargs: routed.append(kwargs) or {
        "pipeline": "project",
        "ok": True,
        "reason": "completed",
    }

    res = factory.run(task_input="포커 게임 만들어줘", role_spec="General", workspace=str(tmp_path))

    assert res["pipeline"] == "project"
    assert routed and routed[0]["workspace"] == str(tmp_path)
    assert routed[0]["requested_role"] == "General"


def test_project_pipeline_writes_planning_artifacts_and_roles(monkeypatch, tmp_path):
    al = _load_launcher(monkeypatch)
    factory = al.AgentFactory()
    pipeline = factory.project_pipeline

    pipeline.research.research_project_brief = lambda agent, task_input, workspace=None: {
        "goal": "브라우저에서 실행되는 포커 게임 구현",
        "constraints": ["network_allowed"],
        "required_skills": ["frontend_game_ui", "gameplay_core", "integration_test_guard"],
        "role_hints": ["frontend_dev", "qa_engineer"],
        "deliverables": ["게임 UI", "게임 규칙", "테스트"],
        "risks": ["룰 판정 오류"],
        "research_notes": ["seed"],
        "tech_stack": ["vanilla_js"],
    }
    pipeline.planner.plan = lambda task_input, project_brief: {
        "execution_strategy": "parallel",
        "roles": [
            {
                "id": "frontend_dev",
                "name": "Frontend Dev",
                "objective": "게임 UI를 구현한다.",
                "required_skills": ["frontend_game_ui"],
            },
            {
                "id": "qa_engineer",
                "name": "QA Engineer",
                "objective": "핵심 플레이 흐름을 검증한다.",
                "required_skills": ["integration_test_guard"],
            },
        ],
        "todo_items": [
            "Frontend Dev: 게임 UI를 구현한다.",
            "QA Engineer: 핵심 플레이 흐름을 검증한다.",
        ],
    }

    def _get_or_create(role_spec, workspace=None):
        return {"name": role_spec, "role": role_spec, "skills": []}

    factory.agent_mgr.get_or_create = _get_or_create
    procure_calls = []
    factory.procurer.procure_multiple = lambda **kwargs: procure_calls.append(
        (kwargs["agent"]["role"], list(kwargs["skill_names"]), kwargs["workspace"])
    ) or list(kwargs["skill_names"])

    import core.project_pipeline as pp

    class _DummyOrchestrator:
        def __init__(self, mr, max_concurrent=5):
            self.mr = mr

        def run_project(self, project_desc, roles, workspace=None):
            return {"current_status": "completed", "roles": roles, "workspace": workspace}

    monkeypatch.setattr(pp, "DynamicOrchestrator", _DummyOrchestrator)

    res = pipeline.run(
        task_input="포커 게임 만들어줘",
        workspace=str(tmp_path),
        execution_mode="approval",
        enable_build=True,
        requested_role="General",
        route={"pipeline": "project"},
    )

    assert res["ok"] is True
    assert sorted(res["roles"]) == ["frontend_dev", "qa_engineer"]
    assert os.path.exists(tmp_path / "planning" / "project_brief.json")
    assert os.path.exists(tmp_path / "planning" / "role_plan.json")
    assert os.path.exists(tmp_path / ".todo.md")
    assert os.path.exists(tmp_path / "agents" / "frontend_dev.yaml")
    assert os.path.exists(tmp_path / "agents" / "qa_engineer.yaml")
    frontend_agent = al.read_yaml(tmp_path / "agents" / "frontend_dev.yaml")
    assert "file_handler" in frontend_agent["skills"]
    assert "core_memory" in frontend_agent["skills"]
    assert "file_handler" in frontend_agent["runtime_rules"]["allowed_skills"]
    assert "core_memory" in frontend_agent["runtime_rules"]["allowed_skills"]
    assert procure_calls == [
        ("Frontend Dev", ["frontend_game_ui"], str(tmp_path)),
        ("QA Engineer", ["integration_test_guard"], str(tmp_path)),
    ]
