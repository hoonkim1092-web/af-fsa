from __future__ import annotations

import os


CLI_PROVIDER_IDS = ("claude_cli", "gemini_cli", "codex_cli")
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
