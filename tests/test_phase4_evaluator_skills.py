"""
Phase 4: Evaluator 스킬 테스트 (15개)

테스트 대상:
- trace_execution: JSONL 로그 파싱 및 실행 흐름 분석 (5개)
- summarize_failure: 실패 원인 분석 및 요약 (5개)
- generate_eval_dataset: 평가 데이터셋 자동 생성 (5개)
"""
import json
import os
import shutil
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="phase4_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write_jsonl(path: str, events: list[dict]):
    """JSONL 파일 생성 헬퍼"""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for evt in events:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")


def _make_success_events(run_id="run_ok", skill="web-search"):
    return [
        {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
         "run_id": run_id, "agent_name": "developer", "task_input": "테스트 작업"},
        {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
         "run_id": run_id, "skill_name": skill, "skill_args": {"query": "Python async"}},
        {"timestamp": "2026-03-13T10:00:03Z", "event_type": "skill_call_end",
         "run_id": run_id, "skill_name": skill, "skill_result": "Found 5 results"},
        {"timestamp": "2026-03-13T10:00:05Z", "event_type": "run_end",
         "run_id": run_id, "ok": True, "reason": "success", "duration_ms": 5000},
    ]


def _make_failure_events(run_id="run_fail", skill="web-search", error="ConnectionError: Network timeout"):
    return [
        {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
         "run_id": run_id, "agent_name": "developer", "task_input": "실패 작업"},
        {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
         "run_id": run_id, "skill_name": skill, "skill_args": {"query": "test"}},
        {"timestamp": "2026-03-13T10:00:02Z", "event_type": "skill_call_end",
         "run_id": run_id, "skill_name": skill, "skill_result": error},
        {"timestamp": "2026-03-13T10:00:03Z", "event_type": "captured_output",
         "run_id": run_id, "content": f"stderr: {error}"},
        {"timestamp": "2026-03-13T10:00:04Z", "event_type": "run_end",
         "run_id": run_id, "ok": False, "reason": "error", "duration_ms": 4000},
    ]


# ===========================================================================
# trace_execution 테스트 (5개)
# ===========================================================================

class TestTraceExecution:
    """trace_execution 스킬 테스트"""

    def test_parse_normal_jsonl(self, tmp_dir):
        """정상 JSONL 파일 파싱 및 출력 구조 검증"""
        from skills.evaluator.trace_execution.skill import apply

        log_file = os.path.join(tmp_dir, "trace_run_ok.jsonl")
        _write_jsonl(log_file, _make_success_events())

        result = apply({"log_file": log_file})

        assert result["ok"] is True
        assert result["run_id"] == "run_ok"
        assert result["agent_name"] == "developer"
        assert result["status"] == "success"
        assert result["total_duration_ms"] == 5000
        assert len(result["skill_calls"]) == 1
        assert result["skill_calls"][0]["skill_name"] == "web-search"

    def test_corrupted_json_handling(self, tmp_dir):
        """손상된 JSON 줄을 건너뛰고 정상 줄만 처리"""
        from skills.evaluator.trace_execution.skill import apply

        log_file = os.path.join(tmp_dir, "trace_corrupted.jsonl")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
                                "run_id": "corr", "agent_name": "test", "task_input": "x"}) + "\n")
            f.write("THIS IS NOT VALID JSON\n")
            f.write("{broken json\n")
            f.write(json.dumps({"timestamp": "2026-03-13T10:00:05Z", "event_type": "run_end",
                                "run_id": "corr", "ok": True, "reason": "success",
                                "duration_ms": 5000}) + "\n")

        result = apply({"log_file": log_file})

        assert result["ok"] is True
        assert result["run_id"] == "corr"
        assert result["status"] == "success"
        assert result["total_events"] == 2  # 손상된 줄 2개 건너뜀

    def test_incomplete_log_missing_end(self, tmp_dir):
        """end 이벤트 누락 시 incomplete 처리"""
        from skills.evaluator.trace_execution.skill import apply

        events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "inc", "agent_name": "test", "task_input": "test"},
            {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
             "run_id": "inc", "skill_name": "code-gen", "skill_args": {"lang": "python"}},
            # skill_call_end 누락, run_end 누락
        ]
        log_file = os.path.join(tmp_dir, "trace_incomplete.jsonl")
        _write_jsonl(log_file, events)

        result = apply({"log_file": log_file})

        assert result["ok"] is True
        assert result["status"] == "incomplete"
        assert result["reason"] == "missing_run_end"
        # 불완전한 스킬 호출도 포함
        assert len(result["skill_calls"]) == 1
        assert result["skill_calls"][0]["status"] == "incomplete"

    def test_output_structure_fields(self, tmp_dir):
        """출력 딕셔너리의 필수 필드 존재 확인"""
        from skills.evaluator.trace_execution.skill import apply

        log_file = os.path.join(tmp_dir, "trace_fields.jsonl")
        _write_jsonl(log_file, _make_success_events("run_fields"))

        result = apply({"log_file": log_file})

        required_keys = ["ok", "run_id", "agent_name", "total_duration_ms",
                         "task_input", "skill_calls", "status", "reason", "total_events"]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

        # skill_call 구조
        sc = result["skill_calls"][0]
        sc_keys = ["sequence", "skill_name", "start_time", "end_time", "duration_ms"]
        for key in sc_keys:
            assert key in sc, f"Missing skill_call key: {key}"

    def test_duration_accuracy(self, tmp_dir):
        """타임스탬프 기반 duration 계산 정확도"""
        from skills.evaluator.trace_execution.skill import apply

        events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "dur", "agent_name": "t", "task_input": "t"},
            {"timestamp": "2026-03-13T10:00:01.500Z", "event_type": "skill_call_start",
             "run_id": "dur", "skill_name": "s1", "skill_args": {}},
            {"timestamp": "2026-03-13T10:00:03.500Z", "event_type": "skill_call_end",
             "run_id": "dur", "skill_name": "s1", "skill_result": "ok"},
            {"timestamp": "2026-03-13T10:00:05Z", "event_type": "run_end",
             "run_id": "dur", "ok": True, "reason": "success", "duration_ms": 5000},
        ]
        log_file = os.path.join(tmp_dir, "trace_dur.jsonl")
        _write_jsonl(log_file, events)

        result = apply({"log_file": log_file})

        assert result["ok"] is True
        # 스킬 호출 duration: 3.5s - 1.5s = 2000ms
        sc = result["skill_calls"][0]
        assert sc["duration_ms"] == 2000

    def test_same_skill_called_twice(self, tmp_dir):
        """같은 스킬이 2번 호출되어도 각각 올바르게 매칭"""
        from skills.evaluator.trace_execution.skill import apply

        events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "dup", "agent_name": "t", "task_input": "t"},
            {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
             "run_id": "dup", "skill_name": "web-search", "skill_args": {"q": "first"}},
            {"timestamp": "2026-03-13T10:00:02Z", "event_type": "skill_call_end",
             "run_id": "dup", "skill_name": "web-search", "skill_result": "result1"},
            {"timestamp": "2026-03-13T10:00:03Z", "event_type": "skill_call_start",
             "run_id": "dup", "skill_name": "web-search", "skill_args": {"q": "second"}},
            {"timestamp": "2026-03-13T10:00:04Z", "event_type": "skill_call_end",
             "run_id": "dup", "skill_name": "web-search", "skill_result": "result2"},
            {"timestamp": "2026-03-13T10:00:05Z", "event_type": "run_end",
             "run_id": "dup", "ok": True, "reason": "success", "duration_ms": 5000},
        ]
        log_file = os.path.join(tmp_dir, "trace_dup.jsonl")
        _write_jsonl(log_file, events)

        result = apply({"log_file": log_file})

        assert result["ok"] is True
        assert len(result["skill_calls"]) == 2
        assert result["skill_calls"][0]["skill_name"] == "web-search"
        assert result["skill_calls"][1]["skill_name"] == "web-search"
        # 첫 번째 호출의 args가 유실되지 않았는지 확인
        assert "first" in result["skill_calls"][0].get("args_summary", "")
        assert "second" in result["skill_calls"][1].get("args_summary", "")

    def test_timeline_anomaly_large_gap(self, tmp_dir):
        """타임라인 갭 분석: 큰 갭이 감지되는지"""
        from skills.evaluator.trace_execution.skill import apply

        events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "gap", "agent_name": "t", "task_input": "t"},
            {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
             "run_id": "gap", "skill_name": "s1", "skill_args": {}},
            # 20초 갭
            {"timestamp": "2026-03-13T10:00:21Z", "event_type": "skill_call_end",
             "run_id": "gap", "skill_name": "s1", "skill_result": "ok"},
            {"timestamp": "2026-03-13T10:00:22Z", "event_type": "run_end",
             "run_id": "gap", "ok": True, "reason": "success", "duration_ms": 22000},
        ]
        log_file = os.path.join(tmp_dir, "trace_gap.jsonl")
        _write_jsonl(log_file, events)

        result = apply({"log_file": log_file})
        assert result["ok"] is True
        assert "anomalies" in result
        assert any(a["type"] == "large_gap" for a in result["anomalies"])

    def test_skill_error_detection(self, tmp_dir):
        """스킬 결과에서 에러 패턴 감지"""
        from skills.evaluator.trace_execution.skill import apply

        events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "err", "agent_name": "t", "task_input": "t"},
            {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
             "run_id": "err", "skill_name": "api-call", "skill_args": {}},
            {"timestamp": "2026-03-13T10:00:02Z", "event_type": "skill_call_end",
             "run_id": "err", "skill_name": "api-call",
             "skill_result": "ConnectionError: Connection refused"},
            {"timestamp": "2026-03-13T10:00:03Z", "event_type": "run_end",
             "run_id": "err", "ok": False, "reason": "error", "duration_ms": 3000},
        ]
        log_file = os.path.join(tmp_dir, "trace_err.jsonl")
        _write_jsonl(log_file, events)

        result = apply({"log_file": log_file})
        assert result["ok"] is True
        assert "skill_errors" in result
        assert len(result["skill_errors"]) >= 1
        assert result["skill_errors"][0]["skill_name"] == "api-call"


# ===========================================================================
# summarize_failure 테스트 (5개)
# ===========================================================================

class TestSummarizeFailure:
    """summarize_failure 스킬 테스트"""

    def test_detect_failure(self, tmp_dir):
        """실패 케이스 감지"""
        from skills.evaluator.summarize_failure.skill import apply

        log_file = os.path.join(tmp_dir, "trace_fail.jsonl")
        _write_jsonl(log_file, _make_failure_events())

        result = apply({"log_file": log_file})

        assert result["ok"] is True
        assert result["failure_type"] != "none"
        assert result["run_id"] == "run_fail"

    def test_last_skill_call_tracking(self, tmp_dir):
        """마지막 스킬 호출 추적"""
        from skills.evaluator.summarize_failure.skill import apply

        events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "r1", "agent_name": "t", "task_input": "t"},
            {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
             "run_id": "r1", "skill_name": "s1", "skill_args": {}},
            {"timestamp": "2026-03-13T10:00:02Z", "event_type": "skill_call_end",
             "run_id": "r1", "skill_name": "s1", "skill_result": "ok"},
            {"timestamp": "2026-03-13T10:00:03Z", "event_type": "skill_call_start",
             "run_id": "r1", "skill_name": "s2-failing", "skill_args": {}},
            {"timestamp": "2026-03-13T10:00:04Z", "event_type": "skill_call_end",
             "run_id": "r1", "skill_name": "s2-failing",
             "skill_result": "TypeError: invalid argument"},
            {"timestamp": "2026-03-13T10:00:05Z", "event_type": "run_end",
             "run_id": "r1", "ok": False, "reason": "error", "duration_ms": 5000},
        ]
        log_file = os.path.join(tmp_dir, "trace_multi.jsonl")
        _write_jsonl(log_file, events)

        result = apply({"log_file": log_file})

        assert result["ok"] is True
        assert result["affected_skill"] == "s2-failing"

    def test_error_type_classification_timeout(self, tmp_dir):
        """에러 타입 분류: timeout"""
        from skills.evaluator.summarize_failure.skill import apply

        events = _make_failure_events(error="Request timed out after 120s")
        events[-1]["reason"] = "timeout"
        log_file = os.path.join(tmp_dir, "trace_timeout.jsonl")
        _write_jsonl(log_file, events)

        result = apply({"log_file": log_file})

        assert result["ok"] is True
        assert result["failure_type"] == "timeout"
        assert result["severity"] == "high"

    def test_similar_failure_search(self, tmp_dir):
        """유사 에러 찾기"""
        from skills.evaluator.summarize_failure.skill import apply

        # 여러 실패 로그 생성
        for i in range(3):
            events = _make_failure_events(run_id=f"sim_{i}", error="timeout error")
            events[-1]["reason"] = "timeout"
            _write_jsonl(os.path.join(tmp_dir, f"trace_sim_{i}.jsonl"), events)

        log_file = os.path.join(tmp_dir, "trace_sim_0.jsonl")
        result = apply({"log_file": log_file})

        assert result["ok"] is True
        assert "similar_failures" in result
        # 자기 자신 제외, 2개 유사 실패
        assert len(result["similar_failures"]) >= 1

    def test_suggestions_generation(self, tmp_dir):
        """수정 제안 생성 검증"""
        from skills.evaluator.summarize_failure.skill import apply

        log_file = os.path.join(tmp_dir, "trace_suggest.jsonl")
        _write_jsonl(log_file, _make_failure_events(error="ConnectionError: refused"))

        result = apply({"log_file": log_file, "include_suggestions": True})

        assert result["ok"] is True
        assert "suggestions" in result
        assert isinstance(result["suggestions"], list)
        assert len(result["suggestions"]) > 0

    def test_error_chain_tracking(self, tmp_dir):
        """에러 체인 추적: 연쇄 실패 감지"""
        from skills.evaluator.summarize_failure.skill import apply

        events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "chain", "agent_name": "t", "task_input": "t"},
            {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
             "run_id": "chain", "skill_name": "s1", "skill_args": {}},
            {"timestamp": "2026-03-13T10:00:02Z", "event_type": "skill_call_end",
             "run_id": "chain", "skill_name": "s1",
             "skill_result": "ConnectionError: refused"},
            {"timestamp": "2026-03-13T10:00:03Z", "event_type": "skill_call_start",
             "run_id": "chain", "skill_name": "s2", "skill_args": {}},
            {"timestamp": "2026-03-13T10:00:04Z", "event_type": "skill_call_end",
             "run_id": "chain", "skill_name": "s2",
             "skill_result": "Error: upstream failed"},
            {"timestamp": "2026-03-13T10:00:05Z", "event_type": "run_end",
             "run_id": "chain", "ok": False, "reason": "error", "duration_ms": 5000},
        ]
        log_file = os.path.join(tmp_dir, "trace_chain.jsonl")
        _write_jsonl(log_file, events)

        result = apply({"log_file": log_file})
        assert result["ok"] is True
        assert "error_chain" in result
        assert len(result["error_chain"]) == 2
        # 연쇄 실패 → severity 상승
        assert result["severity"] in ("high", "critical")


# ===========================================================================
# generate_eval_dataset 테스트 (5개)
# ===========================================================================

class TestGenerateEvalDataset:
    """generate_eval_dataset 스킬 테스트"""

    def test_directory_scan(self, tmp_dir):
        """로그 디렉토리 스캔"""
        from skills.evaluator.generate_eval_dataset.skill import apply

        # 3개 로그 생성 (각각 다른 task_input으로 중복 방지)
        for i in range(3):
            events = _make_success_events(f"scan_{i}", "web-search")
            events[0]["task_input"] = f"unique task {i}: {['search', 'analyze', 'generate'][i]}"
            _write_jsonl(
                os.path.join(tmp_dir, f"trace_scan_{i}.jsonl"),
                events,
            )

        output = os.path.join(tmp_dir, "out.jsonl")
        result = apply({"log_dir": tmp_dir, "output_file": output})

        assert result["ok"] is True
        assert result["total_cases"] == 3

    def test_eval_case_generation(self, tmp_dir):
        """평가 케이스 생성 및 JSONL 형식"""
        from skills.evaluator.generate_eval_dataset.skill import apply

        _write_jsonl(
            os.path.join(tmp_dir, "trace_case_0.jsonl"),
            _make_success_events("case_0", "code-gen"),
        )
        _write_jsonl(
            os.path.join(tmp_dir, "trace_case_1.jsonl"),
            _make_failure_events("case_1", "code-gen"),
        )

        output = os.path.join(tmp_dir, "eval.jsonl")
        result = apply({"log_dir": tmp_dir, "output_file": output})

        assert result["ok"] is True
        assert os.path.exists(output)

        # JSONL 형식 검증
        with open(output, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) == 2
        for line in lines:
            case = json.loads(line)
            assert "input" in case
            assert "expected_skills" in case
            assert "success_rate" in case

    def test_success_rate_calculation(self, tmp_dir):
        """성공률 계산"""
        from skills.evaluator.generate_eval_dataset.skill import apply

        # 2 성공, 1 실패
        _write_jsonl(os.path.join(tmp_dir, "trace_sr_0.jsonl"),
                     _make_success_events("sr_0", "web-search"))
        _write_jsonl(os.path.join(tmp_dir, "trace_sr_1.jsonl"),
                     _make_success_events("sr_1", "web-search"))
        _write_jsonl(os.path.join(tmp_dir, "trace_sr_2.jsonl"),
                     _make_failure_events("sr_2", "web-search"))

        output = os.path.join(tmp_dir, "sr_eval.jsonl")
        result = apply({"log_dir": tmp_dir, "output_file": output})

        assert result["ok"] is True
        assert result["success_cases"] == 2
        assert result["failure_cases"] == 1

    def test_skill_coverage_analysis(self, tmp_dir):
        """스킬별 커버리지 분석"""
        from skills.evaluator.generate_eval_dataset.skill import apply

        _write_jsonl(os.path.join(tmp_dir, "trace_cov_0.jsonl"),
                     _make_success_events("cov_0", "web-search"))
        _write_jsonl(os.path.join(tmp_dir, "trace_cov_1.jsonl"),
                     _make_success_events("cov_1", "code-gen"))
        _write_jsonl(os.path.join(tmp_dir, "trace_cov_2.jsonl"),
                     _make_failure_events("cov_2", "web-search"))

        output = os.path.join(tmp_dir, "cov_eval.jsonl")
        result = apply({"log_dir": tmp_dir, "output_file": output})

        assert result["ok"] is True
        coverage = result["coverage"]
        assert "skills" in coverage
        assert "by_skill" in coverage
        assert "web-search" in coverage["by_skill"]
        assert "code-gen" in coverage["by_skill"]
        # web-search: 1 성공 / 2 총 = 0.5
        assert coverage["by_skill"]["web-search"]["count"] == 2
        assert coverage["by_skill"]["web-search"]["success_rate"] == 0.5

    def test_dataset_quality_metrics(self, tmp_dir):
        """데이터셋 품질 메트릭 산출"""
        from skills.evaluator.generate_eval_dataset.skill import apply

        _write_jsonl(os.path.join(tmp_dir, "trace_q_0.jsonl"),
                     _make_success_events("q_0", "web-search"))
        _write_jsonl(os.path.join(tmp_dir, "trace_q_1.jsonl"),
                     _make_success_events("q_1", "code-gen"))
        _write_jsonl(os.path.join(tmp_dir, "trace_q_2.jsonl"),
                     _make_failure_events("q_2", "web-search"))

        output = os.path.join(tmp_dir, "q_eval.jsonl")
        result = apply({"log_dir": tmp_dir, "output_file": output})

        assert result["ok"] is True
        assert "quality" in result
        quality = result["quality"]
        assert "score" in quality
        assert "diversity" in quality
        assert "balance" in quality
        assert 0 <= quality["score"] <= 1

    def test_jsonl_format_validity(self, tmp_dir):
        """출력 JSONL 파일의 모든 줄이 유효한 JSON"""
        from skills.evaluator.generate_eval_dataset.skill import apply

        for i in range(5):
            ok = i < 3
            if ok:
                _write_jsonl(os.path.join(tmp_dir, f"trace_fmt_{i}.jsonl"),
                             _make_success_events(f"fmt_{i}", "s1"))
            else:
                _write_jsonl(os.path.join(tmp_dir, f"trace_fmt_{i}.jsonl"),
                             _make_failure_events(f"fmt_{i}", "s1"))

        output = os.path.join(tmp_dir, "fmt_eval.jsonl")
        result = apply({"log_dir": tmp_dir, "output_file": output})

        assert result["ok"] is True
        with open(output, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    assert isinstance(parsed, dict), f"Line {line_num} is not a dict"
                except json.JSONDecodeError:
                    pytest.fail(f"Line {line_num} is invalid JSON: {line[:100]}")
