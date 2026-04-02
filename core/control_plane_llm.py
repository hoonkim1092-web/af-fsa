"""
core/control_plane_llm.py
=========================
Control-plane(Lilith, StrategyEvaluator)용 LLM 인터페이스.

해결 순서:
  1. CLI providers (claude_cli > gemini_cli > codex_cli)
  2. Gemini API (GOOGLE_API_KEY가 있을 때만)
  3. 둘 다 없으면 빈 결과 반환 (retry 낭비 없음)

LLMEngine과 동일한 generate() / generate_json() 인터페이스를 제공하여
기존 코드의 monkeypatch 호환성을 유지한다.
"""
from __future__ import annotations

import json
import os
import re


def _parse_json_from_text(text: str) -> dict | None:
    """LLM 응답 텍스트에서 JSON dict를 추출한다. 파싱 실패 시 None 반환."""
    if not text:
        return None
    try:
        if "```json" in text:
            json_block = text.split("```json")[1].split("```")[0].strip()
        else:
            json_block = text.strip()
            m = re.search(r'\{[\s\S]*\}', json_block)
            if m:
                json_block = m.group(0)
        result = json.loads(json_block)
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, IndexError, Exception):
        return None


class ControlPlaneLLM:
    """CLI-first, API-fallback LLM for control-plane components."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or ""
        self._cli_providers: list[str] = []
        self._api_engine = None  # lazy: LLMEngine instance

        self._init_providers()

    # ── 초기화 ────────────────────────────────────────────────
    def _init_providers(self) -> None:
        # 환경변수 AF_CONTROL_PLANE_PROVIDERS 우선 적용 (설정된 경우 auto-detect 생략)
        env_providers = os.environ.get("AF_CONTROL_PLANE_PROVIDERS", "").strip()
        if env_providers:
            self._cli_providers = [p.strip() for p in env_providers.split(",") if p.strip()]
        else:
            # CLI providers 탐색
            try:
                from core.providers.registry import detect_available_cli_providers
                self._cli_providers = detect_available_cli_providers()
            except Exception:
                self._cli_providers = []

        # API key 존재 시 LLMEngine 생성 (CLI와 무관하게 항상 시도)
        try:
            from core.llm_engine import get_current_gemini_key
            if get_current_gemini_key():
                from core.llm_engine import LLMEngine
                self._api_engine = LLMEngine(model_name=self.model_name or None)
        except Exception:
            pass

        if not self._cli_providers and self._api_engine is None:
            print(
                "[ControlPlaneLLM] WARNING: No LLM provider available. "
                "Install a CLI (claude/gemini/codex) or set GOOGLE_API_KEY."
            )

    # ── CLI provider 선택 ─────────────────────────────────────
    _PREFERENCE = ("claude_cli", "gemini_cli", "codex_cli")

    def _pick_cli_provider(self, exclude: set[str] | None = None) -> str:
        """우선순위에 따라 CLI provider를 선택한다."""
        exclude = exclude or set()
        for p in self._PREFERENCE:
            if p in self._cli_providers and p not in exclude:
                return p
        for p in self._cli_providers:
            if p not in exclude:
                return p
        return ""

    # ── CLI 호출 ──────────────────────────────────────────────
    def _generate_via_cli(self, prompt: str) -> str | None:
        """CLI providers를 순회하며 첫 번째 성공 응답을 반환한다."""
        if not self._cli_providers:
            return None

        from core.providers.cli import CliChatRequest, execute_cli_chat
        from core.providers.registry import default_chat_model_for_provider
        from core.failure_classifier import classify_failure, FailureCategory

        tried: set[str] = set()
        while True:
            provider_id = self._pick_cli_provider(exclude=tried)
            if not provider_id:
                break
            tried.add(provider_id)

            try:
                try:
                    _model = default_chat_model_for_provider(provider_id)
                except ValueError:
                    _model = ""
                result = execute_cli_chat(CliChatRequest(
                    provider_id=provider_id,
                    model=_model,
                    system_prompt=(
                        "You are a JSON-responding orchestration engine. "
                        "Always respond with valid JSON only."
                    ),
                    task_input=prompt,
                    workspace=os.getcwd(),
                    timeout_sec=120,
                ))
            except Exception as exc:
                print(f"[ControlPlaneLLM] CLI {provider_id} exception: {exc}")
                continue

            if result.get("ok") and result.get("text"):
                return str(result["text"]).strip()

            # infra 실패면 다음 provider 시도
            reason = str(result.get("reason", ""))
            if classify_failure(reason) == FailureCategory.INFRA:
                print(f"[ControlPlaneLLM] CLI {provider_id} infra failure: {reason}")
                continue

            # 비-infra인데 텍스트가 있으면 반환
            text = str(result.get("text", "") or "").strip()
            if text:
                return text

        return None

    # ── Public API (LLMEngine 호환) ──────────────────────────
    def generate(self, prompt: str) -> str:
        """프롬프트에 대한 텍스트 응답을 반환한다."""
        # 1. CLI
        if self._cli_providers:
            text = self._generate_via_cli(prompt)
            if text:
                return text

        # 2. API fallback
        if self._api_engine:
            return self._api_engine.generate(prompt)

        return ""

    def generate_json(self, prompt: str, output_schema: type | None = None) -> dict:
        """프롬프트에 대한 JSON dict 응답을 반환한다."""
        # 1. CLI
        if self._cli_providers:
            text = self._generate_via_cli(prompt)
            if text:
                parsed = _parse_json_from_text(text)
                if parsed is not None:
                    return parsed

        # 2. API fallback
        if self._api_engine:
            return self._api_engine.generate_json(prompt, output_schema)

        return {}

    def is_available(self) -> bool:
        """사용 가능한 LLM provider가 있는지 반환한다."""
        return bool(self._cli_providers) or self._api_engine is not None
