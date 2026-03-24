import os

from config.schema import factory_config

try:
    from core.providers.registry import (
        engine_api_keys_disabled as _registry_engine_api_keys_disabled,
        get_engine_api_key as _registry_get_engine_api_key,
        supports_cli_bootstrap as _registry_supports_cli_bootstrap,
    )
except Exception:
    _registry_engine_api_keys_disabled = None
    _registry_get_engine_api_key = None
    _registry_supports_cli_bootstrap = None


_CLI_BOOTSTRAP_PROVIDERS = {
    "claude",
    "claude_cli",
    "gemini",
    "gemini_cli",
    "codex",
    "codex_cli",
}


def _truthy_env(name: str) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _config_value(name: str, env_keys: tuple[str, ...]) -> str:
    if isinstance(factory_config, dict):
        value = factory_config.get(name)
    else:
        value = getattr(factory_config, name, None)
    if value:
        return str(value)
    for env_key in env_keys:
        env_value = str(os.getenv(env_key, "") or "").strip()
        if env_value:
            return env_value
    return ""


def _configured_cli_providers() -> list[str]:
    raw = str(os.getenv("AGENT_CHAT_PROVIDER", "") or "").strip().lower()
    return [part.strip() for part in raw.split(",") if part.strip()]


def engine_api_keys_disabled() -> bool:
    if _registry_engine_api_keys_disabled:
        try:
            return bool(_registry_engine_api_keys_disabled())
        except Exception:
            pass
    return _truthy_env("AGENT_DISABLE_ENGINE_API_KEYS")


def supports_cli_bootstrap() -> bool:
    if _registry_supports_cli_bootstrap:
        try:
            return bool(_registry_supports_cli_bootstrap())
        except Exception:
            pass
    if _truthy_env("AGENT_ALLOW_CLI_BOOTSTRAP"):
        return True
    providers = set(_configured_cli_providers())
    return bool(providers & _CLI_BOOTSTRAP_PROVIDERS)


def check_llm_available() -> bool:
    """CLI 프로바이더가 설정되어 있는지 체크한다.
    설정되지 않은 경우 구독/설정 안내 경고를 출력하고 False를 반환한다.
    이 함수는 API 키를 체크하지 않는다. CLI 프로바이더만 본다.

    사용 예:
        if not check_llm_available():
            return _fallback_result(...)
    """
    if _registry_supports_cli_bootstrap:
        try:
            has_cli = bool(_registry_supports_cli_bootstrap())
        except Exception:
            has_cli = False
    else:
        providers = set(_configured_cli_providers())
        has_cli = bool(providers & _CLI_BOOTSTRAP_PROVIDERS)

    if not has_cli:
        print(
            "[WARNING] LLM 프로바이더가 설정되지 않았습니다.\n"
            "  → CLI 프로바이더(Claude Code / Gemini CLI / Codex CLI) 중 하나를 구독하고\n"
            "    환경변수 AGENT_CHAT_PROVIDER=claude_cli (또는 gemini_cli / codex_cli) 를\n"
            "    설정한 뒤 다시 실행해 주세요.\n"
            "  → LLM 없이 실행하는 경우 키워드 기반 폴백(Fallback) 모드로 전환합니다."
        )
    return has_cli


def get_engine_api_key(provider: str) -> str:
    if _registry_get_engine_api_key:
        try:
            return str(_registry_get_engine_api_key(provider) or "")
        except Exception:
            pass
    if engine_api_keys_disabled():
        return ""

    normalized = str(provider or "").strip().lower()
    if normalized in {"google", "gemini"}:
        return _config_value("google_api_key", ("GOOGLE_API_KEY", "GEMINI_API_KEY"))
    if normalized in {"openai", "codex"}:
        return _config_value("openai_api_key", ("OPENAI_API_KEY",))
    if normalized in {"anthropic", "claude"}:
        return _config_value("anthropic_api_key", ("ANTHROPIC_API_KEY",))
    return ""
