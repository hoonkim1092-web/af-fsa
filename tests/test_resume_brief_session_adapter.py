import json
from pathlib import Path

from core.providers.cli import CliChatRequest
from core.providers.session_adapter import finalize_cli_session, prepare_cli_session


def test_finalize_cli_session_updates_resume_brief(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".af_manifest.json").write_text(
        json.dumps(
            {
                "project_desc": "finalize session",
                "state_board": {
                    "current_status": "running",
                    "completed_subtasks": [],
                    "failed_subtasks": [],
                    "interrupted_subtasks": [],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    bridge_calls = []

    def fake_run_bridge(provider_id, repo_root, sessions_root=None, bootstrap_limit=120, max_write=240):
        bridge_calls.append(
            {
                "provider_id": provider_id,
                "repo_root": str(repo_root),
                "sessions_root": str(sessions_root),
            }
        )
        return {"ok": True, "written": 0}

    monkeypatch.setattr("core.providers.session_adapter.run_bridge", fake_run_bridge)

    request = CliChatRequest(
        provider_id="codex_cli",
        model="gpt-5",
        system_prompt="system",
        task_input="continue work",
        workspace=str(workspace),
        run_id="run_resume",
    )
    prepared = prepare_cli_session(request, ["codex", "exec", "continue work"])

    finalize_cli_session(
        request,
        prepared,
        {
            "ok": True,
            "reason": "codex_cli",
            "returncode": 0,
            "stdout": "stdout",
            "stderr": "",
            "text": "Continue from the last successful codex run.",
        },
    )

    brief = (workspace / "resume_brief.md").read_text(encoding="utf-8")
    assert "finalize session" in brief
    assert "codex_cli" in brief
    assert "Continue from the last successful codex run." in brief
    assert bridge_calls
    assert bridge_calls[0]["provider_id"] == "codex"
    assert bridge_calls[0]["sessions_root"].endswith(str(Path(".af_runtime") / "codex_home" / "sessions"))
