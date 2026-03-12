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

from core.skill_registry import check_skill_exists, register_skill
from core.utils import resolve_knowledge_skill_path, resolve_skill_paths, safe_id, skill_markdown_filenames


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


def _resolve_available_skill_path(skill_id: str) -> str | None:
    skill_py, _skill_meta = resolve_skill_paths(skill_id)
    if skill_py:
        return skill_py
    return resolve_knowledge_skill_path(skill_id)


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
            for filename in skill_markdown_filenames():
                md_path = os.path.join(base, skill_name, filename)
                if os.path.exists(md_path):
                    register_skill(skill_name, purpose_desc, md_path, stype="knowledge")
                    return md_path

    return forge_new_skill(skill_name, role, skill_type=skill_type)


def forge_new_skill(skill_name, role, coding_engine=None, skill_type="action"):
    """LLM으로 새 스킬(코드 또는 지식 문서)을 생성(포징)"""
    from model_utils import get_best_model, resolve_dynamic_model
    from core.llm_engine import LLMEngine

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
        # Knowledge skill — skill_creator 모듈로 SKILL.md 기반 생성
        from core.skill_creator import create_skill as creator_create
        skill_dir = creator_create(
            name=skill_name,
            output_dir=FORGE_DIR,
            skill_type="knowledge",
            role=role,
            context=f"Dynamically forged knowledge skill for role '{role}'",
            use_llm=True,
            coding_engine=coding_engine,
        )
        if skill_dir:
            # SKILL.md 또는 skill.md 경로 탐색
            for fname in ("SKILL.md", "skill.md"):
                output_path = os.path.join(skill_dir, fname)
                if os.path.exists(output_path):
                    log("FORGE", f"Knowledge forge complete (skill_creator): {output_path}")
                    register_skill(skill_name, f"Dynamically forged knowledge skill for {role}", output_path, stype="knowledge")
                    return output_path
        log("FORGE", f"Knowledge forge failed via skill_creator")
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

    @staticmethod
    def _record_external_attempts(need_id: str, evidence_pack: dict, result: dict):
        if not isinstance(evidence_pack, dict) or not isinstance(result, dict):
            return
        targets = evidence_pack.get("targets", {})
        if not isinstance(targets, dict):
            return
        target = targets.get(need_id)
        if not isinstance(target, dict):
            return
        attempts = result.get("attempts", [])
        if isinstance(attempts, list):
            target["external_attempts"] = attempts
        installed_from = str(result.get("installed_from") or "").strip()
        if installed_from:
            target["external_installed_from"] = installed_from

    @classmethod
    def _record_external_skip(cls, need_id: str, evidence_pack: dict, source_id: str, reason: str):
        cls._record_external_attempts(
            need_id,
            evidence_pack,
            {
                "need_id": need_id,
                "installed_skill_id": "",
                "installed_from": "",
                "attempts": [
                    {
                        "source_id": source_id,
                        "status": "approval_rejected",
                        "reason": reason,
                    }
                ],
            },
        )

    @staticmethod
    def _extract_external_result(detail: dict, need_id: str) -> tuple[str, dict]:
        if not isinstance(detail, dict):
            return "", {}
        installed = detail.get("installed", {})
        results = detail.get("results", {})
        installed_skill_id = ""
        if isinstance(installed, dict):
            raw_installed = str(installed.get(need_id) or "").strip()
            installed_skill_id = safe_id(raw_installed) if raw_installed else ""
        result = results.get(need_id, {}) if isinstance(results, dict) else {}
        return installed_skill_id, result if isinstance(result, dict) else {}

    def _try_external_install(self, need_id: str, reqs: dict, evidence_pack: dict) -> tuple[str, dict]:
        if hasattr(self.registry, "resolve_and_install_external_detailed"):
            detail = self.registry.resolve_and_install_external_detailed(
                [need_id],
                reqs=reqs,
                evidence_pack=evidence_pack,
            )
            installed_skill_id, result = self._extract_external_result(detail, need_id)
            self._record_external_attempts(need_id, evidence_pack, result)
            return installed_skill_id, result

        if hasattr(self.registry, "resolve_and_install_external"):
            installed = self.registry.resolve_and_install_external(
                [need_id],
                reqs=reqs,
                evidence_pack=evidence_pack,
            )
            installed_skill_id = ""
            if isinstance(installed, dict):
                raw_installed = str(installed.get(need_id) or "").strip()
                installed_skill_id = safe_id(raw_installed) if raw_installed else ""
            result = {
                "need_id": need_id,
                "installed_skill_id": installed_skill_id,
                "installed_from": "external" if installed_skill_id else "",
                "attempts": [] if installed_skill_id else [
                    {
                        "source_id": "external",
                        "status": "miss",
                        "reason": "not_installed",
                    }
                ],
            }
            self._record_external_attempts(need_id, evidence_pack, result)
            return installed_skill_id, result

        return "", {}

    @staticmethod
    def _log_external_outcome(skill_name: str, result: dict):
        if not isinstance(result, dict):
            return
        installed_from = str(result.get("installed_from") or "").strip()
        if installed_from:
            log("EXTERNAL", f"Installed external skill for '{skill_name}' from '{installed_from}'")
            return

        attempts = result.get("attempts", [])
        if not isinstance(attempts, list) or not attempts:
            return

        reasons = []
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            source_id = safe_id(str(attempt.get("source_id") or "external")) or "external"
            status = str(attempt.get("status") or "unknown")
            reason = str(attempt.get("reason") or "no_reason")
            if status == "miss":
                reasons.append(f"{source_id}:miss")
            elif status != "installed":
                reasons.append(f"{source_id}:{reason}")
        if reasons:
            log("EXTERNAL", f"No reusable external skill for '{skill_name}' ({', '.join(reasons)})")

    @staticmethod
    def _build_failure_info(meta):
        if not isinstance(meta, dict):
            return "unknown", ""

        detail_meta = meta.get("last_test_detail")
        if not isinstance(detail_meta, dict):
            detail_meta = {}

        reason = str(detail_meta.get("reason") or meta.get("reason") or "unknown")
        detail = str(
            detail_meta.get("detail")
            or detail_meta.get("stderr")
            or meta.get("detail")
            or ""
        )[:240]
        return reason, detail

    def _log_build_outcome(self, skill_name, ok, code_path, meta):
        if ok and code_path and isinstance(meta, dict):
            built_id = safe_id(str(meta.get("id") or skill_name))
            log("BUILD", f"Built skill '{skill_name}' as '{built_id}'")
            return

        reason, detail = self._build_failure_info(meta)

        if reason == "no_api_key":
            log(
                "BUILD",
                (
                    f"Skipped build for '{skill_name}': no CLI provider configured and no Google API key "
                    "for SDK fallback. Set AGENT_CHAT_PROVIDER or AGENT_BUILDER_PROVIDER for CLI-only mode."
                ),
            )
            return

        if reason == "planning_first_violated":
            log("BUILD", f"Skipped build for '{skill_name}': planning-first gate rejected empty evidence")
            return

        if reason == "missing_evidence_pack":
            log("BUILD", f"Skipped build for '{skill_name}': research did not produce evidence for this target")
            return

        if reason == "guard_block":
            log("BUILD", f"Rejected generated code for '{skill_name}' via safety guard")
            return

        if reason == "builder_cli_failed" or reason.endswith("_cli"):
            suffix = f" ({detail})" if detail else ""
            log("BUILD", f"CLI build failed for '{skill_name}': {reason}{suffix}")
            return

        suffix = f" ({detail})" if detail else ""
        log("BUILD", f"Skill build failed for '{skill_name}': {reason}{suffix}")

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

        exact_matches: dict[str, str] = {}
        unresolved_targets: list[str] = []
        for name in targets:
            exact_path = _resolve_available_skill_path(name)
            if exact_path:
                exact_matches[name] = exact_path
            else:
                unresolved_targets.append(name)

        try:
            research_bundle = self.research.research(agent, reqs, build_targets=unresolved_targets) if (self.research and unresolved_targets) else {}
        except Exception as e:
            log("RESEARCH", f"Research failed: {e}")
            research_bundle = {}

        evidence_pack = research_bundle.get("evidence_pack", {}) if isinstance(research_bundle, dict) else {}
        evidence_targets = evidence_pack.get("targets", {}) if isinstance(evidence_pack, dict) else {}
        auto_approve = execution_mode == "fsa"

        for name in targets:
            if name in exact_matches:
                if approval_gate and not approval_gate(agent.get("role"), [name], "install", auto_approve):
                    continue
                if hasattr(self.registry, "ensure_lock_for_existing_skill"):
                    self.registry.ensure_lock_for_existing_skill(name)
                installable = True
                if hasattr(self.registry, "is_installable"):
                    installable = bool(self.registry.is_installable(name))
                if installable:
                    installed.append(name)
                    continue

            evidence = evidence_targets.get(name, {}) if isinstance(evidence_targets, dict) else {}
            raw_candidate = evidence.get("top_candidate")
            candidate_id = normalize_skill_id(raw_candidate) if raw_candidate else ""
            candidate_path = _resolve_available_skill_path(candidate_id) if candidate_id else None
            verified_candidate = bool(candidate_id and evidence.get("verified") and candidate_path)

            if verified_candidate:
                if approval_gate and not approval_gate(agent.get("role"), [candidate_id], "install", auto_approve):
                    continue
                if hasattr(self.registry, "ensure_lock_for_existing_skill"):
                    self.registry.ensure_lock_for_existing_skill(candidate_id)
                installable = True
                if hasattr(self.registry, "is_installable"):
                    installable = bool(self.registry.is_installable(candidate_id))
                if installable:
                    installed.append(candidate_id)
                    continue

            external_skill_id = ""
            external_result = {}
            external_install_allowed = True
            if approval_gate:
                external_install_allowed = approval_gate(agent.get("role"), [name], "install", auto_approve)

            if external_install_allowed:
                external_skill_id, external_result = self._try_external_install(name, reqs, evidence_pack)
            else:
                self._record_external_skip(name, evidence_pack, "approval_gate", "install_denied")
                external_result = {
                    "need_id": name,
                    "installed_skill_id": "",
                    "installed_from": "",
                    "attempts": [
                        {
                            "source_id": "approval_gate",
                            "status": "approval_rejected",
                            "reason": "install_denied",
                        }
                    ],
                }

            self._log_external_outcome(name, external_result)
            if external_skill_id:
                installable = True
                if hasattr(self.registry, "is_installable"):
                    installable = bool(self.registry.is_installable(external_skill_id))
                if installable:
                    installed.append(external_skill_id)
                    continue

            if approval_gate and not approval_gate(agent.get("role"), [name], "build", auto_approve):
                continue

            ok, code_path, meta = self.builder.build_skill(
                agent=agent,
                skill_name=name,
                reqs=reqs,
                run_id=run_id,
                evidence_pack=evidence_pack,
            )

            self._log_build_outcome(name, ok, code_path, meta)

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
