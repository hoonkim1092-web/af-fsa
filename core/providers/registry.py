from __future__ import annotations

import os


CLI_PROVIDER_IDS = ("claude_cli", "gemini_cli", "codex_cli")

_DEFAULT_MODELS = {
    "claude_cli": "claude",
    "gemini_cli": "gemini",
    "codex_cli": "gpt-5",
}


def parse_provider_list(raw: str | None = None) -> list[str]:
    text = str(raw if raw is not None else os.getenv("AGENT_CHAT_PROVIDER", "")).strip().lower()
    if not text:
        return []
    return [token.strip() for token in text.split(",") if token.strip()]


def get_requested_cli_providers(raw: str | None = None) -> list[str]:
    return [provider for provider in parse_provider_list(raw) if provider in CLI_PROVIDER_IDS]


def supports_cli_bootstrap(raw: str | None = None) -> bool:
    return bool(get_requested_cli_providers(raw))


def default_chat_model_for_provider(provider_id: str) -> str:
    key = str(provider_id or "").strip().lower()
    if key not in _DEFAULT_MODELS:
        raise ValueError(f"unsupported_cli_provider:{provider_id}")
    return _DEFAULT_MODELS[key]
