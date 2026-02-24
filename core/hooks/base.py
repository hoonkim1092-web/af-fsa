from typing import Any

class ContinuationHook:
    """
    Base class for OmO's robust hook pattern.
    Before/After executing an LLM step or tool call,
    these hooks can block, inject context, or setup local servers (like MCP).
    """
    def pre_execute(self, agent_state: dict) -> bool:
        """Return False to hard-block execution."""
        return True
        
    def post_execute(self, agent_state: dict, result: Any) -> Any:
        """Can mutate the result or cleanup resources (e.g. MCP unmount)."""
        return result

import os

class TodoContinuationEnforcer(ContinuationHook):
    """
    (P1) Hook to block execution if no `.todo.md` or 
    Project Board plan has been approved by the user for complex tasks.
    """
    def pre_execute(self, agent_state: dict) -> bool:
        intent = agent_state.get("intent", "trivial")
        workspace = agent_state.get("workspace", os.getcwd())
        
        if intent in ["refactoring", "greenfield"]:
            has_todo_file = os.path.exists(os.path.join(workspace, ".todo.md"))
            has_board = os.path.exists(os.path.join(workspace, "project_board_state.json"))
            
            if not (has_todo_file or has_board):
                print("\n[Todo Enforcer HOOK] ERROR: No structured plan (.todo.md or Project Board) found.")
                print("[Todo Enforcer HOOK] Complex tasks require a plan before LLM execution.")
                print("Routing back to Planning Mode...\n")
                return False
        return True
