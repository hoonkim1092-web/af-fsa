"""
core/pdca_state.py
==================
BKIT 스타일 PDCA 상태 머신.

PDCAPhase  : IDLE → PLAN → PLAN_REVIEW → DESIGN → DESIGN_REVIEW
             → DO → DO_RUNNING → CHECK → CHECK_REVIEW → ITERATE → COMPLETE
ProjectLevel: STARTER / DYNAMIC / ENTERPRISE
PDCAState  : 프로젝트별 상태 (JSON 영속화)
PDCAStateMachine: 유효 전환 검사 + 전환 실행
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ──────────────────────────────────────────────────────────────
# Enum 정의
# ──────────────────────────────────────────────────────────────

class PDCAPhase(str, Enum):
    IDLE           = "idle"
    PLAN           = "plan"
    PLAN_REVIEW    = "plan_review"
    DESIGN         = "design"
    DESIGN_REVIEW  = "design_review"
    DO             = "do"
    DO_RUNNING     = "do_running"
    CHECK          = "check"
    CHECK_REVIEW   = "check_review"
    ITERATE        = "iterate"
    COMPLETE       = "complete"


class ProjectLevel(str, Enum):
    STARTER    = "starter"     # 간단한 앱 (정적 사이트, 스크립트)
    DYNAMIC    = "dynamic"     # 풀스택 (API, DB 포함)
    ENTERPRISE = "enterprise"  # 마이크로서비스, 멀티 에이전트


# ──────────────────────────────────────────────────────────────
# 상태 데이터클래스
# ──────────────────────────────────────────────────────────────

@dataclass
class PDCAState:
    project_id: str
    project_name: str = ""
    task_description: str = ""
    level: str = ProjectLevel.STARTER.value
    phase: str = PDCAPhase.IDLE.value

    # 각 단계 산출물
    plan_brief: dict = field(default_factory=dict)
    design_options: list = field(default_factory=list)   # 3가지 아키텍처 옵션
    selected_design: int = -1                              # 선택된 옵션 인덱스
    gap_analysis: dict = field(default_factory=dict)

    # 반복 카운터
    iterate_count: int = 0
    max_iterate: int = 5

    # 문서 무결성 해시 (ApprovalGate 연동용)
    checkpoint_hashes: dict = field(default_factory=dict)

    # 메타데이터
    created_at: str = ""
    updated_at: str = ""

    # ── 영속화 ──

    @staticmethod
    def _state_path(workspace: str) -> str:
        return os.path.join(workspace, ".af", "pdca_state.json")

    def save(self, workspace: str) -> str:
        """상태를 {workspace}/.af/pdca_state.json 에 저장한다."""
        from core.utils import now_iso
        self.updated_at = now_iso()

        path = self._state_path(workspace)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, workspace: str) -> "PDCAState | None":
        """저장된 상태를 복원한다. 없으면 None 반환."""
        path = cls._state_path(workspace)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return None

    @classmethod
    def create(cls, project_id: str, project_name: str, task_description: str, level: ProjectLevel) -> "PDCAState":
        """새 PDCAState 생성."""
        from core.utils import now_iso
        return cls(
            project_id=project_id,
            project_name=project_name,
            task_description=task_description,
            level=level.value,
            phase=PDCAPhase.IDLE.value,
            created_at=now_iso(),
            updated_at=now_iso(),
        )

    # ── 편의 프로퍼티 ──

    @property
    def phase_enum(self) -> PDCAPhase:
        return PDCAPhase(self.phase)

    @property
    def level_enum(self) -> ProjectLevel:
        return ProjectLevel(self.level)

    def hash_artifact(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]


# ──────────────────────────────────────────────────────────────
# 상태 머신
# ──────────────────────────────────────────────────────────────

_PHASE_LABEL_KO: dict[PDCAPhase, str] = {
    PDCAPhase.IDLE:          "대기",
    PDCAPhase.PLAN:          "기획 중",
    PDCAPhase.PLAN_REVIEW:   "기획 검토",
    PDCAPhase.DESIGN:        "설계 중",
    PDCAPhase.DESIGN_REVIEW: "설계 검토",
    PDCAPhase.DO:            "구현 준비",
    PDCAPhase.DO_RUNNING:    "구현 중",
    PDCAPhase.CHECK:         "검증 중",
    PDCAPhase.CHECK_REVIEW:  "검증 검토",
    PDCAPhase.ITERATE:       "자동 수정 중",
    PDCAPhase.COMPLETE:      "완료",
}

# 각 phase에서 전환 가능한 다음 phase 목록
_VALID_TRANSITIONS: dict[PDCAPhase, list[PDCAPhase]] = {
    PDCAPhase.IDLE:          [PDCAPhase.PLAN],
    PDCAPhase.PLAN:          [PDCAPhase.PLAN_REVIEW],
    PDCAPhase.PLAN_REVIEW:   [PDCAPhase.DESIGN, PDCAPhase.PLAN, PDCAPhase.IDLE],
    PDCAPhase.DESIGN:        [PDCAPhase.DESIGN_REVIEW],
    PDCAPhase.DESIGN_REVIEW: [PDCAPhase.DO, PDCAPhase.DESIGN, PDCAPhase.IDLE],
    PDCAPhase.DO:            [PDCAPhase.DO_RUNNING, PDCAPhase.IDLE],
    PDCAPhase.DO_RUNNING:    [PDCAPhase.CHECK, PDCAPhase.ITERATE],
    PDCAPhase.CHECK:         [PDCAPhase.CHECK_REVIEW],
    PDCAPhase.CHECK_REVIEW:  [PDCAPhase.ITERATE, PDCAPhase.COMPLETE, PDCAPhase.DO],
    PDCAPhase.ITERATE:       [PDCAPhase.CHECK, PDCAPhase.COMPLETE],
    PDCAPhase.COMPLETE:      [PDCAPhase.IDLE, PDCAPhase.PLAN],
}

# /next 커맨드에서 자동으로 이동할 다음 단계
_NEXT_PHASE: dict[PDCAPhase, PDCAPhase] = {
    PDCAPhase.IDLE:          PDCAPhase.PLAN,
    PDCAPhase.PLAN:          PDCAPhase.PLAN_REVIEW,
    PDCAPhase.PLAN_REVIEW:   PDCAPhase.DESIGN,
    PDCAPhase.DESIGN:        PDCAPhase.DESIGN_REVIEW,
    PDCAPhase.DESIGN_REVIEW: PDCAPhase.DO,
    PDCAPhase.DO:            PDCAPhase.DO_RUNNING,
    PDCAPhase.DO_RUNNING:    PDCAPhase.CHECK,
    PDCAPhase.CHECK:         PDCAPhase.CHECK_REVIEW,
    PDCAPhase.CHECK_REVIEW:  PDCAPhase.COMPLETE,
    PDCAPhase.ITERATE:       PDCAPhase.CHECK,
    PDCAPhase.COMPLETE:      PDCAPhase.COMPLETE,
}


class PDCAStateMachine:
    """PDCA 상태 전환을 검증하고 실행한다."""

    def __init__(self, state: PDCAState, workspace: str):
        self.state = state
        self.workspace = workspace

    # ── 현재 상태 정보 ──

    @property
    def current(self) -> PDCAPhase:
        return self.state.phase_enum

    def current_label_ko(self) -> str:
        return _PHASE_LABEL_KO.get(self.current, self.current.value)

    def next_phase(self) -> PDCAPhase | None:
        nxt = _NEXT_PHASE.get(self.current)
        if nxt == self.current:
            return None
        return nxt

    # ── 전환 ──

    def can_transition(self, target: PDCAPhase) -> bool:
        allowed = _VALID_TRANSITIONS.get(self.current, [])
        return target in allowed

    def transition(self, target: PDCAPhase, workspace: str | None = None) -> None:
        """상태를 전환하고 영속화한다."""
        if not self.can_transition(target):
            allowed = [p.value for p in _VALID_TRANSITIONS.get(self.current, [])]
            raise ValueError(
                f"'{self.current.value}' → '{target.value}' 전환 불가. "
                f"허용: {allowed}"
            )
        self.state.phase = target.value
        self.state.save(workspace or self.workspace)

    def force_transition(self, target: PDCAPhase, workspace: str | None = None) -> None:
        """검증 없이 강제 전환 (내부 사용)."""
        self.state.phase = target.value
        self.state.save(workspace or self.workspace)

    # ── 유틸 ──

    def status_lines(self) -> list[str]:
        s = self.state
        level_label = {"starter": "Starter", "dynamic": "Dynamic", "enterprise": "Enterprise"}.get(s.level, s.level)
        lines = [
            f"  프로젝트 : {s.project_name or s.project_id}",
            f"  설명     : {s.task_description[:60]}{'...' if len(s.task_description) > 60 else ''}",
            f"  레벨     : {level_label}",
            f"  단계     : {self.current_label_ko()} ({s.phase})",
        ]
        if s.iterate_count:
            lines.append(f"  반복     : {s.iterate_count}/{s.max_iterate}")
        if s.selected_design >= 0 and s.design_options:
            opt = s.design_options[s.selected_design]
            lines.append(f"  선택된 설계: [{s.selected_design + 1}] {opt.get('name', '')}")
        return lines
