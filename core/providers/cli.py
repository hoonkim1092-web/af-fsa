from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable

from core.providers.session_adapter import finalize_cli_session, prepare_cli_session


@dataclass(frozen=True)
class CliChatRequest:
    provider_id: str
    model: str
    system_prompt: str
    task_input: str
    workspace: str
    run_id: str = ""
    timeout_sec: int = 600
    auto_approve: bool = False


@dataclass(frozen=True)
class CliProviderSpec:
    provider_id: str
    default_command: tuple[str, ...]
    command_env: str
    prompt_flag: str | None = None
    model_flag: str | None = None
    system_prompt_flag: str | None = None
    output_format_flags: tuple[str, ...] = ()
    fixed_flags: tuple[str, ...] = ()
    combine_system_prompt: bool = False


_CLI_SPECS = {
    "claude_cli": CliProviderSpec(
        provider_id="claude_cli",
        default_command=("claude",),
        command_env="AGENT_CLAUDE_CLI_COMMAND",
        prompt_flag="-p",
        model_flag="--model",
        system_prompt_flag="--append-system-prompt",
        output_format_flags=("--output-format", "json"),
    ),
    "gemini_cli": CliProviderSpec(
        provider_id="gemini_cli",
        default_command=("gemini",),
        command_env="AGENT_GEMINI_CLI_COMMAND",
        prompt_flag="-p",
        model_flag="-m",
        output_format_flags=("--output-format", "json"),
        combine_system_prompt=True,
    ),
    "codex_cli": CliProviderSpec(
        provider_id="codex_cli",
        default_command=("codex", "exec"),
        command_env="AGENT_CODEX_CLI_COMMAND",
        model_flag="-m",
        fixed_flags=("-c", 'model_reasoning_effort="low"'),
        combine_system_prompt=True,
    ),
}


def get_cli_provider_spec(provider_id: str) -> CliProviderSpec:
    key = str(provider_id or "").strip().lower()
    try:
        return _CLI_SPECS[key]
    except KeyError as exc:
        raise ValueError(f"unsupported_cli_provider:{provider_id}") from exc


def _split_command_template(text: str) -> list[str]:
    return shlex.split(text, posix=(os.name != "nt"))


def _resolve_base_command(spec: CliProviderSpec) -> list[str]:
    override = str(os.getenv(spec.command_env, "") or "").strip()
    if override:
        override_parts = _split_command_template(override)
        if len(override_parts) == 1 and len(spec.default_command) > 1:
            return override_parts + list(spec.default_command[1:])
        return override_parts
    return list(spec.default_command)


def _compose_prompt(request: CliChatRequest, spec: CliProviderSpec) -> str:
    task_text = str(request.task_input or "").strip()
    workspace_text = str(request.workspace or "").strip()
    if spec.combine_system_prompt and request.system_prompt:
        return (
            f"[System Prompt]\n{request.system_prompt.strip()}\n\n"
            f"[Workspace]\n{workspace_text}\n\n"
            f"[Task]\n{task_text}"
        ).strip()
    return f"[Workspace]\n{workspace_text}\n\n[Task]\n{task_text}".strip()


def build_cli_command(request: CliChatRequest) -> list[str]:
    spec = get_cli_provider_spec(request.provider_id)
    cmd = _resolve_base_command(spec)

    if request.model and spec.model_flag:
        cmd.extend([spec.model_flag, str(request.model)])
    if spec.fixed_flags:
        cmd.extend(spec.fixed_flags)
    if spec.output_format_flags:
        cmd.extend(spec.output_format_flags)
    if request.system_prompt and spec.system_prompt_flag and not spec.combine_system_prompt:
        cmd.extend([spec.system_prompt_flag, str(request.system_prompt)])

    prompt_text = _compose_prompt(request, spec)
    if spec.prompt_flag:
        cmd.extend([spec.prompt_flag, prompt_text])
    else:
        cmd.append(prompt_text)
    return cmd


def _extract_text(stdout: str) -> str:
    raw = str(stdout or "").strip()
    if not raw:
        return ""

    candidates = [raw]
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if lines:
        candidates.append(lines[-1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            for key in ("result", "text", "content", "message", "output"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(payload, list):
            parts = [item for item in payload if isinstance(item, str) and item.strip()]
            if parts:
                return "\n".join(parts)
    return raw


def execute_cli_chat(
    request: CliChatRequest,
    run_command: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    cmd = build_cli_command(request)
    prepared = prepare_cli_session(request, cmd)
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    env.update(prepared.get("env", {}))
    runner = run_command or subprocess.run

    try:
        completed = runner(
            cmd,
            cwd=str(request.workspace),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=max(1, int(request.timeout_sec)),
        )
    except FileNotFoundError as exc:
        result = {
            "ok": False,
            "provider_id": request.provider_id,
            "reason": f"cli_command_not_found:{cmd[0]}",
            "stdout": "",
            "stderr": str(exc),
            "text": "",
            "returncode": None,
            "command": cmd,
        }
        finalize_cli_session(request, prepared, result)
        return result
    except subprocess.TimeoutExpired as exc:
        result = {
            "ok": False,
            "provider_id": request.provider_id,
            "reason": "cli_timeout",
            "stdout": str(getattr(exc, "stdout", "") or ""),
            "stderr": str(getattr(exc, "stderr", "") or ""),
            "text": "",
            "returncode": None,
            "command": cmd,
        }
        finalize_cli_session(request, prepared, result)
        return result

    text = _extract_text(completed.stdout)
    ok = completed.returncode == 0 and bool(text.strip())
    result = {
        "ok": ok,
        "provider_id": request.provider_id,
        "reason": request.provider_id if ok else f"{request.provider_id}_failed",
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "text": text,
        "returncode": completed.returncode,
        "command": cmd,
    }
    finalize_cli_session(request, prepared, result)
    return result
