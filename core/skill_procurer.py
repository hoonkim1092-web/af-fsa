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
from core.utils import now_iso, read_skill_lock, resolve_knowledge_skill_path, resolve_skill_paths, safe_id, skill_markdown_filenames


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
    # registry 확인 — installable 상태도 검증 (§3.6)
    if check_skill_exists(skill_name):
        lock_state = read_skill_lock().get("skills", {}).get(safe_id(skill_name), {})
        if lock_state.get("installable", True):  # 기본값 True (기존 스킬 호환)
            existing_skill_path, _ = resolve_skill_paths(skill_name)
            if existing_skill_path and os.path.exists(existing_skill_path):
                log("REGISTRY", f"Reusing existing skill: {existing_skill_path}")
                return existing_skill_path

    if skill_type == "action":
        found = glob.glob(os.path.join(WAREHOUSE_DIR, "**", f"{skill_name}.py"), recursive=True)
        if found:
            register_skill(skill_name, purpose_desc, found[0], stype="action", source="warehouse")
            return found[0]

        # forge 경로: directory 구조 우선, flat fallback (§3.3)
        forge_path_dir = os.path.join(FORGE_DIR, skill_name, f"{skill_name}.py")
        forge_path_flat = os.path.join(FORGE_DIR, f"{skill_name}.py")
        for forge_path in [forge_path_dir, forge_path_flat]:
            if os.path.exists(forge_path):
                register_skill(skill_name, purpose_desc, forge_path, stype="action", source="forge")
                return forge_path
    else:
        for base in [WAREHOUSE_DIR, FORGE_DIR]:
            source = "warehouse" if base == WAREHOUSE_DIR else "forge"
            for filename in skill_markdown_filenames():
                md_path = os.path.join(base, skill_name, filename)
                if os.path.exists(md_path):
                    register_skill(skill_name, purpose_desc, md_path, stype="knowledge", source=source)
                    return md_path

    return forge_new_skill(skill_name, role, skill_type=skill_type)


# ── forge 전용 정책 (§3.10) ─────────────────────────────────────────────────

FORGE_POLICIES = {
    "quality_gate": {
        "installable_statuses": ["candidate", "canary", "active"],
        "default_stage_on_build": "draft",
    }
}

MAX_LLM_CALLS = 15


# ── Eval→Promotion 공통 함수 (§3.9) ─────────────────────────────────────────

def evaluate_and_promote(
    *,
    skill_name: str,
    code_path: str,
    evals_path: str = "",
    reference_candidate_id: str = "",
    feedback_loop: SkillFeedbackLoop | None = None,
    workspace: str | None = None,
    current_stage: str = "draft",
    project_policies: dict | None = None,
) -> dict:
    """Eval→Promotion 공통 실행. forge_new_skill()과 SkillOrchestrator 양쪽에서 사용."""
    skill_id = safe_id(skill_name)
    baseline_skill_path = ""
    if reference_candidate_id:
        bp, _ = resolve_skill_paths(reference_candidate_id)
        baseline_skill_path = bp or ""

    feedback_path = str(getattr(feedback_loop, "feedback_path", "") or "")
    runs_dir = os.path.join(os.path.abspath(workspace), "runs") if workspace else None

    try:
        eval_report = SkillEvalHarness().evaluate(
            code_path,
            evals_path=evals_path or None,
            baseline_skill_path=baseline_skill_path or None,
            feedback_path=feedback_path or None,
            runs_dir=runs_dir,
        )
        decision = SkillPromotionManager(project_policies=project_policies).apply(
            skill_id,
            eval_report,
            current_stage=current_stage,
            feedback_loop=feedback_loop,
        )
        return {
            "next_stage": getattr(decision, "next_stage", current_stage),
            "installable": bool(getattr(decision, "installable", False)),
            "eval_report_path": str(getattr(eval_report, "report_path", "") or ""),
            "promotion_report_path": str(getattr(decision, "promotion_path", "") or ""),
            "reason": str(getattr(decision, "reason", "") or ""),
        }
    except Exception as exc:
        log("EVAL", f"evaluate_and_promote failed for '{skill_name}': {exc}")
        return {
            "next_stage": current_stage,
            "installable": False,
            "eval_report_path": "",
            "promotion_report_path": "",
            "reason": f"eval_error: {exc}",
        }


# ── forge 헬퍼 함수 (§3.12) ─────────────────────────────────────────────────

def _prepare_forge_context(skill_name, role, coding_engine=None):
    """LLM 초기화 + 도메인 힌트 조달 + 프롬프트 구성."""
    from model_utils import get_best_model, resolve_dynamic_model
    from core.llm_engine import LLMEngine

    if coding_engine is None:
        selected = resolve_dynamic_model("codex")
        coding_engine = selected.model if hasattr(selected, "model") else str(selected)
    elif hasattr(coding_engine, "model"):
        coding_engine = coding_engine.model

    llm = LLMEngine(model_name=get_best_model([coding_engine]))

    # LLM 호출 budget 카운터 (§3.8)
    call_count = [0]

    def counted_generate(prompt_text):
        call_count[0] += 1
        if call_count[0] > MAX_LLM_CALLS:
            raise RuntimeError(f"LLM call budget exceeded: {call_count[0]} > {MAX_LLM_CALLS}")
        return llm.generate(prompt_text)

    # 도메인 힌트 조달 (§3.4) — warehouse 직접 탐색
    reference_candidate = None
    warehouse_matches = glob.glob(os.path.join(WAREHOUSE_DIR, "**", "*.py"), recursive=True)
    for match_path in warehouse_matches:
        match_name = os.path.splitext(os.path.basename(match_path))[0]
        if skill_name in match_name or match_name in skill_name:
            try:
                with open(match_path, encoding="utf-8") as f:
                    reference_candidate = {
                        "candidate_skill_id": match_name,
                        "candidate_path": match_path,
                        "confidence": 0.5,
                        "code_excerpt": f.read()[:5000],
                    }
                break
            except Exception:
                pass

    # 프롬프트 구성 (§3.1) — propose/apply/test 함수 기반
    prompt = (
        f"Write a Python skill module '{skill_name}.py' for the role '{role}'.\n"
        "The module MUST implement these three functions:\n"
        "  def propose(ctx: dict) -> dict:  # 실행 계획 제안. return {'ok': True/False, 'plan': ...}\n"
        "  def apply(ctx: dict) -> dict:    # ★ 핵심 실행 함수. return {'ok': True/False, 'result': ...}\n"
        "  def test(ctx: dict) -> dict:     # 자가 검증. return {'ok': True/False, 'details': ...}\n"
        "apply()가 메인 실행 함수이다. 핵심 로직은 반드시 apply()에 구현하라.\n"
        "ctx dict에는 'task', 'workspace', 'goal' 등의 키가 포함됩니다.\n"
        "Code docstrings and user output MUST be in Korean. Return ONLY the python code."
    )

    # LLM 사전 조회 fallback
    if not reference_candidate:
        try:
            hint = counted_generate(
                f"'{skill_name}' 스킬 구현에 필요한 Python 라이브러리와 핵심 패턴을 간략히 설명해."
            )
            if hint and hint.strip():
                prompt += f"\n\n도메인 힌트:\n{hint}"
        except Exception:
            pass

    # 스킬 디렉토리 생성 (§3.3)
    skill_dir = os.path.join(FORGE_DIR, skill_name)
    os.makedirs(skill_dir, exist_ok=True)
    output_path = os.path.join(skill_dir, f"{skill_name}.py")

    return {
        "llm": llm,
        "coding_engine": coding_engine,
        "counted_generate": counted_generate,
        "prompt": prompt,
        "reference_candidate": reference_candidate,
        "skill_dir": skill_dir,
        "output_path": output_path,
    }


def _generate_and_validate_evals(skill_dir, code, counted_generate):
    """evals.yml 생성 + 검증 + 재시도 (§3.5)."""
    import yaml
    from core.utils import strip_code_fences

    evals_prompt = (
        f"다음 스킬의 apply(ctx) 함수에 대한 "
        f"테스트 케이스 3~5개를 YAML로 작성해.\n\n"
        f"```python\n{code}\n```\n\n"
        "형식:\n"
        "contract:\n"
        "  - name: '케이스명'\n"
        "    ctx: {task: '...', workspace: '/tmp'}\n"
        "    expect_ok: true/false\n"
        "YAML만 반환. 코드 펜스 없이."
    )
    evals_text = counted_generate(evals_prompt)
    if not evals_text or not evals_text.strip():
        raise ValueError("evals 생성 실패: LLM 빈 출력")

    evals_text = strip_code_fences(evals_text)
    try:
        parsed = yaml.safe_load(evals_text)
    except yaml.YAMLError:
        parsed = None

    if isinstance(parsed, dict):
        contract_cases = parsed.get("contract") or parsed.get("cases") or []
    else:
        contract_cases = []

    MIN_EVAL_CASES = 3
    if not isinstance(contract_cases, list) or len(contract_cases) < MIN_EVAL_CASES:
        log("FORGE", f"Eval cases insufficient: {len(contract_cases) if isinstance(contract_cases, list) else 0} < {MIN_EVAL_CASES}, regenerating...")
        retry_prompt = (
            f"이전 시도에서 {len(contract_cases) if isinstance(contract_cases, list) else 0}개 케이스만 생성되었습니다. "
            f"최소 {MIN_EVAL_CASES}개 이상 반드시 생성하세요.\n\n{evals_prompt}"
        )
        evals_text = counted_generate(retry_prompt)
        evals_text = strip_code_fences(evals_text) if evals_text else ""
        try:
            parsed = yaml.safe_load(evals_text) or {}
        except yaml.YAMLError:
            parsed = {}
        contract_cases = parsed.get("contract") or parsed.get("cases") or []
        if not isinstance(contract_cases, list) or len(contract_cases) < MIN_EVAL_CASES:
            raise ValueError(f"evals 재생성 후에도 부족: {len(contract_cases) if isinstance(contract_cases, list) else 0} < {MIN_EVAL_CASES}")

    evals_path = os.path.join(skill_dir, "evals.yml")
    normalized = {"contract": contract_cases}
    with open(evals_path, "w", encoding="utf-8") as f:
        yaml.dump(normalized, f, allow_unicode=True, default_flow_style=False)

    return evals_path


def _evaluate_promote_and_register(skill_name, output_path, evals_path, skill_dir, ref_id):
    """Eval→Promotion→조건부 등록 (§3.6, §3.9, §3.10)."""
    from core.skill_registry import get_global_registry, SkillMetadata

    result = evaluate_and_promote(
        skill_name=skill_name,
        code_path=output_path,
        evals_path=evals_path,
        reference_candidate_id=ref_id,
        workspace=skill_dir,
        current_stage="draft",
        project_policies=FORGE_POLICIES,
    )

    if result["installable"]:
        purpose = f"Dynamically forged action skill: {skill_name}"
        register_skill(skill_name, purpose, output_path, stype="action", source="forge",
                       status=result["next_stage"])
        # 메모리 레지스트리 직접 갱신 — auto_load_from_directories는 forge를 SKIP함 (§3.6)
        try:
            meta = SkillMetadata(
                skill_id=safe_id(skill_name),
                name=skill_name,
                source_path=output_path,
                distribution_source="forge",
            )
            get_global_registry().register(meta)
        except Exception as exc:
            log("FORGE", f"Memory registry update failed (non-fatal): {exc}")
        log("FORGE", f"Forge complete + registered: {output_path} (stage={result['next_stage']})")
        return output_path

    log("FORGE", f"Forge complete but not installable: {skill_name} (stage={result['next_stage']}, reason={result['reason']})")
    return None


def forge_new_skill(skill_name, role, coding_engine=None, skill_type="action"):
    """Forge a new skill through the quality pipeline (Forge→Eval→Promotion)."""
    if skill_type != "action":
        return _forge_knowledge_skill(skill_name, role, coding_engine)

    log("FORGE", f"Forging new action skill: '{skill_name}' for role '{role}'")

    try:
        # §4 step 0~1: 준비
        ctx = _prepare_forge_context(skill_name, role, coding_engine)

        # §4 step 2~3: SkillForge 실행
        from core.skill_forge import SkillForge

        def _llm_generate_adapter(*, prompt, workspace, run_id, **kwargs):
            text = ctx["counted_generate"](prompt)
            if not text or not text.strip():
                raise ValueError(f"LLM returned blank output (run_id={run_id})")
            return text, {"run_id": run_id, "model": ctx["coding_engine"]}

        def _llm_text_adapter(*, prompt, system_prompt=None, workspace, run_id, **kwargs):
            full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            text = ctx["counted_generate"](full_prompt)
            return text, {"run_id": run_id, "model": ctx["coding_engine"]}

        forge = SkillForge(
            generate_code=_llm_generate_adapter,
            generate_text=_llm_text_adapter,
            max_repair_rounds=2,
        )

        run_id = f"forge_{skill_name}_{now_iso()}"
        result = forge.run(
            skill_id=skill_name,
            base_prompt=ctx["prompt"],
            workspace=ctx["skill_dir"],
            run_id=run_id,
            reference_candidate=ctx["reference_candidate"],
        )

        if result.critique and result.critique.should_repair:
            log("FORGE", f"Repair rounds exhausted, code still has issues: {skill_name}")

        # §4 step 4: 코드 저장
        code = result.code
        if not code or not code.strip():
            log("FORGE", f"SkillForge produced empty code: {skill_name}")
            return None

        with open(ctx["output_path"], "w", encoding="utf-8") as handle:
            handle.write(code)
        log("FORGE", f"Code saved: {ctx['output_path']}")

        # §4 step 5: evals 생성
        evals_path = _generate_and_validate_evals(ctx["skill_dir"], code, ctx["counted_generate"])

        # §4 step 6~7: eval→promotion→등록
        ref_id = ""
        if ctx["reference_candidate"]:
            ref_id = ctx["reference_candidate"].get("candidate_skill_id", "")

        return _evaluate_promote_and_register(skill_name, ctx["output_path"], evals_path, ctx["skill_dir"], ref_id)

    except Exception as exc:
        log("FORGE", f"Action forge failed: {exc}")
        return None


def _forge_knowledge_skill(skill_name, role, coding_engine=None):
    """Knowledge 스킬 forge — 기존 로직 유지."""
    from model_utils import resolve_dynamic_model
    from core.skill_creator import create_skill as creator_create

    if coding_engine is None:
        selected = resolve_dynamic_model("codex")
        coding_engine = selected.model if hasattr(selected, "model") else str(selected)
    elif hasattr(coding_engine, "model"):
        coding_engine = coding_engine.model

    log("FORGE", f"Forging new knowledge skill: '{skill_name}' (Engine: {coding_engine})")
    os.makedirs(FORGE_DIR, exist_ok=True)

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
                register_skill(skill_name, f"Dynamically forged knowledge skill for {role}", output_path, stype="knowledge", source="forge")
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
        """Eval→Promotion 실행. 내부적으로 evaluate_and_promote() 공통 함수에 위임."""
        if not code_path or not isinstance(meta, dict):
            return meta

        evals_path = str(meta.get("evals_path") or "").strip()
        if evals_path and not os.path.exists(evals_path):
            evals_path = ""

        reference_candidate_id = safe_id(str(meta.get("reference_candidate_id") or ""))
        current_stage = self._default_build_stage(meta)

        result = evaluate_and_promote(
            skill_name=skill_name,
            code_path=code_path,
            evals_path=evals_path,
            reference_candidate_id=reference_candidate_id,
            feedback_loop=feedback_loop,
            workspace=workspace,
            current_stage=current_stage,
        )

        promoted = dict(meta)
        next_stage = safe_id(str(result.get("next_stage") or current_stage)) or current_stage
        promoted["status"] = next_stage
        promoted["lifecycle_stage"] = next_stage
        promoted["quality_stage"] = next_stage
        promoted["installable"] = result.get("installable", False)
        promoted["last_eval_report"] = result.get("eval_report_path", "")
        promoted["last_promotion_report"] = result.get("promotion_report_path", "")
        promoted["promotion_reason"] = result.get("reason", "")
        promoted["promotion_updated_at"] = now_iso()
        log("EVAL", f"Promoted built skill '{skill_name}' to '{next_stage}' (installable={result.get('installable', False)})")
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

    @staticmethod
    def _try_enhance_skill(
        candidate_id: str,
        decision,
        *,
        workspace: str | None = None,
    ) -> dict:
        """SkillEnhancer를 사용하여 기존 스킬에 누락 capabilities 추가."""
        try:
            from core.skill_enhancer import SkillEnhancer

            gap = getattr(decision, "capability_gap", None)
            missing = list(getattr(gap, "missing_capabilities", [])) if gap else []
            if not missing:
                # capability 정보 없으면 enhance 자체가 의미 없음 → 바로 reuse
                return {"ok": True, "skill_id": candidate_id, "reason": "no_gap_reuse_as_is"}

            enhancer = SkillEnhancer()
            return enhancer.enhance(
                skill_name=candidate_id,
                missing_capabilities=missing,
                workspace=workspace,
            )
        except Exception as exc:
            log("ENHANCE", f"SkillEnhancer 실행 오류: {exc}")
            return {"ok": False, "skill_id": candidate_id, "reason": f"enhancer_exception:{exc}"}

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

            if decision.mode == "enhance" and candidate_id and candidate_path:
                self._log_reuse_decision(name, decision, candidate_path, outcome="enhance")
                self._record_selection_feedback(
                    feedback_loop,
                    skill_id=name,
                    decision_mode=decision.mode,
                    status="selected_for_enhance",
                    run_id=run_id,
                    agent_role=agent_role,
                    decision=decision,
                    payload={"candidate_path": candidate_path},
                )
                # enhance 시도
                enhanced = self._try_enhance_skill(
                    candidate_id, decision, workspace=workspace,
                )
                if enhanced.get("ok"):
                    enhanced_id = safe_id(str(enhanced.get("skill_id") or candidate_id))
                    install_ok = self._maybe_install_skill(agent, enhanced_id, approval_gate, auto_approve)
                    self._record_selection_feedback(
                        feedback_loop,
                        skill_id=name,
                        decision_mode="enhance",
                        status="enhanced_installed" if install_ok else "enhanced_skipped",
                        run_id=run_id,
                        agent_role=agent_role,
                        decision=decision,
                        payload={"enhanced_result": enhanced},
                    )
                    if install_ok:
                        installed.append(enhanced_id)
                        continue
                else:
                    # enhance 실패 → forge fallback
                    log("ENHANCE", f"enhance 실패 for '{name}': {enhanced.get('reason')}, forge로 fallback")
                    self._record_selection_feedback(
                        feedback_loop,
                        skill_id=name,
                        decision_mode="enhance_fallback_forge",
                        status="enhance_failed",
                        run_id=run_id,
                        agent_role=agent_role,
                        decision=decision,
                        payload={"enhance_reason": str(enhanced.get("reason", ""))},
                    )
                    # forge fallback은 아래 forge 블록에서 처리

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


