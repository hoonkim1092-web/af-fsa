from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrchestratorManifestStore:
    FILENAME = ".af_manifest.json"

    def __init__(self, workspace: str | Path, filename: str | None = None, min_write_interval_sec: float = 0.25):
        self.workspace = Path(workspace).resolve()
        self.path = self.workspace / (filename or self.FILENAME)
        self.min_write_interval_sec = max(0.0, float(min_write_interval_sec))
        self._last_write_monotonic = 0.0
        self._last_payload: dict[str, Any] | None = None

    def save_snapshot(
        self,
        state_board: dict[str, Any],
        active_assignments: dict[str, dict[str, Any]] | None = None,
        roles: list[str] | None = None,
        project_desc: str = "",
        force: bool = False,
    ) -> bool:
        payload = {
            "version": 1,
            "updated_at": _now_iso(),
            "workspace": str(self.workspace),
            "project_desc": str(project_desc or ""),
            "roles": list(roles or []),
            "state_board": self._normalized_state_board(state_board),
            "active_assignments": self._normalized_active_assignments(active_assignments or {}),
        }
        if not force and self._should_skip_write(payload):
            return False

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

        self._last_payload = payload
        self._last_write_monotonic = time.monotonic()
        return True

    def load_raw(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def load_resume_state(self) -> dict[str, Any]:
        raw = self.load_raw()
        if not raw:
            return self._normalized_state_board({})

        board = self._normalized_state_board(raw.get("state_board", {}))
        interrupted = list(board.get("interrupted_subtasks", []))
        for item in self._normalized_active_assignments(raw.get("active_assignments", {})).values():
            candidate = {k: v for k, v in item.items() if k in ("role", "subtask", "workspace")}
            if candidate and candidate not in interrupted:
                interrupted.append(candidate)

        board["interrupted_subtasks"] = interrupted
        board["agents_status"] = {role: "idle" for role in board.get("agents_status", {})}
        return board

    def _should_skip_write(self, payload: dict[str, Any]) -> bool:
        if self._last_payload is not None:
            previous = dict(self._last_payload)
            current = dict(payload)
            previous.pop("updated_at", None)
            current.pop("updated_at", None)
            if previous == current:
                if (time.monotonic() - self._last_write_monotonic) < self.min_write_interval_sec:
                    return True
        return False

    @staticmethod
    def _normalized_state_board(state_board: dict[str, Any]) -> dict[str, Any]:
        return {
            "completed_subtasks": list(state_board.get("completed_subtasks", [])),
            "failed_subtasks": list(state_board.get("failed_subtasks", [])),
            "interrupted_subtasks": list(state_board.get("interrupted_subtasks", [])),
            "agents_status": dict(state_board.get("agents_status", {})),
            "current_status": str(state_board.get("current_status", "")),
        }

    @staticmethod
    def _normalized_active_assignments(active_assignments: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for task_id, meta in (active_assignments or {}).items():
            normalized[str(task_id)] = {
                "role": str(meta.get("role", "")),
                "subtask": str(meta.get("subtask", "")),
                "workspace": str(meta.get("workspace", "")),
            }
        return normalized
