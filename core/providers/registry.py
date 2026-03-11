from __future__ import annotations

import os
import shutil


CLI_PROVIDER_IDS = ("claude_cli", "gemini_cli", "codex_cli")

# CLI 실행파일 → 프로바이더 ID 매핑
_CLI_EXECUTABLES = {
    "claude_cli": "claude",
    "gemini_cli": "gemini",
    "codex_cli": "codex",
}
AI_ENGINE_API_KEY_ENVS = ("GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")

_DEFAULT_MODELS = {
    "claude_cli": "claude",
    "gemini_cli": "gemini",
    "codex_cli": "gpt-5",
}

_ENGINE_ENV_BY_ID = {
    "google": "GOOGLE_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "codex": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
}


def _env_truthy(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off"}


def parse_provider_list(raw: str | None = None) -> list[str]:
    text = str(raw if raw is not None else os.getenv("AGENT_CHAT_PROVIDER", "")).strip().lower()
    if not text:
        return []
    return [token.strip() for token in text.split(",") if token.strip()]


def get_requested_cli_providers(raw: str | None = None) -> list[str]:
    return [provider for provider in parse_provider_list(raw) if provider in CLI_PROVIDER_IDS]


def supports_cli_bootstrap(raw: str | None = None) -> bool:
    return bool(get_requested_cli_providers(raw))


def engine_api_keys_disabled(raw_provider: str | None = None) -> bool:
    override = os.getenv("AGENT_DISABLE_ENGINE_API_KEYS")
    if override is not None:
        return _env_truthy(override, default=False)
    return supports_cli_bootstrap(raw_provider)


def get_engine_api_key(engine_id: str, raw_provider: str | None = None) -> str:
    if engine_api_keys_disabled(raw_provider):
        return ""
    key_name = _ENGINE_ENV_BY_ID.get(str(engine_id or "").strip().lower())
    if not key_name:
        return ""
    primary = str(os.getenv(key_name, "") or "").strip()
    if primary:
        return primary
    if key_name == "GOOGLE_API_KEY":
        return str(os.getenv("GEMINI_API_KEY", "") or "").strip()
    return ""


def get_configured_engine_api_key(engine_id: str) -> str:
    """
    Read the raw API key from the environment without applying
    CLI-bootstrap masking. This is only for internal native fallback
    after a CLI attempt has already failed.
    """
    override = os.getenv("AGENT_DISABLE_ENGINE_API_KEYS")
    if override is not None and _env_truthy(override, default=False):
        return ""
    key_name = _ENGINE_ENV_BY_ID.get(str(engine_id or "").strip().lower())
    if not key_name:
        return ""
    primary = str(os.getenv(key_name, "") or "").strip()
    if primary:
        return primary
    if key_name == "GOOGLE_API_KEY":
        return str(os.getenv("GEMINI_API_KEY", "") or "").strip()
    return ""


def is_engine_api_key_env(name: str) -> bool:
    return str(name or "").strip().upper() in AI_ENGINE_API_KEY_ENVS


def strip_engine_api_keys(env: dict[str, str], raw_provider: str | None = None) -> dict[str, str]:
    data = dict(env or {})
    if not engine_api_keys_disabled(raw_provider):
        return data
    return {k: v for k, v in data.items() if not is_engine_api_key_env(k)}


def default_chat_model_for_provider(provider_id: str) -> str:
    key = str(provider_id or "").strip().lower()
    if key not in _DEFAULT_MODELS:
        raise ValueError(f"unsupported_cli_provider:{provider_id}")
    return _DEFAULT_MODELS[key]


def _windows_roaming_npm_dir() -> str:
    appdata = str(os.getenv("APPDATA", "") or "").strip()
    if appdata:
        return os.path.join(appdata, "npm")
    home = str(os.path.expanduser("~") or "").strip()
    if home:
        return os.path.join(home, "AppData", "Roaming", "npm")
    return ""


def detect_installed_cli_providers() -> list[str]:
    """시스템에 실제 설치된 CLI 프로바이더 목록을 반환한다."""
    installed: list[str] = []
    for provider_id, executable in _CLI_EXECUTABLES.items():
        if shutil.which(executable):
            installed.append(provider_id)
            continue
        # Windows npm 글로벌 경로 추가 탐색
        if os.name == "nt":
            npm_dir = _windows_roaming_npm_dir()
            if npm_dir:
                for suffix in (".cmd", ".exe", ".bat"):
                    if os.path.exists(os.path.join(npm_dir, executable + suffix)):
                        installed.append(provider_id)
                        break
    return installed


def detect_available_cli_providers(raw: str | None = None) -> list[str]:
    """설정된 프로바이더 중 실제 설치된 것만 반환한다."""
    requested = get_requested_cli_providers(raw)
    if not requested:
        return []
    installed = set(detect_installed_cli_providers())
    return [p for p in requested if p in installed]


_CLI_DISPLAY_NAMES = {
    "claude_cli": "Claude Code",
    "gemini_cli": "Gemini CLI",
    "codex_cli": "Codex CLI",
}


_CLI_INSTALL_COMMANDS = {
    "claude_cli": "npm install -g @anthropic-ai/claude-code",
    "gemini_cli": "npm install -g @google/gemini-cli",
    "codex_cli": "npm install -g @openai/codex",
}


_CLI_AUTH_COMMANDS = {
    "claude_cli": "claude auth login",
    "gemini_cli": "gemini auth login",
    "codex_cli": "codex login",
}


def get_cli_display_name(provider_id: str) -> str:
    return _CLI_DISPLAY_NAMES.get(provider_id, provider_id)


def get_cli_install_command(provider_id: str) -> str:
    return _CLI_INSTALL_COMMANDS.get(provider_id, "")


def get_cli_auth_command(provider_id: str) -> str:
    return _CLI_AUTH_COMMANDS.get(provider_id, "")

