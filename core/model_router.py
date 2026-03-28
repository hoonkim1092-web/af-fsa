"""
core/model_router.py
====================
에이전트 실행 시 최적 모델/프로바이더 선택을 담당하는 ModelRouter 클래스.

설계 원칙:
  - API KEY는 사용하지 않음 (CLI 프로바이더 전용, API 코드는 복구 대비 보존)
  - 단일 구독 → 모든 작업을 해당 프로바이더로 (시작 시 1회 노티)
  - 복수 구독 → 역할 기반 CLI 프로바이더 자동 선택
  - 구독 없음 → Claude Code 구독 유도 메시지
"""

import os
from typing import Any

from core.providers.registry import (
    default_chat_model_for_provider,
    detect_available_cli_providers,
    detect_installed_cli_providers,
    get_cli_display_name,
    get_requested_cli_providers,
)
from model_utils import (
    _infer_engine_id,
    pick_cli_provider_for_role,
)


# ─────────────────────────────────────────────────────────────────────
# 구독 상태 노티 (세션 당 1회)
# ─────────────────────────────────────────────────────────────────────
_startup_notified = False


def _print_no_subscription_guide() -> None:
    """모든 구독이 없을 때 Claude Code 구독 유도 메시지."""
    W = 62
    sep = "=" * W
    print(f"\n{sep}")
    print("  [Agent Factory] CLI 프로바이더가 감지되지 않았습니다")
    print(sep)
    print()
    print("  AI 엔진이 설치되어 있지 않아 에이전트를 실행할 수 없습니다.")
    print()
    print("  추천: Claude Code 구독 (코드 설계/구현 최고 성능)")
    print(f"  설치: npm install -g @anthropic-ai/claude-code")
    print(f"  인증: claude auth login")
    print()
    print("  설치 후 자동 감지됩니다. 또는 --provider 인자로 직접 지정하세요.")
    print()
    print("  다른 옵션:")
    print("  - Gemini CLI: npm install -g @google/gemini-cli")
    print("  - Codex CLI:  npm install -g @openai/codex")
    print(sep)
    print()


def _print_single_provider_notice(provider_id: str) -> None:
    """단일 프로바이더만 활성일 때 노티."""
    display = get_cli_display_name(provider_id)
    W = 62
    sep = "-" * W
    others = {
        "claude_cli": [("Gemini CLI", "리서치/분석 최적화"), ("Codex CLI", "자동화/파이프라인 최적화")],
        "gemini_cli": [("Claude Code", "코드 설계/구현 최적화"), ("Codex CLI", "자동화/파이프라인 최적화")],
        "codex_cli": [("Claude Code", "코드 설계/구현 최적화"), ("Gemini CLI", "리서치/분석 최적화")],
    }
    print(f"\n+{sep}+")
    print(f"|  [Model Routing] 단일 프로바이더 모드{' ' * (W - 38)}|")
    print(f"+{sep}+")
    print(f"|  활성 구독: {display:<{W - 13}}|")
    print(f"|  모든 작업(기획/실행/검증)이 {display}로 실행됩니다.{' ' * max(0, W - 31 - len(display) - 14)}|")
    print(f"|{' ' * W}|")
    print(f"|  * 역할별 최적 분배를 원하시면 추가 구독을 고려하세요:{' ' * max(0, W - 53)}|")
    for name, desc in others.get(provider_id, []):
        line = f"    - {name}: {desc}"
        print(f"|{line:<{W}}|")
    print(f"+{sep}+")
    print()


def _print_multi_provider_notice(providers: list[str]) -> None:
    """복수 프로바이더 활성 노티."""
    W = 62
    sep = "-" * W
    names = ", ".join(get_cli_display_name(p) for p in providers)
    print(f"\n+{sep}+")
    print(f"|  [Model Routing] 역할 기반 자동 분배 모드{' ' * (W - 42)}|")
    print(f"+{sep}+")
    line = f"  활성 구독: {names}"
    print(f"|{line:<{W}}|")
    print(f"|  에이전트 역할에 따라 최적 프로바이더를 자동 선택합니다.{' ' * max(0, W - 55)}|")
    print(f"+{sep}+")
    print()


def print_startup_routing_notice() -> None:
    """시스템 시작 시 1회 라우팅 상태 알림 출력."""
    global _startup_notified
    if _startup_notified:
        return
    _startup_notified = True

    available = detect_available_cli_providers()
    if not available:
        # 설치된 CLI가 아예 없음
        _print_no_subscription_guide()
        return

    # 명시적으로 설정된 경우(configure_providers/env)와 자동탐지 모두
    # available 기준으로 단일/다중 판정
    if len(available) == 1:
        _print_single_provider_notice(available[0])
    else:
        _print_multi_provider_notice(available)


# ─────────────────────────────────────────────────────────────────────
# ModelRouter
# ─────────────────────────────────────────────────────────────────────
class ModelRouter:
    """
    CLI-only 환경을 위한 모델 라우터.

    - 단일 구독: 모든 요청을 해당 프로바이더로 라우팅
    - 복수 구독: engine_id(역할) 기반으로 최적 프로바이더 선택
    - 구독 없음: 에러 반환 + 구독 유도
    """

    def pick(self, stage: str, agent_config: dict = None, is_complex: bool = True, return_langchain_model: bool = False) -> str | Any:
        """최적 모델명을 반환한다. return_langchain_model=True이면 LangChain ChatModel 인스턴스 반환."""

        # [우선순위 1] 환경변수로 모델 강제 지정
        forced = (os.getenv("AGENT_CHAT_MODEL") or "").strip()
        if forced:
            model_name = forced
        else:
            cli_providers = get_requested_cli_providers()
            if cli_providers:
                provider = self._pick_cli_provider(cli_providers, agent_config)
                model_name = default_chat_model_for_provider(provider)
            else:
                model_name = self._pick_api_model(stage, agent_config, is_complex)

        if return_langchain_model:
            from core.langchain_adapter import LangChainChatModelFactory
            # Infer provider from model name
            provider_key = "gemini"
            if "claude" in model_name or "anthropic" in model_name:
                provider_key = "anthropic"
            elif "gpt" in model_name or "openai" in model_name:
                provider_key = "openai"
            lc_model = LangChainChatModelFactory.create(provider_key, model_name)
            if lc_model is not None:
                return lc_model

        return model_name

    def pick_provider(self, agent_config: dict = None) -> str:
        """최적 CLI 프로바이더 ID를 반환한다."""
        cli_providers = get_requested_cli_providers()
        if not cli_providers:
            return ""
        return self._pick_cli_provider(cli_providers, agent_config)

    def _pick_cli_provider(self, cli_providers: list[str], agent_config: dict = None) -> str:
        """역할 기반 CLI 프로바이더 선택."""
        if len(cli_providers) == 1:
            return cli_providers[0]

        # 역할 추론
        role = ""
        if agent_config:
            role = (
                agent_config.get("role")
                or (agent_config.get("identity") or {}).get("role_summary")
                or agent_config.get("name")
                or ""
            )
        engine_id = _infer_engine_id(role) if role else "researcher_gemini"

        # 가용 프로바이더 중에서 역할 기반 선택
        available = detect_available_cli_providers()
        if not available:
            available = cli_providers

        return pick_cli_provider_for_role(engine_id, available, role_description=role)

    def pick_multiple(self, stage: str = "coding") -> list[tuple[str, str]]:
        """교차검증용: 설치된 모든 (provider_id, model) 쌍을 반환한다.

        Returns:
            [(provider_id, model), ...] — 설치된 CLI 프로바이더 목록.
            빈 리스트면 교차검증 불가.
        """
        providers = detect_installed_cli_providers()
        return [(pid, default_chat_model_for_provider(pid)) for pid in providers]

    def _pick_api_model(self, stage: str, agent_config: dict = None, is_complex: bool = True) -> str:
        """API Key 모드 폴백 (현재 비활성, 나중에 복구 가능)."""
        from core.config_paths import GOOGLE_API_KEY, OPENAI_API_KEY
        from core.requirement_llm import pick_requirement_candidate
        from model_utils import (
            get_best_model, get_dynamic_default_model, resolve_dynamic_model,
        )

        if stage == "chat":
            if not is_complex:
                sel = resolve_dynamic_model("lightweight")
                return sel.model
            role = ""
            if agent_config:
                role = (agent_config.get("role") or
                        (agent_config.get("identity") or {}).get("role_summary") or
                        agent_config.get("name") or "")
            sel = resolve_dynamic_model(_infer_engine_id(role) if role else "researcher_gemini")
            return sel.model

        if stage == "requirement":
            selected = pick_requirement_candidate()
            if selected:
                return selected.model
            return get_best_model(["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-pro"])

        if stage == "reasoning":
            return get_best_model(["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-pro"])

        return get_dynamic_default_model("flash")
