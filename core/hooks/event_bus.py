import os
import re
from typing import Any, List

class HookEventBus:
    """
    Manages execution of multiple hooks based on their Priority.
    PRIORITY 0-30: Guard Rails (Hard Blockers, Mutators)
    PRIORITY 31-70: Logical Gates (Intent checks, Planners)
    PRIORITY 71-100: Context Injectors, Setup
    """
    def __init__(self):
        self._pre_hooks: List[Any] = []
        self._post_hooks: List[Any] = []
        
    def register(self, hook: Any):
        if hasattr(hook, 'pre_execute'):
            self._pre_hooks.append(hook)
        if hasattr(hook, 'post_execute'):
            self._post_hooks.append(hook)
            
        # Sort by priority (lower number = runs first)
        self._pre_hooks.sort(key=lambda x: getattr(x, 'PRIORITY', 50))
        self._post_hooks.sort(key=lambda x: getattr(x, 'PRIORITY', 50))

    def run_pre_execute(self, agent_state: dict) -> bool:
        """Runs pre-hooks. If any hook returns False, execution blocks."""
        for hook in self._pre_hooks:
            name = hook.__class__.__name__
            if not hook.pre_execute(agent_state):
                print(f"[HookEventBus] Execution strictly BLOCKED by {name}")
                return False
        return True
        
    def run_post_execute(self, agent_state: dict, result: Any) -> Any:
        # Mutate result through the chain
        cur_res = result
        for hook in self._post_hooks:
            cur_res = hook.post_execute(agent_state, cur_res)
        return cur_res

# ==========================================
# Hook Implementations
# ==========================================

class ContinuationHook:
    PRIORITY = 50
    def pre_execute(self, agent_state: dict) -> bool:
        return True
    def post_execute(self, agent_state: dict, result: Any) -> Any:
        return result

class IntentGateHook(ContinuationHook):
    PRIORITY = 40  # Logic gate
    
    def pre_execute(self, agent_state: dict) -> bool:
        task_input = str(agent_state.get("task_input", "")).strip()
        
        # Don't block highly structured template inputs
        if "Goal:\n" in task_input and "Constraints:\n" in task_input:
            return True
            
        vague_keywords = ["대충", "알아서", "적당히", "아마", "그냥", "일단"]
        is_vague = any(kw in task_input for kw in vague_keywords) or len(task_input) < 10
        
        if is_vague:
            print("\n🛑 [IntentGate HOOK] ERROR: Task intent is too vague/ambitious.")
            print("💡 Please clarify your intent or use the Template Input.\n")
            return False
        return True

class TodoContinuationEnforcer(ContinuationHook):
    PRIORITY = 45 # Logic gate
    
    def pre_execute(self, agent_state: dict) -> bool:
        task_input = str(agent_state.get("task_input", ""))
        intent = agent_state.get("intent", "trivial")
        risk_level = agent_state.get("risk_level", "normal")
        workspace = agent_state.get("workspace", os.getcwd())
        
        is_complex = len(task_input) > 30 or any(k in task_input.lower() for k in ["refactor", "build", "create", "implement"])
        requires_plan = intent in ["refactoring", "greenfield", "complex_feature"] or risk_level in ["elevated", "strict"] or is_complex
        
        if requires_plan:
            has_todo_file = os.path.exists(os.path.join(workspace, ".todo.md"))
            has_board = os.path.exists(os.path.join(workspace, "project_board_state.json"))
            if not (has_todo_file or has_board):
                print("\n🚫 [Todo Enforcer] ERROR: Complex tasks require a structured plan (.todo.md).")
                print("💡 Check Agent Factory planning templates.\n")
                return False
        return True

class ToolOutputTruncator(ContinuationHook):
    PRIORITY = 10 # Guard rail, runs very early POST
    MAX_TRUNC_LENGTH = 16000 # ~4K tokens loosely
    
    def post_execute(self, agent_state: dict, result: Any) -> Any:
        if isinstance(result, dict) and "stdout" in result:
            o_len = len(str(result["stdout"]))
            if o_len > self.MAX_TRUNC_LENGTH:
                print(f"⚠️ [ToolOutputTruncator] Tool output too long ({o_len} chars). Truncating to {self.MAX_TRUNC_LENGTH}...")
                trunc_msg = f"\n[...Truncated due to extreme length...]"
                result["stdout"] = str(result["stdout"])[:self.MAX_TRUNC_LENGTH] + trunc_msg
        return result
