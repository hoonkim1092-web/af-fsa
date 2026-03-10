import json
import os
import subprocess
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
    assert "permissions" in settings
    assert "Bash(rm:*)" in settings["permissions"]["deny"]
    assert "Bash(git reset --hard:*)" in settings["permissions"]["deny"]
    assert "PYTHONPATH" in prepared["env"]
    assert prepared["env"]["PYTHONPATH"]
    assert state["provider_id"] == "claude_cli"
    assert state["run_id"] == "run_claude_1"
    assert state["destructive_guard_mode"] == "native_deny_rules"


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
    guard_path = Path(prepared["guard_path"])
    guard_text = guard_path.read_text(encoding="utf-8")

    assert prepared["mode"] == "native_hooks"
    assert defaults_path.exists()
    assert settings["hooks"]["SessionStart"]
    assert settings["hooks"]["BeforeAgent"]
    assert settings["hooks"]["AfterAgent"]
    assert settings["hooks"]["PreCompress"]
    assert settings["hooks"]["SessionEnd"]
    assert str(guard_path) in settings["policyPaths"]
    assert guard_path.exists()
    assert 'include_tools = ["run_shell_command"]' in guard_text
    assert 'pattern = "git"' in guard_text
    assert "PYTHONPATH" in prepared["env"]
    assert prepared["env"]["PYTHONPATH"]


def test_prepare_cli_session_sets_codex_shell_guard_on_windows(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    prepared = prepare_cli_session(
        CliChatRequest(
            provider_id="codex_cli",
            model="gpt-5",
            system_prompt="system prompt",
            task_input="ship feature",
            workspace=str(workspace),
            run_id="run_codex_guard",
        ),
        ["codex", "exec", "ship feature"],
    )

    state = _read_json(Path(prepared["state_path"]))
    codex_home = Path(prepared["env"]["CODEX_HOME"])
    sessions_root = Path(prepared["env"]["CODEX_SESSIONS_ROOT"])
    temp_root = Path(prepared["env"]["TEMP"])
    assert codex_home.exists()
    assert sessions_root.exists()
    assert temp_root.exists()
    assert prepared["env"]["TMP"] == str(temp_root)
    if os.name != "nt":
        assert prepared["guard_dir"] == ""
        assert state["destructive_guard_mode"] == "system_prompt_contract"
        return

    guard_dir = Path(prepared["guard_dir"])
    assert guard_dir.exists()
    assert (guard_dir / "cmd.cmd").exists()
    assert (guard_dir / "git.cmd").exists()
    assert (guard_dir / "powershell.cmd").exists()
    assert prepared["env"]["COMSPEC"] == str((guard_dir / "cmd.cmd").resolve())
    assert prepared["env"]["PATH"].split(os.pathsep)[0] == str(guard_dir.resolve())
    assert state["guard_dir"] == str(guard_dir)
    assert state["codex_home"] == str(codex_home)
    assert state["sessions_root"] == str(sessions_root)
    assert state["destructive_guard_mode"] == "shell_proxy_and_system_prompt_contract"


def test_prepare_cli_session_seeds_codex_auth_files(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    source_home = tmp_path / "source_home" / ".codex"
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / "auth.json").write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
    (source_home / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
    (source_home / "cap_sid").write_text("cap-session", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    prepared = prepare_cli_session(
        CliChatRequest(
            provider_id="codex_cli",
            model="gpt-5",
            system_prompt="system prompt",
            task_input="ship feature",
            workspace=str(workspace),
            run_id="run_codex_seed",
        ),
        ["codex", "exec", "ship feature"],
    )

    runtime_home = Path(prepared["env"]["CODEX_HOME"])
    state = _read_json(Path(prepared["state_path"]))

    assert runtime_home != source_home
    assert (runtime_home / "auth.json").read_text(encoding="utf-8") == '{"auth_mode":"chatgpt"}'
    assert (runtime_home / "config.toml").read_text(encoding="utf-8") == 'model = "gpt-5"\n'
    assert (runtime_home / "cap_sid").read_text(encoding="utf-8") == "cap-session"
    assert state["source_codex_home"] == str(source_home.resolve())
    assert state["seeded_auth_files"] == ["auth.json", "config.toml", "cap_sid"]


def test_codex_shell_guard_blocks_destructive_commands_on_windows(tmp_path: Path):
    if os.name != "nt":
        return

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    prepared = prepare_cli_session(
        CliChatRequest(
            provider_id="codex_cli",
            model="gpt-5",
            system_prompt="system prompt",
            task_input="ship feature",
            workspace=str(workspace),
            run_id="run_codex_guard_exec",
        ),
        ["codex", "exec", "ship feature"],
    )
    env = os.environ.copy()
    env.update(prepared["env"])

    comspec = prepared["env"]["COMSPEC"]
    guard_dir = Path(prepared["guard_dir"])
    blocked_stdout = workspace / "blocked_stdout.txt"
    blocked_stderr = workspace / "blocked_stderr.txt"
    allowed_stdout = workspace / "allowed_stdout.txt"
    allowed_stderr = workspace / "allowed_stderr.txt"
    process_stdout = workspace / "process_stdout.txt"
    process_stderr = workspace / "process_stderr.txt"

    with blocked_stdout.open("w", encoding="utf-8") as blocked_out, blocked_stderr.open("w", encoding="utf-8") as blocked_err:
        blocked_shell = subprocess.run(
            [comspec, "/c", "git reset --hard"],
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=blocked_out,
            stderr=blocked_err,
            text=True,
            check=False,
        )
    with allowed_stdout.open("w", encoding="utf-8") as allowed_out, allowed_stderr.open("w", encoding="utf-8") as allowed_err:
        allowed_shell = subprocess.run(
            [comspec, "/c", "echo hello"],
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=allowed_out,
            stderr=allowed_err,
            text=True,
            check=False,
        )
    with process_stdout.open("w", encoding="utf-8") as process_out, process_stderr.open("w", encoding="utf-8") as process_err:
        blocked_process = subprocess.run(
            [str(guard_dir / "powershell.cmd"), "-Command", "Remove-Item foo -Force"],
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=process_out,
            stderr=process_err,
            text=True,
            check=False,
        )

    assert blocked_shell.returncode == 126
    assert "destructive guard blocked command" in blocked_stderr.read_text(encoding="utf-8").lower()
    assert allowed_shell.returncode == 0
    assert "hello" in allowed_stdout.read_text(encoding="utf-8").lower()
    assert blocked_process.returncode == 126
    assert "remove-item" in process_stderr.read_text(encoding="utf-8").lower()


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
