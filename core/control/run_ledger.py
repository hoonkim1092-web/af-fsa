"""
core/control/run_ledger.py
===========================
RunLedger — append-only JSONL 실행 이력 저장소.

기존 .af_manifest.json을 대체하지 않는 sidecar journal.
각 실행(run)의 시작·업데이트·종료를 원자적으로 기록한다.

저장 경로: {workspace}/.af_runtime/control/run_ledger.jsonl
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any

_LEDGER_FILENAME = "run_ledger.jsonl"


@dataclass
class LedgerEntry:
    run_id: str
    issue_id: str                           # IssueContext.issue_id
    workspace: str
    pipeline: str = "project"               # "project" | "single" (기존 라우터 값 그대로)
    work_kind: str = ""
    execution_policy: str = ""
    work_item_slug: str = ""
    state: str = "intake"                   # 상태 머신 현재 상태
    board_summary: dict = field(default_factory=dict)  # {pending:N, in_progress:N, ...}
    change_impact_summary: str = ""         # blast_radius
    started_at: str = ""
    updated_at: str = ""
    closed_at: str = ""                     # 빈 문자열이면 미종료
    outcome: str = ""                       # "success" | "partial" | "failed" | "rolled_back"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LedgerEntry":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def is_active(self) -> bool:
        return not self.closed_at


class RunLedger:
    """
    append-only JSONL journal.

    각 run은 복수의 LedgerEntry로 기록된다:
      1. 시작 시  — state="intake", closed_at=""
      2. 상태 변경 — state=current, closed_at=""
      3. 종료 시  — closed_at=ISO, outcome 설정
    """

    def __init__(self, workspace: str):
        self._workspace = workspace
        self._control_dir = os.path.join(workspace, ".af_runtime", "control")

    @property
    def _ledger_path(self) -> str:
        return os.path.join(self._control_dir, _LEDGER_FILENAME)

    # ── Public API ──

    def append(self, entry: LedgerEntry) -> None:
        """원자적으로 entry를 JSONL에 추가한다."""
        os.makedirs(self._control_dir, exist_ok=True)
        line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
        try:
            with open(self._ledger_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except Exception as exc:
            print(f"[RunLedger] append failed: {exc}")

    def open_run(
        self,
        run_id: str,
        issue_id: str,
        pipeline: str = "project",
        work_kind: str = "",
        execution_policy: str = "",
        work_item_slug: str = "",
        change_impact_summary: str = "",
        metadata: dict | None = None,
    ) -> LedgerEntry:
        """새 run의 시작을 기록한다."""
        from core.utils import now_iso
        entry = LedgerEntry(
            run_id=run_id,
            issue_id=issue_id,
            workspace=self._workspace,
            pipeline=pipeline,
            work_kind=work_kind,
            execution_policy=execution_policy,
            work_item_slug=work_item_slug,
            state="intake",
            change_impact_summary=change_impact_summary,
            started_at=now_iso(),
            updated_at=now_iso(),
            metadata=metadata or {},
        )
        self.append(entry)
        return entry

    def update_run(
        self,
        run_id: str,
        state: str = "",
        board_summary: dict | None = None,
        metadata: dict | None = None,
    ) -> LedgerEntry | None:
        """실행 상태 변경을 기록한다 (기존 항목 수정이 아니라 새 항목 추가)."""
        from core.utils import now_iso
        # 최근 항목 찾기
        latest = self._get_latest(run_id)
        if latest is None:
            return None
        updated = LedgerEntry(
            run_id=latest.run_id,
            issue_id=latest.issue_id,
            workspace=latest.workspace,
            pipeline=latest.pipeline,
            work_kind=latest.work_kind,
            execution_policy=latest.execution_policy,
            work_item_slug=latest.work_item_slug,
            state=state or latest.state,
            board_summary=board_summary or latest.board_summary,
            change_impact_summary=latest.change_impact_summary,
            started_at=latest.started_at,
            updated_at=now_iso(),
            closed_at="",
            outcome="",
            metadata={**latest.metadata, **(metadata or {})},
        )
        self.append(updated)
        return updated

    def close_run(self, run_id: str, outcome: str, state: str = "closed") -> LedgerEntry | None:
        """run 종료를 기록한다. outcome: "success"|"partial"|"failed"|"rolled_back"."""
        from core.utils import now_iso
        latest = self._get_latest(run_id)
        if latest is None:
            return None
        closed = LedgerEntry(
            run_id=latest.run_id,
            issue_id=latest.issue_id,
            workspace=latest.workspace,
            pipeline=latest.pipeline,
            work_kind=latest.work_kind,
            execution_policy=latest.execution_policy,
            work_item_slug=latest.work_item_slug,
            state=state,
            board_summary=latest.board_summary,
            change_impact_summary=latest.change_impact_summary,
            started_at=latest.started_at,
            updated_at=now_iso(),
            closed_at=now_iso(),
            outcome=outcome,
            metadata=latest.metadata,
        )
        self.append(closed)
        return closed

    def get_active_runs(self) -> list[LedgerEntry]:
        """closed_at이 비어 있는 (미종료) 최신 항목 목록."""
        # run_id별로 최신 항목만 유지
        latest_map = self._build_latest_map()
        return [e for e in latest_map.values() if e.is_active]

    def conflict_check(self, affected_files: list[str]) -> list[LedgerEntry]:
        """
        진행 중인 다른 run이 같은 파일을 건드리는지 확인한다.
        충돌하는 run 목록을 반환한다. (G5 해결)
        """
        if not affected_files:
            return []
        affected_set = set(os.path.normpath(f) for f in affected_files)
        conflicting: list[LedgerEntry] = []
        for entry in self.get_active_runs():
            meta_files = entry.metadata.get("affected_files") or []
            entry_files = set(os.path.normpath(f) for f in meta_files)
            if affected_set & entry_files:
                conflicting.append(entry)
        return conflicting

    def get_history(self, limit: int = 50) -> list[LedgerEntry]:
        """최근 N개 항목을 반환한다 (최신 순)."""
        entries = self._read_all()
        return list(reversed(entries[-limit:]))

    # ── 내부 ──

    def _read_all(self) -> list[LedgerEntry]:
        if not os.path.isfile(self._ledger_path):
            return []
        entries: list[LedgerEntry] = []
        try:
            with open(self._ledger_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(LedgerEntry.from_dict(json.loads(line)))
                    except Exception:
                        continue
        except Exception:
            pass
        return entries

    def _build_latest_map(self) -> dict[str, LedgerEntry]:
        """run_id별로 마지막 항목만 남긴 dict."""
        result: dict[str, LedgerEntry] = {}
        for entry in self._read_all():
            result[entry.run_id] = entry
        return result

    def _get_latest(self, run_id: str) -> LedgerEntry | None:
        """특정 run_id의 가장 최근 항목."""
        last = None
        for entry in self._read_all():
            if entry.run_id == run_id:
                last = entry
        return last


__all__ = ["LedgerEntry", "RunLedger"]
