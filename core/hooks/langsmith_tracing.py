"""
LangSmith Tracing Hook for Agent Factory (Phase 3).

Automatically traces agent execution and tool calls to:
1. LangSmith API (when LANGSMITH_API_KEY is set)
2. Local JSONL log file (.system_generated/logs/trace_<run_id>.jsonl)

Real-time capture of stdout/stderr during AgentRunner.run() execution.
All methods are fire-and-forget: tracing errors never block execution.
"""
from __future__ import annotations

import os
import sys
import io
import json
import time
import uuid
from typing import Any
from datetime import datetime

from core.hooks.base import ContinuationHook, ToolCallDecision
from core.langchain_adapter import LANGCHAIN_AVAILABLE
from core.utils import now_iso

# Conditional LangSmith import
_langsmith_client = None
_RunTree = None

if LANGCHAIN_AVAILABLE:
    try:
        from langsmith import Client as _LangSmithClient  # type: ignore
        from langsmith.run_trees import RunTree as _RunTree  # type: ignore
        _langsmith_client = _LangSmithClient
    except ImportError:
        pass


def _is_enabled() -> bool:
    return bool(
        _langsmith_client is not None
        and os.getenv("LANGSMITH_API_KEY")
    )


class _StdoutCapturer:
    """실시간으로 stdout/stderr를 캡처하는 컨텍스트 매니저"""

    def __init__(self, log_file_path: str | None = None):
        self.log_file_path = log_file_path
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.captured_buffer = io.StringIO()
        self._log_handle = None

    def __enter__(self):
        # 파일과 메모리에 동시 기록
        if self.log_file_path:
            os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
            self._log_handle = open(self.log_file_path, 'w', encoding='utf-8')

        # 커스텀 스트림으로 stdout/stderr 리다이렉트
        sys.stdout = self
        sys.stderr = self
        return self

    def __exit__(self, *args):
        # stdout/stderr 복원은 예외 발생과 무관하게 반드시 수행
        try:
            if self._log_handle:
                self._log_handle.close()
        finally:
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr

    def write(self, text: str):
        """stdout/stderr 쓰기 인터셉트. 예외 시에도 원본 stdout 출력 보장."""
        if not text:
            return
        try:
            self.captured_buffer.write(text)
        except Exception:
            pass
        try:
            if self._log_handle:
                self._log_handle.write(text)
                self._log_handle.flush()
        except Exception:
            pass
        try:
            self.original_stdout.write(text)
        except Exception:
            pass

    def flush(self):
        if self._log_handle:
            self._log_handle.flush()
        self.original_stdout.flush()

    def get_captured(self) -> str:
        """캡처된 모든 텍스트 반환"""
        return self.captured_buffer.getvalue()


class LangSmithTracingHook(ContinuationHook):
    """
    Phase 3 강화 버전:
    - LangSmith API 트레이싱 (기존)
    - JSONL 로컬 로깅 (신규)
    - stdout/stderr 캡처 (신규)

    Priority-5 hook. Fire-and-forget: tracing errors never block execution.
    """

    PRIORITY = 5

    def __init__(self):
        self._enabled = _is_enabled()
        self._client = _langsmith_client() if self._enabled else None
        self._run_tree: Any = None
        self._tool_spans: dict[str, Any] = {}

        # Phase 3: JSONL 로깅
        self._run_id: str | None = None
        self._log_file_path: str | None = None
        self._jsonl_handle = None
        self._stdout_capturer: _StdoutCapturer | None = None
        self._start_time: float | None = None

    def _get_log_dir(self) -> str:
        """로그 디렉토리 반환 (.system_generated/logs/)"""
        # 현재 작업 디렉토리 기준으로 설정
        cwd = os.getcwd()
        log_dir = os.path.join(cwd, ".system_generated", "logs")
        return log_dir

    def _open_jsonl(self):
        """JSONL 파일 핸들 열기 (append 모드)."""
        if self._log_file_path and self._jsonl_handle is None:
            os.makedirs(os.path.dirname(self._log_file_path), exist_ok=True)
            self._jsonl_handle = open(self._log_file_path, 'a', encoding='utf-8')

    def _close_jsonl(self):
        """JSONL 파일 핸들 닫기"""
        if self._jsonl_handle:
            try:
                self._jsonl_handle.close()
            except Exception:
                pass
            self._jsonl_handle = None

    def _write_jsonl_entry(self, event: dict):
        """JSONL 형식으로 이벤트 로그 기록"""
        if not self._log_file_path:
            return
        try:
            entry = {
                "timestamp": now_iso(),
                **event
            }
            if self._jsonl_handle is None:
                self._open_jsonl()
            if self._jsonl_handle:
                self._jsonl_handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
                self._jsonl_handle.flush()
        except Exception:
            pass  # fire-and-forget

    def pre_execute(self, agent_state: dict) -> bool:
        """에이전트 실행 전 훅"""
        try:
            # Run ID 결정
            self._run_id = agent_state.get("run_id", str(uuid.uuid4()))
            self._start_time = time.time()

            # Phase 3: JSONL 로그 파일 경로 설정
            log_dir = self._get_log_dir()
            self._log_file_path = os.path.join(log_dir, f"trace_{self._run_id}.jsonl")

            # 동일 run_id 재실행 시 이전 데이터 혼합 방지: 새 실행은 파일을 초기화
            self._close_jsonl()
            os.makedirs(log_dir, exist_ok=True)
            if os.path.exists(self._log_file_path):
                open(self._log_file_path, 'w').close()  # truncate

            # stdout/stderr 캡처 시작
            stdout_log = os.path.join(log_dir, f"stdout_{self._run_id}.log")
            self._stdout_capturer = _StdoutCapturer(stdout_log)
            self._stdout_capturer.__enter__()

            # 초기 이벤트 기록
            self._write_jsonl_entry({
                "event_type": "run_start",
                "run_id": self._run_id,
                "agent_name": agent_state.get("agent", {}).get("name", "unknown"),
                "task_input": agent_state.get("task_input", "")[:500],  # 처음 500자만
            })

            # LangSmith 트레이싱 (기존)
            if self._enabled and _RunTree is not None:
                agent_name = agent_state.get("agent", {}).get("name", "unknown")
                self._run_tree = _RunTree(
                    name=f"agent:{agent_name}",
                    run_type="chain",
                    inputs={"task": agent_state.get("task_input", "")},
                    project_name=os.getenv("LANGSMITH_PROJECT", "agent-factory"),
                )
                self._run_tree.post()
                agent_state["langsmith_run_id"] = str(self._run_tree.id)
        except Exception:
            pass  # fire-and-forget
        return True

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        """에이전트 실행 후 훅.

        주의: agent_runner.py에서 도구 결과에도 run_post_execute()를 호출하므로,
        최종 에이전트 결과(ok + reason 키를 동시에 보유)인 경우에만 run_end를 기록한다.
        도구 결과는 보통 raw string이거나 ok/reason을 동시에 갖지 않는다.
        """
        # 최종 결과인지 판별: ok와 reason 키를 동시에 보유한 dict만
        is_final = (
            isinstance(result, dict)
            and "ok" in result
            and "reason" in result
        )

        if not is_final:
            return result  # 도구 결과 → run_end 기록하지 않음

        try:
            ok = result.get("ok", False)
            reason = result.get("reason", "")
            duration_ms = int((time.time() - self._start_time) * 1000) if self._start_time else 0

            # Phase 3: JSONL 종료 이벤트 기록
            self._write_jsonl_entry({
                "event_type": "run_end",
                "run_id": self._run_id,
                "ok": ok,
                "reason": reason,
                "duration_ms": duration_ms,
            })

            # 캡처된 stdout/stderr 기록
            if self._stdout_capturer:
                captured = self._stdout_capturer.get_captured()
                if captured:
                    # 너무 크면 처음 10000자만
                    self._write_jsonl_entry({
                        "event_type": "captured_output",
                        "run_id": self._run_id,
                        "content": captured[:10000],
                    })

            # LangSmith 트레이싱 (기존)
            if self._enabled and self._run_tree is not None:
                self._run_tree.end(
                    outputs={"ok": ok, "reason": reason},
                    error=None if ok else reason,
                )
                self._run_tree.patch()
        except Exception:
            pass  # fire-and-forget
        finally:
            # stdout/stderr 캡처 종료
            if self._stdout_capturer:
                try:
                    self._stdout_capturer.__exit__(None, None, None)
                except Exception:
                    pass
                self._stdout_capturer = None
            self._close_jsonl()
        return result

    @staticmethod
    def _safe_truncate(text: str, max_len: int) -> str:
        """UTF-8 멀티바이트 문자 경계를 존중하는 안전한 문자열 절단."""
        if len(text) <= max_len:
            return text
        truncated = text[:max_len]
        # 서로게이트 쌍의 중간에서 잘리지 않도록 encode/decode
        return truncated.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")

    def pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict[str, Any]) -> ToolCallDecision:
        """스킬 호출 전 훅"""
        try:
            # Phase 3: JSONL 스킬 호출 이벤트 기록
            safe_args = {}
            for k, v in tool_args.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, str):
                    safe_args[k] = self._safe_truncate(v, 500)
                elif isinstance(v, (int, float, bool, type(None), list, dict)):
                    safe_args[k] = v
                else:
                    safe_args[k] = self._safe_truncate(str(v), 500)

            self._write_jsonl_entry({
                "event_type": "skill_call_start",
                "run_id": self._run_id,
                "skill_name": tool_name,
                "skill_args": safe_args,
            })

            # LangSmith 트레이싱 (기존)
            if self._enabled and self._run_tree is not None and _RunTree is not None:
                span = self._run_tree.create_child(
                    name=f"tool:{tool_name}",
                    run_type="tool",
                    inputs=tool_args,
                )
                span.post()
                span_key = f"{tool_name}_{id(tool_args)}"
                self._tool_spans[span_key] = span
                tool_args = dict(tool_args)
                tool_args["_langsmith_span_key"] = span_key
        except Exception:
            pass  # fire-and-forget

        return ToolCallDecision(allowed=True, tool_args=dict(tool_args))

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        """스킬 호출 후 훅"""
        try:
            result_str = str(result)[:1000]

            # Phase 3: JSONL 스킬 완료 이벤트 기록
            self._write_jsonl_entry({
                "event_type": "skill_call_end",
                "run_id": self._run_id,
                "skill_name": tool_name,
                "skill_result": result_str,
            })

            # LangSmith 트레이싱 (기존)
            if self._enabled:
                span_key = None
                if isinstance(result, dict):
                    span_key = result.get("_langsmith_span_key")
                    if span_key is not None:
                        result = {k: v for k, v in result.items() if k != "_langsmith_span_key"}

                if span_key is None:
                    for k in list(self._tool_spans.keys()):
                        if k.startswith(f"{tool_name}_"):
                            span_key = k
                            break

                span = self._tool_spans.pop(span_key, None) if span_key else None
                if span is not None:
                    span.end(outputs={"result": str(result)[:2000]})
                    span.patch()
        except Exception:
            pass  # fire-and-forget

        return result
