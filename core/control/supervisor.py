"""
core/control/supervisor.py
============================
RuntimeSupervisor — DynamicOrchestrator 바깥의 실행 감독자.

DynamicOrchestrator를 대체하지 않고 감싸는 역할.
heartbeat 기반 stall 감지 + 근본 원인 분석 + 계단식 에스컬레이션.

Stall 처리 흐름:
  1. stalled task의 error/note를 board에서 수집
  2. 동일 task+error가 REPEATED_FAILURE_THRESHOLD회 반복 → 근본 문제 판정
  3. StrategyEvaluator 호출 → action: retry / pivot / abort
  4. retry  → pending 리셋 (새 instruction 있으면 task에 반영)
  5. pivot  → new_instruction을 task에 반영 후 retry
  6. abort  → rollback 전이 (루프 탈출, 근본 문제 기록)
  7. 반복 실패 → 강제 rollback (evaluator 결과 무시)

설정:
  HEARTBEAT_INTERVAL_SEC    = 30    — board 변경 확인 간격
  HEARTBEAT_TIMEOUT_SEC     = 120   — 무변경 지속 시 stall 판정
  MAX_RETRIES               = 2     — evaluator "retry" 최대 허용 횟수
  REPEATED_FAILURE_THRESHOLD = 2    — 동일 에러 N회 반복 시 근본 문제 판정
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
    REPEATED_FAILURE_THRESHOLD = 2   # 동일 에러 N회 반복 → 근본 문제

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
          2. DynamicOrchestrator.run_project() 호출
          3. heartbeat 모니터링 (board mtime+size 주기적 확인)
          4. stall 감지 시: 근본 원인 분석 → retry/pivot/abort 결정
          5. 완료 후 RunLedger 갱신
        """
        self._stop_event.clear()
        self._update_ledger(run_id, state="executing")

        board_tracker = {"last_board_sig": None, "last_change_time": time.time()}
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(run_id, board_tracker),
            daemon=True,
        )
        heartbeat_thread.start()

        result = {"success": False, "error": None}
        try:
            exec_result = self._run_orchestrator(pipeline, prepared, normalized)
            result["success"] = bool(
                exec_result.get("success", False)
                if isinstance(exec_result, dict) else exec_result
            )
            result["orchestrator_result"] = exec_result
        except Exception as exc:
            result["error"] = str(exc)
            print(f"[RuntimeSupervisor] orchestrator failed: {exc}")
        finally:
            self._stop_event.set()
            heartbeat_thread.join(timeout=5)

        state = "completed" if result["success"] else "failed"
        self._update_ledger(run_id, state=state)
        return result

    def _run_orchestrator(self, pipeline: Any, prepared: dict, normalized: Any) -> Any:
        task_input = self._get_task_input(normalized)
        if hasattr(pipeline, "run_project"):
            return pipeline.run_project(task_input, self._workspace)
        if hasattr(pipeline, "execute"):
            return pipeline.execute(task_input, self._workspace)
        print("[RuntimeSupervisor] pipeline has no run_project/execute method")
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
        mtime+size 조합으로 변경 감지 (파일 전체 읽기 없음).
        변경 감지 시에만 board 읽어서 완료 여부 확인.
        """
        board_path = os.path.join(self._workspace, "project_board_state.json")
        if not os.path.isfile(board_path):
            return "alive"
        try:
            stat = os.stat(board_path)
            current_sig = (stat.st_mtime, stat.st_size)
        except Exception:
            return "alive"

        if current_sig != tracker.get("last_board_sig"):
            tracker["last_board_sig"] = current_sig
            tracker["last_change_time"] = time.time()
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
        tasks = board.get("tasks") or []
        if not tasks:
            return False
        return all(t.get("status") in ("completed", "blocked", "failed") for t in tasks)

    def _get_stalled_tasks(self) -> list[dict]:
        """board에서 in_progress 상태인 task를 반환한다."""
        board_path = os.path.join(self._workspace, "project_board_state.json")
        if not os.path.isfile(board_path):
            return []
        try:
            with open(board_path, encoding="utf-8") as f:
                board = json.load(f)
            return [t for t in (board.get("tasks") or []) if t.get("status") == "in_progress"]
        except Exception:
            return []

    # ── Stall 처리: 근본 원인 분석 ──

    def _handle_stall(self, run_id: str, stalled_tasks: list[dict], tracker: dict) -> str:
        """
        Returns: "retried" | "pivoted" | "skipped" | "aborted"

        흐름:
          1. stalled task의 error context 수집
          2. 반복 실패 감지 (근본 문제 → 즉시 rollback)
          3. StrategyEvaluator 호출 → action 결정
          4. retry/pivot → 재시도, abort → rollback
          5. MAX_RETRIES 소진 → skip → 추가 stall 시 rollback
        """
        if not stalled_tasks:
            return "aborted"

        stall_meta = self._load_stall_meta(run_id)
        retry_count = stall_meta.get("retry_count", 0)

        # ── 1. error context 수집 ──
        error_entries = self._collect_task_errors(stalled_tasks)

        # ── 2. 반복 실패 감지 ──
        repeated = self._detect_repeated_failures(stall_meta, error_entries)
        if repeated:
            print(
                f"[RuntimeSupervisor] repeated failure detected in {[e['task_id'] for e in repeated]} "
                f"— treating as fundamental problem, triggering rollback"
            )
            self._record_failures_to_state(run_id, repeated, force_abort=True)
            self._trigger_rollback(run_id)
            self._stop_event.set()
            return "aborted"

        # ── 3. StrategyEvaluator 호출 ──
        eval_results = self._evaluate_stall_causes(stalled_tasks, error_entries)

        # ── 4. 모든 task의 action 집계: abort 하나라도 있으면 rollback ──
        actions = [r.get("action", "abort") for r in eval_results]
        has_abort = any(a == "abort" for a in actions)

        if has_abort or retry_count >= self.MAX_RETRIES:
            if has_abort:
                print(f"[RuntimeSupervisor] evaluator decided abort — triggering rollback")
            else:
                print(f"[RuntimeSupervisor] MAX_RETRIES({self.MAX_RETRIES}) exhausted — triggering rollback")
            self._record_failures_to_state(run_id, eval_results, force_abort=has_abort)
            self._trigger_rollback(run_id)
            self._stop_event.set()
            return "aborted"

        # ── 5. retry 또는 pivot ──
        has_pivot = any(a == "pivot" for a in actions)
        self._apply_eval_results(stalled_tasks, eval_results)
        self._reset_tasks_to_pending(stalled_tasks)

        new_meta = {
            "retry_count": retry_count + 1,
            "failure_history": self._update_failure_history(
                stall_meta.get("failure_history", []), error_entries
            ),
        }
        self._save_stall_meta(run_id, new_meta)
        self._record_failures_to_state(run_id, eval_results, force_abort=False)
        tracker["last_change_time"] = time.time()

        action_label = "pivoted" if has_pivot else "retried"
        print(
            f"[RuntimeSupervisor] stall {action_label} "
            f"({retry_count + 1}/{self.MAX_RETRIES}) for "
            f"{[t.get('task_id') for t in stalled_tasks]}"
        )
        return action_label

    # ── Error context 수집 ──

    def _collect_task_errors(self, stalled_tasks: list[dict]) -> list[dict]:
        """
        stalled task들의 error_log/note를 board + state_board에서 수집한다.
        """
        board_path = os.path.join(self._workspace, "project_board_state.json")
        manifest_path = os.path.join(self._workspace, ".af_manifest.json")

        # board의 task note 수집
        board_notes: dict[str, str] = {}
        try:
            with open(board_path, encoding="utf-8") as f:
                board = json.load(f)
            for t in (board.get("tasks") or []):
                tid = t.get("task_id") or t.get("id") or ""
                note = t.get("note") or t.get("error") or ""
                if tid and note:
                    board_notes[tid] = note
        except Exception:
            pass

        # manifest의 failed_subtasks에서 추가 context 수집
        manifest_errors: dict[str, str] = {}
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            for entry in (manifest.get("state_board", {}).get("failed_subtasks") or []):
                tid = entry.get("task_id") or ""
                reason = entry.get("reason") or ""
                if tid and reason:
                    manifest_errors[tid] = reason
        except Exception:
            pass

        entries = []
        for task in stalled_tasks:
            tid = task.get("task_id") or task.get("id") or ""
            role = task.get("owner_role") or task.get("role") or "unknown"
            instruction = task.get("description") or task.get("subtask") or ""
            error = (
                board_notes.get(tid)
                or manifest_errors.get(tid)
                or task.get("note")
                or "stall: no progress detected"
            )
            entries.append({
                "task_id": tid,
                "role": role,
                "instruction": instruction,
                "error": error,
            })
        return entries

    # ── 반복 실패 감지 ──

    def _detect_repeated_failures(
        self, stall_meta: dict, error_entries: list[dict]
    ) -> list[dict]:
        """
        동일 task_id + 유사 error가 REPEATED_FAILURE_THRESHOLD회 이상이면
        근본 문제로 판정해 해당 entry 목록을 반환한다.
        """
        history = stall_meta.get("failure_history", [])
        # {task_id: [(error_key, count), ...]}
        count_map: dict[str, int] = {}
        for h in history:
            key = f"{h.get('task_id')}|{h.get('error_key', '')}"
            count_map[key] = count_map.get(key, 0) + 1

        repeated = []
        for entry in error_entries:
            error_key = self._error_key(entry["error"])
            key = f"{entry['task_id']}|{error_key}"
            total = count_map.get(key, 0) + 1  # 현재 회차 포함
            if total >= self.REPEATED_FAILURE_THRESHOLD:
                repeated.append({**entry, "repeat_count": total, "error_key": error_key})
        return repeated

    def _error_key(self, error: str) -> str:
        """에러 메시지에서 비교 가능한 짧은 키를 추출한다."""
        # 앞 120자, 소문자, 공백 정규화
        return " ".join(error[:120].lower().split())

    def _update_failure_history(
        self, history: list[dict], error_entries: list[dict]
    ) -> list[dict]:
        """stall_meta failure_history에 새 항목을 추가한다."""
        from core.utils import now_iso
        for entry in error_entries:
            history.append({
                "task_id": entry["task_id"],
                "error_key": self._error_key(entry["error"]),
                "at": now_iso(),
            })
        return history

    # ── StrategyEvaluator 연동 ──

    def _evaluate_stall_causes(
        self, stalled_tasks: list[dict], error_entries: list[dict]
    ) -> list[dict]:
        """
        각 stalled task에 대해 StrategyEvaluator를 호출한다.
        LLM 호출 실패 시 heuristic fallback.

        Returns list of eval result dicts:
          {task_id, role, error, action, reasoning, new_instruction}
        """
        results = []
        evaluator = self._get_evaluator()

        for entry in error_entries:
            if evaluator is not None:
                try:
                    eval_res = evaluator.evaluate_failure(
                        role=entry["role"],
                        instruction=entry["instruction"],
                        error_log=entry["error"],
                    )
                    action = eval_res.get("action", "abort")
                    reasoning = eval_res.get("reasoning", "")
                    new_instruction = eval_res.get("new_instruction", "")
                except Exception as exc:
                    print(f"[RuntimeSupervisor] StrategyEvaluator failed: {exc}")
                    action, reasoning, new_instruction = self._heuristic_action(entry["error"])
            else:
                action, reasoning, new_instruction = self._heuristic_action(entry["error"])

            results.append({
                "task_id": entry["task_id"],
                "role": entry["role"],
                "error": entry["error"],
                "action": action,
                "evaluator_reasoning": reasoning,
                "new_instruction": new_instruction,
            })
            print(
                f"[RuntimeSupervisor] eval task={entry['task_id']!r} "
                f"action={action!r} reason={reasoning[:80]!r}"
            )
        return results

    def _get_evaluator(self) -> Any:
        """StrategyEvaluator 인스턴스를 반환한다. 불가하면 None."""
        try:
            from core.evaluator import StrategyEvaluator
            from core.llm_engine import LLMEngine
            # 기본 모델 사용 (orchestrator와 동일)
            return StrategyEvaluator()
        except Exception:
            return None

    def _heuristic_action(self, error: str) -> tuple[str, str, str]:
        """
        LLM 없을 때 에러 메시지 키워드로 action을 추론하는 fallback.
        Returns: (action, reasoning, new_instruction)
        """
        err_lower = error.lower()
        # 단순 오류 → retry
        if any(k in err_lower for k in ("timeout", "network", "connection", "temporarily")):
            return "retry", "transient error — retry recommended", ""
        # 구조적 오류 → abort
        if any(k in err_lower for k in ("not found", "does not exist", "no module", "import error",
                                          "syntax error", "permission denied")):
            return "abort", "structural error — retry will not help", ""
        # 기본값: retry (1회)
        return "retry", "unknown error — attempting retry", ""

    # ── task 수정 (pivot 적용) ──

    def _apply_eval_results(
        self, stalled_tasks: list[dict], eval_results: list[dict]
    ) -> None:
        """
        pivot action인 경우 new_instruction을 board task의 description에 반영한다.
        """
        board_path = os.path.join(self._workspace, "project_board_state.json")
        if not os.path.isfile(board_path):
            return

        pivot_map = {
            r["task_id"]: r["new_instruction"]
            for r in eval_results
            if r.get("action") == "pivot" and r.get("new_instruction")
        }
        if not pivot_map:
            return

        try:
            with open(board_path, encoding="utf-8") as f:
                board = json.load(f)
            for task in (board.get("tasks") or []):
                tid = task.get("task_id") or task.get("id") or ""
                if tid in pivot_map:
                    task["description"] = pivot_map[tid]
                    task["pivot_applied"] = True
            tmp = board_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(board, f, ensure_ascii=False, indent=2)
            os.replace(tmp, board_path)
        except Exception as exc:
            print(f"[RuntimeSupervisor] apply_eval_results failed: {exc}")

    # ── 상태 머신 연동 ──

    def _record_failures_to_state(
        self, run_id: str, eval_results: list[dict], force_abort: bool
    ) -> None:
        """실패 분석 결과를 MaintenanceStateMachine에 누적 기록한다."""
        try:
            from core.control.maintenance_state import MaintenanceStateMachine
            from core.utils import now_iso
            sm = MaintenanceStateMachine(self._workspace, run_id)
            for r in eval_results:
                sm.record_failure({
                    "task_id": r.get("task_id", ""),
                    "role": r.get("role", ""),
                    "error": r.get("error", ""),
                    "evaluator_action": r.get("action", ""),
                    "evaluator_reasoning": r.get("evaluator_reasoning", ""),
                    "new_instruction": r.get("new_instruction", ""),
                    "repeat_count": r.get("repeat_count", 1),
                    "force_abort": force_abort,
                })
        except Exception as exc:
            print(f"[RuntimeSupervisor] record_failures_to_state failed: {exc}")

    def _trigger_rollback(self, run_id: str) -> None:
        """상태 머신을 rollback 상태로 전이한다."""
        try:
            from core.control.maintenance_state import MaintenanceStateMachine
            sm = MaintenanceStateMachine(self._workspace, run_id)
            if sm.can_transition("rollback"):
                sm.transition("rollback", metadata={"triggered_by": "supervisor_stall_abort"})
            else:
                sm.force_reset(reason="supervisor stall: fundamental failure detected")
        except Exception as exc:
            print(f"[RuntimeSupervisor] trigger_rollback failed: {exc}")

    # ── Board 수정 ──

    def _reset_tasks_to_pending(self, tasks: list[dict]) -> None:
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
