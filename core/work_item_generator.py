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
    deliverables = _clean_list(project_brief.get("deliverables"))
    constraints = _clean_list(project_brief.get("constraints"))
    modules = [m for m in (role_plan.get("modules") or []) if isinstance(m, dict)]
    scope_lines = "\n".join(f"- {_clean(m.get('name'))}" for m in modules if _clean(m.get("name")))
    deliverables_lines = "\n".join(f"- {item}" for item in deliverables) if deliverables else "- (auto-generate needed)"
    constraints_lines = "\n".join(f"- {item}" for item in constraints) if constraints else "- none"
    roles = [r for r in (role_plan.get("roles") or []) if isinstance(r, dict)]
    stakeholder_lines = "\n".join(
        f"- {_clean(role.get('name') or role.get('id'))}" for role in roles if _clean(role.get("name") or role.get("id"))
    ) or "- (auto-generate needed)"
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
        f"{goal or '(additional summary needed)'}\n\n"
        "## Problem Statement\n\n"
        f"{goal or '(additional summary needed)'}\n\n"
        "## Goals\n\n"
        f"{deliverables_lines}\n\n"
        "## Non-Goals\n\n"
        "- (edit required)\n\n"
        "## Scope\n\n"
        f"{scope_lines or '- (auto-generate needed)'}\n\n"
        "## Stakeholders\n\n"
        f"{stakeholder_lines}\n\n"
        "## Success Metrics\n\n"
        f"{deliverables_lines}\n\n"
        "## Risks and Assumptions\n\n"
        f"{constraints_lines}\n\n"
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
    research_lines = _research_bullets(project_brief)
    reference_lines = _reference_bullets(project_brief)

    req_lines: list[str] = []
    for module in modules:
        for item in _clean_list(module.get("feature_slices")):
            req_lines.append(f"- {item}")
    req_text = "\n".join(req_lines) or "- (auto-generate needed)"

    acceptance_lines: list[str] = []
    for task in all_tasks:
        for acc in _clean_list(task.get("acceptance")):
            bullet = f"- {acc}"
            if bullet not in acceptance_lines:
                acceptance_lines.append(bullet)
    acceptance_text = "\n".join(acceptance_lines) or "- (auto-generate needed)"

    scenario_lines: list[str] = []
    for module in modules:
        name = _clean(module.get("name") or "")
        summary = _clean(module.get("summary") or "")
        if name:
            scenario_lines.append(f"- {name}: {summary}")
    scenario_text = "\n".join(scenario_lines) or "- (edit required)"

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
        "- (edit required)\n\n"
        "## Inputs and Outputs\n\n"
        "- (edit required)\n\n"
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
        "- (edit required)\n"
    )


def _generate_implementation_design(
    work_item: str,
    project_brief: dict[str, Any],
    role_plan: dict[str, Any],
) -> str:
    goal = _clean(project_brief.get("goal") or "")
    modules = [m for m in (role_plan.get("modules") or []) if isinstance(m, dict)]
    execution_strategy = _clean(role_plan.get("execution_strategy") or "parallel")
    research_lines = _research_bullets(project_brief)
    reference_lines = _reference_bullets(project_brief)

    module_lines: list[str] = []
    for module in modules:
        name = _clean(module.get("name") or "")
        owner = _clean(module.get("owner_role") or "")
        summary = _clean(module.get("summary") or "")
        depends = _clean_list(module.get("depends_on"))
        if name:
            module_lines.append(f"### {name}")
            module_lines.append(f"- owner: {owner}")
            module_lines.append(f"- objective: {summary}")
            if depends:
                module_lines.append(f"- depends_on: {', '.join(depends)}")
            module_lines.append("")
    module_text = "\n".join(module_lines) or "- (auto-generate needed)"

    flow_lines: list[str] = []
    for index, module in enumerate(modules, start=1):
        name = _clean(module.get("name") or f"Module {index}")
        depends = _clean_list(module.get("depends_on"))
        arrow = f"{', '.join(depends)} -> " if depends else ""
        flow_lines.append(f"{index}. {arrow}{name}")
    flow_text = "\n".join(flow_lines) or "- (auto-generate needed)"

    return (
        "# Implementation Design\n\n"
        "## Metadata\n\n"
        f"- work_item: {work_item}\n"
        "- spec_type: feature\n"
        "- source_spec: feature-spec.md\n"
        "- status: draft\n"
        f"- last_updated: {now_iso()}\n\n"
        "## Design Summary\n\n"
        f"{goal or '(edit required)'}\n"
        f"execution_strategy: {execution_strategy}\n\n"
        "## Planned Modules\n\n"
        f"{module_text}\n\n"
        "## Data Flow\n\n"
        f"{flow_text}\n\n"
        "## Interface Impact\n\n"
        "- (edit required)\n\n"
        "## State And Data Model\n\n"
        "- (edit required)\n\n"
        "## Compatibility Considerations\n\n"
        "- (edit required)\n\n"
        "## Migration Requirement\n\n"
        "- none\n\n"
        "## Risks\n\n"
        "- (edit required)\n\n"
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
    work_dir = os.path.join(os.path.abspath(workspace), WORK_ITEMS_DIR_REL, slug)
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
