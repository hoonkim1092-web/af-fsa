"""
core/skill_procurer.py
======================
스킬 조달 + 제작(포징) 전담 모듈.
factory_manager.py 에서 추출.

주요 함수:
  - procure_skill(skill_name, role): 기존 스킬 검색 → 없으면 제작
  - forge_new_skill(skill_name, role): LLM으로 새 스킬 코드/지식 생성
  - sync_warehouse(): 코어 스킬 저장소 Git 동기화
"""

import os
import re
import glob
import shutil
import datetime
import inspect
import subprocess

from model_utils import get_best_model, resolve_dynamic_model
from core.llm_engine import LLMEngine
from core.skill_registry import check_skill_exists, register_skill
from core.utils import resolve_skill_paths, safe_id


def log(step, msg):
    print(f"[{step}] {msg}")


FACTORY_ROOT = os.getcwd()
AGENT_PROJECT_ROOT = os.getenv("AGENT_PROJECT_ROOT")

if AGENT_PROJECT_ROOT:
    AGENTS_DIR = os.path.join(AGENT_PROJECT_ROOT, "agents")
    FORGE_DIR = os.path.join(AGENT_PROJECT_ROOT, "skills", "forge")
else:
    AGENTS_DIR = os.path.join(FACTORY_ROOT, "agents")
    FORGE_DIR = os.path.join(FACTORY_ROOT, "skills", "forge")

WAREHOUSE_DIR = os.path.join(FACTORY_ROOT, "skills", "warehouse")
ANTIGRAVITY_REPO_URL = "https://github.com/guanyang/antigravity-skills.git"


# =============================================================================
# Warehouse Sync
# =============================================================================
def sync_warehouse():
    log("WAREHOUSE", "Syncing...")
    if not os.path.exists(WAREHOUSE_DIR):
        try:
            subprocess.run(["git", "clone", ANTIGRAVITY_REPO_URL, WAREHOUSE_DIR], check=True)
            log("WAREHOUSE", "Download complete")
        except Exception as e:
            log("WAREHOUSE", f"Download failed: {e}")
    else:
        try:
            subprocess.run(["git", "-C", WAREHOUSE_DIR, "pull"], check=True)
            log("WAREHOUSE", "Update complete")
        except Exception as e:
            log("WAREHOUSE", f"Update failed (local mode): {e}")


def snapshot_registry():
    registry_path = os.path.join(FACTORY_ROOT, "registry.yaml")
    if os.path.exists(registry_path):
        backup_dir = os.path.join(FACTORY_ROOT, "backup_registry")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"registry_{timestamp}.yaml")
        try:
            shutil.copy2(registry_path, backup_path)
            log("BACKUP", f"Registry snapshot created: {backup_path}")
        except Exception as e:
            log("BACKUP", f"Snapshot failed: {e}")


# =============================================================================
# Skill ID Helpers
# =============================================================================
def normalize_skill_id(value):
    base = os.path.splitext(os.path.basename(str(value)))[0].strip().lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return base


def get_installed_skill_ids(agent_name):
    tools_dir = os.path.join(AGENTS_DIR, agent_name, "tools")
    if not os.path.exists(tools_dir):
        return set()
    installed = set()
    for path in glob.glob(os.path.join(tools_dir, "*.py")):
        sid = normalize_skill_id(path)
        if sid:
            installed.add(sid)
    return installed


def get_missing_skills(agent_name, required_skills):
    normalized_required = []
    for raw in required_skills:
        sid = normalize_skill_id(raw)
        if sid and sid not in normalized_required:
            normalized_required.append(sid)

    installed = get_installed_skill_ids(agent_name)
    missing = [sid for sid in normalized_required if sid not in installed]

    log("CHECK", f"required={normalized_required}")
    log("CHECK", f"installed={sorted(installed)}")
    log("CHECK", f"missing={missing}")
    return missing


# =============================================================================
# Procurement & Forging
# =============================================================================
def procure_skill(skill_name, role, skill_type="action"):
    """기존 스킬 검색 → warehouse → forge 순서로 스킬 조달"""
    purpose_desc = f"Skill intended for {role} to handle {skill_name}"
    existing_skill_path = check_skill_exists(skill_name, purpose_desc)

    if existing_skill_path and os.path.exists(existing_skill_path):
        log("REGISTRY", f"Reusing existing skill: {existing_skill_path}")
        return existing_skill_path

    if skill_type == "action":
        found = glob.glob(os.path.join(WAREHOUSE_DIR, "**", f"{skill_name}.py"), recursive=True)
        if found:
            register_skill(skill_name, purpose_desc, found[0], stype="action")
            return found[0]

        forge_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
        if os.path.exists(forge_path):
            register_skill(skill_name, purpose_desc, forge_path, stype="action")
            return forge_path
    else:
        # Knowledge: 디렉토리 기반 탐색
        for base in [WAREHOUSE_DIR, FORGE_DIR]:
            md_path = os.path.join(base, skill_name, "skill.md")
            if os.path.exists(md_path):
                register_skill(skill_name, purpose_desc, md_path, stype="knowledge")
                return md_path

    return forge_new_skill(skill_name, role, skill_type=skill_type)


def forge_new_skill(skill_name, role, coding_engine=None, skill_type="action"):
    """LLM으로 새 스킬(코드 또는 지식 문서)을 생성(포징)"""
    if coding_engine is None:
        sel = resolve_dynamic_model("codex")
        coding_engine = sel.model if hasattr(sel, "model") else str(sel)
    elif hasattr(coding_engine, "model"):
        coding_engine = coding_engine.model

    log("FORGE", f"Forging new {skill_type} skill: '{skill_name}' (Engine: {coding_engine})")
    os.makedirs(FORGE_DIR, exist_ok=True)

    llm = LLMEngine(model_name=get_best_model([coding_engine]))

    if skill_type == "action":
        output_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
        prompt = (
            f"Write a professional Python CLI tool '{skill_name}.py' for the role '{role}'. "
            "Use argparse. Provide clean, robust code only. "
            "Code docstrings and user output MUST be in Korean. Return ONLY the python code."
        )
        try:
            code = llm.generate(prompt)
            code = code.replace("```python", "").replace("```", "").strip()
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(code)
            log("FORGE", f"Action forge complete: {output_path}")
            register_skill(skill_name, f"Dynamically forged action skill for {role}", output_path, stype="action")
            return output_path
        except Exception as e:
            log("FORGE", f"Action forge failed: {e}")
            return None
    else:
        # Knowledge skill (Markdown) — 사용자가 편집 가능한 절차적 지식 문서
        skill_dir = os.path.join(FORGE_DIR, skill_name)
        os.makedirs(skill_dir, exist_ok=True)
        output_path = os.path.join(skill_dir, "skill.md")

        prompt = f"""\
Create a Knowledge Guide (Markdown) for the role '{role}' about '{skill_name}'.
The guide should contain specific steps, checklists, or procedural knowledge.
Return ONLY the markdown content with the following YAML frontmatter at the top:
---
name: "{skill_name}"
description: "Brief summary of what this guide covers"
---

# {skill_name} 가이드
(본문 내용은 한국어로 작성하세요)"""
        try:
            md_content = llm.generate(prompt)
            md_content = md_content.replace("```markdown", "").replace("```", "").strip()
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            log("FORGE", f"Knowledge forge complete: {output_path}")
            register_skill(skill_name, f"Dynamically forged knowledge skill for {role}", output_path, stype="knowledge")
            return output_path
        except Exception as e:
            log("FORGE", f"Knowledge forge failed: {e}")
            return None


# =============================================================================
# SkillOrchestrator — 대량 조달 + 빌더 연동
# =============================================================================
class SkillOrchestrator:
    def __init__(self, registry, research_agent, builder, agent_mgr):
        self.registry = registry
        self.research = research_agent
        self.builder = builder
        self.agent_mgr = agent_mgr

    def procure_multiple(
        self,
        agent,
        skill_names,
        reqs,
        run_id,
        execution_mode="approval",
        approval_gate=None,
        workspace: str | None = None,
    ):
        installed: list[str] = []
        built_metas: list[dict] = []
        targets = [normalize_skill_id(name) for name in skill_names if normalize_skill_id(name)]
        if not targets:
            return installed

        try:
            research_bundle = self.research.research(agent, reqs, build_targets=targets) if self.research else {}
        except Exception as e:
            log("RESEARCH", f"Research failed: {e}")
            research_bundle = {}

        evidence_pack = research_bundle.get("evidence_pack", {}) if isinstance(research_bundle, dict) else {}
        evidence_targets = evidence_pack.get("targets", {}) if isinstance(evidence_pack, dict) else {}
        auto_approve = execution_mode == "fsa"

        for name in targets:
            evidence = evidence_targets.get(name, {}) if isinstance(evidence_targets, dict) else {}
            candidate_id = normalize_skill_id(str(evidence.get("top_candidate", "")))
            candidate_path = resolve_skill_paths(candidate_id)[0] if candidate_id else None
            verified_candidate = bool(candidate_id and evidence.get("verified") and candidate_path)

            action = "install" if verified_candidate else "build"
            approval_target = candidate_id or name
            if approval_gate and not approval_gate(agent.get("role"), [approval_target], action, auto_approve):
                continue

            if verified_candidate:
                if hasattr(self.registry, "ensure_lock_for_existing_skill"):
                    self.registry.ensure_lock_for_existing_skill(candidate_id)
                installable = True
                if hasattr(self.registry, "is_installable"):
                    installable = bool(self.registry.is_installable(candidate_id))
                if installable:
                    installed.append(candidate_id)
                    continue

            ok, code_path, meta = self.builder.build_skill(
                agent=agent,
                skill_name=name,
                reqs=reqs,
                run_id=run_id,
                evidence_pack=evidence_pack,
            )

            if not ok or not code_path or not isinstance(meta, dict):
                continue

            self.registry.register_built(meta, os.path.dirname(code_path))
            built_metas.append(meta)

            built_id = safe_id(str(meta.get("id") or name))
            installable = True
            if hasattr(self.registry, "is_installable"):
                installable = bool(self.registry.is_installable(built_id))
            if installable:
                installed.append(built_id)

        if built_metas and hasattr(self.registry, "workflow_apply"):
            self.registry.workflow_apply(built_metas)
        if installed:
            params = inspect.signature(self.agent_mgr.install_skills).parameters
            install_args = [agent.get("role"), list(dict.fromkeys(installed))]
            if "workspace" in params:
                self.agent_mgr.install_skills(*install_args, workspace=workspace)
            else:
                self.agent_mgr.install_skills(*install_args)
        return list(dict.fromkeys(installed))
