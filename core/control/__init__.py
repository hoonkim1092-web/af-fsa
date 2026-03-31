"""
core/control/
=============
Control Plane — 유지보수/업데이트 운영 계층.

기존 실행기(ProjectPipeline, DynamicOrchestrator)를 대체하지 않고
그 위에 운영 지속성(operational continuity)을 얹는 sidecar 계층.

설계 원칙:
  - 기존 RequestRouter, IntentGate, ProjectPipeline, DynamicOrchestrator 수정 없음
  - 새 status 값을 기존 project_board_state.json에 직접 추가하지 않음
  - 기존 writer(manifest, resume_brief, session_adapter)를 교체하지 않음
  - provider native hook 이름 보존, canonical 정규화는 bridge 내부에서만

공개 인터페이스:
  Phase 1  — WorkKindClassifier, IssueContextManager,
             ExecutionPolicyResolver, RunLedger,
             MaintenanceStateMachine, ChangeImpactProfiler
  Phase 2  — ContinuitySnapshotBuilder, CanonicalLifecycleBridge
  Phase 3  — ControlPlaneIntake, MaintenancePipeline, RollbackManager
  Phase 4  — RuntimeSupervisor, RegressionSafetyGate
"""

from core.control.work_kind import WorkKindClassifier
from core.control.issue_context import IssueContext, IssueContextManager
from core.control.execution_policy import ExecutionPolicy, ExecutionPolicyResolver, POLICY_STAGE_MAP
from core.control.run_ledger import LedgerEntry, RunLedger
from core.control.maintenance_state import MaintenanceStateMachine, VALID_TRANSITIONS
from core.control.change_impact import ImpactProfile, ChangeImpactProfiler
from core.control.continuity_snapshot import ContinuitySnapshot, ContinuitySnapshotBuilder
from core.control.lifecycle_bridge import CanonicalEvent, CanonicalLifecycleBridge, CANONICAL_EVENTS
from core.control.rollback import RollbackPlan, RollbackManager
from core.control.intake import NormalizedRequest, ControlPlaneIntake
from core.control.maintenance_pipeline import MaintenancePipeline
from core.control.supervisor import RuntimeSupervisor
from core.control.regression_gate import RegressionSafetyGate

__all__ = [
    # Phase 1
    "WorkKindClassifier",
    "IssueContext", "IssueContextManager",
    "ExecutionPolicy", "ExecutionPolicyResolver", "POLICY_STAGE_MAP",
    "LedgerEntry", "RunLedger",
    "MaintenanceStateMachine", "VALID_TRANSITIONS",
    "ImpactProfile", "ChangeImpactProfiler",
    # Phase 2
    "ContinuitySnapshot", "ContinuitySnapshotBuilder",
    "CanonicalEvent", "CanonicalLifecycleBridge", "CANONICAL_EVENTS",
    # Phase 3
    "RollbackPlan", "RollbackManager",
    "NormalizedRequest", "ControlPlaneIntake",
    "MaintenancePipeline",
    # Phase 4
    "RuntimeSupervisor",
    "RegressionSafetyGate",
]
