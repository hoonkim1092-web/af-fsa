import importlib
import json
import types

import pytest


def test_config_paths_allows_cli_only_bootstrap_without_api_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_CHAT_PROVIDER", "claude_cli")
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(tmp_path / "proj"))
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_cli_boot")

    import core.config_paths

    cfg = importlib.reload(core.config_paths)
    assert cfg.PROJECT_ID == "proj_cli_boot"
    assert cfg.PROJECT_ROOT.endswith("proj")


def test_cli_provider_registry_defaults_and_filtering():
    from core.providers.registry import (
        default_chat_model_for_provider,
        get_requested_cli_providers,
        supports_cli_bootstrap,
    )

    assert get_requested_cli_providers("claude_cli, gemini, codex_cli") == ["claude_cli", "codex_cli"]
    assert default_chat_model_for_provider("claude_cli") == "claude"
    assert default_chat_model_for_provider("gemini_cli") == "gemini"
    assert default_chat_model_for_provider("codex_cli") == "gpt-5"
    assert supports_cli_bootstrap("gemini_cli") is True
    assert supports_cli_bootstrap("gemini") is False


@pytest.mark.parametrize(
    ("provider_id", "expected_prefix", "expected_items"),
    [
        ("claude_cli", ["claude"], ["-p", "--append-system-prompt", "--output-format", "json"]),
        ("gemini_cli", ["gemini"], ["-p", "--output-format", "json"]),
        ("codex_cli", ["codex", "exec"], ["-c", "model_reasoning_effort=\"low\""]),
    ],
)
def test_build_cli_command_uses_provider_specific_defaults(provider_id, expected_prefix, expected_items):
    from core.providers.cli import CliChatRequest, build_cli_command

    cmd = build_cli_command(
        CliChatRequest(
            provider_id=provider_id,
            model="test-model",
            system_prompt="system prompt",
            task_input="execute task",
            workspace="D:/workspace",
        )
    )

    assert cmd[: len(expected_prefix)] == expected_prefix
    for item in expected_items:
        assert item in cmd
    assert any("execute task" in part for part in cmd)


def test_codex_cli_path_override_keeps_exec_subcommand(monkeypatch):
    from core.providers.cli import CliChatRequest, build_cli_command

    monkeypatch.setenv("AGENT_CODEX_CLI_COMMAND", r"C:\Tools\codex.cmd")

    cmd = build_cli_command(
        CliChatRequest(
            provider_id="codex_cli",
            model="gpt-5",
            system_prompt="system prompt",
            task_input="execute task",
            workspace="D:/workspace",
        )
    )

    assert cmd[:2] == [r"C:\Tools\codex.cmd", "exec"]


def test_agent_runner_uses_cli_provider_before_sdk_fallback(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / ".todo.md").write_text("- execute task\n", encoding="utf-8")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_CHAT_PROVIDER", "codex_cli")
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_cli_runner")

    import core.config_paths
    import core.utils
    import core.agent_runner as ar

    importlib.reload(core.config_paths)
    importlib.reload(core.utils)
    ar = importlib.reload(ar)

    calls = []

    def fake_execute_cli_chat(request, run_command=None):
        calls.append(request)
        return {
            "ok": True,
            "provider_id": request.provider_id,
            "text": "cli output",
            "stdout": "cli output",
            "stderr": "",
            "returncode": 0,
        }

    monkeypatch.setattr(ar, "execute_cli_chat", fake_execute_cli_chat)

    runner = ar.AgentRunner(ar.ModelRouter())
    monkeypatch.setattr(runner, "load_skills", lambda agent: [])
    monkeypatch.setattr(
        runner,
        "build_tool_registry",
        lambda modules, ctx, policy: types.SimpleNamespace(get_active_tools=lambda: []),
    )

    result = runner.run(
        {"name": "cli-agent", "role": "architect", "skills": []},
        "execute task",
        workspace=str(project_root),
    )

    assert result["ok"] is True
    assert result["reason"] == "codex_cli"
    assert calls
    assert calls[0].provider_id == "codex_cli"


def test_execute_cli_chat_persists_failed_launch_state(tmp_path):
    from core.providers.cli import CliChatRequest, execute_cli_chat

    workspace = tmp_path / "proj"
    workspace.mkdir(parents=True, exist_ok=True)

    def missing_runner(*args, **kwargs):
        raise FileNotFoundError("missing cli")

    result = execute_cli_chat(
        CliChatRequest(
            provider_id="codex_cli",
            model="codex",
            system_prompt="system prompt",
            task_input="ship feature",
            workspace=str(workspace),
            run_id="run_missing",
        ),
        run_command=missing_runner,
    )

    state_path = workspace / ".af_runtime" / "cli_sessions" / "codex_cli_run_missing.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert result["ok"] is False
    assert result["reason"].startswith("cli_command_not_found:")
    assert state["ok"] is False
    assert state["reason"].startswith("cli_command_not_found:")
