import json
from pathlib import Path

from core.providers.cli import CliChatRequest
from core.providers.session_adapter import handle_hook_event, prepare_cli_session


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_prepare_cli_session_writes_claude_hook_settings(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    prepared = prepare_cli_session(
        CliChatRequest(
            provider_id="claude_cli",
            model="claude",
            system_prompt="system prompt",
            task_input="ship feature",
            workspace=str(workspace),
            run_id="run_claude_1",
        ),
        ["claude", "-p", "ship feature"],
    )

    settings_path = workspace / ".claude" / "settings.local.json"
    state = _read_json(Path(prepared["state_path"]))
    settings = _read_json(settings_path)

    assert prepared["mode"] == "native_hooks"
    assert settings_path.exists()
    assert settings["hooks"]["SessionStart"]
    assert settings["hooks"]["UserPromptSubmit"]
    assert settings["hooks"]["PreCompact"]
    assert settings["hooks"]["SessionEnd"]
    assert "PYTHONPATH" in prepared["env"]
    assert prepared["env"]["PYTHONPATH"]
    assert state["provider_id"] == "claude_cli"
    assert state["run_id"] == "run_claude_1"


def test_prepare_cli_session_routes_gemini_hooks_via_generated_defaults_file(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    prepared = prepare_cli_session(
        CliChatRequest(
            provider_id="gemini_cli",
            model="gemini",
            system_prompt="system prompt",
            task_input="ship feature",
            workspace=str(workspace),
            run_id="run_gemini_1",
        ),
        ["gemini", "-p", "ship feature"],
    )

    defaults_path = Path(prepared["env"]["GEMINI_CLI_SYSTEM_DEFAULTS_PATH"])
    settings = _read_json(defaults_path)

    assert prepared["mode"] == "native_hooks"
    assert defaults_path.exists()
    assert settings["hooks"]["SessionStart"]
    assert settings["hooks"]["BeforeAgent"]
    assert settings["hooks"]["AfterAgent"]
    assert settings["hooks"]["PreCompress"]
    assert settings["hooks"]["SessionEnd"]
    assert "PYTHONPATH" in prepared["env"]
    assert prepared["env"]["PYTHONPATH"]


def test_handle_hook_event_returns_context_and_runs_bridge(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".af_manifest.json").write_text(
        json.dumps(
            {
                "state_board": {
                    "current_status": "active",
                    "completed_subtasks": ["a"],
                    "failed_subtasks": [],
                    "interrupted_subtasks": [{"role": "frontend", "subtask": "wire ui"}],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / ".todo.md").write_text("- [ ] wire ui\n- [ ] add tests\n", encoding="utf-8")

    bridge_calls = []

    def fake_run_bridge(provider_id, repo_root, sessions_root=None, bootstrap_limit=120, max_write=240):
        bridge_calls.append(
            {
                "provider_id": provider_id,
                "repo_root": str(repo_root),
                "sessions_root": str(sessions_root),
            }
        )
        return {"ok": True, "written": 1}

    monkeypatch.setattr("core.providers.session_adapter.run_bridge", fake_run_bridge)

    context_output = handle_hook_event(
        "claude",
        {
            "hook_event_name": "SessionStart",
            "session_id": "session-1",
            "transcript_path": str(tmp_path / "sessions" / "claude" / "transcript.jsonl"),
        },
        workspace=str(workspace),
        run_id="run_claude_1",
        repo_root=str(tmp_path / "repo"),
    )

    assert "additionalContext" in context_output["hookSpecificOutput"]
    assert "current_status: active" in context_output["hookSpecificOutput"]["additionalContext"]
    assert "wire ui" in context_output["hookSpecificOutput"]["additionalContext"]

    handle_hook_event(
        "claude",
        {
            "hook_event_name": "SessionEnd",
            "session_id": "session-1",
            "transcript_path": str(tmp_path / "sessions" / "claude" / "transcript.jsonl"),
            "stop_hook_active": False,
        },
        workspace=str(workspace),
        run_id="run_claude_1",
        repo_root=str(tmp_path / "repo"),
    )

    assert bridge_calls
    assert bridge_calls[-1]["provider_id"] == "claude"
    assert bridge_calls[-1]["sessions_root"].endswith(str(Path("sessions") / "claude"))


def test_handle_hook_event_skips_gemini_context_in_headless_mode(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    prepare_cli_session(
        CliChatRequest(
            provider_id="gemini_cli",
            model="gemini",
            system_prompt="system prompt",
            task_input="reply exactly",
            workspace=str(workspace),
            run_id="run_gemini_headless",
        ),
        ["gemini", "-p", "reply exactly"],
    )

    output = handle_hook_event(
        "gemini",
        {
            "hook_event_name": "SessionStart",
            "session_id": "session-1",
            "transcript_path": str(tmp_path / "sessions" / "gemini" / "transcript.json"),
        },
        workspace=str(workspace),
        run_id="run_gemini_headless",
        repo_root=str(tmp_path / "repo"),
    )

    assert output is None


def test_handle_hook_event_keeps_gemini_context_for_interactive_sessions(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".todo.md").write_text("- [ ] resume prior task\n", encoding="utf-8")

    prepare_cli_session(
        CliChatRequest(
            provider_id="gemini_cli",
            model="gemini",
            system_prompt="system prompt",
            task_input="resume work",
            workspace=str(workspace),
            run_id="run_gemini_interactive",
        ),
        ["gemini"],
    )

    output = handle_hook_event(
        "gemini",
        {
            "hook_event_name": "SessionStart",
            "session_id": "session-2",
            "transcript_path": str(tmp_path / "sessions" / "gemini" / "transcript.json"),
        },
        workspace=str(workspace),
        run_id="run_gemini_interactive",
        repo_root=str(tmp_path / "repo"),
    )

    assert "additionalContext" in output["hookSpecificOutput"]
    assert "resume prior task" in output["hookSpecificOutput"]["additionalContext"]
