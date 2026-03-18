"""
MemoryConsolidationHook — 실행 에피소드 자동 캡처 + 지식 추출.

Phase 12: post_execute()에서 현재 run의 JSONL 파싱 → EpisodeRecord 생성
→ UnifiedMemoryFacade.record_episode()로 저장.

Stage 4-6 통합: 성공 에피소드가 causal_links를 가진 경우 (FSALoop 재시도 성공)
자동으로 EpisodeMatcher → extract_triple → KnowledgeForger 파이프라인 실행.

PRIORITY=95: CheckpointHook(90) 이후 실행.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MemoryConsolidationHook:
    """Captures execution episodes and consolidates them into unified memory.

    Pending tasks are tracked so that ``flush()`` can be called during
    shutdown to guarantee that every episode is persisted before the
    process exits.
    """

    PRIORITY = 95

    def __init__(self) -> None:
        self._facade: Any = None
        self._graph_adapter: Any = None
        self._pending_tasks: list[asyncio.Task] = []

    def set_facade(self, facade: Any) -> None:
        """Inject UnifiedMemoryFacade after construction."""
        self._facade = facade

    def set_graph_adapter(self, adapter: Any) -> None:
        """Inject KnowledgeGraphAdapter for Stage 4-6 pipeline."""
        self._graph_adapter = adapter

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

            # ── Stage 4-6: Knowledge Forging Pipeline ──────────────
            # Trigger when a success episode has causal_links (retry success)
            if (
                episode.outcome == "success"
                and episode.causal_links
                and self._graph_adapter
            ):
                self._run_forge_pipeline(episode)
        except Exception as exc:
            logger.error("MemoryConsolidationHook failed: %s", exc)

        return result

    # ── Stage 4-6: Knowledge Forging ─────────────────────────────────

    def _run_forge_pipeline(self, success_episode: Any) -> None:
        """Run EpisodeMatcher → KnowledgeForger in background."""
        coro = self._forge_knowledge(success_episode)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            task.add_done_callback(self._on_record_done)
            self._pending_tasks.append(task)
        except RuntimeError:
            try:
                asyncio.run(coro)
            except Exception as exc:
                logger.error("KnowledgeForge sync failed: %s", exc)

    async def _forge_knowledge(self, success_episode: Any) -> None:
        """Full Stage 4→5→6 pipeline."""
        try:
            from core.memory_system.episode_matcher import EpisodeMatcher
            from core.memory_system.knowledge_forger import KnowledgeForger

            # Stage 4: Find failure→success pairs
            matcher = EpisodeMatcher(self._facade)
            pairs = await matcher.find_pairs(success_episode)
            if not pairs:
                logger.debug("KnowledgeForge: no matching failure episodes found")
                return

            # Stage 5+6: Extract triples and forge knowledge
            forger = KnowledgeForger(self._graph_adapter)
            for failure_ep, success_ep in pairs:
                result = await forger.forge(failure_ep, success_ep)
                if result:
                    logger.info(
                        "KnowledgeForge: created triple P(%s)→S(%s) insight='%s'",
                        result["problem_id"][:8],
                        result["solution_id"][:8],
                        result.get("insight", "")[:80],
                    )
        except Exception as exc:
            logger.error("KnowledgeForge pipeline failed: %s", exc)

    # ── Async bridge ───────────────────────────────────────────────────

    def _run_record(self, episode: Any) -> None:
        """Run record_episode in the most appropriate async context."""
        coro = self._facade.record_episode(episode)
        try:
            loop = asyncio.get_running_loop()
            # Already in async context — schedule and track the task
            task = loop.create_task(coro)
            task.add_done_callback(self._on_record_done)
            self._pending_tasks.append(task)
        except RuntimeError:
            # No running loop — create one to run the coroutine synchronously
            try:
                asyncio.run(coro)
            except Exception as exc:
                logger.error("MemoryConsolidation record failed: %s", exc)

    def _on_record_done(self, task: asyncio.Task) -> None:
        """Callback for async task — log errors and remove from pending."""
        # Clean up reference
        try:
            self._pending_tasks.remove(task)
        except ValueError:
            pass

        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("MemoryConsolidation async record failed: %s", exc)

    async def flush(self, timeout: float = 10.0) -> None:
        """Await all pending record tasks.

        Call this during shutdown to ensure that every episode captured
        in the async path is actually persisted before the process exits.
        """
        if not self._pending_tasks:
            return
        pending = list(self._pending_tasks)
        logger.info("MemoryConsolidation: flushing %d pending tasks …", len(pending))
        done, not_done = await asyncio.wait(pending, timeout=timeout)
        for t in not_done:
            t.cancel()
            logger.warning("MemoryConsolidation: task cancelled during flush")
        # Log any errors from completed tasks
        for t in done:
            if not t.cancelled() and t.exception():
                logger.error("MemoryConsolidation flush error: %s", t.exception())
        self._pending_tasks.clear()

    # ── Passthrough hooks ──────────────────────────────────────────────

    def pre_tool_call(self, agent_state: dict, tool_name: str, tool_args: dict) -> Any:
        return None  # No intervention

    def post_tool_call(self, agent_state: dict, tool_name: str, result: Any) -> Any:
        return result
