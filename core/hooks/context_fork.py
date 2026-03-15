"""
Context Fork Hook (Phase 5a)

도구 실행 결과를 값싼 서브 에이전트 모델로 1줄 요약하여
메인 에이전트 컨텍스트 낭비를 극적으로 줄인다.

동작:
  1. post_tool_call에서 원본 결과 텍스트 길이를 확인
  2. CONTEXT_FORK_THRESHOLD(기본 500자)를 초과하면 서브 에이전트 호출
  3. 값싼 모델(Flash/Haiku)이 1줄 요약을 생성
  4. 원본 결과를 요약본으로 교체하여 메인 컨텍스트에 주입
  5. 원본은 JSONL 트레이스에 보존 (LangSmith Hook이 먼저 실행)

Priority: 15 (LangSmith=5 다음, ToolOutputTruncator=10 다음)
"""
from __future__ import annotations

import os
import json
from typing import Any

from core.hooks.base import ContinuationHook


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONTEXT_FORK_THRESHOLD = int(os.getenv("CONTEXT_FORK_THRESHOLD", "500"))
CONTEXT_FORK_MAX_SUMMARY_LEN = int(os.getenv("CONTEXT_FORK_MAX_SUMMARY_LEN", "200"))
CONTEXT_FORK_ENABLED = os.getenv("CONTEXT_FORK_ENABLED", "1") != "0"
CONTEXT_FORK_LLM_TIMEOUT = int(os.getenv("CONTEXT_FORK_LLM_TIMEOUT", "10"))  # seconds

# 요약 대상에서 제외할 도구 (결과가 이미 짧거나 구조적인 도구)
_SKIP_TOOLS = frozenset({
    "core_memory_read", "core_memory_write",
    "approve_tool", "reject_tool",
})


class ContextForkHook(ContinuationHook):
    """
    도구 결과를 서브 에이전트(값싼 모델)로 1줄 요약하는 Hook.

    LangSmith Hook(PRIORITY=5)과 ToolOutputTruncator(PRIORITY=10) 이후에 실행되어
    원본 데이터가 트레이스에 기록된 뒤 요약본으로 교체한다.
    """

    PRIORITY = 15

    _SENTINEL_NO_SUMMARIZER = object()  # "시도했으나 사용 가능한 summarizer 없음" 표시

    def __init__(self, threshold: int | None = None, enabled: bool | None = None):
        self._threshold = threshold if threshold is not None else CONTEXT_FORK_THRESHOLD
        self._enabled = enabled if enabled is not None else CONTEXT_FORK_ENABLED
        self._summarizer = None  # lazy init (None=아직 시도 안 함)
        self._stats = {"calls": 0, "summarized": 0, "saved_chars": 0, "llm_failures": 0}
        self._per_tool_stats: dict[str, dict] = {}  # tool_name → {calls, summarized, saved}
        self._stats_printed = False

    # ------------------------------------------------------------------
    # Hook interface
    # ------------------------------------------------------------------
    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        """도구 결과가 threshold를 초과하면 1줄 요약으로 교체"""
        if not self._enabled:
            return result

        if tool_name in _SKIP_TOOLS:
            return result

        self._stats["calls"] += 1
        ts = self._per_tool_stats.setdefault(tool_name, {"calls": 0, "summarized": 0, "saved": 0})
        ts["calls"] += 1

        result_text = _to_text(result)
        if len(result_text) <= self._threshold:
            return result

        # 서브 에이전트로 요약
        summary = self._summarize(tool_name, result_text, agent_state)
        if summary is None:
            return result  # 요약 실패 시 원본 유지

        saved = len(result_text) - len(summary)
        self._stats["summarized"] += 1
        self._stats["saved_chars"] += saved
        ts["summarized"] += 1
        ts["saved"] += saved

        # 결과가 dict이면 원본 제어 필드 보존 + 요약 첨부
        if isinstance(result, dict):
            forked = {
                "__context_fork__": True,
                "__original_length__": len(result_text),
                "summary": summary,
            }
            # 후속 제어에 필요한 핵심 필드를 원본에서 보존
            for key in ("ok", "reason", "status", "error", "file_path", "run_id", "skill_id"):
                if key in result:
                    forked[key] = result[key]
            return forked
        return summary

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        """실행 종료 시 컨텍스트 절약 통계 출력.

        주의: agent_runner.py에서 도구 호출 결과에도 run_post_execute()를
        호출하므로, 최종 에이전트 결과(ok+reason 키 보유)인 경우에만 1회 출력.
        """
        is_final_result = (
            isinstance(result, dict)
            and "ok" in result
            and "reason" in result
            and not self._stats_printed
        )
        if is_final_result and self._stats["summarized"] > 0:
            self._stats_printed = True
            saved_kb = self._stats["saved_chars"] / 1024
            print(
                f"[ContextFork] {self._stats['summarized']}/{self._stats['calls']} "
                f"tool results summarized, ~{saved_kb:.1f}KB context saved"
            )
        return result

    # ------------------------------------------------------------------
    # Summarizer
    # ------------------------------------------------------------------
    def _summarize(self, tool_name: str, result_text: str, agent_state: dict) -> str | None:
        """값싼 모델로 1줄 요약 생성. 실패 시 fallback 반환."""
        try:
            summarizer = self._get_summarizer()
            if summarizer is None:
                return _fallback_summarize(tool_name, result_text)

            prompt = (
                f"Summarize the following tool execution result in ONE concise line (max {CONTEXT_FORK_MAX_SUMMARY_LEN} chars). "
                f"Include only the key information needed for the calling agent to proceed.\n"
                f"Tool: {tool_name}\n"
                f"Result:\n{result_text[:4000]}"
            )

            summary = _invoke_with_timeout(summarizer, prompt, CONTEXT_FORK_LLM_TIMEOUT)
            if summary and len(summary.strip()) > 0:
                return summary.strip()[:CONTEXT_FORK_MAX_SUMMARY_LEN]

            self._stats["llm_failures"] += 1
            return _fallback_summarize(tool_name, result_text)
        except Exception:
            self._stats["llm_failures"] += 1
            return _fallback_summarize(tool_name, result_text)

    def _get_summarizer(self):
        """값싼 모델 summarizer를 lazy-init하여 반환"""
        if self._summarizer is self._SENTINEL_NO_SUMMARIZER:
            return None  # 이전에 시도했으나 사용 가능한 summarizer 없음
        if self._summarizer is not None:
            return self._summarizer

        # Strategy 1: LangChain 모델 사용
        try:
            from core.model_router import ModelRouter
            mr = ModelRouter()
            lc_model = mr.pick("chat", is_complex=False, return_langchain_model=True)
            if lc_model is not None and hasattr(lc_model, "invoke"):
                def _lc_summarize(prompt: str) -> str:
                    resp = lc_model.invoke(prompt)
                    return resp.content if hasattr(resp, "content") else str(resp)
                self._summarizer = _lc_summarize
                return self._summarizer
        except Exception:
            pass

        # Strategy 2: Google GenAI 직접 호출
        try:
            from google import genai as google_genai
            from google.genai import types as genai_types
            client = google_genai.Client()

            def _genai_summarize(prompt: str) -> str:
                resp = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        max_output_tokens=100,
                        temperature=0.0,
                    ),
                )
                return resp.text if resp and resp.text else ""
            self._summarizer = _genai_summarize
            return self._summarizer
        except Exception:
            pass

        # 사용 가능한 summarizer 없음 — sentinel 마킹하여 재시도 방지
        self._summarizer = self._SENTINEL_NO_SUMMARIZER
        return None

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def per_tool_stats(self) -> dict:
        return {k: dict(v) for k, v in self._per_tool_stats.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke_with_timeout(fn, prompt: str, timeout_sec: int) -> str | None:
    """LLM 호출에 타임아웃을 적용. Windows에서는 signal 미지원이므로 threading 사용."""
    import threading

    result_holder: list = [None]
    error_holder: list = [None]

    def _worker():
        try:
            result_holder[0] = fn(prompt)
        except Exception as e:
            error_holder[0] = e

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)

    if thread.is_alive():
        # 타임아웃 - 스레드가 아직 실행 중이지만 daemon이므로 무시
        return None
    if error_holder[0] is not None:
        raise error_holder[0]
    return result_holder[0]


def _to_text(result: Any) -> str:
    """결과를 텍스트로 변환"""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(result)
    return str(result)


def _fallback_summarize(tool_name: str, result_text: str) -> str:
    """LLM 없이 규칙 기반 요약 (최후 수단)

    dict 결과에서 ok/error/status 같은 핵심 필드를 우선 추출하고,
    일반 텍스트일 경우 첫 의미 있는 줄을 사용한다.
    """
    text = result_text.strip()

    # JSON dict 결과에서 핵심 정보 추출 시도
    status_summary = _extract_dict_status(text)
    if status_summary:
        total_chars = len(text)
        return f"[{tool_name}] {status_summary} ({total_chars} chars)"

    lines = text.split("\n")

    # 첫 번째 의미 있는 줄 추출
    first_meaningful = ""
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "=", "-", "```")):
            first_meaningful = stripped
            break

    if not first_meaningful:
        first_meaningful = lines[0].strip() if lines else ""

    # 길이 제한 + 메타 정보 추가
    total_lines = len(lines)
    total_chars = len(text)
    truncated = first_meaningful[:150]

    return f"[{tool_name}] {truncated}... ({total_lines} lines, {total_chars} chars)"


def _extract_dict_status(text: str) -> str | None:
    """JSON 텍스트에서 ok/error/status 같은 핵심 필드를 추출하여 1줄 요약"""
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
    except (json.JSONDecodeError, ValueError):
        return None

    parts = []
    # ok 필드
    if "ok" in data:
        parts.append(f"ok={data['ok']}")
    # status 필드
    if "status" in data:
        parts.append(f"status={data['status']}")
    # error/reason 필드
    for key in ("error", "reason", "message"):
        if key in data:
            val = str(data[key])[:80]
            parts.append(f"{key}={val}")
            break
    # count/total 필드
    for key in ("count", "total", "total_cases", "total_events"):
        if key in data:
            parts.append(f"{key}={data[key]}")
            break

    if not parts:
        return None
    return ", ".join(parts)
