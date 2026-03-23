"""
core/context_window_manager.py
==============================
컨텍스트 윈도우 효율화 매니저.

chat.send_message() 대신 generate_content()를 직접 사용하여
매 턴마다 컨텍스트를 최적화합니다.

핵심 기능:
  1. Tool Budget    : 미사용 3턴 초과 tool evict + 토큰 예산 제한
  2. History Budget : 최근 N턴 원문 유지, 오래된 턴 tool_result 압축
                     예산 초과 시 오래된 엔트리 제거 (enforce_budget)
  3. System Budget  : Knowledge 스킬 온디맨드 주입 (get_relevant_for_turn)
  4. Reserve        : 응답용 여유 토큰 확보
  5. API 호환성     : Gemini 연속 user role 금지 → function_response 그루핑
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from core.skill_context_config import get_context_tokens

logger = logging.getLogger(__name__)

# 평균 tool definition 크기 추정 (토큰)
_TOOL_DEF_TOKEN_AVG = 150

# 히스토리 항목 하드 상한 — 토큰 예산과 별개로 항목 수 자체를 제한
# 매우 길게 실행되는 프로젝트에서 짧은 항목이 대량 누적되는 상황을 방지
MAX_HISTORY_ENTRIES = 200


# ──────────────────────────────────────────────
# Token estimation
# ──────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    """텍스트의 토큰 수를 추정합니다. (평균 4자 = 1토큰 heuristic)"""
    if not text:
        return 0
    return max(1, len(text) // 4)


# ──────────────────────────────────────────────
# Budget Model
# ──────────────────────────────────────────────
@dataclass
class ContextBudget:
    """컨텍스트 윈도우 예산 배분."""
    total_tokens: int
    tools_pct: float = 0.10
    history_pct: float = 0.60
    system_pct: float = 0.20
    reserve_pct: float = 0.10

    @property
    def tools_tokens(self) -> int:
        return int(self.total_tokens * self.tools_pct)

    @property
    def history_tokens(self) -> int:
        return int(self.total_tokens * self.history_pct)

    @property
    def system_tokens(self) -> int:
        return int(self.total_tokens * self.system_pct)

    @property
    def reserve_tokens(self) -> int:
        return int(self.total_tokens * self.reserve_pct)

    @classmethod
    def for_model(cls, model_name: str) -> "ContextBudget":
        return cls(total_tokens=get_context_tokens(model_name))


# ──────────────────────────────────────────────
# Tool Tracker
# ──────────────────────────────────────────────
class ToolTracker:
    """tool 사용 이력을 추적하여 미사용 tool을 evict합니다."""

    def __init__(self, evict_after_turns: int = 3):
        self.evict_after_turns = evict_after_turns
        self._last_used: Dict[str, int] = {}
        self._call_count: Dict[str, int] = {}
        self._ever_used: set[str] = set()

    def record_use(self, tool_name: str, turn: int) -> None:
        self._last_used[tool_name] = turn
        self._call_count[tool_name] = self._call_count.get(tool_name, 0) + 1
        self._ever_used.add(tool_name)

    def get_active_tools(
        self,
        all_tools: list,
        current_turn: int,
        budget_tokens: int = 0,
    ) -> list:
        """현재 턴에 포함할 tool 목록을 반환합니다.

        evict 조건:
          1. 시간 기준: 한 번도 호출 안 됐고 evict_after_turns 이상 경과
                        OR 호출됐지만 evict_after_turns 이상 미사용
          2. 예산 기준: budget_tokens > 0 이면 tool 개수를 예산 내로 제한
        """
        if current_turn < self.evict_after_turns:
            candidate = list(all_tools)
        else:
            candidate: list = []
            evicted_names: list[str] = []
            for tool in all_tools:
                name = getattr(tool, "__name__", str(tool))
                last = self._last_used.get(name)
                # 한 번도 호출 안 된 경우 (ever_used에 없으면 last도 None)
                if last is None:
                    evicted_names.append(name)
                    continue
                # 호출됐지만 오래된 경우
                if (current_turn - last) > self.evict_after_turns:
                    evicted_names.append(name)
                    continue
                candidate.append(tool)

            if evicted_names:
                logger.info("[CWM] Turn %d: evicted tools: %s", current_turn, evicted_names)

        # ── 예산 기반 추가 제한 (FIX #9) ──────────────────────────
        if budget_tokens > 0 and candidate:
            max_by_budget = max(1, budget_tokens // _TOOL_DEF_TOKEN_AVG)
            if len(candidate) > max_by_budget:
                # 사용 빈도 높은 순으로 유지
                def _priority(tool: Any) -> int:
                    name = getattr(tool, "__name__", str(tool))
                    return self._call_count.get(name, 0)
                candidate = sorted(candidate, key=_priority, reverse=True)[:max_by_budget]

        # 최소 1개는 유지 (빈 tools는 API 에러 가능)
        return candidate if candidate else list(all_tools[:1])

    def get_stale_tools(self, all_tools: list, current_turn: int) -> list[str]:
        """evict 대상 tool 이름 목록 (사용됐지만 오래된 것 + 한 번도 안 쓴 것).
        FIX #8: 이전에는 _last_used만 순회했으나 never-used tool도 포함.
        """
        stale = []
        if current_turn < self.evict_after_turns:
            return stale

        seen = set()
        # 사용됐지만 오래된 것
        for name, last in self._last_used.items():
            if (current_turn - last) > self.evict_after_turns:
                stale.append(name)
            seen.add(name)

        # 한 번도 사용 안 된 것
        for tool in all_tools:
            name = getattr(tool, "__name__", str(tool))
            if name not in seen and name not in self._ever_used:
                stale.append(name)

        return stale

    def get_stats(self) -> dict:
        return {
            "call_count": dict(self._call_count),
            "last_used": dict(self._last_used),
            "ever_used": list(self._ever_used),
        }


# ──────────────────────────────────────────────
# History Entry
# ──────────────────────────────────────────────
@dataclass
class HistoryEntry:
    """단일 대화 턴의 기록."""
    turn: int
    role: str  # "user" | "model" | "function_response"
    content: Any  # raw content
    token_estimate: int = 0
    compressed: bool = False
    original_tokens: int = 0  # 압축 전 토큰 수


# ──────────────────────────────────────────────
# History Manager
# ──────────────────────────────────────────────
TOOL_RESULT_TRUNCATE_THRESHOLD = 500
TOOL_RESULT_TRUNCATE_KEEP = 200


class HistoryManager:
    """대화 히스토리를 직접 관리하고 오래된 턴을 압축합니다.

    Gemini API 호환성:
      - function_response 엔트리는 role="function_response"로 저장
      - build_contents() 에서 같은 턴의 function_response들을 하나의 user Content로 그루핑
      - 이를 통해 연속 user role 금지 규칙을 준수
    """

    def __init__(self, budget_tokens: int, recent_window: int = 4):
        self.budget_tokens = budget_tokens
        self.recent_window = recent_window
        self._entries: list[HistoryEntry] = []
        self._max_turn: int = 0  # 이전 _current_turn → 명확한 이름으로 수정 (FIX #10)

    @property
    def entries(self) -> list[HistoryEntry]:
        return list(self._entries)

    @property
    def total_tokens(self) -> int:
        return sum(e.token_estimate for e in self._entries)

    def add_user_message(self, text: str, turn: int) -> None:
        """사용자 메시지 추가."""
        tokens = estimate_tokens(str(text))
        self._entries.append(HistoryEntry(
            turn=turn,
            role="user",
            content=text,
            token_estimate=tokens,
            original_tokens=tokens,
        ))
        self._max_turn = max(self._max_turn, turn)

    def add_model_response(self, parts_data: list[dict], turn: int) -> None:
        """모델 응답 추가."""
        text_repr = str(parts_data)
        tokens = estimate_tokens(text_repr)
        self._entries.append(HistoryEntry(
            turn=turn,
            role="model",
            content=parts_data,
            token_estimate=tokens,
            original_tokens=tokens,
        ))
        self._max_turn = max(self._max_turn, turn)

    def add_function_response(self, name: str, result: Any, turn: int) -> None:
        """function call 결과 추가. role은 'function_response'로 구분."""
        result_str = str(result)
        tokens = estimate_tokens(result_str)
        self._entries.append(HistoryEntry(
            turn=turn,
            role="function_response",
            content={"name": name, "result": result},
            token_estimate=tokens,
            original_tokens=tokens,
        ))
        self._max_turn = max(self._max_turn, turn)

    def build_contents(self, genai_types: Any = None) -> list:
        """generate_content()에 전달할 contents 리스트를 구성합니다.

        FIX #1: _enforce_budget() 호출 추가
        FIX #2: 같은 턴의 연속 function_response를 하나의 user Content로 그루핑
                → Gemini API 연속 user role 금지 규칙 준수

        Returns:
            Gemini API용 contents 리스트 (반드시 user/model 교대)
        """
        self._compress_old_entries()
        self._enforce_budget()  # FIX #1: 예산 초과 시 오래된 엔트리 제거

        contents: list = []
        i = 0
        while i < len(self._entries):
            entry = self._entries[i]

            if entry.role == "user":
                contents.append(self._make_user_content(entry, genai_types))
                i += 1

            elif entry.role == "model":
                contents.append(self._make_model_content(entry, genai_types))
                i += 1

            elif entry.role == "function_response":
                # FIX #2: 같은 턴의 연속 function_response를 모아 하나의 Content로 묶음
                group = [entry]
                j = i + 1
                while (
                    j < len(self._entries)
                    and self._entries[j].role == "function_response"
                    and self._entries[j].turn == entry.turn
                ):
                    group.append(self._entries[j])
                    j += 1
                contents.append(self._make_grouped_function_response_content(group, genai_types))
                i = j

            else:
                i += 1

        return contents

    def _compress_old_entries(self) -> None:
        """recent_window 밖의 function_response를 압축합니다."""
        # Bug fix: max(0, ...) — 초기 턴(0~recent_window)에서 음수 cutoff 방지
        cutoff_turn = max(0, self._max_turn - self.recent_window)
        for entry in self._entries:
            if entry.turn >= cutoff_turn:
                continue
            if entry.role != "function_response":
                continue
            if entry.compressed:
                continue

            result_str = (
                str(entry.content.get("result", ""))
                if isinstance(entry.content, dict)
                else str(entry.content)
            )
            if len(result_str) > TOOL_RESULT_TRUNCATE_THRESHOLD:
                truncated = result_str[:TOOL_RESULT_TRUNCATE_KEEP] + "\n...[truncated]..."
                if isinstance(entry.content, dict):
                    entry.content["result"] = truncated
                entry.original_tokens = entry.token_estimate
                entry.token_estimate = estimate_tokens(truncated)
                entry.compressed = True
                logger.debug(
                    "[CWM] Compressed turn %d function_response: saved ~%d tokens",
                    entry.turn,
                    entry.original_tokens - entry.token_estimate,
                )

    def _enforce_budget(self) -> None:
        """총 토큰이 budget을 초과하거나 항목 수가 상한을 넘으면 오래된 엔트리 제거.
        FIX #1: build_contents()에서 호출됨.
        최소 2개 엔트리(최신 user+model)는 항상 유지.
        항목 수 하드 상한(MAX_HISTORY_ENTRIES)을 초과해도 제거 — 토큰 예산과 별개.
        """
        while len(self._entries) > MAX_HISTORY_ENTRIES and len(self._entries) > 2:
            removed = self._entries.pop(0)
            logger.debug(
                "[CWM] Entry cap overflow: removed turn=%d role=%s (~%d tok)",
                removed.turn, removed.role, removed.token_estimate,
            )
        while self.total_tokens > self.budget_tokens and len(self._entries) > 2:
            removed = self._entries.pop(0)
            logger.debug(
                "[CWM] Budget overflow: removed turn=%d role=%s (~%d tok)",
                removed.turn, removed.role, removed.token_estimate,
            )

    # ── Content 생성 헬퍼 ──────────────────────────────────────────

    def _make_user_content(self, entry: HistoryEntry, genai_types: Any = None) -> Any:
        if genai_types:
            return genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=str(entry.content))],
            )
        return {"role": "user", "parts": [{"text": str(entry.content)}]}

    def _make_model_content(self, entry: HistoryEntry, genai_types: Any = None) -> Any:
        parts_data = (
            entry.content if isinstance(entry.content, list) else [{"text": str(entry.content)}]
        )
        if genai_types:
            parts = []
            for p in parts_data:
                if "text" in p:
                    parts.append(genai_types.Part.from_text(text=str(p["text"])))
                elif "function_call" in p:
                    fc = p["function_call"]
                    fc_name = fc["name"] if isinstance(fc, dict) else getattr(fc, "name", "")
                    fc_args = fc.get("args", {}) if isinstance(fc, dict) else getattr(fc, "args", {})
                    parts.append(genai_types.Part(
                        function_call=genai_types.FunctionCall(name=fc_name, args=fc_args)
                    ))
            # FIX #3: 빈 parts 방지
            if not parts:
                parts.append(genai_types.Part.from_text(text="[model response]"))
            return genai_types.Content(role="model", parts=parts)

        return {"role": "model", "parts": parts_data if parts_data else [{"text": "[model response]"}]}

    def _make_grouped_function_response_content(
        self,
        entries: list[HistoryEntry],
        genai_types: Any = None,
    ) -> Any:
        """FIX #2: 여러 function_response를 하나의 user Content로 묶어 반환.
        Gemini API는 같은 모델 턴의 여러 function call에 대해
        모든 응답을 하나의 user turn에 담아야 함.
        """
        if genai_types:
            parts = []
            for entry in entries:
                data = (
                    entry.content
                    if isinstance(entry.content, dict)
                    else {"name": "unknown", "result": entry.content}
                )
                parts.append(genai_types.Part.from_function_response(
                    name=str(data.get("name", "unknown")),
                    response={"result": data.get("result", "")},
                ))
            return genai_types.Content(role="user", parts=parts)

        # dict fallback (테스트용)
        parts = []
        for entry in entries:
            data = (
                entry.content
                if isinstance(entry.content, dict)
                else {"name": "unknown", "result": entry.content}
            )
            parts.append({
                "function_response": {
                    "name": str(data.get("name", "unknown")),
                    "response": {"result": data.get("result", "")},
                }
            })
        return {"role": "user", "parts": parts}

    def get_compression_stats(self) -> dict:
        compressed_count = sum(1 for e in self._entries if e.compressed)
        saved_tokens = sum(
            e.original_tokens - e.token_estimate for e in self._entries if e.compressed
        )
        return {
            "total_entries": len(self._entries),
            "compressed_entries": compressed_count,
            "saved_tokens": saved_tokens,
            "total_tokens": self.total_tokens,
            "budget_tokens": self.budget_tokens,
        }


# ──────────────────────────────────────────────
# Knowledge Injector
# ──────────────────────────────────────────────
class KnowledgeInjector:
    """Knowledge 스킬을 sys_prompt에 넣지 않고 필요할 때만 주입합니다."""

    def __init__(self, knowledge_skills: list | None = None):
        self._skills = knowledge_skills or []
        self._injected_ids: set[str] = set()

    def get_catalog_prompt(self) -> str:
        """사용 가능한 Knowledge 스킬 카탈로그 (이름/설명만, 본문 제외)."""
        if not self._skills:
            return ""
        lines = ["\n[Available Knowledge Skills]"]
        for s in self._skills:
            lines.append(f"- {s.name}: {s.description}")
        return "\n".join(lines)

    def get_content(self, skill_id: str) -> str:
        """특정 Knowledge 스킬의 전체 내용을 반환합니다."""
        for s in self._skills:
            if s.id == skill_id or s.name == skill_id:
                self._injected_ids.add(s.id)
                return f"### {s.name}\n{s.description}\n\n{s.content}"
        return f"Knowledge skill '{skill_id}' not found."

    def get_relevant_for_turn(self, model_text: str, budget_tokens: int = 0) -> str:
        """현재 턴 모델 응답 기준으로 관련 Knowledge를 자동 탐지하여 반환합니다.
        FIX #6: get_generate_config()에서 호출됨.
        """
        if not self._skills or not model_text:
            return ""

        try:
            from core.knowledge_skill import filter_relevant_knowledge
            relevant = filter_relevant_knowledge(self._skills, model_text)
        except (ImportError, Exception) as e:
            logger.warning("[CWM] Knowledge filtering unavailable: %s", e)
            return ""

        # 이미 주입된 것 제외
        new_relevant = [s for s in relevant if s.id not in self._injected_ids]
        if not new_relevant:
            return ""

        result_parts = []
        used_tokens = 0
        for s in new_relevant:
            content = f"### {s.name}\n{s.content}"
            tokens = estimate_tokens(content)
            if budget_tokens > 0 and (used_tokens + tokens) > budget_tokens:
                break
            result_parts.append(content)
            self._injected_ids.add(s.id)
            used_tokens += tokens

        if not result_parts:
            return ""
        return "\n[Knowledge Reference]\n" + "\n---\n".join(result_parts)

    @property
    def injected_count(self) -> int:
        return len(self._injected_ids)

    def get_stats(self) -> dict:
        return {
            "total_skills": len(self._skills),
            "injected_ids": list(self._injected_ids),
        }


# ──────────────────────────────────────────────
# Context Window Manager (통합)
# ──────────────────────────────────────────────
class ContextWindowManager:
    """
    컨텍스트 윈도우를 턴 단위로 최적화하는 매니저.

    chat.send_message() 대신 generate_content()와 함께 사용합니다.

    Gemini API 호환성 규칙:
      - 연속 user role 금지: function_response는 같은 턴끼리 그루핑
      - tool rejection/error는 add_user_message가 아닌 record_tool_result로 처리
    """

    def __init__(
        self,
        model_name: str,
        system_prompt: str,
        all_tools: list,
        knowledge_skills: list | None = None,
        evict_after_turns: int = 3,
        recent_window: int = 4,
    ):
        self.budget = ContextBudget.for_model(model_name)
        self.tool_tracker = ToolTracker(evict_after_turns=evict_after_turns)
        self.history = HistoryManager(
            budget_tokens=self.budget.history_tokens,
            recent_window=recent_window,
        )
        self.knowledge = KnowledgeInjector(knowledge_skills)
        self._all_tools = list(all_tools)
        self._base_system_prompt = system_prompt
        self._model_name = model_name
        self._last_model_text: str = ""  # FIX #6: knowledge on-demand에 사용

    def add_user_message(self, text: str, turn: int) -> None:
        self.history.add_user_message(text, turn)

    def record_tool_call(self, tool_name: str, turn: int) -> None:
        self.tool_tracker.record_use(tool_name, turn)

    def record_tool_result(self, tool_name: str, result: Any, turn: int) -> None:
        """tool 결과 기록. 성공/실패/거부 모두 이 메서드로 통일.
        FIX #4: add_user_message 대신 이 메서드를 사용해야
                연속 user role 문제를 방지할 수 있음.
        """
        self.history.add_function_response(tool_name, result, turn)

    def record_model_response(self, response: Any, turn: int) -> None:
        """Gemini response 객체를 히스토리에 기록합니다."""
        parts_data = []
        text_parts = []
        if hasattr(response, "parts") and response.parts:
            for part in response.parts:
                if hasattr(part, "text") and part.text:
                    parts_data.append({"text": part.text})
                    text_parts.append(part.text)
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    parts_data.append({
                        "function_call": {
                            "name": fc.name,
                            "args": dict(fc.args) if fc.args else {},
                        }
                    })
        # 빈 parts여도 role 교번 유지를 위해 항상 model 엔트리 기록
        self.history.add_model_response(parts_data or [{"text": ""}], turn)
        self._last_model_text = " ".join(text_parts)

    def get_generate_config(self, turn: int, genai_types: Any = None) -> dict:
        """현재 턴에 최적화된 generate_content() 설정을 반환합니다.

        FIX #6: get_relevant_for_turn()을 호출하여 Knowledge 온디맨드 주입.

        Returns:
            {
                "contents": [...],
                "system_instruction": "...",
                "tools": [...],
            }
        """
        # 1. Active tools (시간 기반 evict + 예산 기반 제한)
        active_tools = self.tool_tracker.get_active_tools(
            self._all_tools, turn, self.budget.tools_tokens,
        )

        # 2. System prompt: 카탈로그 + 현재 턴 관련 Knowledge 온디맨드 주입
        system = self._base_system_prompt
        catalog = self.knowledge.get_catalog_prompt()
        if catalog:
            system += catalog

        # FIX #6: 이전 모델 응답 텍스트를 기반으로 관련 Knowledge 자동 주입
        if self._last_model_text:
            relevant = self.knowledge.get_relevant_for_turn(
                self._last_model_text,
                budget_tokens=self.budget.system_tokens // 2,
            )
            if relevant:
                system += relevant

        # 3. History (압축 + 예산 집행 + function_response 그루핑)
        contents = self.history.build_contents(genai_types)

        return {
            "contents": contents,
            "system_instruction": system,
            "tools": active_tools,
        }

    def get_knowledge_content(self, skill_id: str) -> str:
        """get_knowledge tool에서 호출: 특정 Knowledge 스킬 내용 반환."""
        return self.knowledge.get_content(skill_id)

    def get_stats(self) -> dict:
        """현재 CWM 상태 통계."""
        return {
            "budget": {
                "total": self.budget.total_tokens,
                "tools": self.budget.tools_tokens,
                "history": self.budget.history_tokens,
                "system": self.budget.system_tokens,
            },
            "tools": self.tool_tracker.get_stats(),
            "history": self.history.get_compression_stats(),
            "knowledge": self.knowledge.get_stats(),
        }
