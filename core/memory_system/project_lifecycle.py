"""
ProjectLifecycleManager — 프로젝트 상태 머신.

Phase 15: CREATED → ACTIVE → MAINTAINING → ARCHIVED 전이.

상태 머신:
  CREATED ──→ ACTIVE ──→ MAINTAINING ──→ ARCHIVED
                 │            │
                 └──→ PAUSED ←┘
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProjectState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    MAINTAINING = "maintaining"
    ARCHIVED = "archived"


# Valid transitions
_TRANSITIONS: dict[ProjectState, set[ProjectState]] = {
    ProjectState.CREATED: {ProjectState.ACTIVE},
    ProjectState.ACTIVE: {ProjectState.MAINTAINING, ProjectState.PAUSED},
    ProjectState.PAUSED: {ProjectState.ACTIVE, ProjectState.MAINTAINING, ProjectState.ARCHIVED},
    ProjectState.MAINTAINING: {ProjectState.PAUSED, ProjectState.ARCHIVED, ProjectState.ACTIVE},
    ProjectState.ARCHIVED: {ProjectState.ACTIVE},  # Unarchive
}

_STATE_FILE = ".system_generated/project_lifecycle.json"


class ProjectLifecycleManager:
    """Manages project lifecycle state machine."""

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = Path(workspace)
        self._state_file = self._workspace / _STATE_FILE
        self._state = ProjectState.CREATED
        self._history: list[dict[str, Any]] = []
        self._metadata: dict[str, Any] = {}
        self._load()

    @property
    def state(self) -> ProjectState:
        return self._state

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def can_transition(self, target: ProjectState) -> bool:
        allowed = _TRANSITIONS.get(self._state, set())
        return target in allowed

    def transition(self, target: ProjectState, reason: str = "") -> bool:
        """
        Attempt state transition. Returns True on success.
        """
        if not self.can_transition(target):
            logger.warning(
                "Invalid transition %s → %s (allowed: %s)",
                self._state.value, target.value,
                [s.value for s in _TRANSITIONS.get(self._state, set())],
            )
            return False

        old = self._state
        self._state = target
        entry = {
            "from": old.value,
            "to": target.value,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._history.append(entry)
        self._save()
        logger.info("Project state: %s → %s (%s)", old.value, target.value, reason)
        return True

    def is_maintaining(self) -> bool:
        return self._state == ProjectState.MAINTAINING

    def is_active(self) -> bool:
        return self._state == ProjectState.ACTIVE

    def set_metadata(self, key: str, value: Any) -> None:
        self._metadata[key] = value
        self._save()

    def get_metadata(self, key: str, default: Any = None) -> Any:
        return self._metadata.get(key, default)

    def _load(self) -> None:
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._state = ProjectState(data.get("state", "created"))
            self._history = data.get("history", [])
            self._metadata = data.get("metadata", {})
        except Exception as exc:
            logger.error("ProjectLifecycle load error: %s", exc)

    def _save(self) -> None:
        """Atomic write: write to temp file, then os.replace()."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "state": self._state.value,
                "history": self._history,
                "metadata": self._metadata,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            content = json.dumps(data, ensure_ascii=False, indent=2)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_file.parent), suffix=".tmp",
            )
            try:
                os.write(fd, content.encode("utf-8"))
                os.close(fd)
                os.replace(tmp_path, str(self._state_file))
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception as exc:
            logger.error("ProjectLifecycle save error: %s", exc)
