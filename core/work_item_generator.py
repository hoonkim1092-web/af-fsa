"""
Generate work-item markdown documents from planning artifacts.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Any

from core.approval_gate import ApprovalGate
from core.file_io import write_text
from core.utils import now_iso

TEMPLATE_DIR_REL = os.path.join("docs", "work-items", "_template")
WORK_ITEMS_DIR_REL = os.path.join("docs", "work-items")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_clean(v) for v in values if _clean(v)]


def _trim_text(value: Any, limit: int = 220) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _research_bullets(project_brief: dict[str, Any], limit: int = 8) -> str:
    lines: list[str] = []
    for item in _clean_list(project_brief.get("evidence_summary"))[:limit]:
        lines.append(f"- {item}")
    if not lines:
        for item in _clean_list(project_brief.get("research_notes"))[:limit]:
            lines.append(f"- {item}")
    notebook_summary = _trim_text(project_brief.get("notebook_summary"), limit=280)
    if notebook_summary and not any("NotebookLM" in line for line in lines):
        lines.append(f"- NotebookLM: {notebook_summary}")
    return "\n".join(lines[:limit]) if lines else "- (additional research needed)"


def _reference_bullets(project_brief: dict[str, Any], limit: int = 8) -> str:
    lines: list[str] = []
    seen: set[str] = set()

    for item in project_brief.get("local_references") or []:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get("path") or item.get("title"))
        detail = _trim_text(item.get("heading") or item.get("excerpt"), limit=180)
        if not label:
            continue
        line = f"- Local: {label}"
        if detail:
            line += f" | {detail}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
        if len(lines) >= limit:
            return "\n".join(lines)

    for item in project_brief.get("web_references") or []:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get("title") or item.get("url"))
        detail = _trim_text(item.get("excerpt"), limit=180)
        if not label:
            continue
        line = f"- Web: {label}"
        if detail:
            line += f" | {detail}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
        if len(lines) >= limit:
            return "\n".join(lines)

    return "\n".join(lines) if lines else "- (no additional references)"


def _slug_from_goal(goal: str) -> str:
    text = goal.lower()[:60]
    text = re.sub("[^a-z0-9\\uac00-\\ud7a3\\s]", " ", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "work-item"


def _make_checklist(tasks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    phase_order = {"scope": 0, "build": 1, "integrate": 2, "verify": 3}
    sorted_tasks = sorted(
        tasks,
        key=lambda item: (
            phase_order.get(_clean(item.get("phase") or "build"), 99),
            _clean(item.get("task_id") or ""),
        ),
    )
    for task in sorted_tasks:
        title = _clean(task.get("title") or task.get("instruction") or "")
        if not title:
            continue
        task_id = _clean(task.get("task_id") or "")
        owner = _clean(task.get("owner_role") or "")
        phase = _clean(task.get("phase") or "build")
        acceptance = _clean_list(task.get("acceptance"))
        artifacts = _clean_list(task.get("artifacts"))
        depends = _clean_list(task.get("depends_on"))

        lines.append(f"- [ ] {title}")
        if task_id:
            lines.append(f"  - task_id: {task_id}")
        if owner:
            lines.append(f"  - owner_role: {owner}")
        lines.append(f"  - phase: {phase}")
        if depends:
            lines.append(f"  - depends_on: {', '.join(depends)}")
        if acceptance:
            lines.append(f"  - acceptance: {'; '.join(acceptance)}")
        if artifacts:
            lines.append(f"  - artifacts: {', '.join(artifacts)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _generate_feature_plan(
    work_item: str,
    project_brief: dict[str, Any],
    role_plan: dict[str, Any],
) -> str:
    goal = _clean(project_brief.get("goal") or "")
    background_context = _clean(project_brief.get("background_context") or "")
    problem_statement = _clean(project_brief.get("problem_statement") or "")
    deliverables = _clean_list(project_brief.get("deliverables"))
    constraints = _clean_list(project_brief.get("constraints"))
    risks = _clean_list(project_brief.get("risks"))
    non_goals = _clean_list(project_brief.get("non_goals"))
    modules = [m for m in (role_plan.get("modules") or []) if isinstance(m, dict)]
    roles = [r for r in (role_plan.get("roles") or []) if isinstance(r, dict)]

    # Scope: module name + summary (1줄)
    scope_lines = "\n".join(
        f"- **{_clean(m.get('name'))}**: {_trim_text(m.get('summary'), 120)}"
        for m in modules if _clean(m.get("name"))
    )
    deliverables_lines = "\n".join(f"- {item}" for item in deliverables) if deliverables else "- (auto-generate needed)"

    # Non-Goals: brief에서 가져오고 없으면 placeholder
    non_goals_lines = "\n".join(f"- {item}" for item in non_goals) if non_goals else "- (edit required)"

    # Stakeholders: 역할 이름 + 목적
    stakeholder_lines = "\n".join(
        f"- {_clean(r.get('name') or r.get('id'))}: {_trim_text(r.get('objective'), 80)}"
        for r in roles if _clean(r.get("name") or r.get("id"))
    ) or "- (auto-generate needed)"

    # Success Metrics: deliverable별 측정 기준 (Goals와 다른 관점)
    metrics_lines = "\n".join(
        f"- {item} — 완성 및 동작 검증됨" for item in deliverables
    ) if deliverables else "- (edit required)"

    # Risks: brief.risks + constraints 합산
    risk_items = risks + [c for c in constraints if any(kw in c for kw in ["제약", "불가", "없이", "미연결", "캐시"])]
    risks_lines = "\n".join(f"- {item}" for item in risk_items) if risk_items else "\n".join(f"- {item}" for item in constraints) if constraints else "- none"

    research_lines = _research_bullets(project_brief)
    reference_lines = _reference_bullets(project_brief)

    return (
        "# Feature Plan\n\n"
        "## Metadata\n\n"
        f"- work_item: {work_item}\n"
        "- owner: (edit required)\n"
        "- status: draft\n"
        f"- last_updated: {now_iso()}\n\n"
        "## Background\n\n"
        f"{background_context or goal or '(additional summary needed)'}\n\n"
        "## Problem Statement\n\n"
        f"{problem_statement or '(edit required)'}\n\n"
        "## Goals\n\n"
        f"{deliverables_lines}\n\n"
        "## Non-Goals\n\n"
        f"{non_goals_lines}\n\n"
        "## Scope\n\n"
        f"{scope_lines or '- (auto-generate needed)'}\n\n"
        "## Stakeholders\n\n"
        f"{stakeholder_lines}\n\n"
        "## Success Metrics\n\n"
        f"{metrics_lines}\n\n"
        "## Risks and Assumptions\n\n"
        f"{risks_lines}\n\n"
        "## Evidence\n\n"
        f"{research_lines}\n\n"
        "## References\n\n"
        f"{reference_lines}\n\n"
        "## Approval Request\n\n"
        "- Review this scope and confirm approval-gate.md when ready.\n"
    )


def _generate_feature_spec(
    work_item: str,
    project_brief: dict[str, Any],
    role_plan: dict[str, Any],
    task_board: dict[str, Any],
) -> str:
    goal = _clean(project_brief.get("goal") or "")
    modules = [m for m in (role_plan.get("modules") or []) if isinstance(m, dict)]
    all_tasks = [t for t in (task_board.get("tasks") or []) if isinstance(t, dict)]
    user_flows = _clean_list(project_brief.get("user_flows"))
    constraints = _clean_list(project_brief.get("constraints"))
    non_goals = _clean_list(project_brief.get("non_goals"))
    data_model = project_brief.get("data_model") or []
    research_lines = _research_bullets(project_brief)
    reference_lines = _reference_bullets(project_brief)

    # User Scenarios: brief.user_flows 우선, 없으면 module summary 활용
    if user_flows:
        scenario_lines = [f"- {flow}" for flow in user_flows]
    else:
        scenario_lines = []
        for module in modules:
            summary = _clean(module.get("summary") or "")
            if summary:
                scenario_lines.append(f"- {summary}")
    scenario_text = "\n".join(scenario_lines) or "- (edit required)"

    # Functional Requirements: feature_slices (구체적 구현 단위)
    req_lines: list[str] = []
    for module in modules:
        name = _clean(module.get("name") or "")
        slices = _clean_list(module.get("feature_slices"))
        if slices:
            for item in slices:
                req_lines.append(f"- [{name}] {item}")
        elif name:
            summary = _clean(module.get("summary") or "")
            req_lines.append(f"- [{name}] {summary or name}")
    req_text = "\n".join(req_lines) or "- (auto-generate needed)"

    # Non-Functional Requirements: constraints 중 비기능 항목 추출
    nfr_keywords = ["성능", "속도", "보안", "안정", "오프라인", "캐시", "용량", "호환", "인터넷 없이", "단독 실행"]
    nfr_lines = [f"- {c}" for c in constraints if any(kw in c for kw in nfr_keywords)]
    nfr_text = "\n".join(nfr_lines) if nfr_lines else "- (edit required)"

    # Inputs / Outputs: data_model에서 생성
    io_lines: list[str] = []
    for entity in (data_model if isinstance(data_model, list) else []):
        if not isinstance(entity, dict):
            continue
        ename = _clean(entity.get("entity") or "")
        fields = _clean_list(entity.get("fields"))
        storage = _clean(entity.get("storage") or "")
        if ename:
            field_str = ", ".join(fields[:6]) if fields else "—"
            io_lines.append(f"- **{ename}** [{storage}]: {field_str}")
    io_text = "\n".join(io_lines) if io_lines else "- (edit required)"

    # Acceptance Criteria: build/verify 태스크의 acceptance만 (scope 제외)
    acceptance_lines: list[str] = []
    for task in all_tasks:
        if _clean(task.get("phase") or "") == "scope":
            continue
        for acc in _clean_list(task.get("acceptance")):
            bullet = f"- {acc}"
            if bullet not in acceptance_lines:
                acceptance_lines.append(bullet)
    acceptance_text = "\n".join(acceptance_lines) or "- (auto-generate needed)"

    # Out Of Scope
    out_of_scope_text = "\n".join(f"- {item}" for item in non_goals) if non_goals else "- (edit required)"

    return (
        "# Feature Spec\n\n"
        "## Metadata\n\n"
        f"- work_item: {work_item}\n"
        "- source_plan: feature-plan.md\n"
        "- status: draft\n"
        f"- last_updated: {now_iso()}\n\n"
        "## Feature Overview\n\n"
        f"{goal or '(edit required)'}\n\n"
        "## User Scenarios\n\n"
        f"{scenario_text}\n\n"
        "## Functional Requirements\n\n"
        f"{req_text}\n\n"
        "## Non-Functional Requirements\n\n"
        f"{nfr_text}\n\n"
        "## Inputs and Outputs\n\n"
        f"{io_text}\n\n"
        "## Exceptions and Failure Scenarios\n\n"
        "- (edit required)\n\n"
        "## Existing Behavior To Preserve\n\n"
        "- (edit required)\n\n"
        "## Acceptance Criteria\n\n"
        f"{acceptance_text}\n\n"
        "## Evidence\n\n"
        f"{research_lines}\n\n"
        "## References\n\n"
        f"{reference_lines}\n\n"
        "## Out Of Scope\n\n"
        f"{out_of_scope_text}\n"
    )


def _generate_implementation_design(
    work_item: str,
    project_brief: dict[str, Any],
    role_plan: dict[str, Any],
) -> str:
    goal = _clean(project_brief.get("goal") or "")
    modules = [m for m in (role_plan.get("modules") or []) if isinstance(m, dict)]
    execution_strategy = _clean(role_plan.get("execution_strategy") or "parallel")
    tech_stack = _clean_list(project_brief.get("tech_stack"))
    architecture_style = _clean(project_brief.get("architecture_style") or "")
    data_model = project_brief.get("data_model") or []
    risks = _clean_list(project_brief.get("risks"))
    constraints = _clean_list(project_brief.get("constraints"))
    research_lines = _research_bullets(project_brief)
    reference_lines = _reference_bullets(project_brief)

    # Design Summary: goal + architecture + tech stack
    tech_str = ", ".join(tech_stack) if tech_stack else ""
    design_summary_parts = [goal or "(edit required)"]
    if architecture_style:
        design_summary_parts.append(f"아키텍처: {architecture_style}")
    if tech_str:
        design_summary_parts.append(f"기술 스택: {tech_str}")
    design_summary_parts.append(f"실행 전략: {execution_strategy}")
    design_summary = "\n\n".join(design_summary_parts)

    # Planned Modules
    module_lines: list[str] = []
    for module in modules:
        name = _clean(module.get("name") or "")
        owner = _clean(module.get("owner_role") or "")
        summary = _clean(module.get("summary") or "")
        depends = _clean_list(module.get("depends_on"))
        delivs = _clean_list(module.get("deliverables"))
        slices = _clean_list(module.get("feature_slices"))
        if name:
            module_lines.append(f"### {name}")
            module_lines.append(f"- owner: {owner}")
            module_lines.append(f"- objective: {summary}")
            if depends:
                module_lines.append(f"- depends_on: {', '.join(depends)}")
            if delivs:
                module_lines.append(f"- deliverables: {', '.join(delivs)}")
            if slices:
                module_lines.append(f"- feature_slices: {', '.join(slices)}")
            module_lines.append("")
    module_text = "\n".join(module_lines) or "- (auto-generate needed)"

    # Data Flow: 실제 depends_on 기반 화살표
    # 먼저 id→name 맵 생성
    id_to_name = {_clean(m.get("id") or ""): _clean(m.get("name") or "") for m in modules if _clean(m.get("id") or "")}
    flow_lines: list[str] = []
    for module in modules:
        name = _clean(module.get("name") or "")
        depends = _clean_list(module.get("depends_on"))
        dep_names = [id_to_name.get(d, d) for d in depends if d]
        if dep_names:
            flow_lines.append(f"- {' + '.join(dep_names)} → **{name}**")
        else:
            flow_lines.append(f"- (시작) → **{name}**")
    flow_text = "\n".join(flow_lines) or "- (auto-generate needed)"

    # State / Data Model
    data_model_lines: list[str] = []
    for entity in (data_model if isinstance(data_model, list) else []):
        if not isinstance(entity, dict):
            continue
        ename = _clean(entity.get("entity") or "")
        fields = _clean_list(entity.get("fields"))
        storage = _clean(entity.get("storage") or "")
        if ename:
            field_str = ", ".join(fields) if fields else "—"
            data_model_lines.append(f"- **{ename}** ({storage}): {field_str}")
    data_model_text = "\n".join(data_model_lines) if data_model_lines else "- (edit required)"

    # Risks: brief.risks + 기술적 constraints
    risk_items = list(risks)
    for c in constraints:
        if any(kw in c for kw in ["불가", "없이", "캐시", "실패", "오류", "제약"]):
            risk_items.append(c)
    risks_text = "\n".join(f"- {r}" for r in risk_items) if risk_items else "- (edit required)"

    return (
        "# Implementation Design\n\n"
        "## Metadata\n\n"
        f"- work_item: {work_item}\n"
        "- spec_type: feature\n"
        "- source_spec: feature-spec.md\n"
        "- status: draft\n"
        f"- last_updated: {now_iso()}\n\n"
        "## Design Summary\n\n"
        f"{design_summary}\n\n"
        "## Planned Modules\n\n"
        f"{module_text}\n\n"
        "## Data Flow\n\n"
        f"{flow_text}\n\n"
        "## Interface Impact\n\n"
        "- (edit required)\n\n"
        "## State And Data Model\n\n"
        f"{data_model_text}\n\n"
        "## Compatibility Considerations\n\n"
        "- (edit required)\n\n"
        "## Migration Requirement\n\n"
        "- none\n\n"
        "## Risks\n\n"
        f"{risks_text}\n\n"
        "## Alternatives Considered\n\n"
        "- (edit required)\n\n"
        "## Design Evidence\n\n"
        f"{research_lines}\n\n"
        "## References\n\n"
        f"{reference_lines}\n\n"
        "## Test Strategy\n\n"
        "- Unit tests per module\n"
        "- Integration tests for cross-module flows\n"
    )


def _generate_implementation_tasks(
    work_item: str,
    role_plan: dict[str, Any],
    task_board: dict[str, Any],
    project_brief: dict[str, Any] | None = None,
) -> str:
    tasks = [t for t in (task_board.get("tasks") or []) if isinstance(t, dict)]
    modules = [m for m in (role_plan.get("modules") or []) if isinstance(m, dict)]
    preconditions = [
        _clean(step.get("name") or "")
        for step in (role_plan.get("planning_steps") or [])
        if isinstance(step, dict) and _clean(step.get("name"))
    ]
    brief = project_brief if isinstance(project_brief, dict) else {}
    research_lines = _research_bullets(brief)

    pre_lines = "\n".join(f"- {item}" for item in preconditions) if preconditions else "- none"
    blocked = [
        _clean(module.get("name") or "")
        for module in modules
        if isinstance(module, dict) and _clean_list(module.get("depends_on"))
    ]
    blocker_lines = "\n".join(f"- {item}" for item in blocked) if blocked else "- none"
    checklist = _make_checklist(tasks) or "- [ ] (edit required)"

    return (
        "# Implementation Tasks\n\n"
        "## Metadata\n\n"
        f"- work_item: {work_item}\n"
        "- source_design: implementation-design.md\n"
        "- status: draft\n"
        f"- last_updated: {now_iso()}\n\n"
        "## Preconditions\n\n"
        f"{pre_lines}\n\n"
        "## Task Evidence\n\n"
        f"{research_lines}\n\n"
        "## Task List\n\n"
        f"{checklist}\n\n"
        "## Blockers\n\n"
        f"{blocker_lines}\n\n"
        "## Rollback Sign-Off\n\n"
        "- Commit after each module-level implementation milestone\n\n"
        "## Definition Of Done\n\n"
        "- All task checkboxes are complete\n"
        "- verification-report.md captures the final outcome\n"
    )


def generate_work_items(
    workspace: str,
    slug: str,
    project_brief: dict[str, Any],
    role_plan: dict[str, Any],
    task_board: dict[str, Any],
) -> dict[str, str]:
    # target_path가 있으면 프로젝트 디렉토리에 문서를 생성하고,
    # 없으면 기존처럼 workspace(agent-factory 내부)에 생성한다.
    target_path = _clean(project_brief.get("target_path") or "")
    if target_path and os.path.isabs(target_path):
        doc_root = os.path.abspath(target_path)
    else:
        doc_root = os.path.abspath(workspace)
    work_dir = os.path.join(doc_root, WORK_ITEMS_DIR_REL, slug)
    os.makedirs(work_dir, exist_ok=True)

    template_dir = os.path.join(os.path.abspath(workspace), TEMPLATE_DIR_REL)
    _copy_extra_templates(template_dir, work_dir)

    files: dict[str, str] = {}
    work_item_id = slug

    plan_content = _generate_feature_plan(work_item_id, project_brief, role_plan)
    plan_path = os.path.join(work_dir, "feature-plan.md")
    write_text(plan_path, plan_content)
    files["feature-plan.md"] = plan_path

    spec_content = _generate_feature_spec(work_item_id, project_brief, role_plan, task_board)
    spec_path = os.path.join(work_dir, "feature-spec.md")
    write_text(spec_path, spec_content)
    files["feature-spec.md"] = spec_path

    design_content = _generate_implementation_design(work_item_id, project_brief, role_plan)
    design_path = os.path.join(work_dir, "implementation-design.md")
    write_text(design_path, design_content)
    files["implementation-design.md"] = design_path

    tasks_content = _generate_implementation_tasks(work_item_id, role_plan, task_board, project_brief=project_brief)
    tasks_path = os.path.join(work_dir, "implementation-tasks.md")
    write_text(tasks_path, tasks_content)
    files["implementation-tasks.md"] = tasks_path

    gate = ApprovalGate(workspace, slug)
    gate.initialize(work_item_id)
    files["approval-gate.md"] = gate.gate_path

    return files


def _copy_extra_templates(template_dir: str, work_dir: str) -> None:
    if not os.path.isdir(template_dir):
        return
    extra = {"verification-report.md", "change-request.md", "bug-fix-spec.md"}
    for filename in extra:
        src = os.path.join(template_dir, filename)
        dst = os.path.join(work_dir, filename)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)


def slug_from_brief(project_brief: dict[str, Any]) -> str:
    goal = _clean(project_brief.get("goal") or "")
    return _slug_from_goal(goal) if goal else "work-item"
