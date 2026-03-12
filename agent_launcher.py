# agent_factory_lite_secure.py
import os
import re
import time
import json
import ast
import yaml
import hashlib
import subprocess
import sys
import importlib.util
import inspect
import functools
import shutil
from datetime import datetime
import getpass
from config.schema import factory_config

# Core utilities are now imported from core.utils
from core.utils import *

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from core.registry import ToolRegistry
from core.tool_runtime import ToolRuntimeWrapper
from core.policy_runtime import PolicyRuntime
from core.hooks.event_bus import HookEventBus, IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator

# =============================================================================
# config.schema is loaded at top
from core.config_paths import *

# [Modularized] 프로젝트 초기화 — core/project_init.py 로 추출
from core.project_init import ensure_project_files
ensure_project_files()

# Constants are now imported from core.utils

# Internal classes and core utilities have been moved to the core/ directory
# for modularity and "Provision Readiness".

# =============================================================================
# Utils
# =============================================================================
# from core.utils import * # Now imported explicitly at the top
# Core components imported from specialized modules
from core.manager import AgentManager, RequirementAnalyzer
from core.researcher import HimariResearchAgent
from core.builder import SandboxedBuilder
from core.registry_manager import RegistryManager
from core.skill_procurer import SkillOrchestrator
from core.agent_runner import ModelRouter, AgentRunner
from core.git_manager import GitManager
from core.fsa_loop import FSALoop
from core.dynamic_orchestrator import DynamicOrchestrator
from core.request_router import RequestRouter
from core.project_pipeline import ProjectPipeline
from core.documentation_policy import ensure_documentation_files, single_task_todo_items, write_project_todo
# Redundant AST and Sandbox logic removed (handled by core.utils and core.executor)

# =============================================================================
# 4) Agent / Requirements
# =============================================================================
# AgentManager, RequirementAnalyzer, HimariResearchAgent, SandboxedBuilder, RegistryManager
# classes have been moved to core/manager.py for better modularity.
# =============================================================================
# 6) Registry / Workflow / Git
# =============================================================================
# RegistryManager moved to core/registry_manager.py

def _safe_write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

class AgentFactory:
    def __init__(self):
        self.mr = ModelRouter()
        self.agent_mgr = AgentManager(self.mr)
        self.req = RequirementAnalyzer(self.mr)
        self.research = HimariResearchAgent(self.mr)
        self.builder = SandboxedBuilder(self.mr)
        self.registry = RegistryManager()
        self.git = GitManager()
        self.runner = AgentRunner(self.mr)
        self.ultra = FSALoop(self.runner, self.agent_mgr)
        self.request_router = RequestRouter()
        # [GAP-3] Unified pipeline: Himari(Skeleton) + Builder(Release)
        self.procurer = SkillOrchestrator(
            registry=self.registry,
            research_agent=self.research,
            builder=self.builder,
            agent_mgr=self.agent_mgr,
        )
        self.project_pipeline = ProjectPipeline(
            mr=self.mr,
            agent_mgr=self.agent_mgr,
            research_agent=self.research,
            procurer=self.procurer,
        )

    def _missing_local_skill_files(self, agent: dict) -> list[str]:
        missing: list[str] = []
        for sid_raw in (agent.get("skills") or []):
            sid = safe_id(str(sid_raw))
            if not sid:
                continue
            if not has_local_skill(sid):
                missing.append(sid)
        return list(dict.fromkeys(missing))

    def _read_autonomy_policy(self) -> dict:
        policies = read_project_policies()
        ap = policies.get("autonomy", {}) if isinstance(policies.get("autonomy"), dict) else {}
        return {
            "max_stage_retries": int(ap.get("max_stage_retries", 2) or 2),
            "strict_quality_gate": bool(ap.get("strict_quality_gate", True)),
            "stop_on_stage_failure": bool(ap.get("stop_on_stage_failure", True)),
        }

    def _read_approval_policy(self) -> dict:
        policies = read_project_policies()
        ap = policies.get("approval_policy", {}) if isinstance(policies.get("approval_policy"), dict) else {}
        return {
            "require_skill_change_approval": bool(ap.get("require_skill_change_approval", False))
        }

    def _ask_skill_change_approval(self, role_spec: str, skills: list[str], action: str, auto_approve: bool = False) -> bool:
        if not skills:
            return True
        if auto_approve:
            print(f"\n[FSA Mode] 스킬 자동 승인: {action} ({skills})")
            return True
        print("\n[승인 요청] 스킬 변경")
        print(f"- 대상 에이전트: {role_spec}")
        print(f"- 작업: {action}")
        print(f"- 스킬 목록: {skills}")
        ans = input("위 스킬 변경을 허용할까요? (yes/no): ").strip().lower()
        return ans in ("y", "yes")

    def _create_workflow_state(self, run_id: str, workflow_path: str, stages: list, base_roles: list[str]) -> str:
        run_dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(run_dir, exist_ok=True)
        state_path = os.path.join(run_dir, "state.json")
        data = {
            "run_id": run_id,
            "project_id": PROJECT_ID,
            "workflow_path": to_portable_path(workflow_path),
            "status": "running",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "base_roles": base_roles,
            "stages": [
                {
                    "id": str(s.get("id", "STAGE")),
                    "name": str(s.get("name", s.get("id", "STAGE"))),
                    "status": "pending",
                    "attempts": 0,
                    "last_error": "",
                    "updated_at": now_iso(),
                }
                for s in stages
            ],
        }
        _safe_write_json(state_path, data)
        return state_path

    def _update_workflow_state(self, state_path: str, stage_id: str, status: str, attempts: int = 0, last_error: str = ""):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if str(data.get("workflow_path", "")).strip():
            data["workflow_path"] = to_portable_path(str(data.get("workflow_path")))
        for s in data.get("stages", []):
            if str(s.get("id")) == str(stage_id):
                s["status"] = status
                if attempts:
                    s["attempts"] = attempts
                if last_error:
                    s["last_error"] = last_error[:300]
                s["updated_at"] = now_iso()
                break
        data["updated_at"] = now_iso()
        _safe_write_json(state_path, data)

    def _finish_workflow_state(self, state_path: str, status: str):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if str(data.get("workflow_path", "")).strip():
            data["workflow_path"] = to_portable_path(str(data.get("workflow_path")))
        data["status"] = status
        data["updated_at"] = now_iso()
        _safe_write_json(state_path, data)

    def _invoke_runner(self, agent: dict, task_input: str, run_id: str, auto_approve: bool, workspace: str | None = None):
        params = inspect.signature(self.runner.run).parameters
        kwargs = {}
        if "run_id" in params:
            kwargs["run_id"] = run_id
        if "auto_approve" in params:
            kwargs["auto_approve"] = auto_approve
        if "workspace" in params:
            kwargs["workspace"] = workspace
        return self.runner.run(agent, task_input, **kwargs) or {}

    def _ensure_single_run_todo(
        self,
        task_input: str,
        role_spec: str,
        workspace: str,
        route: dict | None = None,
        reqs: dict | None = None,
    ) -> str:
        todo_path = os.path.join(workspace, ".todo.md")
        if os.path.exists(todo_path):
            return todo_path

        route_data = route if isinstance(route, dict) else {}
        req_data = reqs if isinstance(reqs, dict) else {}
        intent = str(req_data.get("intent") or route_data.get("intent") or "trivial").strip() or "trivial"
        risk_level = str(req_data.get("risk_level") or "normal").strip() or "normal"
        if not TodoContinuationEnforcer.requires_plan(task_input, intent=intent, risk_level=risk_level):
            return ""

        ensure_documentation_files(workspace)
        return write_project_todo(workspace, single_task_todo_items(task_input, role_spec))

    def _get_agent(self, role_spec: str, workspace: str | None = None) -> dict:
        params = inspect.signature(self.agent_mgr.get_or_create).parameters
        if "workspace" in params:
            return self.agent_mgr.get_or_create(role_spec, workspace=workspace)
        return self.agent_mgr.get_or_create(role_spec)

    def _analyze_requirements(self, agent: dict, task_input: str, workspace: str | None = None) -> dict:
        params = inspect.signature(self.req.analyze).parameters
        if "workspace" in params:
            return self.req.analyze(agent, task_input, workspace=workspace)
        return self.req.analyze(agent, task_input)

    def run(
        self,
        task_input: str,
        role_spec: str = "General",
        enable_build: bool = False,
        execution_mode: str = "approval",
        workspace: str | None = None,
        pipeline_mode: str = "auto",
    ):
        run_id = f"run_{int(time.time())}"
        print(f"\nRUN={run_id}")
        print(f"- Role: {role_spec}")
        print(f"- Mode: {execution_mode}")
        print(f"- Task: {task_input}")

        route = self.request_router.route(task_input=task_input, role_spec=role_spec, pipeline_mode=pipeline_mode)
        if route.get("pipeline") == "project":
            target_workspace = workspace or PROJECT_ROOT
            print(f"\n[Router] project pipeline selected: {route.get('reasoning', '')}")
            return self.project_pipeline.run(
                task_input=task_input,
                workspace=target_workspace,
                execution_mode=execution_mode,
                enable_build=enable_build,
                requested_role=role_spec,
                route=route,
            )

        agent = self._get_agent(role_spec, workspace=workspace)
        reqs = self._analyze_requirements(agent, task_input, workspace=workspace or PROJECT_ROOT)
        self._ensure_single_run_todo(
            task_input=task_input,
            role_spec=role_spec,
            workspace=workspace or PROJECT_ROOT,
            route=route,
            reqs=reqs,
        )
        file_missing = self._missing_local_skill_files(agent)

        skills = reqs.get("missing_skills", [])
        initial_targets = list(dict.fromkeys([safe_id(s) for s in skills] + file_missing))

        skipped_build_targets: list[str] = []
        if initial_targets and not enable_build:
            skipped_build_targets = list(initial_targets)
            print(f"\n[RunOnly] build disabled, skipping: {skipped_build_targets}")

        if initial_targets and enable_build:
            approval_policy = self._read_approval_policy()
            approval_gate = self._ask_skill_change_approval if approval_policy.get("require_skill_change_approval", False) else None
            # [GAP-3] Unified Pipeline: Himari(Skeleton) -> Builder(Release) -> Registry
            installed = self.procurer.procure_multiple(
                agent=agent,
                skill_names=initial_targets,
                reqs=reqs,
                run_id=run_id,
                execution_mode=execution_mode,
                approval_gate=approval_gate,
                workspace=workspace,
            )
            if installed:
                agent = self._get_agent(role_spec, workspace=workspace)

        if execution_mode == "fsa":
            ultra_params = inspect.signature(self.ultra.run_mission).parameters
            ultra_kwargs = {"run_id": run_id}
            if "workspace" in ultra_params:
                ultra_kwargs["workspace"] = workspace
            run_metrics = self.ultra.run_mission(agent, task_input, **ultra_kwargs) or {}
        else:
            run_metrics = self._invoke_runner(agent, task_input, run_id=run_id, auto_approve=False, workspace=workspace)

        append_dashboard_run(
            {
                "ts": now_iso(),
                "type": "single_run",
                "project_id": PROJECT_ID,
                "role": role_spec,
                "task": (task_input or "")[:300],
                "skills_loaded": list(agent.get("skills", []) if isinstance(agent, dict) else []),
                "ok": bool(run_metrics.get("ok", False)),
                "reason": str(run_metrics.get("reason", "")),
                "latency_ms": int(run_metrics.get("latency_ms", 0) or 0),
                "approval_rejects": int(run_metrics.get("approval_rejects", 0) or 0),
                "build_enabled": bool(enable_build),
                "missing_skills_detected": skipped_build_targets,
            }
        )
        return {
            "run_id": run_id,
            "ok": bool(run_metrics.get("ok", False)),
            "reason": str(run_metrics.get("reason", "")),
            "latency_ms": int(run_metrics.get("latency_ms", 0) or 0),
            "approval_rejects": int(run_metrics.get("approval_rejects", 0) or 0),
            "build_enabled": bool(enable_build),
            "missing_skills_detected": skipped_build_targets,
        }

    def run_dynamic_workflow(self, task_input: str, role_specs: list[str]):
        print(f"\n🧭 [DynamicWorkflow] 진정한 리더(Lilith) 주도의 동적 병렬 실행을 시작합니다.")
        orchestrator = DynamicOrchestrator(self.mr)
        state_board = orchestrator.run_project(task_input, role_specs)
        print(f"\n✅ [DynamicWorkflow] 완료. 보드 상태: {json.dumps(state_board, ensure_ascii=False)}")
        return state_board

    def run_workflow(self, task_input: str, workflow_path: str | None = None, role_specs: list[str] | None = None, use_dynamic: bool = False):
        # V3 Pivot: Only use dynamic orchestrator if explicitly requested
        if use_dynamic and role_specs:
            return self.run_dynamic_workflow(task_input, role_specs)

        if not workflow_path:
            policies = read_project_policies()
            wf_cfg = policies.get("workflow", {}) if isinstance(policies, dict) else {}
            default_tpl = str(wf_cfg.get("default_template", "")).strip()
            if default_tpl:
                cand = os.path.join(BASE_DIR, default_tpl)
                workflow_path = cand if os.path.exists(cand) else PROJECT_WORKFLOW_PATH
            else:
                workflow_path = PROJECT_WORKFLOW_PATH

        wf = read_yaml(workflow_path)
        if not wf:
            print(f"⚠️ [Workflow] 워크플로우를 읽을 수 없습니다: {workflow_path}")
            return

        owner = str(wf.get("owner_agent", "")).strip()
        stages = wf.get("stages", []) if isinstance(wf.get("stages"), list) else []
        picked_roles = [r.strip() for r in (role_specs or []) if str(r).strip()]
        if not picked_roles:
            picked_roles = [owner] if owner else ["General"]

        # ... (rest of the static workflow)
        if not stages:
            stages = [{"id": "MAIN", "name": "Main", "objective": task_input}]

        policies = read_project_policies()
        role_map = {}
        if isinstance(policies, dict):
            wf_cfg = policies.get("workflow", {}) if isinstance(policies.get("workflow"), dict) else {}
            role_map = wf_cfg.get("role_map", {}) if isinstance(wf_cfg.get("role_map"), dict) else {}

        print(f"\n🧭 [Workflow] 정적 파이프라인 시작: {workflow_path}")
        print(f"👥 [Workflow] 대상 에이전트: {picked_roles}")
        workflow_run_id = f"wf_{int(time.time())}"
        state_path = self._create_workflow_state(workflow_run_id, workflow_path, stages, picked_roles)
        autonomy = self._read_autonomy_policy()
        max_stage_retries = max(1, int(autonomy.get("max_stage_retries", 2)))
        strict_quality_gate = bool(autonomy.get("strict_quality_gate", True))
        stop_on_stage_failure = bool(autonomy.get("stop_on_stage_failure", True))
        run_count = 0
        failed = False
        for stage in stages:
            sid = str(stage.get("id", "STAGE"))
            sname = str(stage.get("name", sid))
            objective = str(stage.get("objective", "")).strip()
            mapped_roles = role_map.get(sid)
            stage_roles = [r.strip() for r in mapped_roles if str(r).strip()] if isinstance(mapped_roles, list) else picked_roles
            stage_task = (
                f"{task_input}\n"
                f"[Workflow Stage] id={sid}, name={sname}\n"
                f"[Stage Objective] {objective if objective else 'N/A'}"
            )
            print(f"\n📍 [Workflow] Stage {sid}: {sname}")
            self._update_workflow_state(state_path, sid, "in_progress")
            stage_ok = True
            stage_error = ""
            stage_attempts = 0
            for role in stage_roles:
                print(f"🤝 [Workflow] 실행 에이전트: {role}")
                role_ok = False
                role_reason = ""
                for attempt in range(1, max_stage_retries + 1):
                    stage_attempts = max(stage_attempts, attempt)
                    run_params = inspect.signature(self.run).parameters
                    run_kwargs = {"task_input": stage_task, "role_spec": role}
                    if "pipeline_mode" in run_params:
                        run_kwargs["pipeline_mode"] = "single"
                    result = self.run(**run_kwargs) or {}
                    run_count += 1
                    role_ok = bool(result.get("ok", False))
                    role_reason = str(result.get("reason", ""))
                    if role_ok:
                        break
                    print(f"⚠️ [Workflow] 재시도 예정: stage={sid}, role={role}, attempt={attempt}/{max_stage_retries}, reason={role_reason}")
                if not role_ok:
                    stage_ok = False
                    stage_error = f"role={role}, reason={role_reason}"
                    print(f"❌ [Workflow] 단계 실패: {stage_error}")
                    break
            if stage_ok:
                self._update_workflow_state(state_path, sid, "completed", attempts=stage_attempts)
            else:
                self._update_workflow_state(state_path, sid, "failed", attempts=stage_attempts, last_error=stage_error)
                failed = True
                if strict_quality_gate or stop_on_stage_failure:
                    print(f"🛑 [QualityGate] Stage `{sid}` 실패로 다음 단계를 중단합니다.")
                    break

        self._finish_workflow_state(state_path, "failed" if failed else "completed")
        append_dashboard_run(
            {
                "ts": now_iso(),
                "type": "workflow_run",
                "project_id": PROJECT_ID,
                "workflow_path": workflow_path,
                "stage_count": len(stages),
                "run_count": run_count,
                "base_roles": picked_roles,
                "ok": not failed,
                "state_path": state_path,
            }
        )
    
# =============================================================================
# Example Entry Point
# =============================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent Factory CLI")
    parser.add_argument("task", nargs="*", help="Task description")
    parser.add_argument("--mode", choices=["approval", "fsa"], default="approval", help="Execution mode")
    parser.add_argument("--fsa", action="store_true", help="Shortcut for --mode fsa")
    parser.add_argument("--role", default="General", help="Agent role")
    parser.add_argument("--build", action="store_true", help="Enable skill building")
    
    args = parser.parse_args()
    
    task_input = " ".join(args.task).strip()
    execution_mode = "fsa" if (args.fsa or args.mode == "fsa") else "approval"
    
    if not task_input:
        task_input = prompt_mission_template("Agent Factory")
        
    AgentFactory().run(
        task_input=task_input,
        role_spec=args.role,
        enable_build=args.build,
        execution_mode=execution_mode
    )





