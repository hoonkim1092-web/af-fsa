"""
core/work_item_parser.py
========================
편집된 work-item 마크다운 문서를 다시 구조화 데이터로 변환한다.

파싱 대상:
  - feature-plan.md      → {goal, deliverables, constraints, risks}
  - feature-spec.md      → {requirements, acceptance_criteria, scenarios}
  - implementation-tasks.md → [{title, task_id, owner_role, phase, done_criteria}]
  - approval-gate.md     → ApprovalGate._parse() 참조

sync_board_from_work_items():
  - 편집된 implementation-tasks.md 를 기준으로 project_board_state.json 갱신
  - 기존 태스크와 title/task_id 기준으로 매칭 → 추가/수정/삭제 반영
"""
from __future__ import annotations

import os
import re
from typing import Any

from core.utils import now_iso, safe_id


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _extract_section(text: str, heading: str) -> str:
    """## {heading} 아래의 텍스트를 다음 ## 섹션 전까지 추출."""
    pattern = rf"## {re.escape(heading)}\n(.*?)(?=\n##|\Z)"
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else ""


def _bullet_list(section_text: str) -> list[str]:
    """섹션 내 `- ...` 불릿 항목을 추출 (하위 항목 제외)."""
    items: list[str] = []
    for line in section_text.splitlines():
        m = re.match(r"^-\s+(.+)", line)
        if m:
            text = m.group(1).strip()
            # 메타 키-값 라인 (key: value) 은 제외 — 속성이므로
            if not re.match(r"^\w[\w\s]*:\s", text):
                items.append(text)
    return [x for x in items if x and x != "(편집 필요)" and x != "(자동 생성)"]


# ---------------------------------------------------------------------------
# feature-plan.md 파서
# ---------------------------------------------------------------------------

def parse_feature_plan(text: str) -> dict[str, Any]:
    """
    feature-plan.md 텍스트 → 구조화 데이터.

    Returns:
        {goal, deliverables, constraints, risks, scope_modules}
    """
    goal_text = _extract_section(text, "배경")
    if not goal_text:
        goal_text = _extract_section(text, "문제 정의")
    goal = goal_text.splitlines()[0].strip() if goal_text else ""

    deliverables = _bullet_list(_extract_section(text, "목표"))
    constraints = _bullet_list(_extract_section(text, "리스크와 가정"))
    scope_modules = _bullet_list(_extract_section(text, "범위"))

    return {
        "goal": goal,
        "deliverables": deliverables,
        "constraints": constraints,
        "scope_modules": scope_modules,
    }


# ---------------------------------------------------------------------------
# feature-spec.md 파서
# ---------------------------------------------------------------------------

def parse_feature_spec(text: str) -> dict[str, Any]:
    """
    feature-spec.md 텍스트 → 구조화 데이터.

    Returns:
        {requirements, acceptance_criteria, scenarios}
    """
    requirements = _bullet_list(_extract_section(text, "기능 요구사항"))
    acceptance_criteria = _bullet_list(_extract_section(text, "수용 기준"))
    scenarios = _bullet_list(_extract_section(text, "사용자 시나리오"))

    return {
        "requirements": requirements,
        "acceptance_criteria": acceptance_criteria,
        "scenarios": scenarios,
    }


# ---------------------------------------------------------------------------
# implementation-tasks.md 파서
# ---------------------------------------------------------------------------

def parse_implementation_tasks(text: str) -> list[dict[str, Any]]:
    """
    implementation-tasks.md 체크리스트 → task 딕셔너리 목록.

    각 태스크 블록 형식:
      - [ ] {title}
        - task_id: {id}       (선택)
        - 담당 역할: {role}    (선택)
        - 단계: {phase}       (선택)
        - 선행 작업: {deps}   (선택)
        - 완료 조건: {crit}   (선택)
        - 산출물: {artifacts} (선택)
    """
    section = _extract_section(text, "작업 목록")
    if not section:
        section = text  # fallback: 전체 파싱

    tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in section.splitlines():
        # 최상위 체크리스트 항목 - [ ] 또는 - [x]
        m = re.match(r"^-\s+\[([ xX])\]\s+(.+)", line)
        if m:
            if current is not None:
                tasks.append(current)
            done = m.group(1).lower() == "x"
            title = m.group(2).strip()
            current = {
                "title": title,
                "task_id": safe_id(title),
                "owner_role": "",
                "phase": "build",
                "depends_on": [],
                "acceptance": [],
                "artifacts": [],
                "status": "completed" if done else "pending",
            }
            continue

        if current is None:
            continue

        # 하위 속성 항목 (들여쓰기된 - key: value)
        attr = re.match(r"^\s+-\s+(\S.*?):\s*(.*)", line)
        if not attr:
            continue
        key = attr.group(1).strip()
        val = attr.group(2).strip()

        if key == "task_id" and val:
            current["task_id"] = safe_id(val)
        elif key in ("담당 역할", "owner_role") and val:
            current["owner_role"] = safe_id(val)
        elif key in ("단계", "phase") and val:
            current["phase"] = safe_id(val) or "build"
        elif key in ("선행 작업", "depends_on") and val:
            current["depends_on"] = [safe_id(d.strip()) for d in val.split(",") if safe_id(d.strip())]
        elif key in ("완료 조건", "acceptance") and val:
            current["acceptance"] = [c.strip() for c in val.split(";") if c.strip()]
        elif key in ("산출물", "artifacts") and val:
            current["artifacts"] = [a.strip() for a in val.split(",") if a.strip()]
        elif key in ("대상 파일", "target_files") and val:
            current.setdefault("target_files", []).extend(
                [f.strip() for f in val.split(",") if f.strip()]
            )

    if current is not None:
        tasks.append(current)

    return tasks


# ---------------------------------------------------------------------------
# task_board 동기화
# ---------------------------------------------------------------------------

def sync_board_from_work_items(
    workspace: str,
    slug: str,
    existing_board: dict[str, Any],
) -> dict[str, Any]:
    """
    편집된 work-item 문서를 기준으로 project_board를 갱신한다.

    전략:
      - implementation-tasks.md 의 체크리스트가 기준(source of truth)
      - 기존 board.tasks 에서 task_id 또는 title 로 매칭
        - 매칭됨: acceptance, depends_on, artifacts, owner_role, phase 갱신
        - 새 항목: board.tasks 에 추가
        - 기존에 있으나 체크리스트에 없음: 삭제하지 않고 유지 (수동 생성 태스크 보존)
      - feature-spec.md 의 acceptance_criteria 를 매칭 태스크의 acceptance 에 보완
    """
    work_dir = os.path.join(os.path.abspath(workspace), "docs", "work-items", slug)

    # 1. implementation-tasks.md 파싱
    tasks_path = os.path.join(work_dir, "implementation-tasks.md")
    parsed_tasks: list[dict[str, Any]] = []
    if os.path.exists(tasks_path):
        with open(tasks_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        parsed_tasks = parse_implementation_tasks(content)

    # 2. feature-spec.md 파싱 (acceptance_criteria 보완용)
    spec_path = os.path.join(work_dir, "feature-spec.md")
    spec_data: dict[str, Any] = {}
    if os.path.exists(spec_path):
        with open(spec_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        spec_data = parse_feature_spec(content)

    # 3. 기존 board 복사
    board = dict(existing_board or {})
    existing_tasks: list[dict[str, Any]] = [
        dict(t) for t in (board.get("tasks") or []) if isinstance(t, dict)
    ]

    # 기존 태스크 인덱스: task_id → task dict
    existing_by_id: dict[str, dict[str, Any]] = {}
    existing_by_title: dict[str, dict[str, Any]] = {}
    for task in existing_tasks:
        tid = safe_id(str(task.get("task_id") or ""))
        if tid:
            existing_by_id[tid] = task
        title_key = safe_id(str(task.get("title") or task.get("instruction") or ""))
        if title_key:
            existing_by_title[title_key] = task

    # 4. parsed_tasks 로 기존 board 갱신
    new_tasks: list[dict[str, Any]] = []

    for pt in parsed_tasks:
        pt_id = _clean(pt.get("task_id") or "")
        pt_title_key = safe_id(_clean(pt.get("title") or ""))
        matched = existing_by_id.get(pt_id) or existing_by_title.get(pt_title_key)

        if matched:
            # 갱신
            if pt.get("owner_role"):
                matched["owner_role"] = pt["owner_role"]
            if pt.get("phase"):
                matched["phase"] = pt["phase"]
            if pt.get("depends_on"):
                matched["depends_on"] = pt["depends_on"]
            if pt.get("acceptance"):
                matched["acceptance"] = pt["acceptance"]
            if pt.get("artifacts"):
                matched["artifacts"] = pt["artifacts"]
            matched["updated_at"] = now_iso()
        else:
            # 새 태스크 추가
            module_id = ""
            new_task: dict[str, Any] = {
                "task_id": pt_id or safe_id(pt.get("title") or "task"),
                "title": _clean(pt.get("title") or ""),
                "instruction": _clean(pt.get("title") or ""),
                "owner_role": pt.get("owner_role") or "",
                "module_id": module_id,
                "phase": pt.get("phase") or "build",
                "depends_on": pt.get("depends_on") or [],
                "acceptance": pt.get("acceptance") or [],
                "artifacts": pt.get("artifacts") or [],
                "status": pt.get("status") or "pending",
                "notes": [],
                "updated_at": now_iso(),
            }
            new_tasks.append(new_task)

    # 5. spec acceptance_criteria 보완
    global_acceptance = spec_data.get("acceptance_criteria") or []
    if global_acceptance:
        for task in existing_tasks:
            if not task.get("acceptance"):
                task["acceptance"] = list(global_acceptance)

    # 6. 최종 태스크 목록
    board["tasks"] = existing_tasks + new_tasks
    board["updated_at"] = now_iso()
    board["work_item_slug"] = slug

    # role_index 재계산
    role_index: dict[str, list[str]] = {}
    for task in board["tasks"]:
        role = safe_id(str(task.get("owner_role") or ""))
        if role:
            role_index.setdefault(role, [])
            tid = _clean(task.get("task_id") or "")
            if tid and tid not in role_index[role]:
                role_index[role].append(tid)
    board["role_index"] = role_index

    return board
