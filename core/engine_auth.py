import os

from config.schema import factory_config

try:
    from core.providers.registry import (
        configure_providers as _registry_configure_providers,
        detect_installed_cli_providers as _registry_detect_installed,
        engine_api_keys_disabled as _registry_engine_api_keys_disabled,
        get_active_provider_setting as _registry_get_active_provider_setting,
        get_engine_api_key as _registry_get_engine_api_key,
        supports_cli_bootstrap as _registry_supports_cli_bootstrap,
    )
except Exception:
    _registry_configure_providers = None
    _registry_detect_installed = None
    _registry_engine_api_keys_disabled = None
    _registry_get_active_provider_setting = None
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
    """현재 활성 프로바이더 목록을 반환한다 (런타임 레지스트리 우선)."""
    try:
        if _registry_get_active_provider_setting:
            raw = _registry_get_active_provider_setting()
        else:
            raw = str(os.getenv("AGENT_CHAT_PROVIDER", "") or "").strip().lower()
    except Exception:
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
    """설치된 CLI 프로바이더를 자동 탐색하고 런타임 레지스트리에 설정한다.

    AGENT_CHAT_PROVIDER 환경변수를 직접 쓰지 않는다.
    이미 런타임 레지스트리 또는 환경변수로 설정된 경우 건드리지 않는다.
    탐색 우선순위: gemini_cli → claude_cli → codex_cli

    반환값: 자동 선택된 프로바이더 ID (예: "gemini_cli") 또는 None
    """
    # 이미 런타임 또는 환경변수로 설정된 경우 건드리지 않음
    try:
        if _registry_get_active_provider_setting:
            current = _registry_get_active_provider_setting()
        else:
            current = str(os.getenv("AGENT_CHAT_PROVIDER", "") or "").strip()
    except Exception:
        current = ""

    # 명시적으로 설정된(런타임 or env var) 경우만 스킵.
    # 주의: _runtime_providers를 직접 import하면 재할당이 반영 안 됨 →
    # 모듈 참조로 접근하거나 get_active_provider_setting() 사용.
    try:
        import core.providers.registry as _reg
        runtime_set = bool(_reg._runtime_providers)
    except Exception:
        runtime_set = False
    env_set = str(os.getenv("AGENT_CHAT_PROVIDER", "") or "").strip()
    if runtime_set or env_set:
        return None

    try:
        installed = _registry_detect_installed() if _registry_detect_installed else []
    except Exception:
        installed = []

    if not installed:
        return None

    # 우선순위: gemini_cli → claude_cli → codex_cli
    _PRIORITY = ["gemini_cli", "claude_cli", "codex_cli"]
    chosen = next((p for p in _PRIORITY if p in installed), installed[0])

    # 환경변수 대신 런타임 레지스트리에 설정
    if _registry_configure_providers:
        _registry_configure_providers([chosen])
    print(f"[Auto-Config] CLI 프로바이더 자동 감지: {', '.join(installed)} → {chosen} 우선 사용")

    return chosen


def check_llm_available() -> bool:
    """CLI 프로바이더가 설정/설치되어 있는지 체크한다.

    1. 런타임 레지스트리 또는 env var에 설정 있으면 OK.
    2. 없으면 auto_configure_cli_provider()로 자동 탐색.
    3. 그래도 없으면 설치 안내 경고를 출력하고 False 반환.
    """
    auto_configure_cli_provider()

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
