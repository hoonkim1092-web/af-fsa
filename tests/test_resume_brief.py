import json
from pathlib import Path

from scripts.write_resume_brief import refresh_resume_briefs


def test_write_resume_brief_summarizes_manifest_todo_and_latest_session(tmp_path: Path):
    from core.continuity.resume_brief import write_resume_brief

    workspace = tmp_path / "workspace"
    session_dir = workspace / ".af_runtime" / "cli_sessions"
    session_dir.mkdir(parents=True, exist_ok=True)

    (workspace / ".af_manifest.json").write_text(
        json.dumps(
            {
                "project_desc": "ship CLI continuity",
                "roles": ["lilith", "dev"],
                "state_board": {
                    "current_status": "running",
                    "completed_subtasks": [{"role": "pm", "subtask": "design sync flow"}],
                    "failed_subtasks": [{"role": "dev", "subtask": "e2e claude", "reason": "missing binary"}],
                    "interrupted_subtasks": [{"role": "dev", "subtask": "wire resume automation"}],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / ".todo.md").write_text("- [ ] rerun claude e2e\n- [ ] verify gemini hooks\n", encoding="utf-8")
    (session_dir / "claude_cli_run_1.json").write_text(
        json.dumps(
            {
                "provider_id": "claude_cli",
                "run_id": "run_1",
                "updated_at": "2026-03-09T12:34:56+00:00",
                "session_id": "session-1",
                "last_response_excerpt": "Need to rerun after installing claude.",
                "transcript_path": str(workspace / ".af_runtime" / "transcript.jsonl"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    path = write_resume_brief(workspace, trigger="sync")
    content = path.read_text(encoding="utf-8")

    assert path == workspace / "resume_brief.md"
    assert "# Resume Brief" in content or "# 작업 재개 요약" in content
    assert "ship CLI continuity" in content
    assert "wire resume automation" in content
    assert "rerun claude e2e" in content
    assert "claude_cli" in content
    assert "Need to rerun after installing claude." in content


def test_refresh_resume_briefs_resolves_collision_safe_project_root(tmp_path: Path):
    repo_root = tmp_path / "agent-factory"
    project_root = repo_root / "projects" / "agent_factory"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / ".af_manifest.json").write_text(
        json.dumps(
            {
                "project_desc": "project-local state",
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

    results = refresh_resume_briefs(repo_root=repo_root, project_inputs=["agent-factory"])

    assert results[0]["project_id"] == "project_agent_factory"
    assert results[0]["resume_brief_path"].endswith("projects/agent_factory/resume_brief.md")
    assert (project_root / "resume_brief.md").exists()
