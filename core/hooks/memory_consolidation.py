"""
MemoryConsolidationHook — 실행 에피소드 자동 캡처 + 저장.

Phase 12: post_execute()에서 현재 run의 JSONL 파싱 → EpisodeRecord 생성
→ UnifiedMemoryFacade.record_episode()로 저장.

PRIORITY=95: CheckpointHook(90) 이후 실행.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MemoryConsolidationHook:
    """Captures execution episodes and consolidates them into unified memory."""

    PRIORITY = 95

    def __init__(self) -> None:
        self._facade: Any = None

    def set_facade(self, facade: Any) -> None:
        """Inject UnifiedMemoryFacade after construction."""
        self._facade = facade

    def pre_execute(self, agent_state: dict) -> bool:
        """No-op — always allow."""
        return True

    def post_execute(self, agent_state: dict, result: Any) -> Any:
        """Parse JSONL trace and record episode."""
        if not self._facade:
            return result

        try:
            run_id = agent_state.get("run_id", "")
            workspace = agent_state.get("workspace", ".")
            logs_dir = Path(workspace) / ".system_generated" / "logs"
            jsonl_path = logs_dir / f"trace_{run_id}.jsonl"

            if not jsonl_path.exists():
                return result

            from core.memory_system.episode_extractor import extract_episode_from_jsonl
            episode = extract_episode_from_jsonl(jsonl_path)
            if not episode:
                return result

            # Set project_id from agent_state if not set
            if not episode.project_id:
                episode.project_id = agent_state.get("project_id", "")

            # Link causal episodes (FSALoop retry success)
            previous_episode_id = agent_state.get("previous_episode_id")
            if previous_episode_id and episode.outcome == "success":
                episode.causal_links.append(previous_episode_id)

            # Run episode recording safely — handle both async and sync contexts
            self._run_record(episode)

            # Store episode_id in result for FSALoop linking
            if isinstance(result, dict):
                result["_episode_id"] = episode.episode_id

            logger.info(
                "MemoryConsolidation: episode %s (%s) recorded",
                episode.episode_id[:12],
                episode.outcome,
            )
        except Exception as exc:
            logger.error("MemoryConsolidationHook failed: %s", exc)

        return result

    def _run_record(self, episode: Any) -> None:
        """Run record_episode in the most appropriate async context."""
        coro = self._facade.record_episode(episode)
        try:
            loop = asyncio.get_running_loop()
            # Already in async context — schedule with error callback
            task = loop.create_task(coro)
            task.add_done_callback(self._on_record_done)
        except RuntimeError:
            # No running loop — create one to run the coroutine
            try:
                asyncio.run(coro)
            except Exception as exc:
                logger.error("MemoryConsolidation record failed: %s", exc)

    @staticmethod
    def _on_record_done(task: asyncio.Task) -> None:
        """Callback for async task — log errors instead of swallowing."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("MemoryConsolidation async record failed: %s", exc)

    def pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict) -> Any:
        return None  # No intervention

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        return result
