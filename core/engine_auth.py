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


def auto_configure_cli_provider() -> str | None:
    """시스템에 설치된 CLI 프로바이더를 자동 탐색하고 AGENT_CHAT_PROVIDER를 설정한다.

    이미 환경변수가 설정된 경우 그대로 둔다.
    탐색 우선순위: gemini_cli → claude_cli → codex_cli

    반환값: 자동 설정된 프로바이더 ID (예: "gemini_cli") 또는 None
    """
    # 이미 설정되어 있으면 건드리지 않음
    if str(os.getenv("AGENT_CHAT_PROVIDER", "") or "").strip():
        return None

    try:
        from core.providers.registry import detect_installed_cli_providers
        installed = detect_installed_cli_providers()
    except Exception:
        installed = []

    # 우선순위: gemini_cli → claude_cli → codex_cli
    _PRIORITY = ["gemini_cli", "claude_cli", "codex_cli"]
    chosen = next((p for p in _PRIORITY if p in installed), None)
    if not chosen and installed:
        chosen = installed[0]

    if chosen:
        os.environ["AGENT_CHAT_PROVIDER"] = chosen
        print(f"[Auto-Config] CLI 프로바이더 자동 감지: {chosen} → AGENT_CHAT_PROVIDER={chosen}")

    return chosen


def check_llm_available() -> bool:
    """CLI 프로바이더가 설정/설치되어 있는지 체크한다.

    1. 환경변수 AGENT_CHAT_PROVIDER가 이미 설정되어 있으면 OK.
    2. 없으면 auto_configure_cli_provider()로 시스템에서 자동 탐색해 설정한다.
    3. 그래도 없으면 설치 안내 경고를 출력하고 False 반환.

    사용 예:
        if not check_llm_available():
            return _fallback_result(...)
    """
    # 먼저 자동 탐색/설정 시도
    auto_configure_cli_provider()

    # 설정 결과 재확인
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
            "[WARNING] 사용 가능한 LLM CLI 프로바이더가 없습니다.\n"
            "  다음 중 하나를 설치하고 로그인하세요:\n"
            "    • Gemini CLI  : npm install -g @google/gemini-cli  → gemini auth login\n"
            "    • Claude Code : npm install -g @anthropic-ai/claude-code  → claude auth login\n"
            "    • Codex CLI   : npm install -g @openai/codex  → codex login\n"
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
