import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("GOOGLE_API_KEY", "test-key")

from core.agent_runner import AgentRunner
from core.model_router import ModelRouter
from model_utils import ModelSelection, TIER_PRIMARY


class _DummyRegistry:
    def get_active_tools(self):
        return []


class _DummyChat:
    def send_message(self, _msg):
        return SimpleNamespace(parts=[], candidates=[])


def _selection(model_name: str = "models/gemini-2.0-flash"):
    return ModelSelection(model_name, TIER_PRIMARY, "")


def _run_with_classifier(agent: dict, task_input: str, classifier_text: str):
    runner = AgentRunner(ModelRouter())
    with (
        patch("core.model_router.resolve_dynamic_model", return_value=_selection()) as mock_resolve,
        patch.object(runner, "_resolve_system_prompt", return_value="sys"),
        patch.object(runner, "load_skills", return_value=[]),
        patch.object(runner, "build_tool_registry", return_value=_DummyRegistry()),
        patch("core.agent_runner.validate_context_with_schema", return_value=(True, "ok")),
        patch("core.agent_runner.HookEventBus.run_pre_execute", return_value=True),
        patch("core.agent_runner.generate_content_with_self_heal", return_value=SimpleNamespace(text=classifier_text)) as mock_classify,
        patch("core.agent_runner.create_chat_with_self_heal", return_value=_DummyChat()),
    ):
        result = runner.run(agent, task_input)
    return result, mock_resolve, mock_classify


def test_simple_task_routes_to_lightweight():
    agent = {"name": "test_agent", "role": "assistant", "skills": []}

    result, mock_resolve, mock_classify = _run_with_classifier(agent, "Fix a typo", "simple")

    assert result["ok"] is True
    mock_classify.assert_called_once()
    mock_resolve.assert_called_with("lightweight")


def test_complex_task_uses_role_based_engine():
    agent = {"name": "test_agent", "role": "assistant", "skills": []}

    result, mock_resolve, mock_classify = _run_with_classifier(
        agent,
        "Implement the billing integration module end to end.",
        "complex",
    )

    assert result["ok"] is True
    mock_classify.assert_called_once()
    mock_resolve.assert_called_with("researcher_gemini")


def test_research_role_skips_classifier_and_stays_complex():
    runner = AgentRunner(ModelRouter())
    agent = {"name": "researcher", "role": "research", "skills": []}

    with (
        patch("core.model_router.resolve_dynamic_model", return_value=_selection()) as mock_resolve,
        patch.object(runner, "_resolve_system_prompt", return_value="sys"),
        patch.object(runner, "load_skills", return_value=[]),
        patch.object(runner, "build_tool_registry", return_value=_DummyRegistry()),
        patch("core.agent_runner.validate_context_with_schema", return_value=(True, "ok")),
        patch("core.agent_runner.HookEventBus.run_pre_execute", return_value=True),
        patch("core.agent_runner.generate_content_with_self_heal") as mock_classify,
        patch("core.agent_runner.create_chat_with_self_heal", return_value=_DummyChat()),
    ):
        result = runner.run(agent, "Summarize the latest notes.")

    assert result["ok"] is True
    mock_classify.assert_not_called()
    mock_resolve.assert_called_with("researcher_gemini")
