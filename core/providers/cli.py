from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
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
    install_command_env: str = ""
    install_package: str = ""
    prompt_flag: str | None = None
    model_flag: str | None = None
    system_prompt_flag: str | None = None
    output_format_flags: tuple[str, ...] = ()
    fixed_flags: tuple[str, ...] = ()
    combine_system_prompt: bool = False
    workspace_access_flag: str | None = None
    headless_edit_flags: tuple[str, ...] = ()


_CLI_SPECS = {
    "claude_cli": CliProviderSpec(
        provider_id="claude_cli",
        default_command=("claude",),
        command_env="AGENT_CLAUDE_CLI_COMMAND",
        install_command_env="AGENT_CLAUDE_CLI_INSTALL_COMMAND",
        install_package="@anthropic-ai/claude-code",
        prompt_flag="-p",
        model_flag="--model",
        system_prompt_flag="--append-system-prompt",
        output_format_flags=("--output-format", "json"),
        workspace_access_flag="--add-dir",
        headless_edit_flags=("--permission-mode", "acceptEdits"),
    ),
    "gemini_cli": CliProviderSpec(
        provider_id="gemini_cli",
        default_command=("gemini",),
        command_env="AGENT_GEMINI_CLI_COMMAND",
        install_command_env="AGENT_GEMINI_CLI_INSTALL_COMMAND",
        install_package="@google/gemini-cli",
        prompt_flag="-p",
        model_flag="-m",
        output_format_flags=("--output-format", "json"),
        combine_system_prompt=True,
        workspace_access_flag="--include-directories",
        headless_edit_flags=("--approval-mode", "auto_edit"),
    ),
    "codex_cli": CliProviderSpec(
        provider_id="codex_cli",
        default_command=("codex", "exec"),
        command_env="AGENT_CODEX_CLI_COMMAND",
        install_command_env="AGENT_CODEX_CLI_INSTALL_COMMAND",
        install_package="@openai/codex",
        model_flag="-m",
        fixed_flags=("-c", 'model_reasoning_effort="low"'),
        combine_system_prompt=True,
        workspace_access_flag="--add-dir",
        headless_edit_flags=("--full-auto",),
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
    resolved = _resolve_installed_command(spec.default_command)
    if resolved:
        return resolved
    return list(spec.default_command)


def _windows_roaming_npm_dir() -> str:
    appdata = str(os.getenv("APPDATA", "") or "").strip()
    if appdata:
        return os.path.join(appdata, "npm")
    home = str(os.path.expanduser("~") or "").strip()
    if home:
        return os.path.join(home, "AppData", "Roaming", "npm")
    return ""


def _resolve_installed_command(default_command: tuple[str, ...]) -> list[str] | None:
    if not default_command:
        return None

    executable = str(default_command[0]).strip()
    if not executable:
        return None

    found = shutil.which(executable)
    if found:
        return [found] + list(default_command[1:])

    if os.name == "nt":
        npm_dir = _windows_roaming_npm_dir()
        if npm_dir:
            for suffix in (".cmd", ".exe", ".bat"):
                candidate = os.path.join(npm_dir, executable + suffix)
                if os.path.exists(candidate):
                    return [candidate] + list(default_command[1:])

    return None


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off"}


def _default_install_command(spec: CliProviderSpec) -> list[str]:
    if not spec.install_package:
        return []
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    return [npm_cmd, "install", "-g", spec.install_package]


def _resolve_install_command(spec: CliProviderSpec) -> list[str]:
    override = str(os.getenv(spec.install_command_env, "") or "").strip()
    if override:
        return _split_command_template(override)
    return _default_install_command(spec)


def _should_auto_install(request: CliChatRequest, spec: CliProviderSpec) -> bool:
    if not spec.install_package and not spec.install_command_env:
        return False
    return _env_truthy("AGENT_AUTO_INSTALL_CLI", default=bool(request.auto_approve))


def _run_command(
    runner: Callable[..., subprocess.CompletedProcess],
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout_sec: int,
) -> subprocess.CompletedProcess:
    return runner(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=max(1, int(timeout_sec)),
    )


def _attempt_cli_auto_install(
    request: CliChatRequest,
    spec: CliProviderSpec,
    env: dict[str, str],
    run_command: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    install_cmd = _resolve_install_command(spec)
    if not install_cmd:
        return {"ok": False, "attempted": False, "reason": "auto_install_not_configured"}

    runner = run_command or subprocess.run
    timeout_sec = max(120, int(os.getenv("AGENT_CLI_INSTALL_TIMEOUT_SEC", "900") or "900"))

    try:
        completed = _run_command(
            runner,
            install_cmd,
            cwd=str(request.workspace),
            env=env,
            timeout_sec=timeout_sec,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "attempted": True,
            "reason": f"install_command_not_found:{install_cmd[0]}",
            "stdout": "",
            "stderr": str(exc),
            "returncode": None,
            "command": install_cmd,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "attempted": True,
            "reason": "cli_auto_install_timeout",
            "stdout": str(getattr(exc, "stdout", "") or ""),
            "stderr": str(getattr(exc, "stderr", "") or ""),
            "returncode": None,
            "command": install_cmd,
        }

    return {
        "ok": completed.returncode == 0,
        "attempted": True,
        "reason": "cli_auto_install_ok" if completed.returncode == 0 else "cli_auto_install_failed",
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
        "command": install_cmd,
    }


def _build_cli_env(request: CliChatRequest, prepared: dict) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    env.update(prepared.get("env", {}))
    if request.provider_id == "claude_cli":
        env.pop("ANTHROPIC_API_KEY", None)
    if request.provider_id == "codex_cli":
        env.pop("OPENAI_API_KEY", None)
    if request.provider_id == "gemini_cli":
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
            env.pop(name, None)
        env["GOOGLE_GENAI_USE_GCA"] = "true"
    return env


def _compose_prompt(request: CliChatRequest, spec: CliProviderSpec) -> str:
    task_text = str(request.task_input or "").strip()
    workspace_text = str(request.workspace or "").strip()
    if spec.provider_id == "gemini_cli":
        lines = [
            f"Task: {task_text}",
            "Return the final answer directly. Do not inspect files or use tools unless the task explicitly requires it.",
        ]
        if workspace_text:
            lines.extend(["", f"Workspace: {workspace_text}"])
        if request.system_prompt:
            lines.extend(["", "System instructions:", request.system_prompt.strip()])
        return "\n".join(lines).strip()
    if spec.combine_system_prompt and request.system_prompt:
        return (
            f"[System Prompt]\n{request.system_prompt.strip()}\n\n"
            f"[Workspace]\n{workspace_text}\n\n"
            f"[Task]\n{task_text}"
        ).strip()
    return f"[Workspace]\n{workspace_text}\n\n[Task]\n{task_text}".strip()


def _detect_repo_root(workspace: str) -> str:
    current = Path(str(workspace or "")).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return str(candidate)
    return ""


def _should_include_model(request: CliChatRequest, spec: CliProviderSpec) -> bool:
    model = str(request.model or "").strip()
    if not model or not spec.model_flag:
        return False
    lowered = model.lower()
    if spec.provider_id == "gemini_cli" and lowered in {"gemini", "models/gemini"}:
        return False
    if spec.provider_id == "claude_cli" and lowered in {"claude", "models/claude"}:
        return False
    return True


def _build_workspace_access_flags(request: CliChatRequest, spec: CliProviderSpec) -> list[str]:
    if not spec.workspace_access_flag:
        return []
    repo_root = _detect_repo_root(request.workspace)
    workspace = str(request.workspace or "").strip()
    if repo_root and workspace and os.path.normcase(repo_root) != os.path.normcase(workspace):
        return [spec.workspace_access_flag, repo_root]
    return []


def build_cli_command(request: CliChatRequest) -> list[str]:
    spec = get_cli_provider_spec(request.provider_id)
    cmd = _resolve_base_command(spec)

    if _should_include_model(request, spec):
        cmd.extend([spec.model_flag, str(request.model)])
    if spec.headless_edit_flags:
        cmd.extend(spec.headless_edit_flags)
    cmd.extend(_build_workspace_access_flags(request, spec))
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
    install_command_runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    spec = get_cli_provider_spec(request.provider_id)
    cmd = build_cli_command(request)
    prepared = prepare_cli_session(request, cmd)
    env = _build_cli_env(request, prepared)
    runner = run_command or subprocess.run

    try:
        completed = _run_command(
            runner,
            cmd,
            cwd=str(request.workspace),
            env=env,
            timeout_sec=int(request.timeout_sec),
        )
    except FileNotFoundError as exc:
        auto_install = {"ok": False, "attempted": False, "reason": "auto_install_disabled"}
        if _should_auto_install(request, spec):
            auto_install = _attempt_cli_auto_install(
                request,
                spec,
                env,
                run_command=install_command_runner,
            )
            if auto_install.get("ok"):
                retry_cmd = build_cli_command(request)
                try:
                    completed = _run_command(
                        runner,
                        retry_cmd,
                        cwd=str(request.workspace),
                        env=env,
                        timeout_sec=int(request.timeout_sec),
                    )
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
                        "command": retry_cmd,
                        "auto_install": auto_install,
                    }
                    finalize_cli_session(request, prepared, result)
                    return result
                except FileNotFoundError as retry_exc:
                    exc = retry_exc
                    cmd = retry_cmd
                except subprocess.TimeoutExpired as retry_exc:
                    result = {
                        "ok": False,
                        "provider_id": request.provider_id,
                        "reason": "cli_timeout",
                        "stdout": str(getattr(retry_exc, "stdout", "") or ""),
                        "stderr": str(getattr(retry_exc, "stderr", "") or ""),
                        "text": "",
                        "returncode": None,
                        "command": retry_cmd,
                        "auto_install": auto_install,
                    }
                    finalize_cli_session(request, prepared, result)
                    return result

        result = {
            "ok": False,
            "provider_id": request.provider_id,
            "reason": f"cli_command_not_found:{cmd[0]}",
            "stdout": "",
            "stderr": str(exc),
            "text": "",
            "returncode": None,
            "command": cmd,
            "auto_install": auto_install,
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
