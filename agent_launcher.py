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

import google.generativeai as genai
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

if not os.path.exists(POLICIES_PATH):
    with open(POLICIES_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "project_id": PROJECT_ID,
                "workflow": {"default_template": "workflows/two_week_webapp_delivery.yaml", "role_map": {}},
                "quality_gate": {"default_stage_on_build": "candidate", "auto_promote_sequence": ["canary", "active"]},
                "approval_policy": {"default_require_approval": False, "require_skill_change_approval": False},
                "autonomy": {"max_stage_retries": 2, "strict_quality_gate": True, "stop_on_stage_failure": True},
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )
if not os.path.exists(CONTEXT_SCHEMA_PATH):
    with open(CONTEXT_SCHEMA_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "required_keys": ["agent", "data_dir", "artifacts_dir"],
                "types": {"agent": "dict", "data_dir": "str", "artifacts_dir": "str"},
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )
if not os.path.exists(SKILL_LOCK_PATH):
    with open(SKILL_LOCK_PATH, "w", encoding="utf-8") as f:
        yaml.dump({"skills": {}}, f, allow_unicode=True, default_flow_style=False)
if not os.path.exists(DASHBOARD_PATH):
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        json.dump({"project_id": PROJECT_ID, "runs": []}, f, ensure_ascii=False, indent=2)
if not os.path.exists(PROJECT_WORKFLOW_PATH):
    with open(PROJECT_WORKFLOW_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            {"owner_agent": "General", "stages": [{"id": "MAIN", "name": "Main", "objective": "기본 워크플로우"}]},
            f,
            allow_unicode=True,
            default_flow_style=False,
        )
if not os.path.exists(PROJECT_SETTINGS_PATH):
    with open(PROJECT_SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "agent_overrides": {},
                "skill_overrides": {
                    "prefer_project_skills": True,
                },
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )

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
from core.agent_runner import ModelRouter, AgentRunner
from core.git_manager import GitManager
from core.ultra_loop import UltraLoop
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

# 7) Factory
# =============================================================================
class AgentFactory:
    def __init__(self):
        self.mr = ModelRouter()
        self.agent_mgr = AgentManager(self.mr)
        self.req = RequirementAnalyzer(self.mr)
        self.research = HimariResearchAgent(self.mr)
        self.builder = SandboxedBuilder(self.mr)
        self.registry = RegistryManager()
        self.git = GitManager()
        self.runner = AgentRunner(self.mr) # Added Runner
        self.ultra = UltraLoop(self.runner)

    def _missing_local_skill_files(self, agent: dict) -> list[str]:
        missing: list[str] = []
        for sid_raw in (agent.get("skills") or []):
            sid = safe_id(str(sid_raw))
            if not sid:
                continue
            skill_py, _meta = resolve_skill_paths(sid)
            if not skill_py:
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
            print(f"\n[UltraMode] 스킬 자동 승인: {action} ({skills})")
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
            "workflow_path": workflow_path,
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
        data["status"] = status
        data["updated_at"] = now_iso()
        _safe_write_json(state_path, data)

    def run(self, task_input: str, role_spec: str = "General", enable_build: bool = False, execution_mode: str = "approval"):
        run_id = f"run_{int(time.time())}"
        print(f"\nRUN={run_id}")
        print(f"- Role: {role_spec}")
        print(f"- Mode: {execution_mode}")
        print(f"- Task: {task_input}")

        agent = self.agent_mgr.get_or_create(role_spec)
        reqs = self.req.analyze(agent, task_input)
        file_missing = self._missing_local_skill_files(agent)

        skills = reqs.get("missing_skills", [])
        initial_targets = list(dict.fromkeys([safe_id(s) for s in skills] + file_missing))

        skipped_build_targets: list[str] = []
        if initial_targets and not enable_build:
            skipped_build_targets = list(initial_targets)
            print(f"\n[RunOnly] build disabled, skipping: {skipped_build_targets}")

        if initial_targets and enable_build:
            print(f"\n[Build] Needed skills: {initial_targets}")
            built_metas: list[dict] = []

            research = self.research.research(agent, reqs, build_targets=initial_targets)
            evidence_pack = research.get("evidence_pack", {}) if isinstance(research, dict) else {}
            targets = evidence_pack.get("targets", {}) if isinstance(evidence_pack, dict) else {}

            reusable: list[str] = []
            resolved_needs: set[str] = set()
            for need in initial_targets:
                target = targets.get(need, {}) if isinstance(targets, dict) else {}
                top_sid = safe_id(str(target.get("top_candidate", "")))
                verified = bool(target.get("verified", False))
                if top_sid and verified:
                    reusable.append(top_sid)
                    resolved_needs.add(need)

            reusable = list(dict.fromkeys(reusable))
            if reusable:
                for sid in reusable:
                    self.registry.ensure_lock_for_existing_skill(sid)
                installable_reuse = [sid for sid in reusable if self.registry.is_installable(sid)]
                blocked_reuse = [sid for sid in reusable if sid not in installable_reuse]
                if blocked_reuse:
                    print(f"[QualityGate] install blocked: {blocked_reuse}")
                approval_policy = self._read_approval_policy()
                allow_skill_change = True
                is_ultra = (execution_mode == "ultra")
                if installable_reuse and approval_policy.get("require_skill_change_approval", False):
                    allow_skill_change = self._ask_skill_change_approval(role_spec, installable_reuse, "reuse skill install", auto_approve=is_ultra)
                installed = self.agent_mgr.install_skills(role_spec, installable_reuse) if (installable_reuse and allow_skill_change) else []
                if installable_reuse and not allow_skill_change:
                    print("[Approval] reuse install skipped by user.")
                print(f"[Factory] reused install: {installable_reuse} -> agent.skills={installed}")
                agent = self.agent_mgr.get_or_create(role_spec)

            unresolved = [need for need in initial_targets if need not in resolved_needs]

            unresolved_for_external = []
            for need in unresolved:
                target = targets.get(need, {}) if isinstance(targets, dict) else {}
                cands = target.get("candidates", []) if isinstance(target, dict) else []
                if isinstance(cands, list) and cands:
                    unresolved_for_external.append(need)
            if unresolved_for_external:
                print(f"[Factory] external lookup targets: {unresolved_for_external}")
                ext_installed_map = self.research.search_external_and_install(
                    unresolved_for_external, reqs=reqs, registry=self.registry
                )
                ext_skill_ids = list(dict.fromkeys([safe_id(str(sid)) for sid in ext_installed_map.values() if str(sid).strip()]))
                if ext_skill_ids:
                    installable_ext = [sid for sid in ext_skill_ids if self.registry.is_installable(sid)]
                    blocked_ext = [sid for sid in ext_skill_ids if sid not in installable_ext]
                    if blocked_ext:
                        print(f"[QualityGate] external install blocked: {blocked_ext}")
                    approval_policy = self._read_approval_policy()
                    allow_skill_change = True
                    is_ultra = (execution_mode == "ultra")
                    if installable_ext and approval_policy.get("require_skill_change_approval", False):
                        allow_skill_change = self._ask_skill_change_approval(role_spec, installable_ext, "external skill install", auto_approve=is_ultra)
                    installed = self.agent_mgr.install_skills(role_spec, installable_ext) if (installable_ext and allow_skill_change) else []
                    if installable_ext and not allow_skill_change:
                        print("[Approval] external install skipped by user.")
                    print(f"[Factory] external install: {installable_ext} -> agent.skills={installed}")
                    for need in unresolved_for_external:
                        if need in ext_installed_map:
                            resolved_needs.add(need)
                    agent = self.agent_mgr.get_or_create(role_spec)
                else:
                    print("[Factory] no installable external candidates.")

            unresolved = [need for need in initial_targets if need not in resolved_needs]
            if unresolved:
                print(f"[Factory] new build targets: {unresolved}")

            built_skill_ids: list[str] = []
            for need in unresolved:
                ok, _code_path, meta = self.builder.build_skill(
                    agent=agent,
                    skill_name=need,
                    reqs=reqs,
                    run_id=run_id,
                    evidence_pack=evidence_pack,
                )
                if ok:
                    sid = safe_id(str(meta.get("id", need)))
                    skill_dir = os.path.join(SKILLS_DIR, sid)
                    self.registry.register_built(meta, skill_dir)
                    built_metas.append(meta)
                    built_skill_ids.append(sid)
                    print(f"[Factory] build success: {sid}")
                else:
                    print(f"[Factory] build failed: {need} | detail={meta.get('last_test_detail')}")

            if built_metas:
                self.registry.workflow_apply(built_metas)

            if built_skill_ids:
                installable_new = [sid for sid in built_skill_ids if self.registry.is_installable(sid)]
                blocked_new = [sid for sid in built_skill_ids if sid not in installable_new]
                if blocked_new:
                    print(f"[QualityGate] new install blocked: {blocked_new}")
                approval_policy = self._read_approval_policy()
                allow_skill_change = True
                is_ultra = (execution_mode == "ultra")
                if installable_new and approval_policy.get("require_skill_change_approval", False):
                    allow_skill_change = self._ask_skill_change_approval(role_spec, installable_new, "new skill install", auto_approve=is_ultra)
                installed = self.agent_mgr.install_skills(role_spec, installable_new) if (installable_new and allow_skill_change) else []
                if installable_new and not allow_skill_change:
                    print("[Approval] new install skipped by user.")
                print(f"[Factory] new install: {installable_new} -> agent.skills={installed}")
                agent = self.agent_mgr.get_or_create(role_spec)

        try:
            if execution_mode == "ultra":
                run_metrics = self.ultra.run_mission(agent, task_input, run_id=run_id) or {}
            else:
                run_metrics = self.runner.run(agent, task_input, run_id=run_id, auto_approve=False) or {}
        except TypeError as e:
            if "unexpected keyword argument 'run_id'" in str(e):
                run_metrics = self.runner.run(agent, task_input) or {}
            else:
                raise

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

    def run_workflow(self, task_input: str, workflow_path: str | None = None, role_specs: list[str] | None = None):
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

        if not stages:
            stages = [{"id": "MAIN", "name": "Main", "objective": task_input}]

        policies = read_project_policies()
        role_map = {}
        if isinstance(policies, dict):
            wf_cfg = policies.get("workflow", {}) if isinstance(policies.get("workflow"), dict) else {}
            role_map = wf_cfg.get("role_map", {}) if isinstance(wf_cfg.get("role_map"), dict) else {}

        print(f"\n🧭 [Workflow] 시작: {workflow_path}")
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
                    result = self.run(task_input=stage_task, role_spec=role) or {}
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
    parser.add_argument("--mode", choices=["approval", "ultra"], default="approval", help="Execution mode")
    parser.add_argument("--ultra", action="store_true", help="Shortcut for --mode ultra")
    parser.add_argument("--role", default="General", help="Agent role")
    parser.add_argument("--build", action="store_true", help="Enable skill building")
    
    args = parser.parse_args()
    
    task_input = " ".join(args.task).strip()
    execution_mode = "ultra" if (args.ultra or args.mode == "ultra") else "approval"
    
    if not task_input:
        task_input = prompt_mission_template("Agent Factory")
        
    AgentFactory().run(
        task_input=task_input,
        role_spec=args.role,
        enable_build=args.build,
        execution_mode=execution_mode
    )





