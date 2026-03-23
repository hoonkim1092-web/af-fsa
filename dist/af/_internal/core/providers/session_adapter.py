from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.continuity.resume_brief import read_resume_brief_excerpt, write_resume_brief
from core.continuity.runtime_paths import workspace_runtime_dir
from core.destructive_guard import (
    attach_gemini_policy_path,
    merge_claude_destructive_guard,
    write_gemini_destructive_policy,
)
from scripts.session_bridge import run_bridge


def _safe_slug(text: str, fallback: str = "item") -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text or "").strip())
    collapsed = "_".join(part for part in raw.split("_") if part)
    return collapsed or fallback


def _quote_command(parts: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return " ".join(__import__("shlex").quote(part) for part in parts)


def _merge_pythonpath(repo_root: Path) -> str:
    existing = str(os.getenv("PYTHONPATH", "") or "").strip()
    parts = [str(repo_root)]
    if existing:
        parts.extend(part for part in existing.split(os.pathsep) if part)
    return os.pathsep.join(dict.fromkeys(parts))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepend_path(entry: str, existing: str) -> str:
    parts = [str(entry or "").strip()]
    if existing:
        parts.extend(part for part in str(existing).split(os.pathsep) if part)
    return os.pathsep.join(dict.fromkeys(part for part in parts if part))


def _prepend_pathext(*extensions: str, existing: str) -> str:
    wanted = [str(ext or "").strip().upper() for ext in extensions if str(ext or "").strip()]
    current = [part.strip().upper() for part in str(existing or "").split(";") if part.strip()]
    merged = list(dict.fromkeys([*wanted, *current]))
    return ";".join(merged)


@dataclass(frozen=True)
class CliSessionSpec:
    provider_id: str
    bridge_provider_id: str
    mode: str
    session_state_filename: str
    hook_events: tuple[str, ...] = ()


SPECS: dict[str, CliSessionSpec] = {
    "claude_cli": CliSessionSpec(
        provider_id="claude_cli",
        bridge_provider_id="claude",
        mode="native_hooks",
        session_state_filename="settings.local.json",
        hook_events=("SessionStart", "UserPromptSubmit", "PreCompact", "Stop", "SessionEnd"),
    ),
    "gemini_cli": CliSessionSpec(
        provider_id="gemini_cli",
        bridge_provider_id="gemini",
        mode="native_hooks",
        session_state_filename="settings.json",
        hook_events=("SessionStart", "BeforeAgent", "AfterAgent", "PreCompress", "SessionEnd"),
    ),
    "codex_cli": CliSessionSpec(
        provider_id="codex_cli",
        bridge_provider_id="codex",
        mode="wrapper_bridge",
        session_state_filename="session.json",
        hook_events=(),
    ),
}

_CODEX_RUNTIME_SEED_FILES: tuple[str, ...] = ("auth.json", "config.toml", "cap_sid")


def get_cli_session_spec(provider_id: str) -> CliSessionSpec:
    key = str(provider_id or "").strip().lower()
    try:
        return SPECS[key]
    except KeyError as exc:
        raise ValueError(f"unsupported_cli_session_provider:{provider_id}") from exc


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_paths(provider_id: str, workspace: str, run_id: str) -> dict[str, Path]:
    runtime_dir = workspace_runtime_dir(workspace)
    slug = _safe_slug(run_id, fallback="run")
    base = runtime_dir / "cli_sessions"
    return {
        "runtime_dir": runtime_dir,
        "base_dir": base,
        "state_path": base / f"{_safe_slug(provider_id)}_{slug}.json",
        "events_path": base / f"{_safe_slug(provider_id)}_{slug}_events.jsonl",
        "gemini_defaults_path": base / f"{_safe_slug(provider_id)}_{slug}_gemini_defaults.json",
        "gemini_policy_path": base / f"{_safe_slug(provider_id)}_{slug}_destructive_guard.toml",
        "codex_guard_dir": base / f"{_safe_slug(provider_id)}_{slug}_shell_guard",
    }


def _extract_open_todos(workspace_path: Path, limit: int = 8) -> list[str]:
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


def _build_continuity_context(workspace: str, provider_id: str, run_id: str) -> str:
    workspace_path = Path(workspace).resolve()
    manifest_path = workspace_path / ".af_manifest.json"
    lines = [
        "[Agent Factory Continuity]",
        f"provider: {provider_id}",
        f"run_id: {run_id}",
        f"workspace: {workspace_path}",
    ]

    manifest = _load_json(manifest_path)
    state_board = manifest.get("state_board", {}) if isinstance(manifest, dict) else {}
    if isinstance(state_board, dict) and state_board:
        lines.append(f"current_status: {state_board.get('current_status', '')}")
        lines.append(f"completed_count: {len(state_board.get('completed_subtasks', []) or [])}")
        lines.append(f"failed_count: {len(state_board.get('failed_subtasks', []) or [])}")
        interrupted = state_board.get("interrupted_subtasks", []) or []
        if interrupted:
            lines.append("interrupted_subtasks:")
            for item in interrupted[:5]:
                if isinstance(item, dict):
                    role = str(item.get("role", "")).strip()
                    subtask = str(item.get("subtask", "")).strip()
                    lines.append(f"- {role}: {subtask}".rstrip(": "))
                else:
                    lines.append(f"- {str(item).strip()}")

    todos = _extract_open_todos(workspace_path)
    if todos:
        lines.append("open_todos:")
        for todo in todos:
            lines.append(f"- {todo}")

    session_paths = _runtime_paths(provider_id, workspace, run_id)
    state = _load_json(session_paths["state_path"])
    last_response = str(state.get("last_response_excerpt", "")).strip()
    if last_response:
        lines.append("last_response_excerpt:")
        lines.append(last_response)

    resume_brief = read_resume_brief_excerpt(workspace_path)
    if resume_brief:
        lines.append("resume_brief:")
        lines.append(resume_brief)

    return "\n".join(line for line in lines if str(line).strip()).strip()


def _hook_command(provider_base: str, workspace: str, run_id: str, repo_root: Path) -> str:
    script_path = repo_root / "scripts" / "cli_hook_bridge.py"
    return _quote_command(
        [
            sys.executable,
            str(script_path),
            "--provider",
            provider_base,
            "--workspace",
            str(Path(workspace).resolve()),
            "--run-id",
            str(run_id),
            "--repo-root",
            str(repo_root),
        ]
    )


def _merge_named_hook_group(existing_groups: list[Any], hook_name: str, command: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for raw_group in existing_groups or []:
        if not isinstance(raw_group, dict):
            continue
        hooks = []
        for hook in raw_group.get("hooks", []) or []:
            if not isinstance(hook, dict):
                continue
            if str(hook.get("name") or "") == hook_name:
                continue
            hooks.append(dict(hook))
        new_group = dict(raw_group)
        new_group["hooks"] = hooks
        groups.append(new_group)
    groups.append({"hooks": [{"type": "command", "name": hook_name, "command": command}]})
    return groups


def _write_claude_settings(workspace: str, run_id: str) -> Path:
    repo_root = _repo_root()
    settings_path = Path(workspace).resolve() / ".claude" / "settings.local.json"
    data = _load_json(settings_path)
    if not isinstance(data, dict):
        data = {}
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    command = _hook_command("claude", workspace, run_id, repo_root)
    for event_name in ("SessionStart", "UserPromptSubmit", "PreCompact", "Stop", "SessionEnd"):
        hook_name = f"agent_factory_claude_{event_name.lower()}"
        hooks[event_name] = _merge_named_hook_group(hooks.get(event_name, []), hook_name, command)

    data["hooks"] = hooks
    data = merge_claude_destructive_guard(data)
    _save_json(settings_path, data)
    return settings_path


def _write_gemini_defaults(workspace: str, run_id: str, defaults_path: Path, policy_path: Path) -> tuple[Path, Path]:
    repo_root = _repo_root()
    data = _load_json(defaults_path)
    if not isinstance(data, dict):
        data = {}
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    command = _hook_command("gemini", workspace, run_id, repo_root)
    for event_name in ("SessionStart", "BeforeAgent", "AfterAgent", "PreCompress", "SessionEnd"):
        hook_name = f"agent_factory_gemini_{event_name.lower()}"
        hooks[event_name] = _merge_named_hook_group(hooks.get(event_name, []), hook_name, command)

    data["hooks"] = hooks
    guard_path = write_gemini_destructive_policy(policy_path)
    data = attach_gemini_policy_path(data, guard_path)
    _save_json(defaults_path, data)
    return defaults_path, guard_path


def _resolve_delegate_path(command: str) -> str:
    if os.name == "nt" and str(command).lower() == "cmd":
        comspec = str(os.getenv("ComSpec", "") or os.getenv("COMSPEC", "")).strip()
        if comspec:
            return comspec
    for candidate in (command, f"{command}.exe", f"{command}.cmd"):
        found = shutil.which(candidate)
        if found:
            return found
    return command


def _write_codex_shell_guard(paths: dict[str, Path], repo_root: Path) -> tuple[Path, dict[str, str]]:
    guard_dir = paths["codex_guard_dir"]
    guard_dir.mkdir(parents=True, exist_ok=True)

    python_exe = str(Path(sys.executable).resolve())
    proxy_script = str((repo_root / "scripts" / "destructive_guard_proxy.py").resolve())
    delegates = {
        "cmd": _resolve_delegate_path("cmd"),
        "git": _resolve_delegate_path("git"),
        "powershell": _resolve_delegate_path("powershell"),
        "pwsh": _resolve_delegate_path("pwsh"),
    }

    def _wrapper_text(target: str, delegate: str) -> str:
        return (
            "@echo off\r\n"
            "setlocal\r\n"
            f"\"{python_exe}\" \"{proxy_script}\" --target {target} --delegate \"{delegate}\" -- %*\r\n"
            "exit /b %ERRORLEVEL%\r\n"
        )

    wrappers = {
        "cmd.cmd": _wrapper_text("cmd", delegates["cmd"]),
        "git.cmd": _wrapper_text("git", delegates["git"]),
        "powershell.cmd": _wrapper_text("powershell", delegates["powershell"]),
        "pwsh.cmd": _wrapper_text("pwsh", delegates["pwsh"]),
    }
    for filename, content in wrappers.items():
        (guard_dir / filename).write_text(content, encoding="ascii")

    env = {
        "COMSPEC": str((guard_dir / "cmd.cmd").resolve()),
        "PATH": _prepend_path(str(guard_dir.resolve()), str(os.getenv("PATH", "") or "")),
        "PATHEXT": _prepend_pathext(".CMD", ".BAT", existing=str(os.getenv("PATHEXT", "") or "")),
    }
    return guard_dir, env


def _prepare_codex_runtime_env(paths: dict[str, Path]) -> dict[str, str]:
    runtime_dir = Path(paths["runtime_dir"]).resolve()
    codex_home = runtime_dir / "codex_home"
    sessions_root = codex_home / "sessions"
    temp_root = codex_home / "tmp"
    for path in (codex_home, sessions_root, temp_root):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "CODEX_HOME": str(codex_home),
        "CODEX_SESSIONS_ROOT": str(sessions_root),
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
        "TMPDIR": str(temp_root),
    }


def _seed_codex_runtime_home(runtime_home: Path) -> dict[str, Any]:
    runtime_home = Path(runtime_home).resolve()
    candidates: list[Path] = []
    for raw in (str(os.getenv("CODEX_HOME", "") or "").strip(), str(Path.home() / ".codex")):
        if not raw:
            continue
        candidate = Path(os.path.expanduser(raw)).resolve()
        if candidate == runtime_home:
            continue
        if candidate not in candidates:
            candidates.append(candidate)

    seeded_files: list[str] = []
    source_home = ""
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        copied = False
        for name in _CODEX_RUNTIME_SEED_FILES:
            src = candidate / name
            if not src.is_file():
                continue
            try:
                shutil.copy2(src, runtime_home / name)
            except OSError:
                continue
            seeded_files.append(name)
            copied = True
        if copied:
            source_home = str(candidate)
            break

    return {
        "source_codex_home": source_home,
        "seeded_auth_files": seeded_files,
    }


def prepare_cli_session(request, command: list[str]) -> dict[str, Any]:
    spec = get_cli_session_spec(request.provider_id)
    workspace = str(Path(request.workspace).resolve())
    run_id = str(getattr(request, "run_id", "") or f"{spec.provider_id}_run")
    paths = _runtime_paths(spec.provider_id, workspace, run_id)
    repo_root = _repo_root()

    state = {
        "provider_id": spec.provider_id,
        "bridge_provider_id": spec.bridge_provider_id,
        "mode": spec.mode,
        "run_id": run_id,
        "workspace": workspace,
        "model": str(getattr(request, "model", "") or ""),
        "command": list(command),
        "task_preview": str(getattr(request, "task_input", "") or "")[:400],
        "prepared_at": _now_iso(),
    }

    env = {
        "AGENT_CLI_PROVIDER": spec.provider_id,
        "AGENT_CLI_RUN_ID": run_id,
        "AGENT_CLI_WORKSPACE": workspace,
        "AGENT_CLI_REPO_ROOT": str(repo_root),
        "AGENT_CLI_SESSION_STATE_PATH": str(paths["state_path"]),
        "PYTHONPATH": _merge_pythonpath(repo_root),
    }

    settings_path = None
    guard_path = None
    guard_dir = None
    if spec.provider_id == "claude_cli":
        settings_path = _write_claude_settings(workspace, run_id)
    elif spec.provider_id == "gemini_cli":
        settings_path, guard_path = _write_gemini_defaults(
            workspace,
            run_id,
            paths["gemini_defaults_path"],
            paths["gemini_policy_path"],
        )
        env["GEMINI_CLI_SYSTEM_DEFAULTS_PATH"] = str(settings_path)
    elif spec.provider_id == "codex_cli":
        env.update(_prepare_codex_runtime_env(paths))
        codex_seed = _seed_codex_runtime_home(Path(env["CODEX_HOME"]))
        if os.name == "nt":
            guard_dir, guard_env = _write_codex_shell_guard(paths, repo_root)
            env.update(guard_env)
    
    if settings_path is not None:
        state["settings_path"] = str(settings_path)
    if guard_path is not None:
        state["guard_path"] = str(guard_path)
    if guard_dir is not None:
        state["guard_dir"] = str(guard_dir)
    if spec.provider_id == "codex_cli":
        state["codex_home"] = env.get("CODEX_HOME", "")
        state["sessions_root"] = env.get("CODEX_SESSIONS_ROOT", "")
        state.update(codex_seed)
    state["destructive_guard_mode"] = (
        "native_deny_rules"
        if spec.provider_id in {"claude_cli", "gemini_cli"}
        else ("shell_proxy_and_system_prompt_contract" if guard_dir is not None else "system_prompt_contract")
    )

    _save_json(paths["state_path"], state)
    resume_path = write_resume_brief(workspace, trigger="session_prepare")
    return {
        "mode": spec.mode,
        "provider_id": spec.provider_id,
        "bridge_provider_id": spec.bridge_provider_id,
        "state_path": str(paths["state_path"]),
        "events_path": str(paths["events_path"]),
        "env": env,
        "settings_path": str(settings_path) if settings_path else "",
        "guard_path": str(guard_path) if guard_path else "",
        "guard_dir": str(guard_dir) if guard_dir else "",
        "resume_brief_path": str(resume_path),
    }


def finalize_cli_session(request, prepared: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    spec = get_cli_session_spec(request.provider_id)
    state_path = Path(str(prepared.get("state_path", "")).strip())
    state = _load_json(state_path)
    state.update(
        {
            "completed_at": _now_iso(),
            "ok": bool(result.get("ok", False)),
            "reason": str(result.get("reason", "") or ""),
            "returncode": result.get("returncode"),
            "stdout_excerpt": str(result.get("stdout", "") or "")[:1000],
            "stderr_excerpt": str(result.get("stderr", "") or "")[:1000],
            "last_response_excerpt": str(result.get("text", "") or "")[:1000],
        }
    )
    preflight = result.get("preflight")
    if isinstance(preflight, dict):
        state["preflight"] = preflight
    auto_install = result.get("auto_install")
    if isinstance(auto_install, dict):
        state["auto_install"] = auto_install

    bridge_result = None
    if spec.provider_id == "codex_cli":
        sessions_root = (
            str(prepared.get("env", {}).get("CODEX_SESSIONS_ROOT", "")).strip()
            if isinstance(prepared.get("env"), dict)
            else ""
        )
        bridge_result = run_bridge(
            spec.bridge_provider_id,
            repo_root=_repo_root(),
            sessions_root=sessions_root or None,
        )
        state["bridge_result"] = bridge_result

    _save_json(state_path, state)
    resume_path = write_resume_brief(request.workspace, trigger="session_finalize")
    return {"state_path": str(state_path), "bridge_result": bridge_result, "resume_brief_path": str(resume_path)}


def _event_name(payload: dict[str, Any]) -> str:
    for key in ("hook_event_name", "event", "hookEventName"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _assistant_excerpt(payload: dict[str, Any]) -> str:
    for key in ("last_assistant_message", "prompt_response", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:1000]
    return ""


def _is_headless_session(state: dict[str, Any]) -> bool:
    command = state.get("command")
    if not isinstance(command, list):
        return False
    parts = [str(part).strip() for part in command if str(part).strip()]
    return any(part in {"-p", "--prompt"} for part in parts)


def _hook_output(
    provider_base: str,
    event_name: str,
    context: str,
    *,
    headless: bool = False,
) -> dict[str, Any] | None:
    if not context:
        return None
    if provider_base == "claude" and event_name in {"SessionStart", "UserPromptSubmit"}:
        return {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": context,
            }
        }
    if provider_base == "gemini" and headless:
        return None
    if provider_base == "gemini" and event_name in {"SessionStart", "BeforeAgent"}:
        return {
            "hookSpecificOutput": {
                "additionalContext": context,
            }
        }
    return None


def handle_hook_event(
    provider_base: str,
    payload: dict[str, Any],
    workspace: str,
    run_id: str,
    repo_root: str | Path | None = None,
) -> dict[str, Any] | None:
    workspace_path = Path(workspace).resolve()
    repo_root_path = Path(repo_root).resolve() if repo_root else _repo_root()
    provider_id = f"{provider_base}_cli"
    paths = _runtime_paths(provider_id, str(workspace_path), run_id)
    state = _load_json(paths["state_path"])

    event_name = _event_name(payload)
    transcript_path = str(payload.get("transcript_path", "") or "").strip()
    session_id = str(payload.get("session_id", "") or "").strip()
    assistant_excerpt = _assistant_excerpt(payload)
    headless = _is_headless_session(state)

    state.update(
        {
            "provider_id": provider_id,
            "bridge_provider_id": provider_base,
            "run_id": run_id,
            "workspace": str(workspace_path),
            "last_event": event_name,
            "last_event_payload_keys": sorted(payload.keys()),
            "session_id": session_id or state.get("session_id", ""),
            "transcript_path": transcript_path or state.get("transcript_path", ""),
        }
    )
    if assistant_excerpt:
        state["last_response_excerpt"] = assistant_excerpt

    _append_jsonl(
        paths["events_path"],
        {
            "event": event_name,
            "payload": payload,
        },
    )

    bridge_result = None
    if event_name in {"PreCompact", "PreCompress", "SessionEnd"} and transcript_path:
        bridge_result = run_bridge(
            provider_base,
            repo_root=repo_root_path,
            sessions_root=Path(transcript_path).resolve().parent,
        )
        state["bridge_result"] = bridge_result

    state["updated_at"] = _now_iso()
    _save_json(paths["state_path"], state)
    write_resume_brief(workspace_path, trigger=event_name or "hook")

    context = _build_continuity_context(str(workspace_path), provider_id, run_id)
    return _hook_output(provider_base, event_name, context, headless=headless)


__all__ = [
    "CliSessionSpec",
    "finalize_cli_session",
    "get_cli_session_spec",
    "handle_hook_event",
    "prepare_cli_session",
]

