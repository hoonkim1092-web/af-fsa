"""
Phase 5: Context Fork Hook + Pre-flight Evaluator 테스트 (15개)

테스트 대상:
- Context Fork Hook: 도구 결과 요약 (7개)
- Pre-flight Evaluator: 스킬 신뢰도 검증 (8개)
"""
import json
import os
import shutil
import tempfile

import pytest


# ===========================================================================
# Context Fork Hook 테스트 (7개)
# ===========================================================================

class TestContextForkHook:
    """ContextForkHook 단위 테스트"""

    def test_short_result_passthrough(self):
        """threshold 이하 결과는 그대로 통과"""
        from core.hooks.context_fork import ContextForkHook

        hook = ContextForkHook(threshold=500, enabled=True)
        result = "short result"
        output = hook.post_tool_call({}, "some_tool", result)
        assert output == result

    def test_long_result_gets_summarized(self):
        """threshold 초과 결과는 요약됨"""
        from core.hooks.context_fork import ContextForkHook

        hook = ContextForkHook(threshold=50, enabled=True)
        long_result = "A" * 1000
        output = hook.post_tool_call({}, "web_search", long_result)
        # 요약되었으므로 원본보다 짧아야 함
        assert len(str(output)) < len(long_result)

    def test_disabled_hook_passthrough(self):
        """비활성화 시 결과 그대로 통과"""
        from core.hooks.context_fork import ContextForkHook

        hook = ContextForkHook(threshold=10, enabled=False)
        long_result = "A" * 1000
        output = hook.post_tool_call({}, "web_search", long_result)
        assert output == long_result

    def test_skip_tools_not_summarized(self):
        """_SKIP_TOOLS에 포함된 도구는 요약하지 않음"""
        from core.hooks.context_fork import ContextForkHook

        hook = ContextForkHook(threshold=10, enabled=True)
        long_result = "A" * 1000
        output = hook.post_tool_call({}, "core_memory_read", long_result)
        assert output == long_result

    def test_dict_result_contains_fork_metadata(self):
        """dict 결과 요약 시 __context_fork__ 메타 포함"""
        from core.hooks.context_fork import ContextForkHook

        hook = ContextForkHook(threshold=50, enabled=True)
        long_dict = {"stdout": "X" * 1000, "ok": True}
        output = hook.post_tool_call({}, "run_code", long_dict)
        assert isinstance(output, dict)
        assert output.get("__context_fork__") is True
        assert "__original_length__" in output
        assert "summary" in output

    def test_fallback_summary_format(self):
        """LLM 없이 fallback 요약이 올바른 형식"""
        from core.hooks.context_fork import _fallback_summarize

        text = "First meaningful line here.\n" + "Additional detail\n" * 100
        summary = _fallback_summarize("test_tool", text)
        assert summary.startswith("[test_tool]")
        assert "lines" in summary
        assert "chars" in summary
        assert len(summary) < 300

    def test_stats_tracking(self):
        """요약 통계가 올바르게 추적됨"""
        from core.hooks.context_fork import ContextForkHook

        hook = ContextForkHook(threshold=50, enabled=True)
        # 짧은 결과
        hook.post_tool_call({}, "t1", "short")
        # 긴 결과
        hook.post_tool_call({}, "t2", "X" * 500)
        hook.post_tool_call({}, "t3", "Y" * 500)

        assert hook.stats["calls"] == 3
        assert hook.stats["summarized"] == 2
        assert hook.stats["saved_chars"] > 0

    def test_per_tool_stats(self):
        """도구별 통계가 올바르게 추적됨"""
        from core.hooks.context_fork import ContextForkHook

        hook = ContextForkHook(threshold=50, enabled=True)
        hook.post_tool_call({}, "web_search", "X" * 500)
        hook.post_tool_call({}, "web_search", "Y" * 500)
        hook.post_tool_call({}, "code_gen", "Z" * 500)

        pts = hook.per_tool_stats
        assert pts["web_search"]["calls"] == 2
        assert pts["web_search"]["summarized"] == 2
        assert pts["code_gen"]["calls"] == 1

    def test_fallback_dict_status_extraction(self):
        """fallback 요약이 JSON dict에서 ok/error 상태를 추출"""
        from core.hooks.context_fork import _fallback_summarize
        import json

        dict_text = json.dumps({"ok": False, "reason": "timeout", "count": 42, "data": "x" * 1000})
        summary = _fallback_summarize("test_tool", dict_text)
        assert "ok=False" in summary
        assert "reason=timeout" in summary


# ===========================================================================
# Pre-flight Evaluator 테스트 (8개)
# ===========================================================================

class TestPreflightEvaluator:
    """PreflightEvaluator 단위 테스트"""

    @pytest.fixture
    def tmp_dir(self):
        d = tempfile.mkdtemp(prefix="preflight_test_")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def _write_skill(self, tmp_dir: str, name: str, code: str) -> str:
        """테스트용 스킬 파일 생성"""
        skill_dir = os.path.join(tmp_dir, name)
        os.makedirs(skill_dir, exist_ok=True)
        path = os.path.join(skill_dir, "skill.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        return path

    def test_perfect_skill_gets_active(self, tmp_dir):
        """항상 성공하는 스킬은 active"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "perfect-skill", """
def test(ctx):
    return {"ok": True, "message": "pass"}
""")
        ev = PreflightEvaluator(runs=5)
        result = ev.evaluate(path)
        assert result.status == "active"
        assert result.reliability == 1.0
        assert result.pass_count == 5

    def test_failing_skill_gets_rejected(self, tmp_dir):
        """항상 실패하는 스킬은 candidate 이하 (reliability=0)"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "failing-skill", """
def test(ctx):
    return {"ok": False, "reason": "always fails"}
""")
        ev = PreflightEvaluator(runs=5)
        result = ev.evaluate(path)
        # reliability=0 → overall = 0*0.5 + consistency*0.25 + stability*0.15 + speed*0.1 ≈ 0.5
        assert result.status in ("rejected", "candidate")
        assert result.reliability == 0.0
        assert result.fail_count == 5

    def test_error_skill_counted(self, tmp_dir):
        """예외 발생 스킬은 error로 카운트"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "error-skill", """
def test(ctx):
    raise RuntimeError("boom")
""")
        ev = PreflightEvaluator(runs=3)
        result = ev.evaluate(path)
        assert result.error_count == 3
        # reliability=0, consistency=1.0 (no output keys), stability varies
        # overall ≈ 0 + 0.25 + stability*0.15 + speed*0.1 → candidate or rejected
        assert result.status in ("rejected", "candidate")
        assert len(result.errors) == 3

    def test_partial_success_is_candidate(self, tmp_dir):
        """부분 성공 스킬은 candidate"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "partial-skill", """
_counter = 0
def test(ctx):
    global _counter
    _counter += 1
    return {"ok": _counter % 2 == 1}
""")
        ev = PreflightEvaluator(runs=10)
        result = ev.evaluate(path)
        # 5/10 = 0.5 reliability → candidate
        assert result.reliability == 0.5
        assert result.status == "candidate"

    def test_consistency_score(self, tmp_dir):
        """출력 키셋 일관성 점수 계산"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "consistent-skill", """
def test(ctx):
    return {"ok": True, "data": "same", "count": 1}
""")
        ev = PreflightEvaluator(runs=5)
        result = ev.evaluate(path)
        assert result.consistency == 1.0

    def test_speed_score_fast_skill(self, tmp_dir):
        """빠른 스킬은 높은 speed_score"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "fast-skill", """
def test(ctx):
    return {"ok": True}
""")
        ev = PreflightEvaluator(runs=3)
        result = ev.evaluate(path)
        # 순수 Python이므로 100ms 이하 → speed_score ≈ 1.0
        assert result.speed_score >= 0.9
        assert result.avg_duration_ms < 100

    def test_no_test_function_uses_apply(self, tmp_dir):
        """test() 없으면 apply()로 fallback"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "apply-only", """
def apply(ctx):
    return {"ok": True, "result": "applied"}
""")
        ev = PreflightEvaluator(runs=3)
        result = ev.evaluate(path)
        assert result.pass_count == 3
        assert result.status == "active"

    def test_no_functions_returns_error(self, tmp_dir):
        """test/apply 둘 다 없으면 error"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "empty-skill", """
# no test or apply
def propose(ctx):
    return {"skill_id": "empty"}
""")
        ev = PreflightEvaluator(runs=3)
        result = ev.evaluate(path)
        assert result.status == "error"

    def test_stability_score_consistent_speed(self, tmp_dir):
        """일관된 속도의 스킬은 높은 stability"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "stable-skill", """
def test(ctx):
    return {"ok": True, "v": 1}
""")
        ev = PreflightEvaluator(runs=5)
        result = ev.evaluate(path)
        # 순수 Python이므로 매우 일관된 속도
        assert result.stability >= 0.5
        assert result.stddev_duration_ms >= 0

    def test_overall_includes_stability(self, tmp_dir):
        """overall 점수에 stability가 반영됨"""
        from core.skill_preflight import PreflightEvaluator

        path = self._write_skill(tmp_dir, "full-score", """
def test(ctx):
    return {"ok": True, "data": "same"}
""")
        ev = PreflightEvaluator(runs=5)
        result = ev.evaluate(path)
        # reliability=1.0, consistency=1.0, stability~1.0, speed~1.0
        # overall = 1.0*0.5 + 1.0*0.25 + ~1.0*0.15 + ~1.0*0.1 ≈ 1.0
        assert result.overall >= 0.9
        assert result.status == "active"
