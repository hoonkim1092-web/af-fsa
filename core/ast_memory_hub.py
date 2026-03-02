import asyncio
import os
import json
from typing import Dict, List, Callable, Any

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
            # track file -> ast metadata
            self.ast_state: Dict[str, Any] = {}
            self.lock = asyncio.Lock()
            self.initialized = True

    def reset(self):
        """Reset the hub for a new project run."""
        self.global_context = {}
        self.subscribers = {}
        self.ast_state = {}

    def subscribe(self, topic: str, callback: Callable):
        """Subscribe an agent callback to a topic (e.g. 'file_changes', 'ast_updates')"""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        self.subscribers[topic].append(callback)

    async def publish(self, topic: str, payload: Any):
        """Broadcast an event to all subscribers of a topic."""
        callbacks = self.subscribers.get(topic, [])
        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(payload)
                else:
                    cb(payload)
            except Exception as e:
                print(f"[AstMemoryHub] Broadcast error on topic '{topic}': {e}")

    async def update_ast_state(self, filepath: str, author_role: str, changes_summary: str, parsed_ast_data: Any = None):
        """
        Called when an agent successfully modifies a file.
        Updates the global AST state and broadcasts the change.
        """
        async with self.lock:
            # Emulate extracting AST or saving AST meta
            self.ast_state[filepath] = {
                "last_modified_by": author_role,
                "summary": changes_summary,
                "ast_tree": parsed_ast_data or "AST_TREE_MOCK",
                "timestamp": os.path.getmtime(filepath) if os.path.exists(filepath) else 0
            }
            
            # Update global human-readable context for Lilith
            self.global_context[filepath] = f"[{author_role}] {changes_summary}"
        
        # Broadcast to anyone listening (e.g. Lilith or validating agents)
        event = {
            "type": "AST_UPDATE",
            "file": filepath,
            "author": author_role,
            "summary": changes_summary
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
