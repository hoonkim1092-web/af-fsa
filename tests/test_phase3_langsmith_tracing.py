"""
Phase 3: LangSmith Tracing Hook 강화 테스트

JSONL 로깅, stdout/stderr 캡처 기능 검증
"""
import os
import json
import tempfile
import shutil
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.hooks.langsmith_tracing import LangSmithTracingHook, _StdoutCapturer
from core.hooks.base import ToolCallDecision


class TestStdoutCapturer:
    """stdout/stderr 캡처 기능 테스트"""

    def test_capture_stdout_to_file(self):
        """stdout을 파일에 캡처"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")

            with _StdoutCapturer(log_file) as capturer:
                print("hello world")
                print("second line")

            # 파일 확인
            assert os.path.exists(log_file)
            with open(log_file, 'r') as f:
                content = f.read()
                assert "hello world" in content
                assert "second line" in content

            # 메모리 버퍼 확인
            assert "hello world" in capturer.get_captured()

    def test_capture_empty(self):
        """빈 출력 캡처"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")

            with _StdoutCapturer(log_file) as capturer:
                pass  # 아무것도 출력하지 않음

            # 파일이 생성되지 않았거나 비어있음
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    assert f.read() == ""


class TestLangSmithTracingHook:
    """LangSmith Tracing Hook 강화 기능 테스트"""

    def test_jsonl_logging_on_run_start_end(self):
        """run_start와 run_end 이벤트를 JSONL로 기록"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 임시 디렉토리를 작업 디렉토리로 설정
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                hook = LangSmithTracingHook()

                # pre_execute 호출
                agent_state = {
                    "run_id": "test_run_123",
                    "agent": {"name": "TestAgent"},
                    "task_input": "test task"
                }

                assert hook.pre_execute(agent_state) is True

                # JSONL 파일이 생성되었는지 확인
                log_file = os.path.join(tmpdir, ".system_generated", "logs", "trace_test_run_123.jsonl")
                assert os.path.exists(log_file), f"Log file not created: {log_file}"

                # post_execute 호출
                result = {"ok": True, "reason": "success"}
                hook.post_execute(agent_state, result)

                # JSONL 파일 내용 확인
                with open(log_file, 'r') as f:
                    lines = f.readlines()

                # 최소 2개의 이벤트 (run_start, run_end)
                assert len(lines) >= 2, f"Expected at least 2 events, got {len(lines)}"

                # 이벤트 파싱 및 검증
                events = [json.loads(line) for line in lines]

                # run_start 이벤트
                start_event = events[0]
                assert start_event["event_type"] == "run_start"
                assert start_event["run_id"] == "test_run_123"
                assert start_event["agent_name"] == "TestAgent"

                # run_end 이벤트
                end_event = events[-1]
                assert end_event["event_type"] == "run_end"
                assert end_event["ok"] is True
                assert end_event["reason"] == "success"

            finally:
                os.chdir(original_cwd)

    def test_skill_call_logging(self):
        """스킬 호출 이벤트를 JSONL로 기록"""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                hook = LangSmithTracingHook()
                agent_state = {
                    "run_id": "test_run_456",
                    "agent": {"name": "TestAgent"}
                }

                # pre_execute
                hook.pre_execute(agent_state)

                # pre_tool_call
                tool_args = {"query": "test search"}
                decision = hook.pre_tool_call(agent_state, "web_search", tool_args)

                assert decision.allowed is True

                # post_tool_call
                result = {"status": "success", "data": "search results"}
                hook.post_tool_call(agent_state, "web_search", result)

                # JSONL 파일 확인
                log_file = os.path.join(tmpdir, ".system_generated", "logs", "trace_test_run_456.jsonl")

                with open(log_file, 'r') as f:
                    events = [json.loads(line) for line in f.readlines()]

                # skill_call_start와 skill_call_end 이벤트 확인
                skill_events = [e for e in events if "skill" in e.get("event_type", "")]
                assert len(skill_events) >= 2

                assert any(e["event_type"] == "skill_call_start" for e in skill_events)
                assert any(e["event_type"] == "skill_call_end" for e in skill_events)

            finally:
                os.chdir(original_cwd)

    def test_jsonl_format_validity(self):
        """JSONL 파일 형식이 올바른지 확인"""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                hook = LangSmithTracingHook()
                agent_state = {
                    "run_id": "format_test",
                    "agent": {"name": "TestAgent"}
                }

                hook.pre_execute(agent_state)
                hook.post_execute(agent_state, {"ok": True, "reason": "test"})

                log_file = os.path.join(tmpdir, ".system_generated", "logs", "trace_format_test.jsonl")

                # 모든 라인이 유효한 JSON인지 확인
                with open(log_file, 'r') as f:
                    for line_num, line in enumerate(f, 1):
                        try:
                            json.loads(line)
                        except json.JSONDecodeError:
                            pytest.fail(f"Invalid JSON at line {line_num}: {line}")

            finally:
                os.chdir(original_cwd)

    def test_log_dir_creation(self):
        """로그 디렉토리가 자동으로 생성되는지 확인"""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                hook = LangSmithTracingHook()
                agent_state = {
                    "run_id": "dir_test",
                    "agent": {"name": "TestAgent"}
                }

                # .system_generated/logs 디렉토리가 아직 없음
                log_dir = os.path.join(tmpdir, ".system_generated", "logs")
                assert not os.path.exists(log_dir)

                # pre_execute 호출 시 디렉토리가 생성되어야 함
                hook.pre_execute(agent_state)

                assert os.path.exists(log_dir), "Log directory not created"

            finally:
                os.chdir(original_cwd)

    def test_timestamp_in_events(self):
        """모든 이벤트에 timestamp가 있는지 확인"""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                hook = LangSmithTracingHook()
                agent_state = {
                    "run_id": "time_test",
                    "agent": {"name": "TestAgent"}
                }

                hook.pre_execute(agent_state)
                hook.post_execute(agent_state, {"ok": True})

                log_file = os.path.join(tmpdir, ".system_generated", "logs", "trace_time_test.jsonl")

                with open(log_file, 'r') as f:
                    events = [json.loads(line) for line in f.readlines()]

                # 모든 이벤트에 timestamp가 있어야 함
                for event in events:
                    assert "timestamp" in event, f"Missing timestamp in event: {event}"
                    # ISO format 확인 (예: 2026-03-12T...)
                    assert len(event["timestamp"]) > 10

            finally:
                os.chdir(original_cwd)

    def test_safe_args_truncation(self):
        """스킬 인자가 너무 길면 자동으로 잘림"""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                hook = LangSmithTracingHook()
                agent_state = {
                    "run_id": "truncate_test",
                    "agent": {"name": "TestAgent"}
                }

                hook.pre_execute(agent_state)

                # 매우 긴 인자
                long_arg = "x" * 10000
                tool_args = {"query": long_arg}

                hook.pre_tool_call(agent_state, "test_skill", tool_args)

                log_file = os.path.join(tmpdir, ".system_generated", "logs", "trace_truncate_test.jsonl")

                with open(log_file, 'r') as f:
                    events = [json.loads(line) for line in f.readlines()]

                # skill_call_start 이벤트에서 인자가 잘렸는지 확인
                skill_start = [e for e in events if e.get("event_type") == "skill_call_start"]
                if skill_start:
                    args = skill_start[0].get("skill_args", {})
                    # 인자가 500자 이하여야 함
                    arg_str = str(args.get("query", ""))
                    assert len(arg_str) <= 501  # 잘라짐

            finally:
                os.chdir(original_cwd)


class TestPhase3Integration:
    """Phase 3 통합 테스트"""

    def test_hook_event_bus_integration(self):
        """HookEventBus에 등록될 때 정상 작동"""
        from core.hooks.event_bus import HookEventBus

        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                # 환경 변수 비활성화 (LangSmith 없이 테스트)
                old_key = os.environ.pop("LANGSMITH_API_KEY", None)

                bus = HookEventBus()
                hook = LangSmithTracingHook()
                bus.register(hook)

                agent_state = {
                    "run_id": "bus_test",
                    "agent": {"name": "TestAgent"}
                }

                # pre_execute
                assert bus.run_pre_execute(agent_state) is True

                # pre_tool_call
                decision = bus.run_pre_tool_call(agent_state, "skill1", {})
                assert decision.allowed is True

                # post_tool_call
                result = bus.run_post_tool_call(agent_state, "skill1", {"ok": True})

                # post_execute
                final_result = bus.run_post_execute(agent_state, {"ok": True})
                assert final_result.get("ok") is True

                # JSONL 파일 생성 확인
                log_file = os.path.join(tmpdir, ".system_generated", "logs", "trace_bus_test.jsonl")
                if os.path.exists(log_file):
                    with open(log_file, 'r') as f:
                        lines = [json.loads(line) for line in f.readlines() if line.strip()]
                        assert len(lines) > 0

                # 복구
                if old_key:
                    os.environ["LANGSMITH_API_KEY"] = old_key

            finally:
                os.chdir(original_cwd)


# 수동 실행용 엔드투엔드 테스트
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
