import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from core.agent_runner import AgentRunner
from core.ast_memory_hub import AstMemoryHub
from core.continuity import OrchestratorManifestStore, workspace_runtime_file
from core.evaluator import StrategyEvaluator
from core.llm_engine import LLMEngine
from core.manager import AgentManager
from core.utils import print_agent_msg, safe_id, safe_json_load


class DynamicOrchestrator:
    """
    Dynamic multi-agent orchestrator driven by a central PM model.
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
        self.active_assignments: Dict[str, Dict[str, str]] = {}
        self.state_board: Dict[str, Any] = {
            "completed_subtasks": [],
            "failed_subtasks": [],
            "interrupted_subtasks": [],
            "agents_status": {},
            "current_status": "",
        }
        self.memory_hub = AstMemoryHub()
        self.evaluator = StrategyEvaluator(model_name=engine_id)
        self._workspace: str | None = None
        self._manifest_store: OrchestratorManifestStore | None = None
        self._manifest_roles: List[str] = []
        self._manifest_project_desc = ""

    def _runtime_file(self, filename: str, workspace: str | None = None) -> Path:
        target_workspace = workspace or self._workspace
        if target_workspace:
            return workspace_runtime_file(target_workspace, filename)
        return Path(filename)

    def _sync_manifest(self, force: bool = False) -> None:
        if self._manifest_store is None:
            return
        self._manifest_store.save_snapshot(
            self.state_board,
            active_assignments=self.active_assignments,
            roles=self._manifest_roles,
            project_desc=self._manifest_project_desc,
            force=force,
        )

    def _prepare_resume_state(self, project_desc: str, roles: List[str], workspace: str | None) -> None:
        self._workspace = workspace or None
        self._manifest_roles = list(roles or [])
        self._manifest_project_desc = str(project_desc or "")
        self.active_assignments = {}

        if not self._workspace:
            return

        self._manifest_store = OrchestratorManifestStore(self._workspace)
        loaded = self._manifest_store.load_resume_state()
        self.state_board = {
            "completed_subtasks": list(loaded.get("completed_subtasks", [])),
            "failed_subtasks": list(loaded.get("failed_subtasks", [])),
            "interrupted_subtasks": list(loaded.get("interrupted_subtasks", [])),
            "agents_status": dict(loaded.get("agents_status", {})),
            "current_status": str(loaded.get("current_status", "")),
        }
        for role in roles:
            self.state_board["agents_status"].setdefault(role, "idle")
        self.state_board["current_status"] = "running"
        self._sync_manifest(force=True)

    def _open_todo_items(self, workspace: str) -> List[str]:
        todo_path = os.path.join(workspace, ".todo.md")
        if not os.path.exists(todo_path):
            return []

        items: List[str] = []
        with open(todo_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = str(raw or "").strip()
                if line.startswith("- [ ] "):
                    items.append(line[6:].strip())
                elif line.startswith("- [/] "):
                    pass  # 진행 중 항목은 건너뛰기
                elif line.startswith("- ") and not line.startswith("- [x] ") and not line.startswith("- [/] "):
                    items.append(line[2:].strip())
        return [item for item in items if item]

    def _completed_todo_items(self, workspace: str) -> List[str]:
        todo_path = os.path.join(workspace, ".todo.md")
        if not os.path.exists(todo_path):
            return []

        items: List[str] = []
        with open(todo_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = str(raw or "").strip()
                if line.startswith("- [x] "):
                    items.append(line[6:].strip())
        return [item for item in items if item]

    def _completed_subtask_keys(self) -> set[str]:
        keys: set[str] = set()
        for bucket in ("completed_subtasks", "failed_subtasks", "interrupted_subtasks"):
            for item in self.state_board.get(bucket, []) or []:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("subtask") or "").strip()
                if text:
                    keys.add(safe_id(text))
        return keys

    def _todo_matches_role(self, todo_text: str, role: str) -> bool:
        prefix = str(todo_text or "").split(":", 1)[0]
        return safe_id(prefix) == safe_id(role)

    def _todo_role_prefix(self, todo_text: str) -> str:
        text = str(todo_text or "")
        if ":" not in text:
            return ""
        prefix = text.split(":", 1)[0]
        return safe_id(prefix)

    def _todo_fully_completed(self, workspace: str) -> bool:
        completed_items = self._completed_todo_items(workspace)
        open_items = self._open_todo_items(workspace)
        return bool(completed_items) and not open_items

    def _fallback_next_tasks(self, available_roles: List[str], workspace: str) -> List[Dict[str, str]]:
        todo_items = self._open_todo_items(workspace)
        if not todo_items:
            return []

        completed = self._completed_subtask_keys()
        pending = [item for item in todo_items if safe_id(item) not in completed]
        if not pending:
            return []

        tasks: List[Dict[str, str]] = []
        used_items: set[str] = set()

        for role in available_roles:
            match = next(
                (item for item in pending if item not in used_items and self._todo_matches_role(item, role)),
                None,
            )
            if not match:
                continue
            used_items.add(match)
            tasks.append(
                {
                    "assigned_role": role,
                    "subtask_instruction": match,
                    "estimated_complexity": "HIGH",
                }
            )

        for role in available_roles:
            if any(task.get("assigned_role") == role for task in tasks):
                continue
            match = next(
                (
                    item
                    for item in pending
                    if item not in used_items and not self._todo_role_prefix(item)
                ),
                None,
            )
            if not match:
                break
            used_items.add(match)
            tasks.append(
                {
                    "assigned_role": role,
                    "subtask_instruction": match,
                    "estimated_complexity": "HIGH",
                }
            )

        return tasks

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
        if self._todo_fully_completed(target_workspace):
            return []
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
            completed = self._completed_subtask_keys() | {
                safe_id(item) for item in self._completed_todo_items(target_workspace)
            }
            filtered_tasks = []
            for task in tasks:
                if task.get("assigned_role") not in available_roles:
                    continue
                task_key = safe_id(str(task.get("subtask_instruction") or ""))
                if task_key and task_key in completed:
                    continue
                filtered_tasks.append(task)
            log_path = self._runtime_file("dynamic_log.txt", target_workspace)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"\n[Lilith] Cycle: {getattr(self, '_current_cycle', '?')}\n"
                    f"Roles: {available_roles}\nTasks: {tasks}\nFilteredTasks: {filtered_tasks}\nRaw: {json.dumps(data, ensure_ascii=False)}\n"
                )

            if not filtered_tasks:
                fallback_tasks = self._fallback_next_tasks(available_roles, target_workspace)
                if fallback_tasks:
                    return fallback_tasks
                if not data:
                    print_agent_msg("Lilith", "LLM response empty. Retrying next cycle...", "")
                    return []  # 빈 배열 → _orchestration_loop에서 idle 대기
                return []
            return filtered_tasks
        except Exception as exc:
            print_agent_msg("Lilith", f"Failed to dynamically generate next tasks: {exc}", "")
            fallback_tasks = self._fallback_next_tasks(available_roles, target_workspace)
            if fallback_tasks:
                return fallback_tasks
            return []  # 빈 배열 → _orchestration_loop에서 idle 대기

    async def _execute_agent_task(self, role: str, subtask: str, run_id: str, workspace: str | None = None):
        print_agent_msg("System", f"Dispatching [{role}] -> {subtask[:50]}...", "")
        self.state_board["agents_status"][role] = "working"

        try:
            target_workspace = workspace or os.getcwd()
            self.active_assignments.setdefault(
                run_id,
                {"role": role, "subtask": subtask, "workspace": target_workspace},
            )
            self._sync_manifest()
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
                await self.memory_hub.update_ast_state(
                    filepath=f"Project_Scope_{role}",
                    author_role=role,
                    changes_summary=f"Completed subtask: {subtask[:50]}",
                )
                print_agent_msg(role, "Task completed.", "")
                self._sync_manifest()
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
                self._sync_manifest()
        except Exception as exc:
            self.state_board["failed_subtasks"].append(
                {"role": role, "subtask": subtask, "reason": str(exc)}
            )
            print_agent_msg(role, f"Task crashed: {exc}", "")
            self._sync_manifest()
        finally:
            self.state_board["agents_status"][role] = "idle"
            self.active_tasks.pop(run_id, None)
            self.active_assignments.pop(run_id, None)
            self._sync_manifest()

    async def _orchestration_loop(self, project_desc: str, roles: List[str], workspace: str | None = None):
        target_workspace = workspace or os.getcwd()
        for role in roles:
            self.state_board["agents_status"][role] = "idle"

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
                    self.active_assignments[task_id] = {
                        "role": role,
                        "subtask": instruction,
                        "workspace": target_workspace,
                    }
                    self._sync_manifest()
                    task_obj = asyncio.create_task(self._execute_agent_task(role, instruction, task_id, target_workspace))
                    self.active_tasks[task_id] = task_obj

            await asyncio.sleep(2)

        self.state_board["current_status"] = "stopped_max_cycles" if cycle >= max_cycles else "completed"
        self._sync_manifest(force=True)

        if self.active_tasks:
            print_agent_msg("Lilith", "Waiting for remaining tasks to complete before exit...", "")
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)

    def run_project(self, project_desc: str, roles: List[str], workspace: str | None = None) -> Dict[str, Any]:
        print_agent_msg("System", "Initializing Dynamic LLM-Driven Orchestrator (V3)", "")
        self._prepare_resume_state(project_desc, roles, workspace)
        try:
            asyncio.run(self._orchestration_loop(project_desc, roles, workspace))
        except Exception as exc:
            import traceback

            self.state_board["current_status"] = "crashed"
            crash_path = self._runtime_file("crash.log", workspace)
            with crash_path.open("w", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
            self._sync_manifest(force=True)
            print_agent_msg("System", f"Orchestration loop crashed: {exc}", "")
        else:
            self._sync_manifest(force=True)
        return self.state_board
