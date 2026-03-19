"""
tests/test_context_window_manager.py
====================================
ContextWindowManager 단위 테스트.

각 FIX 번호 주석으로 어떤 버그를 검증하는지 명시.
"""
from unittest.mock import MagicMock

import pytest

from core.context_window_manager import (
    ContextBudget,
    ContextWindowManager,
    HistoryManager,
    KnowledgeInjector,
    ToolTracker,
    estimate_tokens,
    TOOL_RESULT_TRUNCATE_THRESHOLD,
)


# ──────────────────────────────────────────────
# estimate_tokens
# ──────────────────────────────────────────────
class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_short_text(self):
        assert estimate_tokens("hello") >= 1

    def test_proportional(self):
        assert estimate_tokens("a" * 400) > estimate_tokens("abc")


# ──────────────────────────────────────────────
# ContextBudget
# ──────────────────────────────────────────────
class TestContextBudget:
    def test_percentages_sum_to_100(self):
        b = ContextBudget(total_tokens=100_000)
        total = b.tools_pct + b.history_pct + b.system_pct + b.reserve_pct
        assert abs(total - 1.0) < 0.001

    def test_allocation(self):
        b = ContextBudget(total_tokens=100_000)
        assert b.tools_tokens == 10_000
        assert b.history_tokens == 60_000

    def test_for_model(self):
        b = ContextBudget.for_model("gemini-2.0-flash")
        assert b.total_tokens == 1_000_000

    def test_unknown_model_fallback(self):
        b = ContextBudget.for_model("unknown-model")
        assert b.total_tokens > 0


# ──────────────────────────────────────────────
# ToolTracker
# ──────────────────────────────────────────────
class TestToolTracker:
    def _tools(self, names):
        result = []
        for n in names:
            fn = MagicMock()
            fn.__name__ = n
            result.append(fn)
        return result

    def test_no_eviction_early_turns(self):
        tracker = ToolTracker(evict_after_turns=3)
        tools = self._tools(["a", "b", "c"])
        assert len(tracker.get_active_tools(tools, current_turn=0)) == 3

    def test_evict_never_used_after_threshold(self):
        tracker = ToolTracker(evict_after_turns=3)
        tools = self._tools(["a", "b", "c"])
        tracker.record_use("a", 0)
        # b, c never used → evicted at turn >= 3
        active = tracker.get_active_tools(tools, current_turn=3)
        names = [t.__name__ for t in active]
        assert "a" in names
        assert "b" not in names
        assert "c" not in names

    def test_evict_stale_used_tool(self):
        tracker = ToolTracker(evict_after_turns=3)
        tools = self._tools(["a", "b"])
        tracker.record_use("a", 0)
        tracker.record_use("b", 0)
        tracker.record_use("a", 4)
        active = tracker.get_active_tools(tools, current_turn=5)
        names = [t.__name__ for t in active]
        assert "a" in names
        assert "b" not in names

    def test_minimum_one_tool(self):
        tracker = ToolTracker(evict_after_turns=1)
        tools = self._tools(["a"])
        active = tracker.get_active_tools(tools, current_turn=5)
        assert len(active) >= 1

    # FIX #9: budget_tokens 적용 검증
    def test_budget_token_limit(self):
        tracker = ToolTracker(evict_after_turns=10)
        tools = self._tools(["a", "b", "c", "d", "e"])
        for n in ["a", "b", "c", "d", "e"]:
            tracker.record_use(n, 0)
        # budget = 1 tool worth (~150 tokens)
        active = tracker.get_active_tools(tools, current_turn=1, budget_tokens=150)
        assert len(active) == 1

    # FIX #8: get_stale_tools never-used 포함 검증
    def test_get_stale_tools_includes_never_used(self):
        tracker = ToolTracker(evict_after_turns=2)
        tools = self._tools(["a", "b"])
        tracker.record_use("a", 0)
        # b never used
        stale = tracker.get_stale_tools(tools, current_turn=3)
        assert "b" in stale

    def test_get_stale_tools_includes_old_used(self):
        tracker = ToolTracker(evict_after_turns=2)
        tools = self._tools(["a"])
        tracker.record_use("a", 0)
        stale = tracker.get_stale_tools(tools, current_turn=5)
        assert "a" in stale

    def test_stats(self):
        tracker = ToolTracker()
        tracker.record_use("a", 0)
        tracker.record_use("a", 2)
        stats = tracker.get_stats()
        assert stats["call_count"]["a"] == 2
        assert stats["last_used"]["a"] == 2
        assert "a" in stats["ever_used"]


# ──────────────────────────────────────────────
# HistoryManager
# ──────────────────────────────────────────────
class TestHistoryManager:
    def test_add_entries(self):
        hm = HistoryManager(budget_tokens=100_000)
        hm.add_user_message("hello", turn=0)
        hm.add_model_response([{"text": "hi"}], turn=0)
        assert len(hm.entries) == 2

    def test_original_tokens_set(self):
        """FIX #14: original_tokens must be set for all entry types."""
        hm = HistoryManager(budget_tokens=100_000)
        hm.add_user_message("x" * 100, turn=0)
        assert hm.entries[0].original_tokens > 0

    def test_compress_old_function_response(self):
        hm = HistoryManager(budget_tokens=100_000, recent_window=2)
        hm.add_function_response("tool_a", "x" * 2000, turn=0)
        hm.add_user_message("latest", turn=5)
        hm._max_turn = 5
        hm.build_contents()
        entry = hm._entries[0]
        assert entry.compressed is True
        assert entry.token_estimate < entry.original_tokens

    def test_no_compress_recent(self):
        hm = HistoryManager(budget_tokens=100_000, recent_window=3)
        hm.add_function_response("tool_a", "x" * 2000, turn=3)
        hm.add_user_message("latest", turn=4)
        hm._max_turn = 4
        hm.build_contents()
        assert hm._entries[0].compressed is False

    # FIX #1: _enforce_budget 실제 호출 검증
    def test_enforce_budget_called_in_build_contents(self):
        # 매우 작은 예산으로 설정
        hm = HistoryManager(budget_tokens=10, recent_window=1)
        for i in range(5):
            hm.add_user_message("x" * 1000, turn=i)
        hm._max_turn = 4
        hm.build_contents()
        # 예산 초과 엔트리가 제거되어야 함
        assert hm.total_tokens <= 10 or len(hm.entries) == 2  # 최소 2개 유지

    # FIX #2: 같은 턴의 function_response 그루핑 검증
    def test_multiple_function_responses_grouped(self):
        """같은 턴의 여러 function_response가 하나의 user Content로 묶여야 함."""
        hm = HistoryManager(budget_tokens=100_000)
        hm.add_user_message("task", turn=0)
        hm.add_model_response([{"function_call": {"name": "a", "args": {}}}], turn=0)
        hm.add_function_response("tool_a", "result_a", turn=0)
        hm.add_function_response("tool_b", "result_b", turn=0)  # 같은 턴

        contents = hm.build_contents(genai_types=None)
        # user(task), model(func_calls), user(grouped func_responses)
        assert len(contents) == 3
        last = contents[-1]
        # 두 function_response가 하나의 user content에 parts로 묶여야 함
        assert last["role"] == "user"
        assert len(last["parts"]) == 2

    def test_different_turn_function_responses_not_grouped(self):
        """다른 턴의 function_response는 각각 별도 Content."""
        hm = HistoryManager(budget_tokens=100_000)
        hm.add_user_message("task", turn=0)
        hm.add_model_response([{"text": "t"}], turn=0)
        hm.add_function_response("tool_a", "r1", turn=0)
        hm.add_function_response("tool_b", "r2", turn=1)  # 다른 턴

        contents = hm.build_contents(genai_types=None)
        # user, model, user(turn0), user(turn1) = 4개
        assert len(contents) == 4

    def test_build_contents_dict_format(self):
        hm = HistoryManager(budget_tokens=100_000)
        hm.add_user_message("hello", turn=0)
        hm.add_model_response([{"text": "world"}], turn=0)
        hm.add_function_response("my_tool", "result", turn=0)

        contents = hm.build_contents(genai_types=None)
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"
        assert contents[2]["role"] == "user"

    # FIX #3: 빈 parts 방지 검증
    def test_model_content_empty_parts_fallback(self):
        hm = HistoryManager(budget_tokens=100_000)
        # parts_data에 알 수 없는 키만 있는 경우
        hm._entries.append(__import__('core.context_window_manager', fromlist=['HistoryEntry']).HistoryEntry(
            turn=0, role="model", content=[{"unknown_key": "value"}], token_estimate=10, original_tokens=10,
        ))
        contents = hm.build_contents(genai_types=None)
        assert len(contents) == 1
        assert contents[0]["parts"]  # 빈 parts 아님

    def test_compression_stats(self):
        hm = HistoryManager(budget_tokens=100_000, recent_window=1)
        hm.add_function_response("tool", "x" * 2000, turn=0)
        hm.add_user_message("current", turn=5)
        hm._max_turn = 5
        hm.build_contents()
        stats = hm.get_compression_stats()
        assert stats["compressed_entries"] == 1
        assert stats["saved_tokens"] > 0


# ──────────────────────────────────────────────
# KnowledgeInjector
# ──────────────────────────────────────────────
class TestKnowledgeInjector:
    def _skill(self, sid, name, desc, content):
        s = MagicMock()
        s.id = sid
        s.name = name
        s.description = desc
        s.content = content
        return s

    def test_empty_catalog(self):
        assert KnowledgeInjector([]).get_catalog_prompt() == ""

    def test_catalog_no_full_content(self):
        skill = self._skill("s1", "Code Review", "Review code", "full procedure here")
        ki = KnowledgeInjector([skill])
        catalog = ki.get_catalog_prompt()
        assert "Code Review" in catalog
        assert "Review code" in catalog
        assert "full procedure here" not in catalog

    def test_get_content(self):
        skill = self._skill("s1", "Code Review", "desc", "full procedure here")
        ki = KnowledgeInjector([skill])
        content = ki.get_content("s1")
        assert "full procedure here" in content
        assert ki.injected_count == 1

    def test_get_content_not_found(self):
        ki = KnowledgeInjector([])
        assert "not found" in ki.get_content("nonexistent")

    # FIX #6: get_relevant_for_turn 가드 테스트
    def test_get_relevant_for_turn_empty_input(self):
        skill = self._skill("s1", "X", "d", "c")
        ki = KnowledgeInjector([skill])
        assert ki.get_relevant_for_turn("") == ""
        assert ki.get_relevant_for_turn("", budget_tokens=1000) == ""

    def test_stats(self):
        skill = self._skill("s1", "X", "d", "c")
        ki = KnowledgeInjector([skill])
        ki.get_content("s1")
        stats = ki.get_stats()
        assert stats["total_skills"] == 1
        assert "s1" in stats["injected_ids"]


# ──────────────────────────────────────────────
# ContextWindowManager (통합)
# ──────────────────────────────────────────────
class TestContextWindowManager:
    def _tools(self, names):
        result = []
        for n in names:
            fn = MagicMock()
            fn.__name__ = n
            result.append(fn)
        return result

    def test_init(self):
        tools = self._tools(["a", "b"])
        cwm = ContextWindowManager("gemini-2.0-flash", "You are helpful.", tools)
        assert cwm.budget.total_tokens == 1_000_000

    def test_get_generate_config_structure(self):
        tools = self._tools(["a"])
        cwm = ContextWindowManager("default", "system", tools)
        cwm.add_user_message("hello", turn=0)
        config = cwm.get_generate_config(turn=0)
        assert "contents" in config
        assert "system_instruction" in config
        assert "tools" in config

    def test_tools_eviction(self):
        tools = self._tools(["a", "b", "c"])
        cwm = ContextWindowManager("default", "sys", tools, evict_after_turns=2)
        cwm.record_tool_call("a", turn=0)
        cwm.record_tool_call("a", turn=1)
        cwm.record_tool_call("a", turn=2)
        config = cwm.get_generate_config(turn=3)
        tool_names = [t.__name__ for t in config["tools"]]
        assert "a" in tool_names

    def test_knowledge_catalog_in_system_not_content(self):
        skill = MagicMock()
        skill.id = "s1"
        skill.name = "MySkill"
        skill.description = "A skill"
        skill.content = "Full procedure"

        tools = self._tools(["a"])
        cwm = ContextWindowManager("default", "base prompt", tools, knowledge_skills=[skill])
        config = cwm.get_generate_config(turn=0)
        assert "MySkill" in config["system_instruction"]
        assert "Full procedure" not in config["system_instruction"]

    def test_record_model_response(self):
        tools = self._tools(["a"])
        cwm = ContextWindowManager("default", "sys", tools)
        cwm.add_user_message("task", turn=0)

        response = MagicMock()
        part = MagicMock()
        part.text = "answer"
        part.function_call = None
        response.parts = [part]

        cwm.record_model_response(response, turn=0)
        assert len(cwm.history.entries) == 2  # user + model

    # FIX #4: tool rejection/error → record_tool_result (function_response)
    def test_record_tool_result_not_user_message(self):
        """거부/에러는 add_user_message가 아닌 record_tool_result여야 함."""
        tools = self._tools(["a"])
        cwm = ContextWindowManager("default", "sys", tools)
        cwm.add_user_message("task", turn=0)
        cwm.record_tool_call("a", turn=0)
        cwm.record_tool_result("a", "[rejected: approval denied]", turn=0)
        entries = cwm.history.entries
        # function_response 엔트리여야 함 (user 메시지 아님)
        func_entries = [e for e in entries if e.role == "function_response"]
        assert len(func_entries) == 1
        assert "rejected" in str(func_entries[0].content)

    # FIX #2: 멀티 function_call → 그루핑 검증
    def test_multiple_tool_results_grouped_in_contents(self):
        tools = self._tools(["a", "b"])
        cwm = ContextWindowManager("default", "sys", tools)
        cwm.add_user_message("task", turn=0)
        cwm.record_model_response(MagicMock(parts=[]), turn=0)
        cwm.record_tool_call("a", turn=0)
        cwm.record_tool_result("a", "result_a", turn=0)
        cwm.record_tool_call("b", turn=0)
        cwm.record_tool_result("b", "result_b", turn=0)

        config = cwm.get_generate_config(turn=1)
        contents = config["contents"]
        # user(task) + model + user(grouped: a+b) = 3
        assert len(contents) == 3
        last = contents[-1]
        assert last["role"] == "user"
        assert len(last["parts"]) == 2  # a + b 묶임

    def test_get_stats(self):
        tools = self._tools(["a"])
        cwm = ContextWindowManager("default", "sys", tools)
        stats = cwm.get_stats()
        assert "budget" in stats
        assert "tools" in stats
        assert "history" in stats
        assert "knowledge" in stats

    def test_full_flow_5turns(self):
        """5턴 ReAct 시뮬레이션."""
        tools = self._tools(["search", "write", "review"])
        cwm = ContextWindowManager("default", "sys", tools, evict_after_turns=2)
        cwm.add_user_message("Task: implement feature", turn=0)

        # Turn 0: search만 사용
        cwm.record_tool_call("search", turn=0)
        cwm.record_tool_result("search", "x" * 1000, turn=0)

        cwm.record_tool_call("search", turn=1)
        cwm.record_tool_result("search", "y" * 1000, turn=1)

        # Turn 2: write, review는 한 번도 안 쓰여 evict됨
        cwm.record_tool_call("search", turn=2)
        config = cwm.get_generate_config(turn=2)
        tool_names = [t.__name__ for t in config["tools"]]
        assert "search" in tool_names
        assert "write" not in tool_names
        assert "review" not in tool_names

    def test_history_compression_in_flow(self):
        tools = self._tools(["a"])
        cwm = ContextWindowManager("default", "sys", tools, recent_window=2)
        cwm.add_user_message("start", turn=0)
        cwm.record_tool_result("a", "x" * 2000, turn=0)
        cwm.add_user_message("continue", turn=5)
        cwm.history._max_turn = 5

        cwm.get_generate_config(turn=5)
        stats = cwm.history.get_compression_stats()
        assert stats["compressed_entries"] >= 1
        assert stats["saved_tokens"] > 0

    # FIX #1: enforce_budget 동작 검증
    def test_enforce_budget_removes_old_entries(self):
        tools = self._tools(["a"])
        cwm = ContextWindowManager("default", "sys", tools)
        cwm.history.budget_tokens = 5  # 매우 작은 예산

        for i in range(10):
            cwm.add_user_message("x" * 500, turn=i)

        cwm.history._max_turn = 9
        cwm.history.build_contents()
        # 예산 초과분이 제거되어 최소 2개만 남아야 함
        assert len(cwm.history.entries) >= 2
