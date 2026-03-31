"""
core/control/execution_policy.py
=================================
ExecutionPolicyResolver — 실행 정책 결정기.

work_kind + risk_level + blast_radius + continuity_health를 조합해
어떤 파이프라인 경로를 사용할지 결정한다.

policy:
  quick_fix       — 빠른 단일 버그 수정 (approval/QA 스킵)
  standard_update — 일반 유지보수/기능 업데이트
  deep_update     — 고위험 또는 광범위한 변경
  full_bootstrap  — 신규 프로젝트 (기존 prepare() 전체 사용)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any

# ──────────────────────────────────────────────────────────────
# Policy → 파이프라인 스테이지 매핑 (설계 문서 4.6 기반)
# ──────────────────────────────────────────────────────────────
POLICY_STAGE_MAP: dict[str, dict[str, Any]] = {
    "quick_fix": {
        "requires_approval":          False,
        "requires_work_item_docs":    False,
        "requires_agent_qa":          False,
        "requires_parallel_critique": False,
        "requires_regression_test":   True,
        "skippable_stages":           ["evidence_retry", "critique", "agent_qa", "convergence_loop"],
        "max_retries":                1,
    },
    "standard_update": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          "auto",   # iMAD gate로 자동 결정
        "requires_parallel_critique": False,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                2,
    },
    "deep_update": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          True,
        "requires_parallel_critique": True,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                2,
    },
    "full_bootstrap": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          True,
        "requires_parallel_critique": True,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                3,
    },
}


@dataclass
class ExecutionPolicy:
    policy_id: str
    execution_policy: str                   # "quick_fix" | "standard_update" | "deep_update" | "full_bootstrap"
    requires_approval: bool
    requires_work_item_docs: bool
    requires_agent_qa: bool | str           # bool 또는 "auto"
    requires_parallel_critique: bool
    requires_regression_test: bool
    skippable_stages: list[str] = field(default_factory=list)
    max_retries: int = 2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionPolicy":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def stage_skippable(self, stage: str) -> bool:
        """주어진 단계가 이 policy에서 스킵 가능한지 확인한다."""
        return stage in self.skippable_stages


class ExecutionPolicyResolver:
    """work_kind + risk_level + blast_radius → ExecutionPolicy."""

    def resolve(
        self,
        work_kind: str,
        risk_level: str = "normal",
        blast_radius: str = "module",
        continuity_health: str = "healthy",
        workspace: str = "",
    ) -> ExecutionPolicy:
        """
        판정 규칙 (우선순위 순):
          1. work_kind == "new_project"
             → full_bootstrap
          2. risk_level == "critical" OR blast_radius == "system_wide"
             → deep_update
          3. continuity_health == "interrupted" (미완료 세션 재개)
             → deep_update (안전하게)
          4. work_kind == "bugfix" AND risk_level == "low" AND blast_radius == "isolated"
             → quick_fix
          5. 그 외
             → standard_update
        """
        policy_name = self._pick_policy(work_kind, risk_level, blast_radius, continuity_health)
        cfg = POLICY_STAGE_MAP[policy_name]

        from core.utils import now_iso
        policy_id = f"policy-{now_iso()[:10].replace('-', '')}"

        return ExecutionPolicy(
            policy_id=policy_id,
            execution_policy=policy_name,
            requires_approval=bool(cfg["requires_approval"]),
            requires_work_item_docs=bool(cfg["requires_work_item_docs"]),
            requires_agent_qa=cfg["requires_agent_qa"],
            requires_parallel_critique=bool(cfg["requires_parallel_critique"]),
            requires_regression_test=bool(cfg["requires_regression_test"]),
            skippable_stages=list(cfg["skippable_stages"]),
            max_retries=int(cfg["max_retries"]),
        )

    def save(self, policy: ExecutionPolicy, workspace: str) -> None:
        """실행 정책을 .af_runtime/control/execution_policy.json에 저장한다."""
        control_dir = os.path.join(workspace, ".af_runtime", "control")
        os.makedirs(control_dir, exist_ok=True)
        path = os.path.join(control_dir, "execution_policy.json")
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(policy.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[ExecutionPolicyResolver] save failed: {exc}")

    def load(self, workspace: str) -> ExecutionPolicy | None:
        """저장된 실행 정책을 불러온다."""
        path = os.path.join(workspace, ".af_runtime", "control", "execution_policy.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return ExecutionPolicy.from_dict(json.load(f))
        except Exception:
            return None

    # ── 내부 ──

    def _pick_policy(
        self,
        work_kind: str,
        risk_level: str,
        blast_radius: str,
        continuity_health: str,
    ) -> str:
        if work_kind == "new_project":
            return "full_bootstrap"

        if risk_level == "critical" or blast_radius == "system_wide":
            return "deep_update"

        if continuity_health == "interrupted":
            return "deep_update"

        if (
            work_kind == "bugfix"
            and risk_level == "low"
            and blast_radius == "isolated"
        ):
            return "quick_fix"

        return "standard_update"


__all__ = ["ExecutionPolicy", "ExecutionPolicyResolver", "POLICY_STAGE_MAP"]
