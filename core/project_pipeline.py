import json
import os
import time

from core.bootstrap_roles import ProjectPlanningDirector, build_bootstrap_agent
from core.dynamic_orchestrator import DynamicOrchestrator
from core.utils import (
    append_dashboard_run,
    now_iso,
    read_yaml,
    safe_id,
    to_portable_path,
    write_text,
    write_yaml,
)


class ProjectPipeline:
    """Front-loads research and planning before multi-agent execution."""

    def __init__(self, mr, agent_mgr, research_agent, procurer):
        self.mr = mr
        self.agent_mgr = agent_mgr
        self.research = research_agent
        self.procurer = procurer
        self.planner = ProjectPlanningDirector(mr)

    def _write_json(self, path: str, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _planning_dir(self, workspace: str) -> str:
        planning_dir = os.path.join(workspace, "planning")
        os.makedirs(planning_dir, exist_ok=True)
        return planning_dir

    def _role_agent_path(self, role_id: str, workspace: str) -> str:
        return os.path.join(workspace, "agents", f"{safe_id(role_id)}.yaml")

    def _write_todo(self, workspace: str, role_plan: dict) -> str:
        todo_items = [str(x).strip() for x in (role_plan.get("todo_items") or []) if str(x).strip()]
        if not todo_items:
            todo_items = [f"{item.get('name')}: {item.get('objective')}" for item in (role_plan.get("roles") or [])]
        lines = ["# Project TODO", ""]
        for item in todo_items:
            lines.append(f"- [ ] {item}")
        todo_path = os.path.join(workspace, ".todo.md")
        write_text(todo_path, "\n".join(lines).strip() + "\n")
        return todo_path

    def _materialize_roles(
        self,
        role_plan: dict,
        project_brief: dict,
        workspace: str,
        execution_mode: str,
        enable_build: bool,
        run_id: str,
    ) -> tuple[list[str], dict[str, list[str]]]:
        roles: list[str] = []
        installed_map: dict[str, list[str]] = {}
        os.makedirs(os.path.join(workspace, "agents"), exist_ok=True)

        for item in (role_plan.get("roles") or []):
            if not isinstance(item, dict):
                continue
            role_id = safe_id(str(item.get("id") or item.get("name") or "role"))
            if not role_id:
                continue
            role_name = str(item.get("name") or role_id)
            objective = str(item.get("objective") or project_brief.get("goal") or "").strip()
            required_skills = [safe_id(str(s)) for s in (item.get("required_skills") or []) if str(s).strip()]

            agent = self.agent_mgr.get_or_create(role_id, workspace=workspace)
            agent_path = self._role_agent_path(role_id, workspace)
            agent_data = read_yaml(agent_path) if os.path.exists(agent_path) else dict(agent)
            agent_data["name"] = str(agent_data.get("name") or role_name)
            agent_data["role"] = role_name
            agent_data["project_role"] = {
                "objective": objective,
                "required_skills": required_skills,
                "updated_at": now_iso(),
            }
            write_yaml(agent_path, agent_data)

            if required_skills and enable_build:
                reqs = {
                    "goal": objective or project_brief.get("goal") or role_name,
                    "constraints": list(project_brief.get("constraints") or []),
                    "missing_skills": required_skills,
                }
                installed = self.procurer.procure_multiple(
                    agent=agent_data,
                    skill_names=required_skills,
                    reqs=reqs,
                    run_id=f"{run_id}_{role_id}",
                    execution_mode=execution_mode,
                    approval_gate=None,
                    workspace=workspace,
                )
                installed_map[role_id] = installed
            elif required_skills:
                self.agent_mgr.install_skills(role_id, required_skills, workspace=workspace)
                installed_map[role_id] = list(required_skills)
            else:
                installed_map[role_id] = []

            roles.append(role_id)

        return list(dict.fromkeys(roles)), installed_map

    def run(
        self,
        task_input: str,
        workspace: str,
        execution_mode: str = "approval",
        enable_build: bool = False,
        requested_role: str = "",
        route: dict | None = None,
    ) -> dict:
        target_workspace = os.path.abspath(workspace)
        os.makedirs(target_workspace, exist_ok=True)
        planning_dir = self._planning_dir(target_workspace)
        run_id = f"project_run_{int(time.time())}"

        research_agent = build_bootstrap_agent("research_director")
        project_brief = self.research.research_project_brief(
            research_agent,
            task_input,
            workspace=target_workspace,
        )
        project_brief["requested_role"] = requested_role
        project_brief["route"] = route or {}
        project_brief["generated_at"] = now_iso()
        project_brief_path = os.path.join(planning_dir, "project_brief.json")
        self._write_json(project_brief_path, project_brief)

        pd_agent = build_bootstrap_agent("pd_director")
        role_plan = self.planner.plan(task_input, project_brief)
        role_plan["generated_at"] = now_iso()
        role_plan["pd_agent"] = {
            "id": pd_agent["id"],
            "name": pd_agent["name"],
        }
        role_plan_path = os.path.join(planning_dir, "role_plan.json")
        self._write_json(role_plan_path, role_plan)

        todo_path = self._write_todo(target_workspace, role_plan)
        roles, installed_map = self._materialize_roles(
            role_plan=role_plan,
            project_brief=project_brief,
            workspace=target_workspace,
            execution_mode=execution_mode,
            enable_build=enable_build,
            run_id=run_id,
        )

        orchestrator = DynamicOrchestrator(self.mr, max_concurrent=5)
        board = orchestrator.run_project(task_input, roles, target_workspace)
        status = str(board.get("current_status", "unknown"))

        append_dashboard_run(
            {
                "ts": now_iso(),
                "type": "project_run",
                "project_id": os.path.basename(target_workspace),
                "task": (task_input or "")[:300],
                "ok": status == "completed",
                "reason": status,
                "pipeline": "project",
                "roles": roles,
                "planning_files": [
                    to_portable_path(project_brief_path),
                    to_portable_path(role_plan_path),
                    to_portable_path(todo_path),
                ],
            }
        )

        return {
            "run_id": run_id,
            "pipeline": "project",
            "ok": status == "completed",
            "reason": status,
            "roles": roles,
            "installed_skills": installed_map,
            "project_brief_path": to_portable_path(project_brief_path),
            "role_plan_path": to_portable_path(role_plan_path),
            "todo_path": to_portable_path(todo_path),
            "board": board,
        }
