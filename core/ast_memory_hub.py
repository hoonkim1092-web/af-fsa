import asyncio
import inspect
import logging
import os
import time
from typing import Dict, List, Callable, Any

logger = logging.getLogger(__name__)

# Maximum history entries kept per file before oldest are pruned.
_MAX_HISTORY_PER_FILE = 50


class AstMemoryHub:
    """
    Agent Factory V3: Contextual Memory Hub
    A Centralized Pub/Sub memory store that tracks AST state across the project.
    Instead of isolated contexts, agents subscribe to the global AST changes.
    When one agent modifies a file or structure, the Hub broadcasts the delta
    so other concurrent agents are aware of the new reality.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(AstMemoryHub, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "initialized"):
            self.global_context: Dict[str, Any] = {}
            self.subscribers: Dict[str, List[Callable]] = {}
            # track file -> ast metadata (latest snapshot)
            self.ast_state: Dict[str, Any] = {}
            # track file -> change history (timeline)
            self.ast_history: Dict[str, List[Dict[str, Any]]] = {}
            self.lock = asyncio.Lock()
            self.initialized = True

    def reset(self):
        """Reset the hub for a new project run."""
        self.global_context = {}
        self.subscribers = {}
        self.ast_state = {}
        self.ast_history = {}

    def subscribe(self, topic: str, callback: Callable):
        """Subscribe an agent callback to a topic (e.g. 'file_changes', 'ast_updates')"""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        self.subscribers[topic].append(callback)

    async def publish(self, topic: str, payload: Any):
        """Broadcast an event to all subscribers of a topic (parallel)."""
        callbacks = self.subscribers.get(topic, [])
        if not callbacks:
            return

        async def _invoke(cb: Callable) -> None:
            try:
                if inspect.iscoroutinefunction(cb):
                    await asyncio.wait_for(cb(payload), timeout=10.0)
                else:
                    cb(payload)
            except asyncio.TimeoutError:
                logger.error("[AstMemoryHub] Callback timed out on topic '%s'", topic)
            except Exception as e:
                logger.error("[AstMemoryHub] Broadcast error on topic '%s': %s", topic, e)

        await asyncio.gather(*[_invoke(cb) for cb in callbacks])

    async def update_ast_state(self, filepath: str, author_role: str, changes_summary: str, parsed_ast_data: Any = None):
        """
        Called when an agent successfully modifies a file.
        Updates the global AST state, appends to history, and broadcasts.
        """
        ts = os.path.getmtime(filepath) if os.path.exists(filepath) else 0

        async with self.lock:
            snapshot = {
                "last_modified_by": author_role,
                "summary": changes_summary,
                "ast_tree": parsed_ast_data or "AST_TREE_MOCK",
                "timestamp": ts,
            }

            # Latest snapshot (overwrite — used for fast lookups)
            self.ast_state[filepath] = snapshot

            # History timeline (append — used for causal tracing)
            history_entry = {
                "author": author_role,
                "summary": changes_summary,
                "timestamp": ts,
                "wall_time": time.time(),
            }
            file_history = self.ast_history.setdefault(filepath, [])
            file_history.append(history_entry)
            # Prune oldest entries when history grows too large
            if len(file_history) > _MAX_HISTORY_PER_FILE:
                self.ast_history[filepath] = file_history[-_MAX_HISTORY_PER_FILE:]

            # Human-readable context (latest)
            self.global_context[filepath] = f"[{author_role}] {changes_summary}"

        # Broadcast to anyone listening (e.g. Lilith or validating agents)
        event = {
            "type": "AST_UPDATE",
            "file": filepath,
            "author": author_role,
            "summary": changes_summary,
        }
        await self.publish("ast_updates", event)
        await self.publish("global_context_changed", self.get_summary())

    def get_summary(self) -> str:
        """Returns a string representation of the global knowledge for LLM prompts."""
        if not self.global_context:
            return "No files modified yet."

        lines = []
        for fp, desc in self.global_context.items():
            lines.append(f"- {fp}: {desc}")
        return "\n".join(lines)

    def get_file_ast(self, filepath: str) -> Any:
        return self.ast_state.get(filepath, {})

    def get_file_history(self, filepath: str) -> List[Dict[str, Any]]:
        """Return the change history timeline for a file."""
        return list(self.ast_history.get(filepath, []))
