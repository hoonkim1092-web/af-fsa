"""
core/model_router.py
====================
에이전트 실행 시 최적 모델 선택을 담당하는 ModelRouter 클래스.
agent_runner.py에서 분리.
"""

import os
from core.config_paths import GOOGLE_API_KEY, OPENAI_API_KEY
from core.providers.registry import (
    default_chat_model_for_provider,
    get_requested_cli_providers,
)
from model_utils import (
    get_best_model, resolve_dynamic_model,
    _infer_engine_id, get_dynamic_default_model,
)


class ModelRouter:
    def pick(self, stage: str, agent_config: dict = None, is_complex: bool = True) -> str:
        if stage == "chat":
            # [우선순위 1] 환경변수로 강제 지정 시 무조건 우선
            forced = (os.getenv("AGENT_CHAT_MODEL") or "").strip()
            if forced:
                return forced

            # [우선순위 2] simple 작업 → 비용 최적화(Flash/Lightweight 계열)
            cli_providers = get_requested_cli_providers(os.getenv("AGENT_CHAT_PROVIDER"))
            if cli_providers:
                return default_chat_model_for_provider(cli_providers[0])

            if not is_complex:
                sel = resolve_dynamic_model("lightweight")
                return sel.model

            # [우선순위 3] complex 작업 → 에이전트 역할 기반 동적 최적 모델
            # (provider 환경변수로 Claude/Codex 강제 가능)
            provider_raw = (os.getenv("AGENT_CHAT_PROVIDER") or "").strip().lower()
            providers = [p.strip() for p in provider_raw.split(",") if p.strip()]
            for provider in providers:
                if provider == "codex" and OPENAI_API_KEY:
                    return "codex-5.3"
                if provider == "claude":
                    from model_utils import _pick_anthropic_model
                    return _pick_anthropic_model("sonnet") or "claude-4.6"

            # [우선순위 4] 역할 기반 동적 모델 선택 (model_utils.resolve_dynamic_model)
            role = ""
            if agent_config:
                role = (agent_config.get("role") or
                        (agent_config.get("identity") or {}).get("role_summary") or
                        agent_config.get("name") or "")
            sel = resolve_dynamic_model(_infer_engine_id(role) if role else "researcher_gemini")
            return sel.model

        # 기획/추론 단계 → 실시간 가용 고성능 모델 반환 (하드코딩 배제)
        if stage in ("requirement", "reasoning"):
            return get_best_model(["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-pro"])

        # 기본(정규화/Flash 단계) → API에서 최신 Flash 계열 동적 선택
        from model_utils import get_dynamic_default_model
        return get_dynamic_default_model("flash")


