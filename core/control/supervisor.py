"""
core/control/supervisor.py
============================
RuntimeSupervisor — DynamicOrchestrator 바깥의 실행 감독자.

DynamicOrchestrator를 대체하지 않고 감싸는 역할.
heartbeat 기반 stall 감지 + retry/skip/abort 시퀀스.

설정:
  HEARTBEAT_INTERVAL_SEC = 30    — board 변경 확인 간격
  HEARTBEAT_TIMEOUT_SEC  = 120   — 이 시간 동안 변경 없으면 stall 판정
  MAX_RETRIES            = 2     — task 재시도 최대 횟수
"""
from __future__ import annotations

import json
import os
import time
import threading
from typing import Any


class RuntimeSupervisor:
    """
    오케스트레이터 바깥의 실행 감독자.
    DynamicOrchestrator.run_project()를 감싸기만 함.
    """

    HEARTBEAT_INTERVAL_SEC = 30
    HEARTBEAT_TIMEOUT_SEC = 120
    MAX_RETRIES = 2

    def __init__(self, workspace: str):
        self._workspace = workspace
        self._stop_event = threading.Event()

    def supervise(
        self,
        pipeline: Any,
        prepared: dict,
        normalized: Any,
        run_id: str,
    ) -> dict:
        """
        실행 감독 흐름:
          1. RunLedger 상태를 "executing"으로 갱신
          2. DynamicOrchestrator.run_project() 또는 pipeline execute 호출
          3. heartbeat 모니터링 (board_state 주기적 확인)
          4. stall 감지 시: retry → skip → abort
          5. 완료 후 RunLedger 갱신
        """
        self._stop_event.clear()

        # RunLedger 상태 갱신
        self._update_ledger(run_id, state="executing")

        # heartbeat 스레드 시작
        board_tracker = {"last_board_sig": None, "last_change_time": time.time()}
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(run_id, board_tracker),
            daemon=True,
        )
        heartbeat_thread.start()

        # 실행
        result = {"success": False, "error": None}
        try:
            exec_result = self._run_orchestrator(pipeline, prepared, normalized)
            result["success"] = bool(exec_result.get("success", False) if isinstance(exec_result, dict) else exec_result)
            result["orchestrator_result"] = exec_result
        except Exception as exc:
            result["error"] = str(exc)
            print(f"[RuntimeSupervisor] orchestrator failed: {exc}")
        finally:
            self._stop_event.set()
            heartbeat_thread.join(timeout=5)

        # 결과 갱신
        state = "completed" if result["success"] else "failed"
        self._update_ledger(run_id, state=state)

        return result

    def _run_orchestrator(self, pipeline: Any, prepared: dict, normalized: Any) -> Any:
        """
        DynamicOrchestrator.run_project() 또는 pipeline의 execute 메서드를 호출한다.
        기존 API를 그대로 사용.
        """
        task_input = self._get_task_input(normalized)
        workspace = self._workspace

        # DynamicOrchestrator가 pipeline에 있을 때
        if hasattr(pipeline, "run_project"):
            return pipeline.run_project(task_input, workspace)

        # ProjectPipeline.execute가 있을 때
        if hasattr(pipeline, "execute"):
            return pipeline.execute(task_input, workspace)

        print(f"[RuntimeSupervisor] pipeline has no run_project/execute method")
        return {"success": False, "error": "no executable method"}

    # ── Heartbeat 모니터링 ──

    def _heartbeat_loop(self, run_id: str, tracker: dict) -> None:
        """board 변경을 주기적으로 확인하고 stall을 감지한다."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self.HEARTBEAT_INTERVAL_SEC)
            if self._stop_event.is_set():
                break

            status = self._check_heartbeat(tracker)
            if status == "completed":
                break
            elif status == "stalled":
                stalled_tasks = self._get_stalled_tasks()
                action = self._handle_stall(run_id, stalled_tasks, tracker)
                if action == "aborted":
                    break

    def _check_heartbeat(self, tracker: dict) -> str:
        """
        Returns: "alive" | "stalled" | "completed"

        판정:
          - board에 변경 있음 (hash 변경) → "alive"
          - HEARTBEAT_TIMEOUT_SEC 초과 무변경 → "stalled"
          - board 완료 상태 → "completed"
        """
        board_path = os.path.join(self._workspace, "project_board_state.json")
        if not os.path.isfile(board_path):
            return "alive"

        # mtime + size 조합으로 변경 감지 — 파일 전체 읽기/MD5 불필요
        try:
            stat = os.stat(board_path)
            current_sig = (stat.st_mtime, stat.st_size)
        except Exception:
            return "alive"

        if current_sig != tracker.get("last_board_sig"):
            tracker["last_board_sig"] = current_sig
            tracker["last_change_time"] = time.time()
            # 완료 여부는 변경이 감지됐을 때만 확인 (파일 읽기 1회로 제한)
            try:
                with open(board_path, encoding="utf-8") as f:
                    board = json.load(f)
                if self._is_board_complete(board):
                    return "completed"
            except Exception:
                pass
            return "alive"

        elapsed = time.time() - tracker["last_change_time"]
        if elapsed > self.HEARTBEAT_TIMEOUT_SEC:
            return "stalled"

        return "alive"

    def _is_board_complete(self, board: dict) -> bool:
        """board의 모든 태스크가 completed 또는 blocked인지 확인한다."""
        tasks = board.get("tasks") or []
        if not tasks:
            return False
        return all(
            t.get("status") in ("completed", "blocked", "failed")
            for t in tasks
        )

    def _get_stalled_tasks(self) -> list[dict]:
        """in_progress 상태의 stalled task를 반환한다."""
        board_path = os.path.join(self._workspace, "project_board_state.json")
        if not os.path.isfile(board_path):
            return []
        try:
            with open(board_path, encoding="utf-8") as f:
                board = json.load(f)
            return [t for t in (board.get("tasks") or []) if t.get("status") == "in_progress"]
        except Exception:
            return []

    def _handle_stall(self, run_id: str, stalled_tasks: list[dict], tracker: dict) -> str:
        """
        Returns: "retried" | "skipped" | "aborted"

        stall 대응:
          1. in_progress 태스크를 pending으로 리셋
          2. retry_count < MAX_RETRIES → 재시도
          3. retry_count >= MAX_RETRIES → skip
          4. 전체 stall (모든 태스크 stall) → abort
        """
        if not stalled_tasks:
            return "aborted"

        stall_meta = self._load_stall_meta(run_id)
        retry_count = stall_meta.get("retry_count", 0)

        if retry_count < self.MAX_RETRIES:
            print(f"[RuntimeSupervisor] stall detected, retry {retry_count + 1}/{self.MAX_RETRIES}")
            self._reset_tasks_to_pending(stalled_tasks)
            self._save_stall_meta(run_id, {"retry_count": retry_count + 1})
            tracker["last_change_time"] = time.time()
            return "retried"

        elif retry_count == self.MAX_RETRIES:
            # B3 Fix: retry 소진 시 skip 1회 허용.
            # 이전 조건 `< MAX_RETRIES + 1`은 retry_count > MAX_RETRIES 케이스를
            # 실질적으로 차단해 else가 dead code였음. == 으로 명확히 구분.
            print(f"[RuntimeSupervisor] stall retry exhausted, skipping stalled tasks")
            self._mark_tasks_skipped(stalled_tasks)
            self._save_stall_meta(run_id, {"retry_count": retry_count + 1, "skipped": True})
            tracker["last_change_time"] = time.time()
            return "skipped"

        else:
            # retry_count > MAX_RETRIES: skip 후에도 stall 재발 → abort
            print(f"[RuntimeSupervisor] stall abort: all tasks stalled after skip")
            self._stop_event.set()
            return "aborted"

    def _reset_tasks_to_pending(self, tasks: list[dict]) -> None:
        """task status를 pending으로 리셋한다 (board 파일 직접 수정)."""
        board_path = os.path.join(self._workspace, "project_board_state.json")
        if not os.path.isfile(board_path):
            return
        try:
            with open(board_path, encoding="utf-8") as f:
                board = json.load(f)
            stall_ids = {t.get("task_id") for t in tasks}
            for task in (board.get("tasks") or []):
                if task.get("task_id") in stall_ids:
                    task["status"] = "pending"
            tmp = board_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(board, f, ensure_ascii=False, indent=2)
            os.replace(tmp, board_path)
        except Exception as exc:
            print(f"[RuntimeSupervisor] reset_tasks failed: {exc}")

    def _mark_tasks_skipped(self, tasks: list[dict]) -> None:
        """task status를 blocked로 표시한다."""
        board_path = os.path.join(self._workspace, "project_board_state.json")
        if not os.path.isfile(board_path):
            return
        try:
            with open(board_path, encoding="utf-8") as f:
                board = json.load(f)
            stall_ids = {t.get("task_id") for t in tasks}
            for task in (board.get("tasks") or []):
                if task.get("task_id") in stall_ids:
                    task["status"] = "blocked"
            tmp = board_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(board, f, ensure_ascii=False, indent=2)
            os.replace(tmp, board_path)
        except Exception as exc:
            print(f"[RuntimeSupervisor] mark_tasks_skipped failed: {exc}")

    # ── RunLedger 연동 ──

    def _update_ledger(self, run_id: str, state: str) -> None:
        try:
            from core.control.run_ledger import RunLedger
            ledger = RunLedger(self._workspace)
            ledger.update_run(run_id=run_id, state=state)
        except Exception as exc:
            print(f"[RuntimeSupervisor] ledger update failed: {exc}")

    # ── stall 메타 저장 ──

    def _stall_meta_path(self, run_id: str) -> str:
        return os.path.join(self._workspace, ".af_runtime", "control", f"stall_{run_id}.json")

    def _load_stall_meta(self, run_id: str) -> dict:
        path = self._stall_meta_path(run_id)
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_stall_meta(self, run_id: str, meta: dict) -> None:
        path = self._stall_meta_path(run_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
        except Exception:
            pass

    # ── 헬퍼 ──

    def _get_task_input(self, normalized: Any) -> str:
        if isinstance(normalized, dict):
            return normalized.get("raw_input", "")
        return getattr(normalized, "raw_input", "") or ""


__all__ = ["RuntimeSupervisor"]
