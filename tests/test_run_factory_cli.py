import importlib
import os
import sys
import types

import pytest


def _install_fake_agent_launcher(monkeypatch, calls):
    fake_module = types.ModuleType("agent_launcher")

    class FakeFactory:
        def run(self, **kwargs):
            calls.append(("run", kwargs))
            return {"ok": True}

        def run_workflow(self, **kwargs):
            calls.append(("run_workflow", kwargs))
            return {"ok": True}

    fake_module.AgentFactory = FakeFactory
    monkeypatch.setitem(sys.modules, "agent_launcher", fake_module)


def test_run_factory_cli_sets_provider_and_projects_root(monkeypatch, tmp_path):
    calls = []
    _install_fake_agent_launcher(monkeypatch, calls)
    monkeypatch.delenv("AGENT_CHAT_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_CODEX_CLI_COMMAND", raising=False)
    monkeypatch.delenv("AGENT_PROJECTS_DIR", raising=False)
    monkeypatch.delenv("AGENT_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("AGENT_PROJECT_ID", raising=False)
    monkeypatch.delenv("AGENT_AUTO_INSTALL_CLI", raising=False)
    monkeypatch.delenv("AGENT_DISABLE_ENGINE_API_KEYS", raising=False)

    import run_factory_cli

    cli = importlib.reload(run_factory_cli)
    projects_root = tmp_path / "custom-projects"
    provider_command = r"C:\Tools\codex.cmd"

    cli.main(
        [
            "--project",
            "demo",
            "--task",
            "return ok",
            "--provider",
            "codex_cli",
            "--provider-command",
            provider_command,
            "--projects-root",
            str(projects_root),
        ]
    )

    expected_root = str((projects_root / "demo").resolve())
    assert os.environ["AGENT_CHAT_PROVIDER"] == "codex_cli"
    assert os.environ["AGENT_CODEX_CLI_COMMAND"] == provider_command
    assert os.environ["AGENT_PROJECTS_DIR"] == str(projects_root.resolve())
    assert os.environ["AGENT_PROJECT_ROOT"] == expected_root
    assert "AGENT_DISABLE_ENGINE_API_KEYS" not in os.environ
    assert calls == [
        (
            "run",
            {
                "task_input": "return ok",
                "role_spec": "General Assistant",
                "enable_build": False,
                "execution_mode": "approval",
                "pipeline_mode": "auto",
            },
        )
    ]
    for name in (
        "AGENT_CHAT_PROVIDER",
        "AGENT_CODEX_CLI_COMMAND",
        "AGENT_PROJECTS_DIR",
        "AGENT_PROJECT_ROOT",
        "AGENT_PROJECT_ID",
        "AGENT_AUTO_INSTALL_CLI",
        "AGENT_DISABLE_ENGINE_API_KEYS",
    ):
        os.environ.pop(name, None)


def test_run_factory_cli_rejects_provider_command_without_provider(monkeypatch):
    calls = []
    _install_fake_agent_launcher(monkeypatch, calls)

    import run_factory_cli

    cli = importlib.reload(run_factory_cli)

    with pytest.raises(SystemExit):
        cli.main(
            [
                "--project",
                "demo",
                "--task",
                "return ok",
                "--provider-command",
                r"C:\Tools\codex.cmd",
            ]
        )

    assert calls == []


def test_run_factory_cli_chat_passes_pipeline_options(monkeypatch, tmp_path):
    calls = []

    def _fake_run_interactive(**kwargs):
        calls.append(kwargs)

    fake_chat = types.ModuleType("core.interactive_chat")
    fake_chat.run_interactive = _fake_run_interactive
    monkeypatch.setitem(sys.modules, "core.interactive_chat", fake_chat)

    import run_factory_cli

    cli = importlib.reload(run_factory_cli)
    cli.main(
        [
            "--project",
            "demo",
            "--chat",
            "--role",
            "Frontend Architect",
            "--model",
            "gpt-5.4",
            "--mode",
            "fsa",
            "--build",
            "--pipeline",
            "project",
            "--projects-root",
            str(tmp_path / "projects"),
        ]
    )

    assert len(calls) == 1
    assert calls[0]["project_id"] == "demo"
    assert calls[0]["role"] == "Frontend Architect"
    assert calls[0]["model"] == "gpt-5.4"
    assert calls[0]["auto_approve"] is True
    assert calls[0]["execution_mode"] == "fsa"
    assert calls[0]["pipeline_mode"] == "project"
    assert calls[0]["enable_build"] is True
