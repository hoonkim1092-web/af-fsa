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
import re

class TodoContinuationEnforcer(ContinuationHook):
    """
    (P1) Hook to block execution if no `.todo.md` or 
    Project Board plan has been approved by the user for complex tasks.
    """
    def pre_execute(self, agent_state: dict) -> bool:
        # Require .todo.md or project_board_state.json for high-risk or complex tasks
        intent = agent_state.get("intent", "trivial")
        risk_level = agent_state.get("risk_level", "normal")
        workspace = agent_state.get("workspace", os.getcwd())
        
        requires_plan = intent in ["refactoring", "greenfield", "complex_feature"] or risk_level in ["elevated", "strict"]
        
        if requires_plan:
            has_todo_file = os.path.exists(os.path.join(workspace, ".todo.md"))
            has_board = os.path.exists(os.path.join(workspace, "project_board_state.json"))
            
            if not (has_todo_file or has_board):
                print("\n🚫 [Todo Enforcer HOOK] ERROR: No structured plan (.todo.md or Project Board) found.")
                print("🚫 [Todo Enforcer HOOK] Complex tasks (or elevated risk tasks) require a plan before LLM execution.")
                print("💡 [Action Required] Please create a `.todo.md` file with explicit steps, or switch to Planning Mode.\n")
                return False
        return True

class IntentGateHook(ContinuationHook):
    """
    Enforces the IntentGate pattern. If the task is too vague, it hard-blocks
    and forces the LLM to ask clarifying questions (A/B options) instead of guessing.
    """
    def pre_execute(self, agent_state: dict) -> bool:
        task_input = str(agent_state.get("task_input", "")).strip()
        vague_keywords = ["대충", "알아서", "적당히", "아마", "그냥", "일단"]
        
        is_vague = any(kw in task_input for kw in vague_keywords) or len(task_input) < 10
        
        if is_vague:
            print("\n🛑 [IntentGate HOOK] ERROR: Task intent is too vague or ambitious.")
            print("🛑 [IntentGate HOOK] User input must be concrete. The LLM might hallucinate without clear scope.")
            print("💡 [Action Required] Please clarify your intent or choose a concrete A/B scope option.\n")
            return False
            
        return True
