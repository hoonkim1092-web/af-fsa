from __future__ import annotations

import datetime
import glob
import inspect
import os
import re
import shutil
import subprocess

from core.policy import resolve_quality_gate_policy
from core.skill_eval_harness import SkillEvalHarness
from core.skill_feedback import SkillFeedbackLoop
from core.skill_promotion import SkillPromotionManager
from core.skill_registry import check_skill_exists, register_skill
from core.skill_retrieval_engine import SkillRetrievalEngine
from core.utils import now_iso, resolve_knowledge_skill_path, resolve_skill_paths, safe_id, skill_markdown_filenames


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



def log(step, msg):
    print(f"[{step}] {msg}")



def sync_warehouse():
    log("WAREHOUSE", "Syncing...")
    if not os.path.exists(WAREHOUSE_DIR):
        try:
            subprocess.run(["git", "clone", ANTIGRAVITY_REPO_URL, WAREHOUSE_DIR], check=True)
            log("WAREHOUSE", "Download complete")
        except Exception as exc:
            log("WAREHOUSE", f"Download failed: {exc}")
    else:
        try:
            subprocess.run(["git", "-C", WAREHOUSE_DIR, "pull"], check=True)
            log("WAREHOUSE", "Update complete")
        except Exception as exc:
            log("WAREHOUSE", f"Update failed (local mode): {exc}")



def snapshot_registry():
    registry_path = os.path.join(FACTORY_ROOT, "registry.yaml")
    if not os.path.exists(registry_path):
        return
    backup_dir = os.path.join(FACTORY_ROOT, "backup_registry")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"registry_{timestamp}.yaml")
    try:
        shutil.copy2(registry_path, backup_path)
        log("BACKUP", f"Registry snapshot created: {backup_path}")
    except Exception as exc:
        log("BACKUP", f"Snapshot failed: {exc}")



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



def procure_skill(skill_name, role, skill_type="action"):
    """Find an existing skill or forge a new one."""
    purpose_desc = f"Skill intended for {role} to handle {skill_name}"
    # check_skill_exists() returns bool (not a path) — look up the actual path separately
    if check_skill_exists(skill_name):
        existing_skill_path, _ = resolve_skill_paths(skill_name)
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
        for base in [WAREHOUSE_DIR, FORGE_DIR]:
            for filename in skill_markdown_filenames():
                md_path = os.path.join(base, skill_name, filename)
                if os.path.exists(md_path):
                    register_skill(skill_name, purpose_desc, md_path, stype="knowledge")
                    return md_path

    return forge_new_skill(skill_name, role, skill_type=skill_type)



def forge_new_skill(skill_name, role, coding_engine=None, skill_type="action"):
    """Forge a new skill via the legacy dynamic path."""
    from model_utils import get_best_model, resolve_dynamic_model
    from core.llm_engine import LLMEngine

    if coding_engine is None:
        selected = resolve_dynamic_model("codex")
        coding_engine = selected.model if hasattr(selected, "model") else str(selected)
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
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(code)
            log("FORGE", f"Action forge complete: {output_path}")
            register_skill(skill_name, f"Dynamically forged action skill for {role}", output_path, stype="action")
            return output_path
        except Exception as exc:
            log("FORGE", f"Action forge failed: {exc}")
            return None

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
        for fname in ("SKILL.md", "skill.md"):
            output_path = os.path.join(skill_dir, fname)
            if os.path.exists(output_path):
                log("FORGE", f"Knowledge forge complete (skill_creator): {output_path}")
                register_skill(skill_name, f"Dynamically forged knowledge skill for {role}", output_path, stype="knowledge")
                return output_path
    log("FORGE", "Knowledge forge failed via skill_creator")
    return None


class SkillOrchestrator:
    def __init__(self, registry, research_agent, builder, agent_mgr):
        self.registry = registry
        self.research = research_agent
        self.builder = builder
        self.agent_mgr = agent_mgr
        self.retrieval_engine = SkillRetrievalEngine()

    @staticmethod
    def _feedback_loop(workspace: str | None) -> SkillFeedbackLoop:
        return SkillFeedbackLoop.for_workspace(workspace)

    @staticmethod
    def _record_feedback(callback, *, context: str):
        try:
            callback()
        except Exception as exc:
            log("FEEDBACK", f"{context} failed: {exc}")

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

    @staticmethod
    def _log_reuse_decision(skill_name: str, decision, candidate_path: str | None = None, outcome: str = "decision"):
        candidate = decision.candidate_skill_id or "none"
        suffix = f", path={candidate_path}" if candidate_path else ""
        log(
            "REUSE",
            (
                f"{outcome} for '{skill_name}': mode={decision.mode}, candidate={candidate}, "
                f"confidence={decision.confidence:.2f}, score={decision.score}{suffix}"
            ),
        )

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

    def _default_build_stage(self, meta: dict) -> str:
        stage = safe_id(str((meta or {}).get("lifecycle_stage") or (meta or {}).get("status") or ""))
        if stage and stage != "draft":
            return stage

        if hasattr(self.registry, "apply_quality_gate"):
            try:
                gated = self.registry.apply_quality_gate(meta or {})
            except Exception:
                gated = {}
            if isinstance(gated, dict):
                gated_stage = safe_id(str(gated.get("lifecycle_stage") or gated.get("status") or ""))
                if gated_stage:
                    return gated_stage

        try:
            from core.registry_manager import read_project_policies as _read_project_policies

            quality_gate = resolve_quality_gate_policy(_read_project_policies())
        except Exception:
            quality_gate = resolve_quality_gate_policy({})
        return safe_id(str(quality_gate.get("default_stage_on_build") or "draft")) or "draft"

    def _evaluate_and_promote_built_skill(
        self,
        *,
        skill_name: str,
        code_path: str,
        meta: dict,
        feedback_loop: SkillFeedbackLoop,
        workspace: str | None,
    ) -> dict:
        if not code_path or not isinstance(meta, dict):
            return meta

        skill_id = safe_id(str(meta.get("id") or skill_name))
        evals_path = str(meta.get("evals_path") or "").strip()
        if evals_path and not os.path.exists(evals_path):
            evals_path = ""

        baseline_skill_path = ""
        reference_candidate_id = safe_id(str(meta.get("reference_candidate_id") or ""))
        if reference_candidate_id:
            baseline_skill_path, _meta_path = resolve_skill_paths(reference_candidate_id)
            baseline_skill_path = baseline_skill_path or ""

        feedback_path = str(getattr(feedback_loop, "feedback_path", "") or "")
        runs_dir = os.path.join(os.path.abspath(workspace), "runs") if workspace else None
        current_stage = self._default_build_stage(meta)

        try:
            eval_report = SkillEvalHarness().evaluate(
                code_path,
                evals_path=evals_path or None,
                baseline_skill_path=baseline_skill_path or None,
                feedback_path=feedback_path or None,
                runs_dir=runs_dir,
            )
            decision = SkillPromotionManager().apply(
                skill_id,
                eval_report,
                current_stage=current_stage,
                feedback_loop=feedback_loop,
            )
        except Exception as exc:
            log("EVAL", f"Post-build eval/promotion skipped for '{skill_name}': {exc}")
            fallback = dict(meta)
            fallback["status"] = current_stage
            fallback["lifecycle_stage"] = current_stage
            fallback["quality_stage"] = current_stage
            return fallback

        promoted = dict(meta)
        next_stage = safe_id(str(getattr(decision, "next_stage", "") or current_stage)) or current_stage
        promoted["status"] = next_stage
        promoted["lifecycle_stage"] = next_stage
        promoted["quality_stage"] = next_stage
        promoted["installable"] = bool(getattr(decision, "installable", False))
        promoted["last_eval_report"] = str(getattr(eval_report, "report_path", "") or "")
        promoted["last_promotion_report"] = str(getattr(decision, "promotion_path", "") or "")
        promoted["promotion_reason"] = str(getattr(decision, "reason", "") or "")
        promoted["promotion_updated_at"] = now_iso()
        log("EVAL", f"Promoted built skill '{skill_name}' to '{next_stage}' (installable={bool(getattr(decision, 'installable', False))})")
        return promoted

    def _record_selection_feedback(
        self,
        feedback_loop: SkillFeedbackLoop,
        *,
        skill_id: str,
        decision_mode: str,
        status: str,
        run_id: str,
        agent_role: str,
        decision=None,
        payload: dict | None = None,
    ):
        decision_confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
        decision_score = float(getattr(decision, "score", 0.0) or 0.0)
        decision_candidate = str(getattr(decision, "candidate_skill_id", "") or "")
        self._record_feedback(
            lambda: feedback_loop.record_selection(
                skill_id=skill_id,
                decision_mode=decision_mode,
                status=status,
                run_id=run_id,
                agent_role=agent_role,
                candidate_skill_id=decision_candidate,
                confidence=decision_confidence,
                score=decision_score,
                payload=payload or {},
            ),
            context=f"selection:{skill_id}:{decision_mode}",
        )

    def _maybe_install_skill(self, agent: dict, skill_id: str, approval_gate, auto_approve: bool) -> bool:
        if approval_gate and not approval_gate(agent.get("role"), [skill_id], "install", auto_approve):
            return False
        if hasattr(self.registry, "ensure_lock_for_existing_skill"):
            self.registry.ensure_lock_for_existing_skill(skill_id)
        installable = True
        if hasattr(self.registry, "is_installable"):
            installable = bool(self.registry.is_installable(skill_id))
        return installable

    def _build_and_register(
        self,
        *,
        agent: dict,
        skill_name: str,
        reqs: dict,
        run_id: str,
        evidence_pack: dict,
        built_metas: list[dict],
        feedback_loop: SkillFeedbackLoop,
        workspace: str | None,
    ) -> str | None:
        ok, code_path, meta = self.builder.build_skill(
            agent=agent,
            skill_name=skill_name,
            reqs=reqs,
            run_id=run_id,
            evidence_pack=evidence_pack,
        )
        if ok and code_path and isinstance(meta, dict):
            meta = self._evaluate_and_promote_built_skill(
                skill_name=skill_name,
                code_path=code_path,
                meta=meta,
                feedback_loop=feedback_loop,
                workspace=workspace,
            )
        self._log_build_outcome(skill_name, ok, code_path, meta)

        stage = ""
        if isinstance(meta, dict):
            stage = str(meta.get("lifecycle_stage") or meta.get("status") or "")
        reason, detail = self._build_failure_info(meta)
        self._record_feedback(
            lambda: feedback_loop.record_build(
                skill_id=str(meta.get("id") or skill_name) if isinstance(meta, dict) else skill_name,
                ok=ok,
                run_id=run_id,
                agent_role=str(agent.get("role") or ""),
                lifecycle_stage=stage,
                payload={
                    "requested_skill_id": safe_id(skill_name),
                    "code_path": code_path or "",
                    "reason": reason,
                    "detail": detail,
                },
            ),
            context=f"build:{skill_name}",
        )

        if not ok or not code_path or not isinstance(meta, dict):
            return None
        self.registry.register_built(meta, os.path.dirname(code_path))
        built_metas.append(meta)
        return safe_id(str(meta.get("id") or skill_name))

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

        feedback_loop = self._feedback_loop(workspace)
        agent_role = str(agent.get("role") or "")

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
        except Exception as exc:
            log("RESEARCH", f"Research failed: {exc}")
            research_bundle = {}

        evidence_pack = research_bundle.get("evidence_pack", {}) if isinstance(research_bundle, dict) else {}
        evidence_targets = evidence_pack.get("targets", {}) if isinstance(evidence_pack, dict) else {}
        auto_approve = execution_mode == "fsa"

        for name in targets:
            if name in exact_matches:
                install_ok = self._maybe_install_skill(agent, name, approval_gate, auto_approve)
                if install_ok:
                    installed.append(name)
                self._record_selection_feedback(
                    feedback_loop,
                    skill_id=name,
                    decision_mode="exact_match",
                    status="installed" if install_ok else "skipped",
                    run_id=run_id,
                    agent_role=agent_role,
                    payload={"path": exact_matches[name]},
                )
                continue

            evidence = evidence_targets.get(name, {}) if isinstance(evidence_targets, dict) else {}
            if not isinstance(evidence, dict):
                evidence = {}
            decision = self.retrieval_engine.decide_reuse(name, evidence, feedback_loop=feedback_loop)
            evidence["reuse_decision"] = decision.to_dict()
            candidate_id = decision.candidate_skill_id
            candidate_path = _resolve_available_skill_path(candidate_id) if candidate_id else None

            if decision.mode == "ranked_reuse" and candidate_id and candidate_path:
                self._log_reuse_decision(name, decision, candidate_path, outcome="reuse")
                install_ok = self._maybe_install_skill(agent, candidate_id, approval_gate, auto_approve)
                self._record_selection_feedback(
                    feedback_loop,
                    skill_id=name,
                    decision_mode=decision.mode,
                    status="installed" if install_ok else "skipped",
                    run_id=run_id,
                    agent_role=agent_role,
                    decision=decision,
                    payload={"candidate_path": candidate_path, "installed_skill_id": candidate_id},
                )
                if install_ok:
                    installed.append(candidate_id)
                    continue

            if decision.mode == "shadow_reuse" and candidate_id and candidate_path:
                self._log_reuse_decision(name, decision, candidate_path, outcome="adapt")
                self._record_selection_feedback(
                    feedback_loop,
                    skill_id=name,
                    decision_mode=decision.mode,
                    status="selected_for_build",
                    run_id=run_id,
                    agent_role=agent_role,
                    decision=decision,
                    payload={"candidate_path": candidate_path},
                )
                if approval_gate and not approval_gate(agent.get("role"), [name], "build", auto_approve):
                    self._record_selection_feedback(
                        feedback_loop,
                        skill_id=name,
                        decision_mode=decision.mode,
                        status="approval_denied",
                        run_id=run_id,
                        agent_role=agent_role,
                        decision=decision,
                        payload={"candidate_path": candidate_path},
                    )
                    continue
                built_id = self._build_and_register(
                    agent=agent,
                    skill_name=name,
                    reqs=reqs,
                    run_id=run_id,
                    evidence_pack=evidence_pack,
                    built_metas=built_metas,
                    feedback_loop=feedback_loop,
                    workspace=workspace,
                )
                if built_id:
                    installable = True
                    if hasattr(self.registry, "is_installable"):
                        installable = bool(self.registry.is_installable(built_id))
                    if installable:
                        installed.append(built_id)
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
            self._record_selection_feedback(
                feedback_loop,
                skill_id=name,
                decision_mode="external_install",
                status="installed" if external_skill_id else "miss",
                run_id=run_id,
                agent_role=agent_role,
                payload={
                    "installed_skill_id": external_skill_id,
                    "installed_from": str(external_result.get("installed_from") or ""),
                    "attempts": list(external_result.get("attempts") or []),
                },
            )
            if external_skill_id:
                installable = True
                if hasattr(self.registry, "is_installable"):
                    installable = bool(self.registry.is_installable(external_skill_id))
                if installable:
                    installed.append(external_skill_id)
                    continue

            if approval_gate and not approval_gate(agent.get("role"), [name], "build", auto_approve):
                self._record_selection_feedback(
                    feedback_loop,
                    skill_id=name,
                    decision_mode="forge",
                    status="approval_denied",
                    run_id=run_id,
                    agent_role=agent_role,
                )
                continue

            self._record_selection_feedback(
                feedback_loop,
                skill_id=name,
                decision_mode="forge",
                status="selected_for_build",
                run_id=run_id,
                agent_role=agent_role,
            )
            built_id = self._build_and_register(
                agent=agent,
                skill_name=name,
                reqs=reqs,
                run_id=run_id,
                evidence_pack=evidence_pack,
                built_metas=built_metas,
                feedback_loop=feedback_loop,
                workspace=workspace,
            )
            if built_id:
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


