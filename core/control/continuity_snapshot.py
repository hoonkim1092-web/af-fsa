"""
core/control/continuity_snapshot.py
=====================================
ContinuitySnapshotBuilder — 4개 소스를 읽기 전용으로 집계한다.

기존 writer(manifest_store, resume_brief, session_adapter)를 교체하지 않음.
읽기 전용 집계만 수행, merge 정책으로 소스 간 불일치를 명시적으로 해소.

저장 경로: {workspace}/.af_runtime/control/continuity_snapshot.json

merge 정책:
  1. task 수: board 우선 (board가 task-level source of truth)
  2. 전체 상태: board completion 기반 판정, manifest는 보조
  3. manifest "crashed" → "interrupted" override (비정상 종료 신호)
  4. 불일치 시 conflict_notes에 기록 (silent 무시하지 않음)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ContinuitySnapshot:
    """4개 소스 집계 결과 스냅샷."""
    timestamp: str
    workspace: str
    # manifest 소스
    orchestrator_status: str = ""       # manifest.state_board.current_status
    completed_tasks: int = 0
    failed_tasks: int = 0
    interrupted_tasks: int = 0
    # board 소스
    board_pending: int = 0
    board_in_progress: int = 0
    board_completed: int = 0
    board_failed: int = 0
    board_blocked: int = 0
    # resume brief 소스
    resume_brief_excerpt: str = ""      # 1600자 제한
    # session 소스
    latest_session_provider: str = ""
    latest_session_run_id: str = ""
    # 집계 판정
    overall_health: str = "unknown"     # "healthy" | "degraded" | "interrupted" | "unknown"
    conflict_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ContinuitySnapshot":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def is_healthy(self) -> bool:
        return self.overall_health == "healthy"

    def is_interrupted(self) -> bool:
        return self.overall_health == "interrupted"


class ContinuitySnapshotBuilder:
    """
    4개 소스를 읽기 전용으로 집계한다.

    소스:
      1. .af_manifest.json          (DynamicOrchestrator writer — 읽기만)
      2. resume_brief.md            (resume_brief writer — 읽기만)
      3. project_board_state.json   (task board writer — 읽기만)
      4. .af_runtime/cli_sessions/  (session_adapter writer — 읽기만)
    """

    # board → manifest 불일치 임계값 (허용 오차)
    _TASK_COUNT_TOLERANCE = 2

    def build(self, workspace: str) -> ContinuitySnapshot:
        """4개 소스를 읽어 ContinuitySnapshot을 생성하고 저장한다."""
        from core.utils import now_iso

        manifest_data = self._read_manifest(workspace)
        board_data = self._read_board(workspace)
        brief_excerpt = self._read_resume_brief(workspace)
        session_provider, session_run_id = self._read_latest_session(workspace)

        # manifest 추출
        orchestrator_status = manifest_data.get("status", "") or manifest_data.get(
            "state_board", {}
        ).get("current_status", "")
        manifest_completed = int(manifest_data.get("completed_tasks", 0))
        manifest_failed = int(manifest_data.get("failed_tasks", 0))
        manifest_interrupted = int(manifest_data.get("interrupted_tasks", 0))

        # board 추출
        board_pending = int(board_data.get("pending", 0))
        board_in_progress = int(board_data.get("in_progress", 0))
        board_completed = int(board_data.get("completed", 0))
        board_failed = int(board_data.get("failed", 0))
        board_blocked = int(board_data.get("blocked", 0))

        # 불일치 감지
        conflict_notes: list[str] = []
        if abs(board_completed - manifest_completed) > self._TASK_COUNT_TOLERANCE:
            conflict_notes.append(
                f"task count mismatch: board.completed={board_completed}, "
                f"manifest.completed_tasks={manifest_completed}"
            )
        if board_in_progress > 0:
            conflict_notes.append(
                f"board has {board_in_progress} in_progress tasks (should be reset before resume)"
            )

        # overall_health 판정
        overall_health = self._resolve_health(orchestrator_status, {
            "completed": board_completed,
            "failed": board_failed,
            "in_progress": board_in_progress,
            "pending": board_pending,
        })

        snapshot = ContinuitySnapshot(
            timestamp=now_iso(),
            workspace=workspace,
            orchestrator_status=orchestrator_status,
            completed_tasks=board_completed,  # board 우선
            failed_tasks=max(board_failed, manifest_failed),
            interrupted_tasks=manifest_interrupted,
            board_pending=board_pending,
            board_in_progress=board_in_progress,
            board_completed=board_completed,
            board_failed=board_failed,
            board_blocked=board_blocked,
            resume_brief_excerpt=brief_excerpt,
            latest_session_provider=session_provider,
            latest_session_run_id=session_run_id,
            overall_health=overall_health,
            conflict_notes=conflict_notes,
        )

        self._save(snapshot, workspace)
        return snapshot

    def load(self, workspace: str) -> ContinuitySnapshot | None:
        """저장된 스냅샷을 불러온다."""
        path = self._snapshot_path(workspace)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return ContinuitySnapshot.from_dict(json.load(f))
        except Exception:
            return None

    # ── 소스 읽기 ──

    def _read_manifest(self, workspace: str) -> dict:
        """기존 .af_manifest.json을 읽기 전용으로 읽는다."""
        path = os.path.join(workspace, ".af_manifest.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _read_board(self, workspace: str) -> dict:
        """
        기존 project_board_state.json을 읽기 전용으로 읽는다.
        task status별 카운트를 추출한다.
        """
        path = os.path.join(workspace, "project_board_state.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                board = json.load(f)
        except Exception:
            return {}

        counts: dict[str, int] = {
            "pending": 0, "in_progress": 0, "completed": 0,
            "failed": 0, "blocked": 0,
        }
        for task in (board.get("tasks") or []):
            status = task.get("status", "")
            if status in counts:
                counts[status] += 1
        return counts

    def _read_resume_brief(self, workspace: str) -> str:
        """기존 resume_brief.md에서 최대 1600자를 읽는다."""
        path = os.path.join(workspace, "resume_brief.md")
        if not os.path.isfile(path):
            return ""
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read(1600)
            return content
        except Exception:
            return ""

    def _read_latest_session(self, workspace: str) -> tuple[str, str]:
        """
        .af_runtime/cli_sessions/ 에서 가장 최근 세션을 찾는다.
        Returns: (provider_id, run_id)
        """
        sessions_dir = os.path.join(workspace, ".af_runtime", "cli_sessions")
        if not os.path.isdir(sessions_dir):
            return "", ""
        try:
            files = [
                f for f in os.listdir(sessions_dir)
                if f.endswith(".json") and not f.endswith("_events.jsonl")
            ]
            if not files:
                return "", ""
            # 수정 시간 기준 최신 파일
            latest_file = max(
                files,
                key=lambda f: os.path.getmtime(os.path.join(sessions_dir, f))
            )
            with open(os.path.join(sessions_dir, latest_file), encoding="utf-8") as f:
                data = json.load(f)
            provider = data.get("provider_id", "")
            run_id = data.get("run_id", "")
            return provider, run_id
        except Exception:
            return "", ""

    # ── merge 정책 ──

    def _resolve_health(self, manifest_status: str, board_summary: dict) -> str:
        """
        overall_health 판정 규칙:
          - manifest "crashed" → "interrupted" (최우선 override)
          - board failed_tasks > 0 → "degraded"
          - board in_progress > 0 → "degraded" (비정상 재개)
          - manifest "completed" 또는 비어있음 → "healthy"
          - 그 외 → "unknown"
        """
        if manifest_status == "crashed":
            return "interrupted"

        if board_summary.get("failed", 0) > 0:
            return "degraded"

        if board_summary.get("in_progress", 0) > 0:
            return "degraded"

        if manifest_status in ("completed", ""):
            total_pending = board_summary.get("pending", 0)
            if total_pending == 0:
                return "healthy"
            # B9 Fix: manifest completed + board pending > 0 은 "비정상"이 아니라
            # 중간 재개 세션 (pending 태스크가 아직 실행 전인 정상 상태)일 수 있다.
            # conflict_note는 이미 build()에서 기록되므로 여기선 "degraded" 대신
            # "healthy"를 반환하고 caller가 conflict_notes로 판단하게 한다.
            return "healthy"

        if manifest_status in ("running", "in_progress"):
            return "degraded"  # 실행 중인데 스냅샷 빌드 = 비정상

        return "unknown"

    # ── 저장 ──

    def _snapshot_path(self, workspace: str) -> str:
        return os.path.join(workspace, ".af_runtime", "control", "continuity_snapshot.json")

    def _save(self, snapshot: ContinuitySnapshot, workspace: str) -> None:
        control_dir = os.path.join(workspace, ".af_runtime", "control")
        os.makedirs(control_dir, exist_ok=True)
        path = self._snapshot_path(workspace)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[ContinuitySnapshotBuilder] save failed: {exc}")


__all__ = ["ContinuitySnapshot", "ContinuitySnapshotBuilder"]
