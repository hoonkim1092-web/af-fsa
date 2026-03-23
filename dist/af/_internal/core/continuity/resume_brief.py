from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.documentation_policy import resume_brief_strings
from core.continuity.runtime_paths import workspace_runtime_dir


RESUME_BRIEF_FILENAME = "resume_brief.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_manifest(workspace_path: Path) -> dict[str, Any]:
    return _load_json(workspace_path / ".af_manifest.json")


def _open_todos(workspace_path: Path, limit: int = 8) -> list[str]:
    todo_path = workspace_path / ".todo.md"
    if not todo_path.exists():
        return []
    items: list[str] = []
    for line in todo_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ] "):
            items.append(stripped[6:].strip())
        if len(items) >= limit:
            break
    return items


def _sort_session_state(payload: dict[str, Any], path: Path) -> tuple[str, int]:
    timestamp = ""
    for key in ("updated_at", "completed_at", "prepared_at"):
        candidate = str(payload.get(key, "") or "").strip()
        if candidate:
            timestamp = candidate
            break
    try:
        mtime_ns = int(path.stat().st_mtime_ns)
    except Exception:
        mtime_ns = 0
    return (timestamp, mtime_ns)


def _latest_session_state(workspace_path: Path) -> tuple[Path | None, dict[str, Any]]:
    sessions_root = workspace_runtime_dir(workspace_path) / "cli_sessions"
    if not sessions_root.exists():
        return None, {}

    latest_path: Path | None = None
    latest_payload: dict[str, Any] = {}
    latest_key = ("", -1)

    for path in sessions_root.glob("*.json"):
        payload = _load_json(path)
        if not isinstance(payload, dict) or not payload:
            continue
        key = _sort_session_state(payload, path)
        if key > latest_key:
            latest_key = key
            latest_path = path
            latest_payload = payload
    return latest_path, latest_payload


def _format_task(item: Any, include_reason: bool = False) -> str:
    if isinstance(item, dict):
        role = str(item.get("role", "") or "").strip()
        subtask = str(item.get("subtask", "") or "").strip()
        reason = str(item.get("reason", "") or "").strip()
        left = f"{role}: {subtask}".strip(": ").strip()
        if include_reason and reason:
            return f"{left} ({reason})" if left else reason
        return left or reason or json.dumps(item, ensure_ascii=False)
    return str(item).strip()


def build_resume_brief(workspace: str | Path, trigger: str = "manual") -> str:
    workspace_path = Path(workspace).resolve()
    strings = resume_brief_strings()
    labels = dict(strings["labels"])
    manifest = _load_manifest(workspace_path)
    state_board = manifest.get("state_board", {}) if isinstance(manifest, dict) else {}
    if not isinstance(state_board, dict):
        state_board = {}

    roles = [str(role).strip() for role in manifest.get("roles", []) if str(role).strip()]
    completed = list(state_board.get("completed_subtasks", []) or [])
    failed = list(state_board.get("failed_subtasks", []) or [])
    interrupted = list(state_board.get("interrupted_subtasks", []) or [])
    todos = _open_todos(workspace_path)
    session_path, session_state = _latest_session_state(workspace_path)

    lines = [
        f"# {strings['title']}",
        "",
        f"- {labels['generated_at']}: {_now_iso()}",
        f"- {labels['trigger']}: {str(trigger or 'manual').strip()}",
        f"- {labels['workspace']}: `{workspace_path}`",
    ]

    project_desc = str(manifest.get("project_desc", "") or "").strip()
    current_status = str(state_board.get("current_status", "") or "").strip()
    if project_desc:
        lines.append(f"- {labels['project']}: {project_desc}")
    if roles:
        lines.append(f"- {labels['roles']}: {', '.join(roles)}")
    if current_status:
        lines.append(f"- {labels['status']}: {current_status}")
    lines.extend(
        [
            f"- {labels['completed_count']}: {len(completed)}",
            f"- {labels['failed_count']}: {len(failed)}",
            f"- {labels['interrupted_count']}: {len(interrupted)}",
        ]
    )

    if interrupted:
        lines.extend(["", f"## {labels['interrupted_subtasks']}"])
        for item in interrupted[:5]:
            text = _format_task(item)
            if text:
                lines.append(f"- {text}")

    if failed:
        lines.extend(["", f"## {labels['recent_failures']}"])
        for item in failed[:5]:
            text = _format_task(item, include_reason=True)
            if text:
                lines.append(f"- {text}")

    if todos:
        lines.extend(["", f"## {labels['open_todos']}"])
        for todo in todos:
            lines.append(f"- {todo}")

    if session_state:
        lines.extend(["", f"## {labels['latest_cli_session']}"])
        provider_id = str(session_state.get("provider_id", "") or "").strip()
        run_id = str(session_state.get("run_id", "") or "").strip()
        model = str(session_state.get("model", "") or "").strip()
        session_id = str(session_state.get("session_id", "") or "").strip()
        transcript_path = str(session_state.get("transcript_path", "") or "").strip()
        last_response = str(session_state.get("last_response_excerpt", "") or "").strip()
        if provider_id:
            lines.append(f"- {labels['provider']}: `{provider_id}`")
        if run_id:
            lines.append(f"- {labels['run_id']}: `{run_id}`")
        if model:
            lines.append(f"- {labels['model']}: `{model}`")
        if session_id:
            lines.append(f"- {labels['session_id']}: `{session_id}`")
        if transcript_path:
            lines.append(f"- {labels['transcript']}: `{transcript_path}`")
        if session_path is not None:
            lines.append(f"- {labels['state_file']}: `{session_path.name}`")
        if last_response:
            lines.extend(["", f"### {labels['last_response_excerpt']}", last_response])

    return "\n".join(lines).strip() + "\n"


def write_resume_brief(
    workspace: str | Path,
    trigger: str = "manual",
    output_path: str | Path | None = None,
) -> Path:
    workspace_path = Path(workspace).resolve()
    target = Path(output_path).resolve() if output_path else (workspace_path / RESUME_BRIEF_FILENAME)
    content = build_resume_brief(workspace_path, trigger=trigger)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            if target.read_text(encoding="utf-8") == content:
                return target
        except Exception:
            pass
    target.write_text(content, encoding="utf-8")
    return target


def read_resume_brief_excerpt(workspace: str | Path, max_chars: int = 1600) -> str:
    path = Path(workspace).resolve() / RESUME_BRIEF_FILENAME
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not text:
        return ""
    return text[: max(0, int(max_chars))]


__all__ = [
    "RESUME_BRIEF_FILENAME",
    "build_resume_brief",
    "read_resume_brief_excerpt",
    "write_resume_brief",
]
