import json
import os
import time
from dataclasses import dataclass, field

from core.approval_gate import ApprovalGate
from core.bootstrap_roles import ProjectPlanningDirector, build_bootstrap_agent
from core.documentation_policy import ensure_documentation_files, write_project_todo
from core.dynamic_orchestrator import DynamicOrchestrator
from core.project_task_board import (
    board_todo_items,
    build_project_board,
    enrich_role_plan,
    write_project_board,
    write_task_execution_plan,
)
from core.utils import (
    append_dashboard_run,
    now_iso,
    read_yaml,
    safe_id,
    to_portable_path,
    write_yaml,
)
from core.work_item_generator import generate_work_items, slug_from_brief
from core.work_item_parser import sync_board_from_work_items

PROJECT_ROLE_BASELINE_SKILLS = ("file_handler", "core_memory")


@dataclass
class PreparedProject:
    """
    prepare() 의 결과 객체.

    승인 전까지 execute() 를 호출하면 안 된다.
    approval_gate.is_execution_open() 이 True 일 때만 execute() 진행.
    """

    run_id: str
    workspace: str
    work_item_slug: str
    project_brief: dict
    role_plan: dict
    task_board: dict
    planning_files: list[str] = field(default_factory=list)
    work_item_files: dict[str, str] = field(default_factory=dict)
    # 하위 호환: 개별 경로 필드
    project_brief_path: str = ""
    role_plan_path: str = ""
    task_board_path: str = ""
    task_execution_plan_path: str = ""
    todo_path: str = ""

    def work_item_dir(self) -> str:
        return os.path.join(self.workspace, "docs", "work-items", self.work_item_slug)

    def gate(self) -> ApprovalGate:
        return ApprovalGate(self.workspace, self.work_item_slug)

    def summary_lines(self) -> list[str]:
        roles = self.role_plan.get("roles") or []
        tasks = self.task_board.get("tasks") or []
        modules = self.role_plan.get("modules") or []
        lines = [
            f"  목표: {self.project_brief.get('goal', '')}",
            f"  역할 수: {len(roles)}",
            f"  모듈 수: {len(modules)}",
            f"  작업 수: {len(tasks)}",
            f"  work-item: {self.work_item_dir()}",
        ]
        return lines


class ProjectPipeline:
    """
    2-Phase 프로젝트 파이프라인.

    Phase 1 — prepare():  문서 생성 + work-item 자동 채움. 에이전트 실행 없음.
    Phase 2 — execute():  승인 확인 → 편집 반영 → 에이전트 실행.

    하위 호환:
      run() = prepare() + 자동 승인 + execute()
    """

    def __init__(self, mr, agent_mgr, research_agent, procurer,
                 broker=None, reservation_mgr=None, visualizer=None):
        self.mr = mr
        self.agent_mgr = agent_mgr
        self.research = research_agent
        self.procurer = procurer
        self.planner = ProjectPlanningDirector(mr)
        self._broker = broker
        self._reservation_mgr = reservation_mgr
        self._visualizer = visualizer

    def _write_json(self, path: str, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _planning_dir(self, workspace: str) -> str:
        planning_dir = os.path.join(workspace, "planning")
        os.makedirs(planning_dir, exist_ok=True)
        return planning_dir

    def _role_agent_path(self, role_id: str, workspace: str) -> str:
        return os.path.join(workspace, "agents", f"{safe_id(role_id)}.yaml")

    def _merge_role_baseline(self, agent_data: dict) -> dict:
        merged = dict(agent_data or {})
        existing_skills = [safe_id(str(s)) for s in (merged.get("skills") or []) if str(s).strip()]
        merged["skills"] = list(dict.fromkeys(existing_skills + list(PROJECT_ROLE_BASELINE_SKILLS)))

        runtime_rules = merged.get("runtime_rules", {})
        if not isinstance(runtime_rules, dict):
            runtime_rules = {}
        allowed_skills = [
            safe_id(str(s))
            for s in (runtime_rules.get("allowed_skills") or [])
            if str(s).strip()
        ]
        runtime_rules["allowed_skills"] = list(
            dict.fromkeys(allowed_skills + list(PROJECT_ROLE_BASELINE_SKILLS))
        )
        merged["runtime_rules"] = runtime_rules
        return merged

    def _write_todo(self, workspace: str, role_plan: dict, task_board: dict | None = None) -> str:
        todo_items = board_todo_items(task_board or {})
        if not todo_items:
            todo_items = [str(x).strip() for x in (role_plan.get("todo_items") or []) if str(x).strip()]
        if not todo_items:
            todo_items = [f"{item.get('name')}: {item.get('objective')}" for item in (role_plan.get("roles") or [])]
        return write_project_todo(workspace, todo_items)

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
            role_modules = [
                module
                for module in (role_plan.get("modules") or [])
                if isinstance(module, dict) and safe_id(str(module.get("owner_role") or "")) == role_id
            ]
            feature_slices: list[str] = []
            for module in role_modules:
                for slice_name in (module.get("feature_slices") or []):
                    text = str(slice_name).strip()
                    if text and text not in feature_slices:
                        feature_slices.append(text)

            agent = self.agent_mgr.get_or_create(role_id, workspace=workspace)
            agent_path = self._role_agent_path(role_id, workspace)
            agent_data = read_yaml(agent_path) if os.path.exists(agent_path) else dict(agent)
            agent_data = self._merge_role_baseline(agent_data)
            agent_data["name"] = str(agent_data.get("name") or role_name)
            agent_data["role"] = role_name
            agent_data["project_role"] = {
                "objective": objective,
                "required_skills": required_skills,
                "owned_modules": [module.get("id") for module in role_modules if str(module.get("id") or "").strip()],
                "feature_slices": feature_slices,
                "planning_steps": [
                    str(step.get("id") or "").strip()
                    for step in (role_plan.get("planning_steps") or [])
                    if isinstance(step, dict) and str(step.get("id") or "").strip()
                ],
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

    # ------------------------------------------------------------------
    # Phase 1: prepare
    # ------------------------------------------------------------------

    def prepare(
        self,
        task_input: str,
        workspace: str,
        execution_mode: str = "approval",
        enable_build: bool = False,
        requested_role: str = "",
        route: dict | None = None,
    ) -> PreparedProject:
        """
        Phase 1: 문서를 생성하고 work-item 을 자동으로 채운다.

        에이전트를 실행하지 않는다.
        반환된 PreparedProject 에서 gate().approve() 후 execute() 를 호출해야 한다.
        """
        target_workspace = os.path.abspath(workspace)
        os.makedirs(target_workspace, exist_ok=True)
        ensure_documentation_files(target_workspace)
        planning_dir = self._planning_dir(target_workspace)
        run_id = f"project_run_{int(time.time())}"

        # -- Research --
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

        # -- Planning --
        pd_agent = build_bootstrap_agent("pd_director")
        role_plan_raw = self.planner.plan(task_input, project_brief)
        role_plan = enrich_role_plan(task_input, project_brief, role_plan_raw)
        role_plan["generated_at"] = now_iso()
        role_plan["pd_agent"] = {
            "id": pd_agent["id"],
            "name": pd_agent["name"],
        }
        role_plan_path = os.path.join(planning_dir, "role_plan.json")
        self._write_json(role_plan_path, role_plan)

        # -- Task Board --
        task_board = build_project_board(project_brief, role_plan)
        task_board_path = write_project_board(target_workspace, task_board)
        task_execution_plan_path = write_task_execution_plan(
            target_workspace, project_brief, role_plan, task_board
        )
        todo_path = self._write_todo(target_workspace, role_plan, task_board)

        # -- Work Items (★ 신규) --
        slug = slug_from_brief(project_brief)
        work_item_files = generate_work_items(
            workspace=target_workspace,
            slug=slug,
            project_brief=project_brief,
            role_plan=role_plan,
            task_board=task_board,
        )

        planning_files = [
            to_portable_path(project_brief_path),
            to_portable_path(role_plan_path),
            to_portable_path(task_board_path),
            to_portable_path(task_execution_plan_path),
            to_portable_path(todo_path),
        ] + [to_portable_path(p) for p in work_item_files.values()]

        return PreparedProject(
            run_id=run_id,
            workspace=target_workspace,
            work_item_slug=slug,
            project_brief=project_brief,
            role_plan=role_plan,
            task_board=task_board,
            planning_files=planning_files,
            work_item_files=work_item_files,
            project_brief_path=to_portable_path(project_brief_path),
            role_plan_path=to_portable_path(role_plan_path),
            task_board_path=to_portable_path(task_board_path),
            task_execution_plan_path=to_portable_path(task_execution_plan_path),
            todo_path=to_portable_path(todo_path),
        )

    # ------------------------------------------------------------------
    # Phase 2: execute
    # ------------------------------------------------------------------

    def execute(
        self,
        prepared: PreparedProject,
        enable_build: bool = False,
        execution_mode: str = "approval",
    ) -> dict:
        """
        Phase 2: 승인된 프로젝트를 실행한다.

        실행 전 검사:
          1. gate.is_execution_open() — execution_open: true 확인
          2. gate.check_validity()   — 승인 후 문서 변경 없음 확인
        통과 후:
          3. work-item 편집 내용을 task_board 에 반영
          4. 역할 구체화 (YAML 에이전트 파일 생성)
          5. DynamicOrchestrator.run_project() 실행
        """
        workspace = prepared.workspace
        gate = prepared.gate()

        # -- 승인 확인 --
        if not gate.is_execution_open():
            return {
                "ok": False,
                "reason": "approval_required",
                "message": "approval-gate.md 를 승인한 후 실행하세요.",
                "gate_path": gate.gate_path,
            }

        # -- 문서 변경 감지 --
        valid, changed = gate.check_validity()
        if not valid:
            gate.invalidate(reason=f"변경된 문서: {', '.join(changed)}")
            return {
                "ok": False,
                "reason": "documents_changed_after_approval",
                "changed_files": changed,
                "message": "승인 후 문서가 변경되었습니다. 재승인 후 실행하세요.",
            }

        # -- 편집 내용 반영 --
        updated_board = sync_board_from_work_items(
            workspace=workspace,
            slug=prepared.work_item_slug,
            existing_board=prepared.task_board,
        )
        write_project_board(workspace, updated_board)

        # -- 역할 구체화 --
        roles, installed_map = self._materialize_roles(
            role_plan=prepared.role_plan,
            project_brief=prepared.project_brief,
            workspace=workspace,
            execution_mode=execution_mode,
            enable_build=enable_build,
            run_id=prepared.run_id,
        )

        # -- 에이전트 실행 --
        task_input = str(prepared.project_brief.get("goal") or "")
        orchestrator = DynamicOrchestrator(
            self.mr, max_concurrent=5, broker=self._broker, visualizer=self._visualizer
        )
        run_board = orchestrator.run_project(task_input, roles, workspace)
        status = str(run_board.get("current_status", "unknown"))

        append_dashboard_run(
            {
                "ts": now_iso(),
                "type": "project_run",
                "project_id": os.path.basename(workspace),
                "task": task_input[:300],
                "ok": status == "completed",
                "reason": status,
                "pipeline": "project",
                "roles": roles,
                "planning_files": prepared.planning_files,
                "work_item_slug": prepared.work_item_slug,
            }
        )

        return {
            "run_id": prepared.run_id,
            "pipeline": "project",
            "ok": status == "completed",
            "reason": status,
            "roles": roles,
            "installed_skills": installed_map,
            "work_item_slug": prepared.work_item_slug,
            "work_item_dir": prepared.work_item_dir(),
            "planning_files": prepared.planning_files,
            "board": run_board,
            # 하위 호환 — 기존 코드가 직접 키로 접근하는 경우를 위해
            "project_brief_path": prepared.project_brief_path,
            "role_plan_path": prepared.role_plan_path,
            "task_board_path": prepared.task_board_path,
            "task_execution_plan_path": prepared.task_execution_plan_path,
            "todo_path": prepared.todo_path,
        }

    # ------------------------------------------------------------------
    # 하위 호환: run() = prepare + 자동 승인 + execute
    # ------------------------------------------------------------------

    def run(
        self,
        task_input: str,
        workspace: str,
        execution_mode: str = "approval",
        enable_build: bool = False,
        requested_role: str = "",
        route: dict | None = None,
    ) -> dict:
        """
        하위 호환 메서드.

        기존 코드에서 run() 을 직접 호출하면 자동 승인으로 동작한다.
        CLI 에서는 prepare() → 사용자 승인 → execute() 흐름을 사용한다.
        """
        prepared = self.prepare(
            task_input=task_input,
            workspace=workspace,
            execution_mode=execution_mode,
            enable_build=enable_build,
            requested_role=requested_role,
            route=route,
        )
        # 자동 승인 (하위 호환)
        prepared.gate().approve(approver="auto")

        return self.execute(
            prepared=prepared,
            enable_build=enable_build,
            execution_mode=execution_mode,
        )
