import inspect
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
    research_evidence: dict = field(default_factory=dict)
    research_evidence_path: str = ""
    # target_path가 있으면 문서는 그 경로에, 없으면 workspace에 생성
    doc_root: str = ""

    def _effective_doc_root(self) -> str:
        """work-item 문서가 실제로 저장된 루트 경로."""
        return self.doc_root if self.doc_root else self.workspace

    def work_item_dir(self) -> str:
        return os.path.join(self._effective_doc_root(), "docs", "work-items", self.work_item_slug)

    def gate(self) -> ApprovalGate:
        return ApprovalGate(self._effective_doc_root(), self.work_item_slug)

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
        import tempfile
        dir_ = os.path.dirname(path) or "."
        with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            tmp = f.name
        os.replace(tmp, path)

    def _planning_dir(self, workspace: str) -> str:
        planning_dir = os.path.join(workspace, "planning")
        os.makedirs(planning_dir, exist_ok=True)
        return planning_dir

    # ── Checkpoint helpers ──────────────────────────────────────────────

    def _checkpoint_dir(self, workspace: str) -> str:
        d = os.path.join(workspace, ".checkpoint")
        os.makedirs(d, exist_ok=True)
        return d

    def _save_checkpoint(self, workspace: str, stage: str, data: dict) -> None:
        """단계 완료 시 결과를 .checkpoint/{stage}.json에 저장."""
        import hashlib as _hl
        path = os.path.join(self._checkpoint_dir(workspace), f"{stage}.json")
        payload = {
            "stage": stage,
            "saved_at": now_iso(),
            "data_hash": _hl.md5(
                json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            "data": data,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"[Checkpoint] save failed for stage={stage}: {exc}")

    def _load_checkpoint(self, workspace: str, stage: str) -> dict | None:
        """체크포인트가 존재하면 data를 반환, 없으면 None."""
        path = os.path.join(self._checkpoint_dir(workspace), f"{stage}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            return payload.get("data")
        except Exception:
            return None

    # ── Structural Gate (Rubric-based) ─────────────────────────────────

    def run_structural_gate(self, artifact: dict, artifact_type: str = "architecture_plan") -> dict:
        """Rubric Compiler를 사용해 artifact를 평가하고 gate 결과를 반환.

        Returns:
            {"pass": bool, "status": str, "rubric_score": float,
             "errors": [...], "warnings": [...], "weakest_dimensions": [...]}
        """
        try:
            from core.rubric_compiler import RubricCompiler
            result = RubricCompiler().evaluate(artifact, artifact_type)
            return {
                "pass": result.status in ("pass", "pass_with_warnings"),
                "status": result.status,
                "rubric_score": result.total_score,
                "errors": result.errors,
                "warnings": result.warnings,
                "weakest_dimensions": result.weakest_dimensions(2),
                "dimension_scores": {
                    d.name: {"score": d.score, "raw": d.raw_score}
                    for d in result.dimension_scores
                },
            }
        except Exception as exc:
            print(f"[ProjectPipeline] rubric gate failed, falling back: {exc}")
            return self._basic_structural_check(artifact)

    def _basic_structural_check(self, artifact: dict) -> dict:
        errors, warnings = [], []
        if not artifact.get("goal"):
            errors.append("goal is missing")
        if not artifact.get("deliverables"):
            warnings.append("deliverables is empty")
        if not artifact.get("acceptance_criteria"):
            warnings.append("acceptance_criteria is empty")
        return {
            "pass": not errors,
            "status": "pass" if not errors else "fail",
            "rubric_score": 0.5,
            "errors": errors,
            "warnings": warnings,
            "weakest_dimensions": [],
        }

    # ── Gate B: iMAD Debate Necessity Classifier ────────────────────────

    def needs_agent_qa(
        self,
        critique_result: dict,
        structural_gate_result: dict,
        task_profile: dict | None = None,
    ) -> bool:
        """Agent QA가 필요한지 iMAD 패턴으로 판단한다."""
        tp = task_profile or {}
        if tp.get("complexity_tier") == "critical":
            return True
        if tp.get("execution_check_required"):
            return True
        if tp.get("risk_level") == "high":
            return True

        critique_score = float(critique_result.get("score") or 0.0)
        gate_pass = bool(structural_gate_result.get("pass"))
        confirmed_gaps = list(critique_result.get("confirmed_gaps") or [])
        gate_warnings = len(structural_gate_result.get("warnings") or [])

        if gate_pass and critique_score >= 0.85 and not confirmed_gaps:
            return False

        return (
            critique_score < 0.75
            or len(confirmed_gaps) > 0
            or gate_warnings > 2
        )

    # ── Gate A: Confidence Gate (Fast Path) ─────────────────────────────

    def should_fast_path(self, task_profile: dict, evidence_result: dict) -> bool:
        """Gate A: simple 요청을 Draft→Gate→Accept로 단축할지 판단한다."""
        tp = task_profile or {}
        ev = evidence_result or {}
        return (
            tp.get("complexity_tier") == "simple"
            and float(ev.get("score") or 0.0) >= 0.7
            and not tp.get("comparison_mode", False)
            and tp.get("risk_level", "normal") == "low"
        )

    # ── Convergence Detection ────────────────────────────────────────────

    def revision_loop_with_convergence(
        self,
        draft: dict,
        critique_feedback: dict,
        evidence: dict,
        rewrite_fn,
        score_fn,
        max_iterations: int = 5,
        convergence_threshold: float = 0.02,
        min_iterations: int = 2,
        critique_fn=None,
    ) -> tuple[dict, float, int]:
        """자가진화 revision loop: 비평 → 수정 → 재비평 → 수정.

        ISE StrategyLedger와 동일한 원리:
        - 매 iteration마다 새로운 비평 (critique_fn)
        - 이전 실패/무개선 시도 이력을 rewrite_fn에 주입
        - 같은 수정을 반복하지 않도록 학습

        critique_fn(artifact, evidence) → dict: 새 비평 결과
        없으면 초기 critique_feedback 재사용 (하위호환).

        Returns: (최종 artifact, 최종 score, 실제 반복 횟수)
        """
        current = dict(draft)
        previous_score = score_fn(current)
        live_feedback = dict(critique_feedback)

        if max_iterations <= 0:
            return current, previous_score, 0

        actual_iterations = 0
        consecutive_no_gain = 0
        best_artifact, best_score = dict(current), previous_score

        # ── 자가진화 이력: ISE StrategyLedger와 동일 패턴 ──
        revision_history: list[dict] = []

        for i in range(max_iterations):
            actual_iterations = i + 1

            # ── Step 1: 비평(critique) — 매 iteration마다 재평가
            if critique_fn is not None and actual_iterations > 1:
                try:
                    live_feedback = critique_fn(current, evidence)
                    print(f"[RevisionLoop] iter={actual_iterations} re-critique score={live_feedback.get('score', '?')}")
                except Exception as exc:
                    print(f"[RevisionLoop] critique_fn failed at iter {actual_iterations}: {exc}")

            # ── Step 2: 이전 시도 이력을 피드백에 주입
            if revision_history:
                live_feedback = dict(live_feedback)
                history_lines = []
                for h in revision_history[-3:]:
                    history_lines.append(
                        f"- iter {h['iter']}: score {h['score']:.3f} "
                        f"(delta {h['delta']:+.3f}) — {h['summary']}"
                    )
                live_feedback["_revision_history"] = (
                    "[이전 수정 시도 — 같은 접근을 반복하지 마세요]\n"
                    + "\n".join(history_lines)
                )

            # ── Step 3: 수정(rewrite) — 최신 비평 + 이력 기반
            try:
                revised = rewrite_fn(current, live_feedback, evidence)
            except Exception as exc:
                print(f"[RevisionLoop] rewrite_fn failed at iteration {actual_iterations}: {exc}")
                break

            new_score = score_fn(revised)
            delta = new_score - previous_score
            print(f"[RevisionLoop] iter={actual_iterations} score={new_score:.3f} delta={delta:+.3f}")

            # ── Step 4: 이력 기록 (자가진화 학습 데이터)
            revision_history.append({
                "iter": actual_iterations,
                "score": new_score,
                "delta": delta,
                "summary": "improved" if delta > 0 else "no gain" if delta == 0 else "regressed",
                "feedback_keys": list(live_feedback.get("confirmed_gaps", []))[:3],
            })

            if new_score > best_score:
                best_artifact, best_score = dict(revised), new_score
                consecutive_no_gain = 0
            else:
                consecutive_no_gain += 1

            # ── 수렴 판단: min_iterations 충족 후
            if actual_iterations >= min_iterations and consecutive_no_gain >= 2:
                print(f"[RevisionLoop] 수렴 — 연속 {consecutive_no_gain}회 무개선 (iter={actual_iterations})")
                break

            if actual_iterations >= min_iterations and delta < convergence_threshold:
                print(f"[RevisionLoop] 수렴 감지 (delta={delta:.3f} < {convergence_threshold})")
                break

            current, previous_score = revised, new_score

        return best_artifact, best_score, actual_iterations

    # ── Targeted Final Rewrite ───────────────────────────────────────────

    def targeted_rewrite(
        self,
        artifact: dict,
        qa_findings: list[str],
        critique_gaps: list[str],
        gate_warnings: list[str],
        rewrite_section_fn,
    ) -> dict:
        """영향받는 섹션만 재작성한다 (전체 재작성 대신)."""
        all_feedback = qa_findings + critique_gaps + gate_warnings
        if not all_feedback:
            return artifact

        _field_keywords = {
            "goal":                 ["goal", "목표", "요청", "정합성"],
            "deliverables":         ["deliverable", "산출물", "owner"],
            "risks":                ["risk", "위험", "mitigation", "대응"],
            "acceptance_criteria":  ["acceptance", "criteria", "검증", "테스트"],
            "roles":                ["role", "역할", "담당자"],
            "implementation_notes": ["implementation", "구현", "기술", "tech"],
            "evidence_summary":     ["evidence", "근거", "grounding", "출처"],
        }

        field_feedback_map: dict[str, list[str]] = {}
        for fb in all_feedback:
            fb_lower = fb.lower()
            for field_key, keywords in _field_keywords.items():
                if any(kw in fb_lower for kw in keywords):
                    field_feedback_map.setdefault(field_key, []).append(fb)

        if not field_feedback_map:
            return artifact

        revised = dict(artifact)
        for field_key, feedbacks in field_feedback_map.items():
            if field_key not in revised:
                continue
            try:
                revised[field_key] = rewrite_section_fn(field_key, revised[field_key], feedbacks)
            except Exception as exc:
                print(f"[TargetedRewrite] {field_key} failed: {exc}")

        revised["_rewrite_log"] = {
            "type": "targeted",
            "sections_modified": list(field_feedback_map.keys()),
            "feedback_count": len(all_feedback),
        }
        return revised

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
        risk_level = str((route or {}).get("risk_level") or "normal").strip()
        comparison_mode = bool((route or {}).get("comparison_mode", False))
        research_evidence: dict = {}
        collect_evidence = getattr(self.research, "collect_project_evidence", None)
        if callable(collect_evidence):
            from core.research_verifier import ResearchVerifier
            verifier = ResearchVerifier()
            try:
                _collect_kwargs = dict(
                    workspace=target_workspace,
                    risk_level=risk_level,
                    comparison_mode=comparison_mode,
                )
                import inspect as _inspect
                _ce_params = _inspect.signature(collect_evidence).parameters
                _supports_risk = "risk_level" in _ce_params

                def _evidence_fn():
                    kw = dict(_collect_kwargs)
                    if not _supports_risk:
                        kw.pop("risk_level", None)
                        kw.pop("comparison_mode", None)
                    try:
                        return collect_evidence(task_input, **kw) or {}
                    except Exception as exc:
                        print(f"[ProjectPipeline] collect_project_evidence failed: {exc}")
                        return {}

                research_evidence, _vr = verifier.verify_with_retry(
                    evidence_fn=_evidence_fn,
                    task_input=task_input,
                )
                if _vr.status != "pass":
                    print(
                        f"[ProjectPipeline] evidence quality={_vr.status} "
                        f"score={_vr.score} gaps={_vr.gaps}"
                    )
            except Exception as exc:
                # Graceful degradation: 로컬만으로 진행
                print(f"[ProjectPipeline] research verification failed: {exc}")
                try:
                    research_evidence = collect_evidence(task_input, workspace=target_workspace) or {}
                    research_evidence.setdefault("_warnings", []).append(f"evidence_degraded: {exc}")
                except Exception:
                    research_evidence = {"_stage_degraded": "evidence_acquisition", "_warnings": [str(exc)]}
        research_evidence_path = os.path.join(planning_dir, "research_evidence.json")
        self._write_json(research_evidence_path, research_evidence)
        self._save_checkpoint(target_workspace, "evidence_acquisition", research_evidence)

        # -- Brief (graceful degradation) --
        from core.pipeline_quality import PipelineStageGuard
        _guard = PipelineStageGuard()

        def _gen_brief():
            brief_params = inspect.signature(self.research.research_project_brief).parameters
            if "evidence_bundle" in brief_params:
                return self.research.research_project_brief(
                    research_agent, task_input,
                    workspace=target_workspace,
                    evidence_bundle=research_evidence,
                )
            return self.research.research_project_brief(
                research_agent, task_input, workspace=target_workspace,
            )

        project_brief = _guard.run(
            stage="draft_brief",
            fn=_gen_brief,
            fallback=lambda exc: {
                "goal": task_input[:200],
                "_stage_degraded": "draft_brief",
                "_warnings": [str(exc)],
            },
        )
        if not isinstance(project_brief, dict):
            project_brief = {"goal": task_input[:200]}
        project_brief["requested_role"] = requested_role
        project_brief["route"] = route or {}
        project_brief["generated_at"] = now_iso()
        project_brief_path = os.path.join(planning_dir, "project_brief.json")
        self._write_json(project_brief_path, project_brief)
        self._save_checkpoint(target_workspace, "draft_brief", project_brief)

        # -- Planning (graceful degradation) --
        pd_agent = build_bootstrap_agent("pd_director")

        def _gen_role_plan():
            raw = self.planner.plan(task_input, project_brief)
            return enrich_role_plan(task_input, project_brief, raw)

        role_plan = _guard.run(
            stage="role_planning",
            fn=_gen_role_plan,
            fallback=lambda exc: {
                "roles": [],
                "modules": [],
                "_stage_degraded": "role_planning",
                "_warnings": [str(exc)],
            },
        )
        if not isinstance(role_plan, dict):
            role_plan = {"roles": [], "modules": []}
        role_plan["generated_at"] = now_iso()
        role_plan["pd_agent"] = {
            "id": pd_agent["id"],
            "name": pd_agent["name"],
        }
        role_plan_path = os.path.join(planning_dir, "role_plan.json")
        self._write_json(role_plan_path, role_plan)
        self._save_checkpoint(target_workspace, "role_plan", role_plan)

        # -- Task Board --
        task_board = build_project_board(project_brief, role_plan)
        task_board_path = write_project_board(target_workspace, task_board)
        task_execution_plan_path = write_task_execution_plan(
            target_workspace, project_brief, role_plan, task_board
        )
        todo_path = self._write_todo(target_workspace, role_plan, task_board)

        # -- Work Items (★ 신규) --
        slug = slug_from_brief(project_brief)
        # target_path가 절대경로면 문서를 그 경로에 생성, 아니면 workspace 사용
        _raw_target = str(project_brief.get("target_path") or "").strip()
        doc_root = os.path.abspath(_raw_target) if (_raw_target and os.path.isabs(_raw_target)) else target_workspace
        work_item_files = generate_work_items(
            workspace=target_workspace,
            slug=slug,
            project_brief=project_brief,
            role_plan=role_plan,
            task_board=task_board,
        )

        planning_files = [
            to_portable_path(research_evidence_path),
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
            research_evidence=research_evidence,
            research_evidence_path=to_portable_path(research_evidence_path),
            doc_root=doc_root,
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
        from core.model_router import print_startup_routing_notice
        print_startup_routing_notice()

        task_input = str(prepared.project_brief.get("goal") or "")
        orchestrator = DynamicOrchestrator(
            self.mr, max_concurrent=5, terminal_per_agent=True,
            broker=self._broker, visualizer=self._visualizer
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
            "research_evidence_path": prepared.research_evidence_path,
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
