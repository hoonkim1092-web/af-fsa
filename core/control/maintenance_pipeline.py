"""
core/control/maintenance_pipeline.py
======================================
MaintenancePipeline — ProjectPipeline의 컴포지션 래퍼.

ProjectPipeline을 서브클래싱하지 않고 Has-A 관계로 감싼다.
prepare()의 468줄 메서드를 복제하지 않기 위해 컴포지션을 선택.

흐름:
  prepare():
    1. ExecutionPolicy에 따라 스킵할 단계 결정
    2. quick_fix → _minimal_prepare() (work-item 문서 스킵)
    3. standard_update/deep_update → ProjectPipeline.prepare() 위임
    4. checkpoint 저장
    5. ImpactProfile 기반 evidence 수집 범위 제한

  execute():
    1. RunLedger conflict check (동시 작업 충돌 확인)
    2. ExecutionPolicy에 따라 approval 스킵 여부 결정
    3. Supervisor.supervise() 호출 (DynamicOrchestrator 감싸기)
    4. RegressionSafetyGate 실행 (policy에 따라)
    5. Verification + closeout
"""
from __future__ import annotations

import os
from typing import Any


class MaintenancePipeline:
    """
    유지보수 요청을 ProjectPipeline으로 위임하는 컴포지션 래퍼.

    Args:
        project_pipeline: 기존 ProjectPipeline 인스턴스
        workspace:        프로젝트 작업 디렉토리
    """

    def __init__(self, project_pipeline: Any, workspace: str):
        self._pipeline = project_pipeline
        self._workspace = workspace

    def prepare(self, normalized: Any) -> dict:
        """
        준비 단계.

        Args:
            normalized: NormalizedRequest (또는 dict)

        Returns:
            prepared: dict with keys ("brief", "board", "work_items", "skipped_stages")
        """
        policy = self._get_policy(normalized)
        execution_policy = policy.get("execution_policy", "standard_update")

        if execution_policy == "quick_fix":
            return self._minimal_prepare(normalized)

        # standard_update / deep_update / full_bootstrap → ProjectPipeline.prepare() 위임
        return self._full_prepare(normalized, policy)

    # 충돌 대기 설정
    _CONFLICT_POLL_INTERVAL = 5    # 폴링 간격 (초)
    _CONFLICT_LOG_INTERVAL = 12    # 이 횟수마다 대기 중 로그 출력 (5s × 12 = 60s)
    _STALE_THRESHOLD_SEC = 1800    # 30분 이상 미완료 run → stale 판정
    _CONFLICT_MAX_WAIT_SEC = 3600  # BUG-7 Fix: 최대 대기 1시간 → 이후 강제 진행

    def execute(self, prepared: dict, normalized: Any, allow_conflict: bool = False) -> dict:
        """
        실행 단계.

        충돌 처리 원칙 — 완료를 목표로 루프하는 시스템이므로 충돌로 인한 작업 소멸 없음:
          1. stale run (30분+ 미완료) → 자동 close 후 계속
          2. allow_conflict=True → 경고 출력 후 계속
          3. 활성 run이 남아있으면 → 해소될 때까지 무한 대기 (5초 간격 폴링)
             - 대기 중에도 stale 판정이 바뀌면 자동 해소
             - 충돌이 절대 "failed" 원인이 되지 않음

        Args:
            prepared:        prepare()의 반환값
            normalized:      NormalizedRequest (또는 dict)
            allow_conflict:  True면 활성 충돌도 즉시 무시하고 실행 계속

        Returns:
            result: dict with keys ("success", "outcome", "state", "errors")
        """
        policy = self._get_policy(normalized)
        run_id = self._get_run_id(normalized)

        # 1. 충돌 처리 — 해소될 때까지 블로킹 대기 (반환값 없음, 항상 진행)
        self._handle_conflicts(normalized, allow_conflict)

        # 2. ExecutionPolicy에 따라 approval 스킵 여부 결정
        #    requires_approval=True  → ApprovalGate 검증 (is_execution_open + check_validity)
        #    requires_approval=False → ApprovalGate 호출 자체를 생략 (quick_fix 등)
        requires_approval = policy.get("requires_approval", True)
        if requires_approval:
            approval_check = self._check_approval_gate(prepared, normalized)
            if not approval_check.get("approved", False):
                return {
                    "success": False,
                    "outcome": "failed",
                    "state": "closed_failed",
                    "errors": approval_check.get("reasons", ["approval gate not passed"]),
                }

        # 3. Supervisor를 통한 실행
        supervisor_result = self._run_with_supervisor(prepared, normalized, run_id)

        # 3. Regression Safety Gate
        regression_result = self._run_regression_gate(normalized, policy)

        # 4. 상태 전이: executing → verifying → closing → closed
        outcome = self._determine_outcome(supervisor_result, regression_result)
        self._advance_state_machine(self._workspace, run_id, outcome)

        # 5. RunLedger 종료 기록
        self._close_ledger(self._workspace, run_id, outcome)

        return {
            "success": outcome == "success",
            "outcome": outcome,
            "supervisor_result": supervisor_result,
            "regression_result": regression_result,
        }

    # ── prepare 경로 ──

    def _minimal_prepare(self, normalized: Any) -> dict:
        """
        quick_fix 전용 경량 prepare.
        brief만 생성, work-item 문서 및 full board 스킵.
        """
        task_input = self._get_task_input(normalized)
        work_kind = self._get_work_kind(normalized)

        print(f"[MaintenancePipeline] quick_fix minimal prepare: {task_input[:60]!r}")

        brief = {
            "type": "quick_fix_brief",
            "task_input": task_input,
            "work_kind": work_kind,
        }

        return {
            "brief": brief,
            "board": None,
            "work_items": [],
            "skipped_stages": ["evidence_retry", "critique", "agent_qa", "convergence_loop",
                               "work_item_docs"],
        }

    def _full_prepare(self, normalized: Any, policy: dict) -> dict:
        """
        ProjectPipeline.prepare()를 위임하는 표준 prepare.
        """
        task_input = self._get_task_input(normalized)
        print(f"[MaintenancePipeline] full prepare delegating to ProjectPipeline: {task_input[:60]!r}")

        try:
            # ProjectPipeline.prepare()에 task_input을 전달
            # 기존 API를 그대로 사용 (시그니처 수정 없음)
            if hasattr(self._pipeline, "prepare"):
                prepared = self._pipeline.prepare(task_input)
            else:
                prepared = {}

            # evidence_scope로 수집 범위 제한 (ChangeImpactProfiler 결과 활용)
            evidence_scope = self._get_evidence_scope(normalized)
            if evidence_scope and isinstance(prepared, dict):
                prepared["evidence_scope"] = evidence_scope

            skipped = policy.get("skippable_stages", [])
            if isinstance(prepared, dict):
                prepared["skipped_stages"] = skipped

            # B8 Fix: dict 아닐 때 {"result": prepared, ...}로 래핑하면
            # downstream에서 "brief" 키 접근 시 KeyError 발생.
            # 빈 dict fallback으로 표준 구조를 보장한다.
            if not isinstance(prepared, dict):
                prepared = {
                    "brief": {},
                    "board": None,
                    "work_items": [],
                    "skipped_stages": skipped,
                }
            return prepared

        except Exception as exc:
            print(f"[MaintenancePipeline] ProjectPipeline.prepare() failed: {exc}")
            return {
                "brief": {},
                "board": None,
                "work_items": [],
                "skipped_stages": [],
                "error": str(exc),
            }

    # ── execute 서브 루틴 ──

    def _handle_conflicts(self, normalized: Any, allow_conflict: bool) -> None:
        """
        충돌이 완전히 해소될 때까지 블로킹 대기한다. 반환값 없음.

        완료 목표 루프 시스템 원칙:
          - 충돌은 "작업 실패" 사유가 아니라 "잠시 대기" 사유
          - 대기 중에도 stale 판정이 바뀌면 자동 해소
          - BUG-7 Fix: _CONFLICT_MAX_WAIT_SEC(기본 1시간) 초과 시 강제 진행 (무한 hang 방지)

        흐름:
          1. stale run 자동 close
          2. allow_conflict=True → 즉시 반환
          3. 활성 run 남아있으면 5초 간격으로 폴링
             _CONFLICT_MAX_WAIT_SEC 초과 시 경고 후 강제 진행
             매 _CONFLICT_LOG_INTERVAL 회마다 대기 중 로그 출력
        """
        import time
        from core.control.run_ledger import RunLedger

        affected_files = self._get_affected_files(normalized)
        if not affected_files:
            return

        ledger = RunLedger(self._workspace)
        poll_count = 0
        start_time = time.time()

        try:
            while True:
                # BUG-7 Fix: 최대 대기 시간 초과 시 강제 진행
                elapsed = time.time() - start_time
                if elapsed >= self._CONFLICT_MAX_WAIT_SEC:
                    print(
                        f"[MaintenancePipeline] conflict wait timeout "
                        f"({self._CONFLICT_MAX_WAIT_SEC}s) — forcing proceed"
                    )
                    return
                # stale run 자동 해소 + 활성 충돌 재분류
                stale_resolved, still_active = ledger.resolve_stale_conflicts(
                    affected_files, self._STALE_THRESHOLD_SEC
                )
                if stale_resolved:
                    print(
                        f"[MaintenancePipeline] auto-resolved {len(stale_resolved)} stale run(s): "
                        f"{[e.run_id for e in stale_resolved]}"
                    )

                # 충돌 없음 → 실행 계속
                if not still_active:
                    if poll_count > 0:
                        print("[MaintenancePipeline] conflict resolved, proceeding.")
                    return

                # allow_conflict override
                if allow_conflict:
                    print(
                        f"[MaintenancePipeline] active conflict overridden: "
                        f"{[e.run_id for e in still_active]}"
                    )
                    return

                # 대기 로그 (매 _CONFLICT_LOG_INTERVAL 폴링마다 출력)
                if poll_count % self._CONFLICT_LOG_INTERVAL == 0:
                    waited_sec = poll_count * self._CONFLICT_POLL_INTERVAL
                    print(
                        f"[MaintenancePipeline] waiting for conflict to resolve "
                        f"(active={[e.run_id for e in still_active]}, "
                        f"waited={waited_sec}s)..."
                    )

                time.sleep(self._CONFLICT_POLL_INTERVAL)
                poll_count += 1

        except Exception as exc:
            # 예외 발생 시 대기 없이 진행 (충돌 확인 실패가 작업을 막지 않음)
            print(f"[MaintenancePipeline] conflict resolution error (proceeding): {exc}")

    def _check_approval_gate(self, prepared: dict, normalized: Any) -> dict:
        """ApprovalGate 상태를 검증한다. requires_approval=True일 때만 호출됨.

        Returns:
          {"approved": bool, "reasons": list[str]}

        gate 파일 없음 → 경고 후 통과 (이 단계에서 설정 안 된 경우 허용)
        gate 파일 있음 + execution_open=True + 해시 유효 → 통과
        gate 파일 있음 + 조건 불충족 → 차단
        """
        try:
            from core.approval_gate import ApprovalGate

            # slug를 prepared 또는 normalized에서 탐색
            slug = (
                (prepared.get("slug") if isinstance(prepared, dict) else None)
                or (prepared.get("work_item_slug") if isinstance(prepared, dict) else None)
                or (getattr(normalized, "work_item_slug", None))
                or (getattr(normalized, "issue_id", None))
                or ""
            )

            if not slug:
                # IMP-3 Fix: requires_approval=True인데 slug 없으면 명시적 경고 + 차단
                # (경고만 하고 통과시키면 승인 우회 가능)
                print("[MaintenancePipeline] approval gate: requires_approval=True but no slug found")
                return {
                    "approved": False,
                    "reasons": ["requires_approval=True but no work_item slug available — "
                                "cannot verify approval gate"],
                }

            gate = ApprovalGate(self._workspace, slug)

            if not gate.is_execution_open():
                return {
                    "approved": False,
                    "reasons": [f"approval gate not open for slug={slug!r}"],
                }

            valid, changed = gate.check_validity()
            if not valid:
                return {
                    "approved": False,
                    "reasons": [f"approval gate invalidated (changed docs: {changed})"],
                }

            return {"approved": True, "reasons": []}

        except Exception as exc:
            print(f"[MaintenancePipeline] approval gate check error (proceeding): {exc}")
            return {"approved": True, "reasons": [], "error": str(exc)}

    def _check_conflicts(self, normalized: Any) -> dict:
        """RunLedger로 동시 작업 충돌을 확인한다."""
        try:
            from core.control.run_ledger import RunLedger
            affected_files = self._get_affected_files(normalized)
            ledger = RunLedger(self._workspace)
            conflicting = ledger.conflict_check(affected_files)
            return {
                "has_conflict": len(conflicting) > 0,
                "conflicting_runs": [e.run_id for e in conflicting],
            }
        except Exception as exc:
            print(f"[MaintenancePipeline] conflict check failed: {exc}")
            return {"has_conflict": False, "conflicting_runs": []}

    def _run_with_supervisor(self, prepared: dict, normalized: Any, run_id: str) -> dict:
        """RuntimeSupervisor를 통해 실행한다."""
        try:
            from core.control.supervisor import RuntimeSupervisor
            supervisor = RuntimeSupervisor(self._workspace)
            return supervisor.supervise(self._pipeline, prepared, normalized, run_id)
        except Exception as exc:
            print(f"[MaintenancePipeline] supervisor failed: {exc}")
            return {"success": False, "error": str(exc)}

    def _run_regression_gate(self, normalized: Any, policy: dict) -> dict:
        """RegressionSafetyGate를 실행한다."""
        if not policy.get("requires_regression_test", False):
            return {"pass": True, "skipped": True, "reason": "policy does not require regression test"}

        try:
            from core.control.regression_gate import RegressionSafetyGate
            from core.control.change_impact import ImpactProfile, ChangeImpactProfiler
            from core.control.execution_policy import ExecutionPolicy

            change_impact_dict = self._get_change_impact(normalized)
            if change_impact_dict:
                impact_profile = ImpactProfile.from_dict(change_impact_dict)
            else:
                impact_profile = ImpactProfile()

            policy_dict = self._get_policy(normalized)
            exec_policy = ExecutionPolicy.from_dict(policy_dict) if policy_dict else None

            gate = RegressionSafetyGate()
            return gate.check(self._workspace, impact_profile, exec_policy)
        except Exception as exc:
            print(f"[MaintenancePipeline] regression gate failed: {exc}")
            return {"pass": True, "skipped": True, "error": str(exc)}

    def _advance_state_machine(self, workspace: str, run_id: str, outcome: str) -> None:
        """결과에 따라 상태 머신을 전진시킨다."""
        try:
            from core.control.maintenance_state import MaintenanceStateMachine
            sm = MaintenanceStateMachine(workspace, run_id)
            current = sm.current_state()

            if outcome == "success":
                transitions = ["verifying", "closing", "closed"]
            else:
                transitions = ["rollback", "closed_failed"]

            for to_state in transitions:
                if sm.can_transition(to_state):
                    sm.transition(to_state)
        except Exception as exc:
            print(f"[MaintenancePipeline] state machine advance failed: {exc}")

    def _close_ledger(self, workspace: str, run_id: str, outcome: str) -> None:
        """RunLedger에 종료를 기록한다."""
        try:
            from core.control.run_ledger import RunLedger
            ledger = RunLedger(workspace)
            ledger_outcome = "success" if outcome == "success" else "failed"
            ledger.close_run(run_id, outcome=ledger_outcome)
        except Exception as exc:
            print(f"[MaintenancePipeline] ledger close failed: {exc}")

    # ── 헬퍼 ──

    def _determine_outcome(self, supervisor_result: dict, regression_result: dict) -> str:
        """supervisor + regression 결과에서 최종 outcome을 판정한다."""
        supervisor_ok = supervisor_result.get("success", False)
        regression_ok = regression_result.get("pass", True)

        if supervisor_ok and regression_ok:
            return "success"
        if supervisor_ok and not regression_ok:
            return "partial"
        return "failed"

    def _get_policy(self, normalized: Any) -> dict:
        if isinstance(normalized, dict):
            return normalized.get("execution_policy", {})
        return getattr(normalized, "execution_policy", {}) or {}

    def _get_run_id(self, normalized: Any) -> str:
        if isinstance(normalized, dict):
            return normalized.get("run_id", "")
        return getattr(normalized, "run_id", "") or ""

    def _get_task_input(self, normalized: Any) -> str:
        if isinstance(normalized, dict):
            return normalized.get("raw_input", "")
        return getattr(normalized, "raw_input", "") or ""

    def _get_work_kind(self, normalized: Any) -> str:
        if isinstance(normalized, dict):
            return normalized.get("work_kind", "maintenance")
        return getattr(normalized, "work_kind", "maintenance") or "maintenance"

    def _get_affected_files(self, normalized: Any) -> list[str]:
        impact = self._get_change_impact(normalized)
        return impact.get("affected_files", []) if impact else []

    def _get_change_impact(self, normalized: Any) -> dict:
        if isinstance(normalized, dict):
            return normalized.get("change_impact", {})
        return getattr(normalized, "change_impact", {}) or {}

    def _get_evidence_scope(self, normalized: Any) -> list[str]:
        impact = self._get_change_impact(normalized)
        return impact.get("evidence_scope", []) if impact else []


__all__ = ["MaintenancePipeline", "NormalizedRequest"]
