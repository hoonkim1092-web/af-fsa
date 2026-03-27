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


# ──────────────────────────────────────────────────────────────
# 런타임 프로바이더 레지스트리 (AGENT_CHAT_PROVIDER 환경변수 대체)
#
# 우선순위:
#   1. configure_providers()로 런타임에 설정된 값
#   2. 하위 호환: AGENT_CHAT_PROVIDER 환경변수 (외부 override 전용)
#   3. 아무것도 없으면 → detect_installed_cli_providers() 자동 탐지
# ──────────────────────────────────────────────────────────────
_runtime_providers: list[str] = []


def configure_providers(providers: list[str]) -> None:
    """런타임에 사용할 CLI 프로바이더를 설정한다.

    AGENT_CHAT_PROVIDER 환경변수를 직접 쓰는 대신 이 함수를 사용한다.
    --provider CLI 인자, auto_configure_cli_provider() 등에서 호출.
    """
    global _runtime_providers
    _runtime_providers = [p for p in (providers or []) if p in CLI_PROVIDER_IDS]


def get_active_provider_setting() -> str:
    """현재 활성 프로바이더 설정값을 반환한다 (parse_provider_list 호환 형식).

    런타임 설정 → 환경변수 하위호환 → 자동탐지 순으로 확인.
    """
    # 1. 런타임 설정 우선
    if _runtime_providers:
        return ",".join(_runtime_providers)
    # 2. 환경변수 하위호환 (외부 도구/테스트에서 설정한 경우)
    env_val = str(os.getenv("AGENT_CHAT_PROVIDER", "") or "").strip()
    if env_val:
        return env_val
    # 3. 자동탐지: 설치된 모든 CLI 프로바이더
    installed = detect_installed_cli_providers()
    return ",".join(installed)


def parse_provider_list(raw: str | None = None) -> list[str]:
    text = str(raw if raw is not None else get_active_provider_setting()).strip().lower()
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


_installed_cli_cache: list[str] | None = None
_installed_cli_cache_ts: float = 0.0
_INSTALLED_CLI_CACHE_TTL = 60.0  # 60초 TTL


def detect_installed_cli_providers() -> list[str]:
    """시스템에 실제 설치된 CLI 프로바이더 목록을 반환한다 (60초 캐시).

    shutil.which()는 시스템 PATH를 탐색하므로 비용이 있음.
    60초 내 재호출은 캐시 결과를 반환한다.
    """
    import time
    global _installed_cli_cache, _installed_cli_cache_ts

    now = time.monotonic()
    if _installed_cli_cache is not None and (now - _installed_cli_cache_ts) < _INSTALLED_CLI_CACHE_TTL:
        return list(_installed_cli_cache)

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

    _installed_cli_cache = installed
    _installed_cli_cache_ts = now
    return list(installed)


def invalidate_installed_cli_cache() -> None:
    """설치 캐시를 강제 무효화한다 (테스트 또는 CLI 설치 직후 사용)."""
    global _installed_cli_cache, _installed_cli_cache_ts
    _installed_cli_cache = None
    _installed_cli_cache_ts = 0.0


def detect_available_cli_providers(raw: str | None = None) -> list[str]:
    """설정된 프로바이더 중 실제 설치된 것만 반환한다.

    명시적으로 raw를 전달한 경우: raw 기반 필터링.
    raw 미전달: get_active_provider_setting() 기반 (런타임/env/자동탐지).
    """
    requested = get_requested_cli_providers(raw)
    if not requested:
        # 설정 자체가 없으면 설치된 전체 반환
        return detect_installed_cli_providers()
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

