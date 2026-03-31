"""
core/control/intake.py
========================
ControlPlaneIntake — 유지보수 요청의 정규화 입구.

기존 RequestRouter를 감싸되 대체하지 않음.
RequestRouter.route() 결과를 입력으로 받아 Control Plane 처리를 추가한다.

흐름:
  1. RequestRouter.route() 결과를 받음 (기존 흐름 유지)
  2. WorkKindClassifier로 2차 분류
  3. IssueContextManager로 issue binding
  4. ContinuitySnapshotBuilder로 이전 상태 집계
  5. ExecutionPolicyResolver로 실행 정책 결정
  6. ChangeImpactProfiler로 영향 범위 분석 (maintenance일 때만)
  7. MaintenanceStateMachine 초기화
  8. RunLedger에 시작 기록
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class NormalizedRequest:
    """Control Plane을 통과한 정규화 요청."""
    raw_input: str
    route: dict                     # RequestRouter.route() 결과
    work_kind: str                  # "new_project" | "maintenance" | "bugfix" | "feature_update" | "refactor"
    issue_kind: str                 # "incident" | "planned_update" | "regression" | "backlog_item" | "research_task"
    issue_context: dict             # IssueContext.to_dict()
    execution_policy: dict          # ExecutionPolicy.to_dict()
    continuity_snapshot: dict       # ContinuitySnapshot.to_dict()
    change_impact: dict             # ImpactProfile.to_dict() (maintenance일 때만, 아니면 {})
    state: str                      # 현재 상태 머신 단계
    run_id: str                     # 이번 run의 고유 ID
    workspace: str

    def to_dict(self) -> dict:
        return {
            "raw_input": self.raw_input,
            "route": self.route,
            "work_kind": self.work_kind,
            "issue_kind": self.issue_kind,
            "issue_context": self.issue_context,
            "execution_policy": self.execution_policy,
            "continuity_snapshot": self.continuity_snapshot,
            "change_impact": self.change_impact,
            "state": self.state,
            "run_id": self.run_id,
            "workspace": self.workspace,
        }


class ControlPlaneIntake:
    """
    유지보수 요청의 정규화 입구.

    설계 원칙:
      - RequestRouter.route() 결과를 입력으로 받음 (RequestRouter 직접 호출하지 않음)
      - 기존 pipeline 흐름에서 이 클래스를 호출하는 방식으로 연결
      - 각 서브 시스템은 독립적으로 실패해도 전체 흐름을 막지 않음
    """

    def normalize(
        self,
        task_input: str,
        workspace: str,
        route: dict,
        board: dict | None = None,
    ) -> NormalizedRequest:
        """
        요청을 정규화하고 NormalizedRequest를 반환한다.

        Args:
            task_input: 원본 사용자 입력
            workspace:  프로젝트 작업 디렉토리
            route:      RequestRouter.route() 결과
            board:      project_board_state (있으면 impact 분석에 사용)

        Returns:
            NormalizedRequest
        """
        from core.utils import now_iso
        import uuid

        # B4 Fix: hex 6자리(16.7M)는 충돌 위험 → uuid4 전체 32자리 사용
        run_id = f"run-{now_iso()[:19].replace(':', '').replace('-', '')}-{uuid.uuid4().hex}"

        # ── 2차 분류 ──
        work_kind, issue_kind = self._classify_work_kind(route, workspace)

        # ── 이슈 컨텍스트 ──
        issue_context = self._bind_issue_context(
            task_input, workspace, work_kind, issue_kind,
            route.get("intent", ""),
        )

        # ── 연속성 스냅샷 ──
        continuity_snapshot = self._build_continuity_snapshot(workspace)

        # ── 영향 분석 (maintenance/bugfix/refactor인 경우에만) ──
        needs_impact = work_kind in ("maintenance", "bugfix", "refactor", "feature_update")
        change_impact = self._profile_change_impact(task_input, workspace, board) \
            if needs_impact else {}

        # ── 실행 정책 ──
        blast_radius = change_impact.get("blast_radius", "module") if change_impact else "module"
        risk_level = issue_context.get("risk_level", "normal") if issue_context else "normal"
        continuity_health = continuity_snapshot.get("overall_health", "healthy")
        execution_policy = self._resolve_execution_policy(
            work_kind, risk_level, blast_radius, continuity_health, workspace,
        )

        # ── 상태 머신 초기화 ──
        state = self._initialize_state_machine(workspace, run_id)

        # ── RunLedger 기록 ──
        self._open_ledger_run(
            workspace, run_id,
            issue_id=issue_context.get("issue_id", ""),
            pipeline=route.get("pipeline", "project"),
            work_kind=work_kind,
            execution_policy=execution_policy.get("execution_policy", ""),
            blast_radius=blast_radius,
            affected_files=change_impact.get("affected_files", []) if change_impact else [],
        )

        return NormalizedRequest(
            raw_input=task_input,
            route=route,
            work_kind=work_kind,
            issue_kind=issue_kind,
            issue_context=issue_context,
            execution_policy=execution_policy,
            continuity_snapshot=continuity_snapshot,
            change_impact=change_impact,
            state=state,
            run_id=run_id,
            workspace=workspace,
        )

    # ── 서브 시스템 호출 (실패 시 빈 값 반환) ──

    def _classify_work_kind(self, route: dict, workspace: str) -> tuple[str, str]:
        try:
            from core.control.work_kind import WorkKindClassifier
            return WorkKindClassifier().classify(route, workspace)
        except Exception as exc:
            print(f"[ControlPlaneIntake] work_kind classification failed: {exc}")
            return "maintenance", "planned_update"

    def _bind_issue_context(
        self,
        task_input: str,
        workspace: str,
        work_kind: str,
        issue_kind: str,
        intent: str,
    ) -> dict:
        try:
            from core.control.issue_context import IssueContextManager
            title = task_input[:120].strip() or f"{work_kind} request"
            mgr = IssueContextManager(workspace)
            ctx = mgr.bind(
                work_kind=work_kind,
                issue_kind=issue_kind,
                title=title,
                risk_level=self._infer_risk_level(intent, work_kind),
                source="user_request",
            )
            return ctx.to_dict()
        except Exception as exc:
            print(f"[ControlPlaneIntake] issue_context bind failed: {exc}")
            return {}

    def _build_continuity_snapshot(self, workspace: str) -> dict:
        try:
            from core.control.continuity_snapshot import ContinuitySnapshotBuilder
            snapshot = ContinuitySnapshotBuilder().build(workspace)
            return snapshot.to_dict()
        except Exception as exc:
            print(f"[ControlPlaneIntake] continuity snapshot failed: {exc}")
            return {}

    def _profile_change_impact(
        self,
        task_input: str,
        workspace: str,
        board: dict | None,
    ) -> dict:
        try:
            from core.control.change_impact import ChangeImpactProfiler
            profile = ChangeImpactProfiler().profile(task_input, workspace, board)
            return profile.to_dict()
        except Exception as exc:
            print(f"[ControlPlaneIntake] change_impact profiling failed: {exc}")
            return {}

    def _resolve_execution_policy(
        self,
        work_kind: str,
        risk_level: str,
        blast_radius: str,
        continuity_health: str,
        workspace: str,
    ) -> dict:
        try:
            from core.control.execution_policy import ExecutionPolicyResolver
            resolver = ExecutionPolicyResolver()
            policy = resolver.resolve(work_kind, risk_level, blast_radius, continuity_health)
            resolver.save(policy, workspace)
            return policy.to_dict()
        except Exception as exc:
            print(f"[ControlPlaneIntake] execution_policy resolve failed: {exc}")
            return {}

    def _initialize_state_machine(self, workspace: str, run_id: str) -> str:
        try:
            from core.control.maintenance_state import MaintenanceStateMachine
            sm = MaintenanceStateMachine(workspace, run_id)
            record = sm.initialize()
            return record.current_state
        except Exception as exc:
            print(f"[ControlPlaneIntake] state machine init failed: {exc}")
            return "intake"

    def _open_ledger_run(
        self,
        workspace: str,
        run_id: str,
        issue_id: str,
        pipeline: str,
        work_kind: str,
        execution_policy: str,
        blast_radius: str,
        affected_files: list[str],
    ) -> None:
        try:
            from core.control.run_ledger import RunLedger
            ledger = RunLedger(workspace)
            ledger.open_run(
                run_id=run_id,
                issue_id=issue_id,
                pipeline=pipeline,
                work_kind=work_kind,
                execution_policy=execution_policy,
                change_impact_summary=blast_radius,
                metadata={"affected_files": affected_files},
            )
        except Exception as exc:
            print(f"[ControlPlaneIntake] ledger open_run failed: {exc}")

    def _infer_risk_level(self, intent: str, work_kind: str) -> str:
        """
        intent + work_kind에서 초기 risk_level을 추론한다.

        B5 Fix: 이전 코드는 모든 분기가 "normal"을 반환하는 dead code였음.
        실질적인 risk 신호를 반영하도록 수정.
        """
        # trivial/question → low risk
        if intent in ("trivial", "question"):
            return "low"
        # 새 프로젝트는 기본 normal
        if work_kind == "new_project":
            return "normal"
        # 단순 bugfix → normal
        if work_kind == "bugfix" or intent == "debugging":
            return "normal"
        # 기존 코드 구조 변경 → high
        if work_kind == "refactor":
            return "high"
        # 기능 추가/유지보수 → normal
        if work_kind in ("feature_update", "maintenance"):
            return "normal"
        return "normal"


__all__ = ["NormalizedRequest", "ControlPlaneIntake"]
