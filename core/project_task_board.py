from __future__ import annotations

import json
import os
from typing import Any

from core.file_io import write_text
from core.file_lock import locked_file
from core.utils import now_iso, safe_id

BOARD_FILENAME = "project_board_state.json"
TASK_EXECUTION_PLAN_REL_PATH = os.path.join("docs", "task_execution_plan.md")
_PHASE_ORDER = {"scope": 0, "build": 1, "integrate": 2, "verify": 3}


def default_planning_steps() -> list[dict[str, Any]]:
    return [
        {
            "id": "scope_contracts",
            "name": "범위와 계약 정의",
            "objective": "기능 경계를 모듈 단위로 나누고 역할별 인터페이스를 고정한다.",
            "exit_criteria": [
                "모든 작업이 owner_role과 depends_on을 가진다.",
                "핵심 산출물이 모듈별로 정리된다.",
            ],
        },
        {
            "id": "vertical_slice_build",
            "name": "기능 슬라이스 구현",
            "objective": "독립 배포 가능한 작은 기능 단위로 구현을 진행한다.",
            "exit_criteria": [
                "각 모듈이 최소 1개의 구현 작업을 가진다.",
                "기능 슬라이스가 파일/산출물 기준으로 분리된다.",
            ],
        },
        {
            "id": "integration_handoff",
            "name": "통합과 핸드오프",
            "objective": "역할 간 의존성을 정리하고 결과를 다음 작업자가 이어받을 수 있게 만든다.",
            "exit_criteria": [
                "의존 작업이 정리되고 handoff 기준이 명시된다.",
                "검증 전에 필요한 연결 작업이 완료된다.",
            ],
        },
        {
            "id": "verification_closeout",
            "name": "검증과 마감",
            "objective": "기능 동작, 회귀 리스크, 남은 이슈를 명시적으로 검증한다.",
            "exit_criteria": [
                "검증 작업이 존재한다.",
                "잔여 리스크와 후속 작업이 기록된다.",
            ],
        },
    ]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(values: Any) -> list[str]:
    cleaned = [_clean_text(item) for item in (values or []) if _clean_text(item)]
    return list(dict.fromkeys(cleaned))


def _role_name(role: dict[str, Any]) -> str:
    return _clean_text(role.get("name") or role.get("id") or "Role")


def _role_objective(role: dict[str, Any], fallback_goal: str) -> str:
    objective = _clean_text(role.get("objective"))
    return objective or fallback_goal or f"{_role_name(role)} 범위를 구현한다."


def _module_status(tasks: list[dict[str, Any]]) -> str:
    statuses = {str(task.get("status") or "pending") for task in tasks}
    if tasks and statuses == {"completed"}:
        return "completed"
    if "failed" in statuses:
        return "at_risk"
    if "in_progress" in statuses:
        return "in_progress"
    return "pending"


def _pick_owner_role(deliverable: str, roles: list[dict[str, Any]]) -> str:
    text = safe_id(deliverable)
    if not roles:
        return "general_dev"
    keyword_map = (
        ("qa", ("qa", "test", "guard", "verify", "검증", "테스트")),
        ("frontend", ("ui", "screen", "page", "front", "layout", "ux", "웹")),
        ("backend", ("api", "server", "db", "auth", "storage", "data")),
        ("logic", ("logic", "rule", "engine", "state", "game", "workflow")),
        ("design", ("design", "wireframe", "visual", "prototype")),
    )
    lowered = deliverable.lower()
    for bucket, tokens in keyword_map:
        if any(token in text or token in lowered for token in tokens):
            for role in roles:
                role_id = safe_id(role.get("id") or "")
                if bucket in role_id or (bucket == "qa" and "test" in role_id):
                    return role_id
    return safe_id(roles[0].get("id") or "general_dev")


def _normalize_roles(role_plan: dict[str, Any], fallback_goal: str) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for raw in (role_plan.get("roles") or []):
        if not isinstance(raw, dict):
            continue
        role_id = safe_id(raw.get("id") or raw.get("name") or "role")
        if not role_id:
            continue
        roles.append(
            {
                "id": role_id,
                "name": _role_name(raw),
                "objective": _role_objective(raw, fallback_goal),
                "required_skills": [safe_id(skill) for skill in _clean_list(raw.get("required_skills")) if safe_id(skill)],
                "owned_modules": [safe_id(item) for item in _clean_list(raw.get("owned_modules")) if safe_id(item)],
            }
        )
    return roles


def _normalize_planning_steps(role_plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw_steps = role_plan.get("planning_steps")
    if not isinstance(raw_steps, list):
        return default_planning_steps()

    steps: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            continue
        step_id = safe_id(raw.get("id") or raw.get("name") or f"step_{index}")
        if not step_id:
            continue
        steps.append(
            {
                "id": step_id,
                "name": _clean_text(raw.get("name") or step_id),
                "objective": _clean_text(raw.get("objective")),
                "exit_criteria": _clean_list(raw.get("exit_criteria")),
            }
        )
    return steps or default_planning_steps()


def _task_template(role_name: str, module_name: str, summary: str) -> list[dict[str, Any]]:
    focus = _clean_text(summary) or module_name
    return [
        {
            "phase": "scope",
            "title": f"{role_name}: {module_name} 범위와 인터페이스를 정의한다.",
            "instruction": f"{role_name}: {module_name} 범위와 인터페이스를 정의하고 구현 순서를 고정한다.",
            "acceptance": [
                f"{module_name} 범위가 명확히 정리된다.",
                "의존성과 산출물이 명시된다.",
            ],
        },
        {
            "phase": "build",
            "title": f"{role_name}: {module_name} 기능을 구현한다.",
            "instruction": f"{role_name}: {focus} 기능을 작은 슬라이스로 나눠 구현한다.",
            "acceptance": [
                f"{module_name}의 핵심 기능이 구현된다.",
                "관련 파일과 산출물이 갱신된다.",
            ],
        },
        {
            "phase": "verify",
            "title": f"{role_name}: {module_name} 결과를 검증하고 handoff를 남긴다.",
            "instruction": f"{role_name}: {module_name} 결과를 검증하고 다음 작업자가 이어받을 handoff 메모를 남긴다.",
            "acceptance": [
                "검증 결과가 정리된다.",
                "잔여 리스크와 후속 작업이 기록된다.",
            ],
        },
    ]


def _auto_modules(task_input: str, project_brief: dict[str, Any], roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deliverables = _clean_list(project_brief.get("deliverables"))
    goal = _clean_text(project_brief.get("goal") or task_input)
    modules: list[dict[str, Any]] = []
    module_index = 0
    role_module_counts: dict[str, int] = {}

    for deliverable in deliverables:
        owner_role = _pick_owner_role(deliverable, roles)
        owner = next((role for role in roles if role["id"] == owner_role), roles[0] if roles else {"id": owner_role, "name": owner_role, "objective": goal})
        role_module_counts[owner_role] = role_module_counts.get(owner_role, 0) + 1
        module_index += 1
        modules.append(
            {
                "id": safe_id(f"{owner_role}_module_{module_index}"),
                "name": deliverable,
                "summary": f"{deliverable}를 독립 작업 단위로 구현한다.",
                "owner_role": owner_role,
                "depends_on": [],
                "deliverables": [deliverable],
                "feature_slices": [deliverable],
                "tasks": _task_template(_role_name(owner), deliverable, deliverable),
            }
        )

    for role in roles:
        if role_module_counts.get(role["id"], 0):
            continue
        module_index += 1
        modules.append(
            {
                "id": safe_id(f"{role['id']}_module_{module_index}"),
                "name": _role_name(role),
                "summary": role["objective"] or goal,
                "owner_role": role["id"],
                "depends_on": [],
                "deliverables": _clean_list(project_brief.get("deliverables"))[:1] or [role["objective"]],
                "feature_slices": [role["objective"]],
                "tasks": _task_template(_role_name(role), _role_name(role), role["objective"] or goal),
            }
        )
    return modules


def _normalize_tasks(module: dict[str, Any], owner_role: str, owner_name: str) -> list[dict[str, Any]]:
    raw_tasks = module.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raw_tasks = _task_template(owner_name, _clean_text(module.get("name") or owner_name), _clean_text(module.get("summary")))

    tasks: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_tasks, start=1):
        if isinstance(raw, str):
            raw = {"title": raw, "instruction": raw}
        if not isinstance(raw, dict):
            continue
        phase = safe_id(raw.get("phase") or "build") or "build"
        task_id = safe_id(raw.get("id") or f"{module['id']}_{phase}_{index}")
        if not task_id:
            continue
        title = _clean_text(raw.get("title") or raw.get("instruction") or f"{owner_name}: {_clean_text(module.get('name') or module['id'])} 작업 {index}")
        instruction = _clean_text(raw.get("instruction") or title)
        tasks.append(
            {
                "id": task_id,
                "title": title,
                "instruction": instruction,
                "owner_role": safe_id(raw.get("owner_role") or owner_role),
                "phase": phase,
                "depends_on": [safe_id(item) for item in _clean_list(raw.get("depends_on")) if safe_id(item)],
                "acceptance": _clean_list(raw.get("acceptance")),
                "artifacts": _clean_list(raw.get("artifacts")),
                "status": _clean_text(raw.get("status") or "pending") or "pending",
            }
        )
    return tasks


def enrich_role_plan(task_input: str, project_brief: dict[str, Any], role_plan: dict[str, Any]) -> dict[str, Any]:
    plan = dict(role_plan or {})
    goal = _clean_text(project_brief.get("goal") or task_input)
    roles = _normalize_roles(plan, goal)
    planning_steps = _normalize_planning_steps(plan)
    raw_modules = plan.get("modules")
    modules_input = raw_modules if isinstance(raw_modules, list) and raw_modules else _auto_modules(task_input, project_brief, roles)

    normalized_modules: list[dict[str, Any]] = []
    owned_modules_by_role: dict[str, list[str]] = {role["id"]: [] for role in roles}
    previous_module_id = ""

    for index, raw_module in enumerate(modules_input, start=1):
        if not isinstance(raw_module, dict):
            continue
        owner_role = safe_id(raw_module.get("owner_role") or _pick_owner_role(_clean_text(raw_module.get("name")), roles))
        owner = next((role for role in roles if role["id"] == owner_role), None)
        if owner is None and roles:
            owner = roles[0]
            owner_role = owner["id"]
        elif owner is None:
            owner = {"id": owner_role or "general_dev", "name": owner_role or "General Dev", "objective": goal}
            owner_role = owner["id"]

        module_id = safe_id(raw_module.get("id") or f"{owner_role}_module_{index}")
        if not module_id:
            continue
        depends_on = [safe_id(item) for item in _clean_list(raw_module.get("depends_on")) if safe_id(item)]
        if not depends_on and previous_module_id and plan.get("execution_strategy") == "sequential":
            depends_on = [previous_module_id]
        module = {
            "id": module_id,
            "name": _clean_text(raw_module.get("name") or f"{owner['name']} Module {index}"),
            "summary": _clean_text(raw_module.get("summary") or owner.get("objective") or goal),
            "owner_role": owner_role,
            "depends_on": depends_on,
            "deliverables": _clean_list(raw_module.get("deliverables")) or _clean_list(project_brief.get("deliverables"))[:1],
            "feature_slices": _clean_list(raw_module.get("feature_slices")) or [_clean_text(raw_module.get("summary") or raw_module.get("name"))],
        }
        tasks = _normalize_tasks({**raw_module, "id": module_id, "name": module["name"], "summary": module["summary"]}, owner_role, _role_name(owner))
        if tasks:
            tasks[0]["depends_on"] = list(dict.fromkeys(tasks[0]["depends_on"] + depends_on))
            for task_index in range(1, len(tasks)):
                previous_task_id = tasks[task_index - 1]["id"]
                tasks[task_index]["depends_on"] = list(dict.fromkeys(tasks[task_index]["depends_on"] + [previous_task_id]))
        module["tasks"] = tasks
        normalized_modules.append(module)
        owned_modules_by_role.setdefault(owner_role, []).append(module_id)
        previous_module_id = module_id

    normalized_roles: list[dict[str, Any]] = []
    for role in roles:
        normalized_roles.append({**role, "owned_modules": owned_modules_by_role.get(role["id"], role.get("owned_modules") or [])})

    task_todo_items = [task["instruction"] for module in normalized_modules for task in module.get("tasks", [])]
    todo_items = _clean_list(plan.get("todo_items")) or task_todo_items

    return {
        "execution_strategy": _clean_text(plan.get("execution_strategy") or "parallel") or "parallel",
        "planning_steps": planning_steps,
        "roles": normalized_roles,
        "modules": normalized_modules,
        "todo_items": todo_items,
        "plan_summary": {
            "goal": goal,
            "role_count": len(normalized_roles),
            "module_count": len(normalized_modules),
            "task_count": len(task_todo_items),
        },
    }


def build_project_board(project_brief: dict[str, Any], role_plan: dict[str, Any]) -> dict[str, Any]:
    modules: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    role_index: dict[str, list[str]] = {}

    for module in (role_plan.get("modules") or []):
        if not isinstance(module, dict):
            continue
        module_tasks = []
        for raw_task in (module.get("tasks") or []):
            if not isinstance(raw_task, dict):
                continue
            task = {
                "task_id": _clean_text(raw_task.get("id")),
                "title": _clean_text(raw_task.get("title") or raw_task.get("instruction")),
                "instruction": _clean_text(raw_task.get("instruction") or raw_task.get("title")),
                "owner_role": safe_id(raw_task.get("owner_role") or module.get("owner_role")),
                "module_id": _clean_text(module.get("id")),
                "phase": safe_id(raw_task.get("phase") or "build") or "build",
                "depends_on": [safe_id(item) for item in _clean_list(raw_task.get("depends_on")) if safe_id(item)],
                "acceptance": _clean_list(raw_task.get("acceptance")),
                "artifacts": _clean_list(raw_task.get("artifacts")),
                "status": _clean_text(raw_task.get("status") or "pending") or "pending",
                "notes": [],
                "updated_at": now_iso(),
            }
            if not task["task_id"] or not task["instruction"]:
                continue
            tasks.append(task)
            module_tasks.append(task)
            role_index.setdefault(task["owner_role"], []).append(task["task_id"])

        modules.append(
            {
                "id": _clean_text(module.get("id")),
                "name": _clean_text(module.get("name")),
                "summary": _clean_text(module.get("summary")),
                "owner_role": safe_id(module.get("owner_role")),
                "depends_on": [safe_id(item) for item in _clean_list(module.get("depends_on")) if safe_id(item)],
                "deliverables": _clean_list(module.get("deliverables")),
                "feature_slices": _clean_list(module.get("feature_slices")),
                "task_ids": [task["task_id"] for task in module_tasks],
                "status": _module_status(module_tasks),
            }
        )

    board = {
        "version": 1,
        "generated_at": now_iso(),
        "goal": _clean_text(project_brief.get("goal")),
        "execution_strategy": _clean_text(role_plan.get("execution_strategy") or "parallel"),
        "planning_steps": role_plan.get("planning_steps") or default_planning_steps(),
        "roles": role_plan.get("roles") or [],
        "modules": modules,
        "tasks": tasks,
        "role_index": role_index,
        "summary": {},
    }
    return _recalculate_board(board)


def _recalculate_board(board: dict[str, Any]) -> dict[str, Any]:
    tasks = [task for task in (board.get("tasks") or []) if isinstance(task, dict)]
    task_map = {str(task.get("task_id")): task for task in tasks if _clean_text(task.get("task_id"))}
    for module in (board.get("modules") or []):
        if not isinstance(module, dict):
            continue
        module_tasks = [task_map[task_id] for task_id in module.get("task_ids", []) if task_id in task_map]
        module["status"] = _module_status(module_tasks)

    summary = {
        "total_tasks": len(tasks),
        "pending_tasks": sum(1 for task in tasks if task.get("status") == "pending"),
        "in_progress_tasks": sum(1 for task in tasks if task.get("status") == "in_progress"),
        "completed_tasks": sum(1 for task in tasks if task.get("status") == "completed"),
        "failed_tasks": sum(1 for task in tasks if task.get("status") == "failed"),
        "blocked_tasks": sum(1 for task in tasks if task.get("status") == "blocked"),
    }
    summary["open_tasks"] = summary["total_tasks"] - summary["completed_tasks"]
    board["summary"] = summary
    board["updated_at"] = now_iso()
    return board


def board_todo_items(board: dict[str, Any]) -> list[str]:
    return [
        _clean_text(task.get("instruction"))
        for task in (board.get("tasks") or [])
        if isinstance(task, dict) and _clean_text(task.get("instruction"))
    ]


def write_project_board(workspace: str, board: dict[str, Any]) -> str:
    path = os.path.join(os.path.abspath(workspace), BOARD_FILENAME)
    write_text(path, json.dumps(_recalculate_board(dict(board or {})), ensure_ascii=False, indent=2) + "\n")
    return path


def load_project_board(workspace: str) -> dict[str, Any]:
    path = os.path.join(os.path.abspath(workspace), BOARD_FILENAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {}
        return _recalculate_board(data)
    except Exception:
        return {}


def board_is_complete(board: dict[str, Any]) -> bool:
    summary = board.get("summary") if isinstance(board, dict) else {}
    if not isinstance(summary, dict):
        return False
    total = int(summary.get("total_tasks") or 0)
    completed = int(summary.get("completed_tasks") or 0)
    return bool(total) and total == completed


def _dependency_satisfied(dep: str, board: dict[str, Any], completed_ids: set[str]) -> bool:
    dependency = safe_id(dep)
    if not dependency:
        return True
    if dependency in completed_ids:
        return True
    for task in (board.get("tasks") or []):
        if safe_id(task.get("task_id")) == dependency and task.get("status") == "completed":
            return True
    for module in (board.get("modules") or []):
        if safe_id(module.get("id")) == dependency and module.get("status") == "completed":
            return True
    return False


def next_board_tasks(board: dict[str, Any], available_roles: list[str], completed_ids: set[str] | None = None) -> list[dict[str, str]]:
    if not isinstance(board, dict):
        return []
    completed = {safe_id(item) for item in (completed_ids or set()) if safe_id(item)}
    chosen: list[dict[str, str]] = []
    used_roles: set[str] = set()
    tasks = [task for task in (board.get("tasks") or []) if isinstance(task, dict)]
    ordered_tasks = sorted(
        tasks,
        key=lambda item: (
            _PHASE_ORDER.get(str(item.get("phase") or "build"), 99),
            str(item.get("module_id") or ""),
            str(item.get("task_id") or ""),
        ),
    )

    for task in ordered_tasks:
        role = safe_id(task.get("owner_role"))
        if not role or role in used_roles or role not in available_roles:
            continue
        if str(task.get("status") or "pending") not in {"pending", "blocked"}:
            continue
        task_key = safe_id(task.get("task_id") or task.get("instruction"))
        if task_key in completed:
            continue
        dependencies = [safe_id(dep) for dep in task.get("depends_on", []) if safe_id(dep)]
        if not all(_dependency_satisfied(dep, board, completed) for dep in dependencies):
            continue
        chosen.append(
            {
                "assigned_role": role,
                "subtask_instruction": _clean_text(task.get("instruction")),
                "estimated_complexity": "HIGH" if str(task.get("phase") or "") in {"build", "integrate"} else "LOW",
                "task_id": _clean_text(task.get("task_id")),
            }
        )
        used_roles.add(role)
    return chosen


def update_project_board_task(workspace: str, role: str, instruction: str, status: str, note: str = "", task_id: str = "") -> bool:
    board_path = os.path.join(os.path.abspath(workspace), BOARD_FILENAME)
    with locked_file(board_path):
        board = load_project_board(workspace)
        if not board:
            return False
        target_task_id = safe_id(task_id)
        target_instruction = safe_id(instruction)
        target_role = safe_id(role)
        updated = False
        for task in (board.get("tasks") or []):
            if not isinstance(task, dict):
                continue
            task_key = safe_id(str(task.get("task_id") or ""))
            instruction_key = safe_id(str(task.get("instruction") or ""))
            if target_task_id:
                matched = task_key == target_task_id
            else:
                matched = instruction_key == target_instruction and safe_id(str(task.get("owner_role") or "")) == target_role
            if not matched:
                continue
            task["status"] = _clean_text(status or "pending") or "pending"
            task["updated_at"] = now_iso()
            if note:
                task.setdefault("notes", [])
                task["notes"] = _clean_list(task.get("notes")) + [_clean_text(note)]
            updated = True
            break
        if not updated:
            return False
        write_project_board(workspace, board)
    return True


def append_project_board_note(workspace: str, note: str, task_id: str = "", role: str = "", instruction: str = "") -> bool:
    note_text = _clean_text(note)
    if not note_text:
        return False

    board_path = os.path.join(os.path.abspath(workspace), BOARD_FILENAME)
    with locked_file(board_path):
        board = load_project_board(workspace)
        if not board:
            return False

        target_task_id = safe_id(task_id)
        target_instruction = safe_id(instruction)
        target_role = safe_id(role)

        updated = False
        for task in (board.get("tasks") or []):
            if not isinstance(task, dict):
                continue
            task_key = safe_id(str(task.get("task_id") or ""))
            instruction_key = safe_id(str(task.get("instruction") or ""))
            if target_task_id:
                matched = task_key == target_task_id
            elif target_instruction:
                matched = instruction_key == target_instruction and (
                    not target_role or safe_id(str(task.get("owner_role") or "")) == target_role
                )
            else:
                matched = False
            if not matched:
                continue
            task.setdefault("notes", [])
            task["notes"] = _clean_list(task.get("notes")) + [note_text]
            task["updated_at"] = now_iso()
            updated = True
            break

        if not updated:
            return False
        write_project_board(workspace, board)
    return True

def reset_in_progress_tasks(workspace: str) -> bool:
    board = load_project_board(workspace)
    if not board:
        return False
    changed = False
    for task in (board.get("tasks") or []):
        if task.get("status") == "in_progress":
            task["status"] = "pending"
            task["updated_at"] = now_iso()
            changed = True
    if changed:
        write_project_board(workspace, board)
    return changed


def board_prompt_digest(board: dict[str, Any], max_tasks: int = 12, max_instruction_chars: int = 200) -> str:
    if not board:
        return "No project board available."
    lines = [
        f"Goal: {_clean_text(board.get('goal'))}",
        f"Execution Strategy: {_clean_text(board.get('execution_strategy'))}",
        f"Summary: {json.dumps(board.get('summary', {}), ensure_ascii=False)}",
        "Open Tasks:",
    ]
    count = 0
    for task in (board.get("tasks") or []):
        if not isinstance(task, dict):
            continue
        if task.get("status") == "completed":
            continue
        deps = ", ".join(task.get("depends_on", []) or [])
        instruction = str(task.get("instruction") or "")
        if len(instruction) > max_instruction_chars:
            instruction = instruction[:max_instruction_chars].rstrip() + "..."
        lines.append(f"- [{task.get('status', 'pending')}] {task.get('owner_role')}: {instruction} | depends_on={deps or '-'}")
        count += 1
        if count >= max_tasks:
            remaining = sum(
                1 for t in (board.get("tasks") or [])
                if isinstance(t, dict) and t.get("status") != "completed"
            ) - count
            if remaining > 0:
                lines.append(f"- ... ({remaining} more tasks not shown)")
            break
    if count == 0:
        lines.append("- none")
    return "\n".join(lines)


def write_task_execution_plan(workspace: str, project_brief: dict[str, Any], role_plan: dict[str, Any], board: dict[str, Any]) -> str:
    target_path = os.path.join(os.path.abspath(workspace), TASK_EXECUTION_PLAN_REL_PATH)

    research_lines: list[str] = []
    for item in _clean_list(project_brief.get("evidence_summary"))[:8]:
        research_lines.append(f"- {item}")
    if not research_lines:
        for item in _clean_list(project_brief.get("research_notes"))[:6]:
            research_lines.append(f"- {item}")
    notebook_summary = _clean_text(project_brief.get("notebook_summary"))
    if notebook_summary:
        trimmed = notebook_summary if len(notebook_summary) <= 280 else notebook_summary[:277].rstrip() + "..."
        research_lines.append(f"- NotebookLM: {trimmed}")
    if not research_lines:
        research_lines.append("- (additional research needed)")

    lines = [
        "# Task Execution Plan",
        "",
        "## Overview",
        f"- project_goal: {_clean_text(project_brief.get('goal'))}",
        f"- execution_strategy: {_clean_text(role_plan.get('execution_strategy') or 'parallel')}",
        f"- role_count: {len(role_plan.get('roles') or [])}",
        f"- module_count: {len(role_plan.get('modules') or [])}",
        f"- task_count: {len(board.get('tasks') or [])}",
        "",
        "## Evidence",
        *research_lines,
        "",
        "## Stage Order",
    ]
    for index, step in enumerate(role_plan.get("planning_steps") or default_planning_steps(), start=1):
        lines.append(f"{index}. {step.get('name')}")
        lines.append(f"   objective: {_clean_text(step.get('objective'))}")
        criteria = _clean_list(step.get("exit_criteria"))
        lines.append(f"   exit_criteria: {', '.join(criteria) if criteria else '-'}")
    lines.extend(["", "## Module Breakdown By Role"])

    role_lookup = {safe_id(role.get("id")): role for role in (role_plan.get("roles") or []) if isinstance(role, dict)}
    for module in (role_plan.get("modules") or []):
        if not isinstance(module, dict):
            continue
        owner = role_lookup.get(safe_id(module.get("owner_role")), {})
        lines.append(f"### {module.get('name')}")
        lines.append(f"- owner_role: {_role_name(owner) if owner else _clean_text(module.get('owner_role'))}")
        lines.append(f"- objective: {_clean_text(module.get('summary'))}")
        lines.append(f"- feature_slices: {', '.join(_clean_list(module.get('feature_slices'))) or '-'}")
        lines.append(f"- deliverables: {', '.join(_clean_list(module.get('deliverables'))) or '-'}")
        depends = ", ".join(_clean_list(module.get("depends_on"))) or "-"
        lines.append(f"- depends_on: {depends}")
        lines.append("- tasks:")
        for task in (module.get("tasks") or []):
            if not isinstance(task, dict):
                continue
            acceptance = ", ".join(_clean_list(task.get("acceptance"))) or "-"
            lines.append(f"  - [{task.get('phase', 'build')}] {task.get('instruction')}")
            lines.append(f"    acceptance: {acceptance}")
        lines.append("")

    lines.extend(
        [
            "## Execution Rules",
            "- Each task should finish as a small, independent slice of work.",
            "- Resolve dependencies using `depends_on` before parallelizing the next step.",
            "- Define scope and file boundaries before implementation begins.",
            "- Keep verification work as separate tasks instead of burying it inside build tasks.",
            "",
            "## Handoff Rules",
            "- Agents should communicate using task_id-scoped handoff, blocker, decision_request, decision_response, review_request, review_result, and result messages.",
            "- Include relevant file paths and acceptance criteria in each handoff or review request.",
            "- Every blocker should state what is blocked, why, and what decision or input is required.",
            "- Each receiving agent should check the inbox and acknowledge required messages before starting work.",
            "",
        ]
    )
    write_text(target_path, "\n".join(lines).rstrip() + "\n")
    return target_path
