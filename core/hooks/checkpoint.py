"""
Checkpoint persistence hook.

Saves and restores agent_state to/from disk between execution cycles,
enabling crash recovery and resumable workflows.
"""
from __future__ import annotations

import json
import os
from typing import Any

from core.hooks.base import ContinuationHook


class CheckpointHook(ContinuationHook):
    """
    Saves agent_state to runs/{run_id}/checkpoint.json after each execution.
    Restores from checkpoint if one exists on pre_execute.
    """

    PRIORITY = 90  # Run late (after most hooks)

    def __init__(self, runs_dir: str | None = None):
        self._runs_dir = runs_dir

    def _checkpoint_path(self, agent_state: dict) -> str | None:
        run_id = agent_state.get("run_id")
        if not run_id:
            return None
        base = self._runs_dir or agent_state.get("runs_dir", "runs")
        return os.path.join(base, run_id, "checkpoint.json")

    def pre_execute(self, agent_state: dict) -> bool:
        path = self._checkpoint_path(agent_state)
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # Restore serializable state without overwriting runtime objects
                for key in ("cycle", "current_task", "eval_history", "metadata"):
                    if key in saved:
                        agent_state[key] = saved[key]
                print(f"♻️ [Checkpoint] Restored state from {path}")
            except Exception as e:
                print(f"⚠️ [Checkpoint] Failed to restore: {e}")
        return True

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        path = self._checkpoint_path(agent_state)
        if not path:
            return result

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Save only serializable parts of agent_state
            serializable = {}
            for key, val in agent_state.items():
                if isinstance(val, (str, int, float, bool, list, dict, type(None))):
                    serializable[key] = val
            serializable["_last_result_ok"] = result.get("ok", False) if isinstance(result, dict) else bool(result)

            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception as e:
            print(f"⚠️ [Checkpoint] Failed to save: {e}")
            # 실패 시 tmp 파일 정리
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except OSError:
                pass

        return result
