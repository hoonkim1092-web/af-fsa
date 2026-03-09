import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from core.agent_runner import AgentRunner
from core.ast_memory_hub import AstMemoryHub
from core.continuity.manifest_store import OrchestratorManifestStore
from core.continuity.runtime_paths import workspace_runtime_file
from core.evaluator import StrategyEvaluator
from core.llm_engine import LLMEngine
from core.manager import AgentManager
from core.utils import print_agent_msg, safe_json_load


class DynamicOrchestrator:
    """
    Agent Factory V3: dynamic multi-agent orchestrator driven by a central PM model.
    """

    def __init__(self, mr, max_concurrent: int = 5):
        self.mr = mr
        self.max_concurrent = max_concurrent
        self.agent_mgr = AgentManager(self.mr)
        self.runner = AgentRunner(self.mr)

        engine_id = self.mr.pick("orchestrator") if hasattr(self.mr, "pick") else "gemini-1.5-pro-latest"
        self.llm = LLMEngine(model_name=engine_id)

        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.active_task_meta: Dict[str, Dict[str, Any]] = {}
        self.state_board: Dict[str, Any] = {
            "completed_subtasks": [],
            "failed_subtasks": [],
            "interrupted_subtasks": [],
            "agents_status": {},
            "current_status": "",
        }
        self.memory_hub = AstMemoryHub()
        self.evaluator = StrategyEvaluator(model_name=engine_id)
        self.manifest_store: Optional[OrchestratorManifestStore] = None
        self._workspace: str = ""
        self._roles: List[str] = []
        self._project_desc: str = ""

    def _runtime_file(self, filename: str):
        workspace = self._workspace or os.getcwd()
        return workspace_runtime_file(workspace, filename)

    def _append_runtime_log(self, filename: str, message: str) -> None:
        path = self._runtime_file(filename)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(message)

    def _ensure_state_defaults(self) -> None:
        self.state_board.setdefault("completed_subtasks", [])
        self.state_board.setdefault("failed_subtasks", [])
        self.state_board.setdefault("interrupted_subtasks", [])
        self.state_board.setdefault("agents_status", {})
        self.state_board.setdefault("current_status", "")

    def _snapshot_state(self, force: bool = False) -> None:
        self._ensure_state_defaults()
        if not self.manifest_store:
            return
        self.manifest_store.save_snapshot(
            self.state_board,
            active_assignments=self.active_task_meta,
            roles=self._roles,
            project_desc=self._project_desc,
            force=force,
        )

    def _initialize_runtime_state(self, project_desc: str, roles: List[str], workspace: str | None = None) -> None:
        self._workspace = workspace or os.getcwd()
        self._roles = list(roles)
        self._project_desc = project_desc
        self.manifest_store = OrchestratorManifestStore(self._workspace)
        self.state_board = self.manifest_store.load_resume_state()
        self._ensure_state_defaults()
        for role in roles:
            self.state_board["agents_status"].setdefault(role, "idle")
        self.state_board["current_status"] = "running"
        self._snapshot_state(force=True)

    async def _lilith_decide_next(
        self,
        project_desc: str,
        roles: List[str],
        workspace: str | None = None,
    ) -> List[Dict[str, str]]:
        working_count = sum(1 for status in self.state_board["agents_status"].values() if status == "working")
        if working_count >= self.max_concurrent:
            return []

        available_roles = [role for role in roles if self.state_board["agents_status"].get(role, "idle") == "idle"]
        if not available_roles:
            return []

        todo_content = ""
        target_workspace = workspace or os.getcwd()
        todo_path = os.path.join(target_workspace, ".todo.md")
        if os.path.exists(todo_path):
            with open(todo_path, "r", encoding="utf-8") as handle:
                todo_content = handle.read()

        prompt = f"""
        You are Lilith, the Master Orchestrator of Agent Factory V3.
        Your goal is to complete this project: {project_desc}

        ## Tactical Plan (.todo.md):
        {todo_content}

        ## Current Board State:
        Completed works: {json.dumps(self.state_board['completed_subtasks'], ensure_ascii=False)}
        Failed works: {json.dumps(self.state_board['failed_subtasks'], ensure_ascii=False)}
        Interrupted works: {json.dumps(self.state_board['interrupted_subtasks'], ensure_ascii=False)}

        ## Global Context (Shared AST Memory):
        {self.memory_hub.get_summary()}

        ## Available Idle Agents:
        {json.dumps(available_roles, ensure_ascii=False)}

        ## Instruction:
        Based on the roadmap and current state, determine the next immediate sub-tasks
        that should be executed in parallel. If the project is complete, return an empty array.

        Return JSON ONLY:
        {{
            "next_tasks": [
                {{"assigned_role": "role_name", "subtask_instruction": "detailed instruction", "estimated_complexity": "LOW/HIGH"}}
            ]
        }}
        """

        try:
            response = await asyncio.to_thread(self.llm.generate_json, prompt)
            data = response if isinstance(response, dict) else safe_json_load(response)
            tasks = data.get("next_tasks", [])
            self._append_runtime_log(
                "dynamic_log.txt",
                (
                    f"\n[Lilith] Cycle: {getattr(self, '_current_cycle', '?')}\n"
                    f"Roles: {available_roles}\nTasks: {tasks}\nRaw: {json.dumps(data, ensure_ascii=False)}\n"
                ),
            )
            if not tasks:
                if not data:
                    print_agent_msg("Lilith", "LLM response empty. Retrying next cycle...", "")
                    return [{"assigned_role": "__placeholder__", "subtask_instruction": "retry"}]
                return []
            return [task for task in tasks if task.get("assigned_role") in available_roles]
        except Exception as exc:
            print_agent_msg("Lilith", f"Failed to dynamically generate next tasks: {exc}", "")
            return [{"assigned_role": "__placeholder__", "subtask_instruction": "error_retry"}]

    async def _execute_agent_task(self, role: str, subtask: str, run_id: str, workspace: str | None = None):
        print_agent_msg("System", f"Dispatching [{role}] -> {subtask[:50]}...", "")
        self.state_board["agents_status"][role] = "working"
        self._snapshot_state()

        try:
            target_workspace = workspace or os.getcwd()
            agent_data = self.agent_mgr.get_or_create(role, workspace=target_workspace)
            result = await asyncio.to_thread(
                self.runner.run,
                agent_data,
                subtask,
                run_id,
                True,
                target_workspace,
            )

            if result and result.get("ok"):
                self.state_board["completed_subtasks"].append(
                    {"role": role, "subtask": subtask, "result": "Success"}
                )
                self._snapshot_state()
                await self.memory_hub.update_ast_state(
                    filepath=f"Project_Scope_{role}",
                    author_role=role,
                    changes_summary=f"Completed subtask: {subtask[:50]}",
                )
                print_agent_msg(role, "Task completed.", "")
            else:
                reason = result.get("reason", "Unknown error") if result else "No result"
                print_agent_msg(role, f"Task failed: {reason[:100]}", "")
                eval_res = await asyncio.to_thread(
                    self.evaluator.evaluate_failure,
                    role=role,
                    instruction=subtask,
                    error_log=reason,
                )
                self.state_board["failed_subtasks"].append(
                    {
                        "role": role,
                        "subtask": subtask,
                        "reason": reason,
                        "evaluator_action": eval_res.get("action"),
                        "evaluator_advice": eval_res.get("new_instruction"),
                    }
                )
                self._snapshot_state()
                if eval_res.get("action") == "pivot":
                    print_agent_msg("Evaluator", f"Strategy pivot required for {role}.", "")
                else:
                    print_agent_msg("Evaluator", f"Suggesting retry for {role}.", "")
        except Exception as exc:
            self.state_board["failed_subtasks"].append(
                {"role": role, "subtask": subtask, "reason": str(exc)}
            )
            self._snapshot_state()
            print_agent_msg(role, f"Task crashed: {exc}", "")
        finally:
            self.state_board["agents_status"][role] = "idle"
            self.active_tasks.pop(run_id, None)
            self.active_task_meta.pop(run_id, None)
            self._snapshot_state()

    async def _orchestration_loop(self, project_desc: str, roles: List[str], workspace: str | None = None):
        target_workspace = workspace or os.getcwd()
        self._initialize_runtime_state(project_desc, roles, target_workspace)

        cycle = 0
        max_cycles = 15

        while cycle < max_cycles:
            cycle += 1
            self._current_cycle = cycle
            print_agent_msg("Lilith", f"--- Dynamic Sync Cycle {cycle} ---", "")

            new_tasks = await self._lilith_decide_next(project_desc, roles, target_workspace)
            if not new_tasks:
                active_workers = [
                    role for role, status in self.state_board["agents_status"].items() if status == "working"
                ]
                if not active_workers:
                    print_agent_msg("Lilith", "No more tasks to assign and no agents are working.", "")
                    break
                print_agent_msg("Lilith", f"Waiting for active agents: {', '.join(active_workers)}", "")
                await asyncio.sleep(5)
                continue

            for task in new_tasks:
                role = task.get("assigned_role")
                instruction = task.get("subtask_instruction", "")
                if role == "__placeholder__":
                    continue
                if role and instruction and role in roles and self.state_board["agents_status"].get(role) == "idle":
                    task_id = f"run_{int(time.time())}_{role}"
                    self.state_board["agents_status"][role] = "working"
                    self.active_task_meta[task_id] = {
                        "role": role,
                        "subtask": instruction,
                        "workspace": target_workspace,
                    }
                    self._snapshot_state()
                    task_obj = asyncio.create_task(self._execute_agent_task(role, instruction, task_id, target_workspace))
                    self.active_tasks[task_id] = task_obj

            await asyncio.sleep(2)

        if cycle >= max_cycles:
            self.state_board["current_status"] = "stopped_max_cycles"
            print_agent_msg("Lilith", "Maximum orchestration cycles reached.", "")
        else:
            self.state_board["current_status"] = "completed"

        if self.active_tasks:
            print_agent_msg("Lilith", "Waiting for remaining tasks to complete before exit...", "")
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)

        self._snapshot_state(force=True)

    def run_project(self, project_desc: str, roles: List[str], workspace: str | None = None) -> Dict[str, Any]:
        print_agent_msg("System", "Initializing Dynamic LLM-Driven Orchestrator (V3)", "")
        try:
            asyncio.run(self._orchestration_loop(project_desc, roles, workspace))
        except Exception as exc:
            self.state_board["current_status"] = "crashed"
            self._snapshot_state(force=True)
            import traceback

            with self._runtime_file("crash.log").open("w", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
            print_agent_msg("System", f"Orchestration loop crashed: {exc}", "")
        return self.state_board
