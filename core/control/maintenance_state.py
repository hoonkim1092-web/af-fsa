"""
core/control/maintenance_state.py
===================================
MaintenanceStateMachine — 유지보수 작업 생명주기 상태 머신.

선형 12단계 흐름을 명시적 상태 전이로 교체해
재개/분기/롤백이 코드 수준에서 강제되도록 한다.

저장 경로: {workspace}/.af_runtime/control/maintenance_state.json
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any

# ──────────────────────────────────────────────────────────────
# 허용된 상태 전이 (v2 설계 문서 3.2)
# ──────────────────────────────────────────────────────────────
VALID_TRANSITIONS: dict[str, list[str]] = {
    "intake":             ["classified"],
    "classified":         ["context_bound"],
    "context_bound":      ["policy_resolved"],
    "policy_resolved":    ["preparing"],
    "preparing":          ["awaiting_approval", "approved"],  # quick_fix는 preparing→approved
    "awaiting_approval":  ["approved", "closed_failed"],      # 사용자 거부 가능
    "approved":           ["executing"],
    "executing":          ["verifying", "retrying", "rollback"],
    "retrying":           ["executing", "rollback"],           # max 2회
    "verifying":          ["closing", "rollback"],
    "closing":            ["closed"],
    "rollback":           ["closed_failed"],
    "closed":             [],
    "closed_failed":      [],
}

# 종료 상태 집합
TERMINAL_STATES = {"closed", "closed_failed"}

# 초기 상태
INITIAL_STATE = "intake"


@dataclass
class StateRecord:
    """상태 머신의 현재 스냅샷."""
    run_id: str
    workspace: str
    current_state: str = INITIAL_STATE
    previous_state: str = ""
    retry_count: int = 0
    transition_log: list[dict[str, str]] = field(default_factory=list)
    failure_reasons: list[dict] = field(default_factory=list)  # 실패 분석 이력
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StateRecord":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def is_terminal(self) -> bool:
        return self.current_state in TERMINAL_STATES

    @property
    def is_failed(self) -> bool:
        return self.current_state == "closed_failed"


class MaintenanceStateMachine:
    """유지보수 작업 상태 전이를 관리하고 영속화한다."""

    def __init__(self, workspace: str, run_id: str):
        self._workspace = workspace
        self._run_id = run_id
        self._control_dir = os.path.join(workspace, ".af_runtime", "control")

    # ── Public API ──

    def initialize(self) -> StateRecord:
        """새 상태 레코드를 생성하고 저장한다. (INITIAL_STATE = "intake")"""
        from core.utils import now_iso
        record = StateRecord(
            run_id=self._run_id,
            workspace=self._workspace,
            current_state=INITIAL_STATE,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        self._save(record)
        return record

    def transition(self, to_state: str, metadata: dict | None = None) -> StateRecord:
        """
        현재 상태에서 to_state로 전이한다.
        허용되지 않은 전이이면 ValueError를 발생시킨다.
        """
        from core.utils import now_iso
        record = self.load()
        if record is None:
            record = self.initialize()

        current = record.current_state
        allowed = VALID_TRANSITIONS.get(current, [])

        if to_state not in allowed:
            raise ValueError(
                f"[StateMachine] Invalid transition: {current!r} → {to_state!r}. "
                f"Allowed: {allowed}"
            )

        # retry 카운터 관리
        if to_state == "retrying":
            record.retry_count += 1
        if to_state == "executing" and current == "retrying":
            pass  # 카운터 유지

        record.previous_state = current
        record.current_state = to_state
        record.updated_at = now_iso()
        record.transition_log.append({
            "from": current,
            "to": to_state,
            "at": record.updated_at,
            "metadata": json.dumps(metadata or {}),
        })
        if metadata:
            record.metadata.update(metadata)

        self._save(record)
        print(f"[StateMachine] {current} → {to_state} (run={self._run_id})")
        return record

    def load(self) -> StateRecord | None:
        """저장된 상태 레코드를 불러온다."""
        path = self._state_path()
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return StateRecord.from_dict(json.load(f))
        except Exception:
            return None

    def current_state(self) -> str:
        """현재 상태를 문자열로 반환한다."""
        record = self.load()
        return record.current_state if record else INITIAL_STATE

    def can_transition(self, to_state: str) -> bool:
        """현재 상태에서 to_state로 전이 가능한지 확인한다."""
        current = self.current_state()
        return to_state in VALID_TRANSITIONS.get(current, [])

    def record_failure(self, failure_entry: dict) -> StateRecord | None:
        """
        실패 분석 결과를 StateRecord.failure_reasons에 누적 저장한다.
        상태 전이는 하지 않음 — 분석 이력만 기록.

        failure_entry 구조 (권장):
          {
            "task_id": str,
            "role": str,
            "error": str,
            "evaluator_action": "retry" | "pivot" | "abort",
            "evaluator_reasoning": str,
            "new_instruction": str,
            "repeat_count": int,
            "at": str (ISO timestamp),
          }
        """
        from core.utils import now_iso
        record = self.load()
        if record is None:
            return None
        entry = {**failure_entry, "at": failure_entry.get("at") or now_iso()}
        record.failure_reasons.append(entry)
        record.updated_at = now_iso()
        self._save(record)
        return record

    def force_reset(self, reason: str = "") -> StateRecord:
        """비상 리셋: rollback → closed_failed로 강제 전환."""
        from core.utils import now_iso
        record = self.load()
        if record is None:
            record = self.initialize()
        if record.is_terminal:
            return record
        record.previous_state = record.current_state
        record.current_state = "closed_failed"
        record.updated_at = now_iso()
        record.transition_log.append({
            "from": record.previous_state,
            "to": "closed_failed",
            "at": record.updated_at,
            "metadata": json.dumps({"force_reset": True, "reason": reason}),
        })
        self._save(record)
        return record

    # ── 내부 ──

    def _state_path(self) -> str:
        return os.path.join(self._control_dir, "maintenance_state.json")

    def _save(self, record: StateRecord) -> None:
        os.makedirs(self._control_dir, exist_ok=True)
        path = self._state_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[StateMachine] save failed: {exc}")


__all__ = ["MaintenanceStateMachine", "StateRecord", "VALID_TRANSITIONS", "TERMINAL_STATES", "INITIAL_STATE"]
