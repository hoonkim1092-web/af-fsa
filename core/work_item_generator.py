"""
core/work_item_generator.py
============================
ProjectPipeline.prepare() 의 planning 데이터를 docs/work-items/{slug}/ 문서로 변환.

생성 문서:
  - feature-plan.md      : project_brief → 배경, 목표, 범위, 리스크
  - feature-spec.md      : project_brief + role_plan → 기능 요구사항, 수용 기준
  - implementation-design.md : role_plan → 모듈 구조, 데이터 흐름
  - implementation-tasks.md  : task_board.tasks → 마크다운 체크리스트
  - approval-gate.md     : ApprovalGate.initialize()

생성 후에는 사용자가 문서를 편집할 수 있으며,
work_item_parser.py 가 편집된 내용을 다시 구조화 데이터로 변환한다.
"""
from __future__ import annotations

import os
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


def _slug_from_goal(goal: str) -> str:
    """프로젝트 목표에서 slug 생성."""
    import re
    text = goal.lower()[:60]
    text = re.sub(r"[^a-z0-9가-힣\s]", " ", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "work-item"


def _make_checklist(tasks: list[dict[str, Any]]) -> str:
    """task_board.tasks를 마크다운 체크리스트로 변환."""
    lines: list[str] = []
    phase_order = {"scope": 0, "build": 1, "integrate": 2, "verify": 3}
    sorted_tasks = sorted(
        tasks,
        key=lambda t: (
            phase_order.get(_clean(t.get("phase") or "build"), 99),
            _clean(t.get("task_id") or ""),
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
            lines.append(f"  - 담당 역할: {owner}")
        lines.append(f"  - 단계: {phase}")
        if depends:
            lines.append(f"  - 선행 작업: {', '.join(depends)}")
        if acceptance:
            lines.append(f"  - 완료 조건: {'; '.join(acceptance)}")
        if artifacts:
            lines.append(f"  - 산출물: {', '.join(artifacts)}")
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
    deliverables_lines = "\n".join(f"- {d}" for d in deliverables) if deliverables else "- (자동 생성)"
    constraints_lines = "\n".join(f"- {c}" for c in constraints) if constraints else "- 없음"
    roles = [r for r in (role_plan.get("roles") or []) if isinstance(r, dict)]
    stakeholder_lines = "\n".join(
        f"- {_clean(r.get('name') or r.get('id'))}" for r in roles if _clean(r.get("name") or r.get("id"))
    ) or "- (자동 생성)"

    return (
        "# Feature Plan\n"
        "\n"
        "## Metadata\n"
        "\n"
        f"- work_item: {work_item}\n"
        f"- owner: (편집 필요)\n"
        f"- status: draft\n"
        f"- last_updated: {now_iso()}\n"
        "\n"
        "## 배경\n"
        "\n"
        f"{goal}\n"
        "\n"
        "## 문제 정의\n"
        "\n"
        f"{goal}\n"
        "\n"
        "## 목표\n"
        "\n"
        f"{deliverables_lines}\n"
        "\n"
        "## 비목표\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 범위\n"
        "\n"
        f"{scope_lines or '- (자동 생성)'}\n"
        "\n"
        "## 이해관계자\n"
        "\n"
        f"{stakeholder_lines}\n"
        "\n"
        "## 성공 기준\n"
        "\n"
        f"{deliverables_lines}\n"
        "\n"
        "## 리스크와 가정\n"
        "\n"
        f"{constraints_lines}\n"
        "\n"
        "## 승인 요청 사항\n"
        "\n"
        "- 위 범위와 목표에 동의하면 approval-gate.md를 승인한다.\n"
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

    req_lines: list[str] = []
    for m in modules:
        slices = _clean_list(m.get("feature_slices"))
        for s in slices:
            req_lines.append(f"- {s}")

    acceptance_lines: list[str] = []
    for task in all_tasks:
        for acc in _clean_list(task.get("acceptance")):
            if acc not in acceptance_lines:
                acceptance_lines.append(f"- {acc}")

    scenario_lines: list[str] = []
    for m in modules:
        name = _clean(m.get("name") or "")
        summary = _clean(m.get("summary") or "")
        if name:
            scenario_lines.append(f"- {name}: {summary}")

    return (
        "# Feature Spec\n"
        "\n"
        "## Metadata\n"
        "\n"
        f"- work_item: {work_item}\n"
        f"- source_plan: feature-plan.md\n"
        f"- status: draft\n"
        f"- last_updated: {now_iso()}\n"
        "\n"
        "## 기능 개요\n"
        "\n"
        f"{goal}\n"
        "\n"
        "## 사용자 시나리오\n"
        "\n"
        + ("\n".join(scenario_lines) or "- (편집 필요)")
        + "\n"
        "\n"
        "## 기능 요구사항\n"
        "\n"
        + ("\n".join(req_lines) or "- (자동 생성)")
        + "\n"
        "\n"
        "## 비기능 요구사항\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 입력과 출력\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 예외와 실패 시나리오\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 유지해야 할 기존 동작\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 수용 기준\n"
        "\n"
        + ("\n".join(acceptance_lines) or "- (자동 생성)")
        + "\n"
        "\n"
        "## 범위 제외 항목\n"
        "\n"
        "- (편집 필요)\n"
    )


def _generate_implementation_design(
    work_item: str,
    project_brief: dict[str, Any],
    role_plan: dict[str, Any],
) -> str:
    goal = _clean(project_brief.get("goal") or "")
    modules = [m for m in (role_plan.get("modules") or []) if isinstance(m, dict)]
    execution_strategy = _clean(role_plan.get("execution_strategy") or "parallel")

    module_lines: list[str] = []
    for m in modules:
        name = _clean(m.get("name") or "")
        owner = _clean(m.get("owner_role") or "")
        summary = _clean(m.get("summary") or "")
        depends = _clean_list(m.get("depends_on"))
        if name:
            module_lines.append(f"### {name}")
            module_lines.append(f"- 담당: {owner}")
            module_lines.append(f"- 목표: {summary}")
            if depends:
                module_lines.append(f"- 선행: {', '.join(depends)}")
            module_lines.append("")

    flow_lines: list[str] = []
    for i, m in enumerate(modules, start=1):
        name = _clean(m.get("name") or f"Module {i}")
        depends = _clean_list(m.get("depends_on"))
        arrow = f"{', '.join(depends)} → " if depends else ""
        flow_lines.append(f"{i}. {arrow}{name}")

    return (
        "# Implementation Design\n"
        "\n"
        "## Metadata\n"
        "\n"
        f"- work_item: {work_item}\n"
        f"- spec_type: feature\n"
        f"- source_spec: feature-spec.md\n"
        f"- status: draft\n"
        f"- last_updated: {now_iso()}\n"
        "\n"
        "## 설계 요약\n"
        "\n"
        f"{goal}\n"
        f"실행 전략: {execution_strategy}\n"
        "\n"
        "## 수정 대상 모듈\n"
        "\n"
        + ("\n".join(module_lines) or "- (자동 생성)\n")
        + "\n"
        "## 데이터 흐름\n"
        "\n"
        + ("\n".join(flow_lines) or "- (자동 생성)")
        + "\n"
        "\n"
        "## 인터페이스 영향\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 상태 및 데이터 모델\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 호환성 고려사항\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 마이그레이션 필요 여부\n"
        "\n"
        "- 없음\n"
        "\n"
        "## 리스크\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 대안 비교\n"
        "\n"
        "- (편집 필요)\n"
        "\n"
        "## 테스트 전략\n"
        "\n"
        "- 단위 테스트: 각 모듈별\n"
        "- 통합 테스트: 모듈 간 데이터 흐름 검증\n"
    )


def _generate_implementation_tasks(
    work_item: str,
    role_plan: dict[str, Any],
    task_board: dict[str, Any],
) -> str:
    tasks = [t for t in (task_board.get("tasks") or []) if isinstance(t, dict)]
    modules = [m for m in (role_plan.get("modules") or []) if isinstance(m, dict)]
    preconditions = [
        _clean(step.get("name") or "")
        for step in (role_plan.get("planning_steps") or [])
        if isinstance(step, dict) and _clean(step.get("name"))
    ]

    pre_lines = "\n".join(f"- {p}" for p in preconditions) if preconditions else "- 없음"

    # 블로커 (선행 의존성이 있는 모듈)
    blocked = [
        _clean(m.get("name") or "")
        for m in modules
        if isinstance(m, dict) and _clean_list(m.get("depends_on"))
    ]
    blocker_lines = "\n".join(f"- {b}" for b in blocked) if blocked else "- 없음"

    checklist = _make_checklist(tasks)

    return (
        "# Implementation Tasks\n"
        "\n"
        "## Metadata\n"
        "\n"
        f"- work_item: {work_item}\n"
        f"- source_design: implementation-design.md\n"
        f"- status: draft\n"
        f"- last_updated: {now_iso()}\n"
        "\n"
        "## 선행 조건\n"
        "\n"
        f"{pre_lines}\n"
        "\n"
        "## 작업 목록\n"
        "\n"
        + (checklist or "- [ ] (편집 필요)")
        + "\n"
        "\n"
        "## 차단 요소\n"
        "\n"
        f"{blocker_lines}\n"
        "\n"
        "## 롤백 포인트\n"
        "\n"
        "- 각 모듈 구현 완료 후 git commit\n"
        "\n"
        "## 완료 정의\n"
        "\n"
        "- 모든 작업 체크박스가 완료됨\n"
        "- verification-report.md에 결과 기록됨\n"
    )


def generate_work_items(
    workspace: str,
    slug: str,
    project_brief: dict[str, Any],
    role_plan: dict[str, Any],
    task_board: dict[str, Any],
) -> dict[str, str]:
    """
    planning 결과를 work-item 문서 집합으로 변환한다.

    Returns:
        {파일명: 절대경로} 매핑
    """
    work_dir = os.path.join(os.path.abspath(workspace), WORK_ITEMS_DIR_REL, slug)
    os.makedirs(work_dir, exist_ok=True)

    # _template/의 나머지 파일들 복사 (verification-report, change-request 등)
    template_dir = os.path.join(os.path.abspath(workspace), TEMPLATE_DIR_REL)
    _copy_extra_templates(template_dir, work_dir)

    work_item_id = slug

    files: dict[str, str] = {}

    # 1. feature-plan.md
    plan_content = _generate_feature_plan(work_item_id, project_brief, role_plan)
    plan_path = os.path.join(work_dir, "feature-plan.md")
    write_text(plan_path, plan_content)
    files["feature-plan.md"] = plan_path

    # 2. feature-spec.md
    spec_content = _generate_feature_spec(work_item_id, project_brief, role_plan, task_board)
    spec_path = os.path.join(work_dir, "feature-spec.md")
    write_text(spec_path, spec_content)
    files["feature-spec.md"] = spec_path

    # 3. implementation-design.md
    design_content = _generate_implementation_design(work_item_id, project_brief, role_plan)
    design_path = os.path.join(work_dir, "implementation-design.md")
    write_text(design_path, design_content)
    files["implementation-design.md"] = design_path

    # 4. implementation-tasks.md
    tasks_content = _generate_implementation_tasks(work_item_id, role_plan, task_board)
    tasks_path = os.path.join(work_dir, "implementation-tasks.md")
    write_text(tasks_path, tasks_content)
    files["implementation-tasks.md"] = tasks_path

    # 5. approval-gate.md (초기화)
    gate = ApprovalGate(workspace, slug)
    gate.initialize(work_item_id)
    files["approval-gate.md"] = gate.gate_path

    return files


def _copy_extra_templates(template_dir: str, work_dir: str) -> None:
    """_template에서 아직 없는 파일만 복사 (이미 생성된 파일은 덮어쓰지 않음)."""
    if not os.path.isdir(template_dir):
        return
    extra = {"verification-report.md", "change-request.md", "bug-fix-spec.md"}
    for filename in extra:
        src = os.path.join(template_dir, filename)
        dst = os.path.join(work_dir, filename)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)


def slug_from_brief(project_brief: dict[str, Any]) -> str:
    """project_brief에서 work-item slug를 생성."""
    goal = _clean(project_brief.get("goal") or "")
    return _slug_from_goal(goal) if goal else "work-item"
