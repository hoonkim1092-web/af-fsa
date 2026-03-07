import os
import time
import json
import asyncio
from typing import List, Dict, Any, Optional

from core.llm_engine import LLMEngine
from core.utils import now_iso, print_agent_msg, safe_json_load
from core.agent_runner import AgentRunner
from core.manager import AgentManager
from core.ast_memory_hub import AstMemoryHub
from core.evaluator import StrategyEvaluator

class DynamicOrchestrator:
    """
    Agent Factory V3: Dynamic LLM-Driven Orchestrator (Sisyphus Competitor)
    Replaces static ThreadPool loops with an intelligent, dynamic task queue
    managed by a central PM AI (Lilith).
    """
    def __init__(self, mr, max_concurrent: int = 5):
        self.mr = mr
        self.max_concurrent = max_concurrent
        self.agent_mgr = AgentManager(self.mr)
        self.runner = AgentRunner(self.mr)
        
        # We use the 'pm' or 'orchestrator' model for Lilith.
        # Fallback to research_pro if specific orchestrator model isn't mapped.
        engine_id = self.mr.pick("orchestrator") if hasattr(self.mr, "pick") else "gemini-1.5-pro-latest"
        self.llm = LLMEngine(model_name=engine_id)
        
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.state_board: Dict[str, Any] = {
            "completed_subtasks": [],
            "failed_subtasks": [],
            "agents_status": {}
        }
        self.memory_hub = AstMemoryHub()
        self.evaluator = StrategyEvaluator(model_name=engine_id)
        
    async def _lilith_decide_next(self, project_desc: str, roles: List[str], workspace: str | None = None) -> List[Dict[str, str]]:
        """
        Lilith (Main AI) analyzes the current board state and actively determines the next 
        set of micro-tasks to spawn and assign to available agents.
        """
        working_count = sum(1 for s in self.state_board["agents_status"].values() if s == "working")
        if working_count >= self.max_concurrent:
            return [] # Reached max concurrency

        available_roles = [r for r in roles if self.state_board["agents_status"].get(r, "idle") == "idle"]
        
        if not available_roles:
            return [] # Everyone is busy
            
        # [Stability FIX] Inject the tactical plan (.todo.md) into context
        todo_content = ""
        target_workspace = workspace or os.getcwd()  # Keep arg/local names separated.
        todo_path = os.path.join(target_workspace, ".todo.md")
        if os.path.exists(todo_path):
            with open(todo_path, "r", encoding="utf-8") as f:
                todo_content = f.read()

        prompt = f"""
        You are Lilith, the Master Orchestrator (Sisyphus-class) of Agent Factory V3.
        Your goal is to complete this project: {project_desc}
        
        ## Tactical Plan (.todo.md):
        {todo_content}

        ## Current Board State:
        Completed works: {json.dumps(self.state_board['completed_subtasks'], ensure_ascii=False)}
        Failed works: {json.dumps(self.state_board['failed_subtasks'], ensure_ascii=False)}
        
        ## Global Context (Shared AST Memory):
        {self.memory_hub.get_summary()}
        
        ## Available Idle Agents:
        {json.dumps(available_roles, ensure_ascii=False)}
        
        ## INSTRUCTION:
        Based on the .todo.md roadmap and the current state, determine the NEXT immediate sub-tasks that should be executed in parallel.
        Assign them to the available idle agents. You do not have to assign work to everyone if not needed.
        If the project is completely finished and no more tasks are needed (all .todo.md items reached), return an empty array.
        
        Return JSON ONLY:
        {{
            "next_tasks": [
                {{"assigned_role": "role_name", "subtask_instruction": "detailed instruction", "estimated_complexity": "LOW/HIGH"}}
            ]
        }}
        """
        
        try:
            # We run the synchronous LLM call in a thread pool to avoid blocking the event loop
            response_text = await asyncio.to_thread(self.llm.generate_json, prompt)
            
            # generate_json usually returns a dict directly
            if isinstance(response_text, dict):
                data = response_text
            else:
                data = safe_json_load(response_text)
                
            tasks = data.get("next_tasks", [])
            with open("dynamic_log.txt", "a", encoding="utf-8") as f:
                f.write(f"\n[Lilith] Cycle: {getattr(self, '_current_cycle', '?')}\nRoles: {available_roles}\nTasks: {tasks}\nRaw: {json.dumps(data, ensure_ascii=False)}\n")
                
            if not tasks:
                print_agent_msg("Lilith", f"[Debug] Raw response returned empty tasks. Raw data: {response_text}", "🔍")
                # [Stability FIX] If data is null or tasks are truly empty despite available roles,
                # return a placeholder to keep the loop alive during API 503 spikes.
                if not data:
                    print_agent_msg("Lilith", "LLM response empty (possible API Overload). Retrying next cycle...", "⚠️")
                    return [{"assigned_role": "__placeholder__", "subtask_instruction": "retry"}]
                return []
            else:
                print_agent_msg("Lilith", f"[Debug] LLM returned tasks: {tasks}. Available roles: {available_roles}", "🔍")
                
            return [t for t in tasks if t.get("assigned_role") in available_roles]
        except Exception as e:
            print_agent_msg("Lilith", f"Failed to dynamically generate next tasks: {e}", "🚨")
            return [{"assigned_role": "__placeholder__", "subtask_instruction": "error_retry"}]

    async def _execute_agent_task(self, role: str, subtask: str, run_id: str, workspace: str | None = None):
        """Wrapper to execute a task via AgentRunner asynchronously."""
        print_agent_msg("System", f"Dispatching [{role}] -> {subtask[:50]}...", "")
        self.state_board["agents_status"][role] = "working"
        
        try:
            target_workspace = workspace or os.getcwd()
            agent_data = self.agent_mgr.get_or_create(role, workspace=target_workspace)
            
            # Execute the actual synchronous AgentRunner in a background thread
            result = await asyncio.to_thread(
                self.runner.run, 
                agent_data, 
                subtask, 
                run_id, 
                True,  # auto_approve = True for fully autonomous dynamic execution
                target_workspace
            )
            
            if result and result.get("ok"):
                self.state_board["completed_subtasks"].append({
                    "role": role,
                    "subtask": subtask,
                    "result": "Success"
                })
                # Phase 2: Update Shared AST Memory Hub
                # In a real AST-grep integration, we would parse the actual diffs here
                await self.memory_hub.update_ast_state(
                    filepath=f"Project_Scope_{role}",
                    author_role=role,
                    changes_summary=f"Completed subtask: {subtask[:50]}"
                )
                print_agent_msg(role, f"Task Completed!", "✅")
            else:
                reason = result.get("reason", "Unknown error") if result else "No result"
                print_agent_msg(role, f"Task Failed: {reason[:100]}", "❌")
                
                # Phase 3: Strategy Pivot & Evaluator
                eval_res = await asyncio.to_thread(
                    self.evaluator.evaluate_failure,
                    role=role,
                    instruction=subtask,
                    error_log=reason
                )
                
                self.state_board["failed_subtasks"].append({
                    "role": role,
                    "subtask": subtask,
                    "reason": reason,
                    "evaluator_action": eval_res.get("action"),
                    "evaluator_advice": eval_res.get("new_instruction")
                })
                
                if eval_res.get("action") == "pivot":
                    print_agent_msg("Evaluator", f"Strategy Pivot Required for {role}!", "🔄")
                else:
                    print_agent_msg("Evaluator", f"Suggesting Retry for {role}.", "🔁")
                
        except Exception as e:
            self.state_board["failed_subtasks"].append({
                "role": role,
                "subtask": subtask,
                "reason": str(e)
            })
            print_agent_msg(role, f"Task Crashed: {e}", "💥")
        finally:
            self.state_board["agents_status"][role] = "idle"
            if run_id in self.active_tasks:
                del self.active_tasks[run_id]

    async def _orchestration_loop(self, project_desc: str, roles: List[str], workspace: str | None = None):
        """The main dynamic event loop."""
        target_workspace = workspace or os.getcwd()
        for r in roles:
            self.state_board["agents_status"][r] = "idle"
            
        cycle = 0
        max_cycles = 15 # Safety limit to prevent infinite loops
        
        while cycle < max_cycles:
            cycle += 1
            self._current_cycle = cycle # Track cycle for logging
            print_agent_msg("Lilith", f"--- Dynamic Sync Cycle {cycle} ---", "👑")
            
            # 1. Ask Lilith for the next tickets
            new_tasks = await self._lilith_decide_next(project_desc, roles, target_workspace)
            
            if not new_tasks:
                # Check if anyone is still working
                active_workers = [r for r, s in self.state_board["agents_status"].items() if s == "working"]
                if not active_workers:
                    print_agent_msg("Lilith", "No more tasks to assign, and no agents are working. Project Complete.", "🏁")
                    break
                else:
                    print_agent_msg("Lilith", f"Waiting for active agents to finish... ({', '.join(active_workers)})", "⏳")
                    await asyncio.sleep(5)
                    continue

            # 2. Dispatch the new tasks
            dispatch_futures = []
            for t in new_tasks:
                role = t.get("assigned_role")
                instruction = t.get("subtask_instruction", "")
                
                # [Stability FIX] Handle __placeholder__ to keep cycle moving without erroring on role lookup
                if role == "__placeholder__":
                    continue
                
                if role and instruction and role in roles and self.state_board["agents_status"].get(role) == "idle":
                    task_id = f"run_{int(time.time())}_{role}"
                    
                    # Mark as busy right away to prevent double assignment in same cycle
                    self.state_board["agents_status"][role] = "working"
                    
                    # Create async task
                    coro = self._execute_agent_task(role, instruction, task_id, target_workspace)
                    task_obj = asyncio.create_task(coro)
                    self.active_tasks[task_id] = task_obj
                    dispatch_futures.append(task_obj)
            
            # We don't wait for them to finish here! We just fired them.
            # We sleep briefly, then loop again so Lilith can monitor and spawn MORE tasks if needed.
            await asyncio.sleep(2)
            
        if cycle >= max_cycles:
            self.state_board["current_status"] = "stopped_max_cycles"
            print_agent_msg("Lilith", "Maximum orchestration cycles reached. Forcing shutdown.", "🛑")
        else:
            self.state_board["current_status"] = "completed"
            
        if self.active_tasks:
            print_agent_msg("Lilith", "Waiting for remaining tasks to complete before exit...", "⏳")
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)

    def run_project(self, project_desc: str, roles: List[str], workspace: str | None = None) -> Dict[str, Any]:
        """
        Entry point for the dynamic orchestrator.
        """
        print_agent_msg("System", "🚀 Initializing Dynamic LLM-Driven Orchestrator (V3)", "⚡")
        
        # We use asyncio.run for the top-level async entry point.
        # This prevents issues with 'loop already running' or deprecated get_event_loop() behavior.
        try:
            asyncio.run(self._orchestration_loop(project_desc, roles, workspace))
        except Exception as e:
            import traceback
            with open("crash.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            print_agent_msg("System", f"Orchestration loop crashed: {e}", "🛑")
        
        return self.state_board
