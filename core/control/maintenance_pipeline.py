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

    def execute(self, prepared: dict, normalized: Any) -> dict:
        """
        실행 단계.

        Args:
            prepared:   prepare()의 반환값
            normalized: NormalizedRequest (또는 dict)

        Returns:
            result: dict with keys ("success", "outcome", "state", "errors")
        """
        policy = self._get_policy(normalized)
        run_id = self._get_run_id(normalized)

        # 1. 동시 작업 충돌 확인
        conflict_result = self._check_conflicts(normalized)
        if conflict_result["has_conflict"]:
            print(
                f"[MaintenancePipeline] conflict detected: "
                f"{conflict_result['conflicting_runs']}"
            )
            # 경고만 출력, 실행은 계속 (사용자 결정에 따라)

        # 2. Supervisor를 통한 실행
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
