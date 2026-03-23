import json
import os

from core.agent_runner import ModelRouter
from core.llm_engine import LLMEngine
from core.utils import now_iso, safe_id, safe_json_load

_POLICY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "policy.yaml")


def _load_task_decomposition_policy() -> dict:
    """policy.yaml 의 task_decomposition 섹션을 로드한다."""
    try:
        import yaml
        with open(_POLICY_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("task_decomposition") or {}
    except Exception:
        return {}


def _build_policy_rules() -> str:
    """정책 파일에서 프롬프트 규칙 문자열을 생성한다."""
    policy = _load_task_decomposition_policy()
    if not policy:
        # 폴백: 기본 규칙
        return (
            "- 2 to 5 roles only.\n"
            "- Prefer practical implementation roles.\n"
            "- required_skills, role ids, module ids, task ids, owner_role must be English snake_case.\n"
            "- planning_steps should usually be 3 to 5 items.\n"
            "- Split work into modules that can be implemented independently.\n"
            "- Every module should have small tasks, not one giant task.\n"
            "- Use explicit dependencies only when needed.\n"
            "- Include at least one verify task overall.\n"
            "- Keep objectives and summaries concrete."
        )
    lines = []
    roles = policy.get("roles") or {}
    if roles:
        lines.append(f"- {roles.get('min', 2)} to {roles.get('max', 5)} roles only.")
        if roles.get("prefer"):
            lines.append(f"- Prefer {roles['prefer'].replace('_', ' ')} roles.")
    steps = policy.get("planning_steps") or {}
    if steps:
        lines.append(f"- planning_steps should usually be {steps.get('min', 3)} to {steps.get('max', 5)} items.")
    tasks = policy.get("tasks") or {}
    if tasks.get("naming_convention"):
        lines.append(f"- required_skills, role ids, module ids, task ids, owner_role must be English {tasks['naming_convention']}.")
    for constraint in (policy.get("constraints") or []):
        lines.append(f"- {constraint}")
    return "\n".join(lines)


BOOTSTRAP_ROLES = {
    "research_director": {
        "name": "Himari Bootstrap",
        "role": "Project Research Director",
        "tone": "precise",
        "traits": ["research", "planning", "evidence"],
        "system_ko": (
            "프로젝트 착수 전에 필요한 스킬, 제약, 위험, 역할 분해 근거를 정리한다."
        ),
        "signature_lines": ["근거부터 고정한다.", "자료를 모은 뒤 설계한다."],
    },
    "pd_director": {
        "name": "Lilith Bootstrap",
        "role": "Project Planning Director",
        "tone": "directive",
        "traits": ["orchestration", "planning", "delivery"],
        "system_ko": (
            "리서치 브리프를 바탕으로 역할, 모듈, 단계별 작업 계획을 구조화한다."
        ),
        "signature_lines": ["역할부터 나눈다.", "계획 없이 실행하지 않는다."],
    },
}


def build_bootstrap_agent(role_id: str) -> dict:
    sid = safe_id(role_id)
    base = BOOTSTRAP_ROLES.get(sid, {})
    return {
        "id": sid,
        "name": base.get("name", sid),
        "role": base.get("role", sid),
        "tone": base.get("tone", "precise"),
        "traits": list(base.get("traits", [])),
        "system_ko": base.get("system_ko", ""),
        "signature_lines": list(base.get("signature_lines", [])),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


class ProjectPlanningDirector:
    """Builds a role plan from the research brief."""

    def __init__(self, mr: ModelRouter):
        self.mr = mr
        engine_id = self.mr.pick("orchestrator") if hasattr(self.mr, "pick") else "gemini-1.5-pro-latest"
        self.llm = LLMEngine(model_name=engine_id)

    def _fallback_roles(self, task_input: str, project_brief: dict) -> dict:
        goal = str(project_brief.get("goal") or task_input).strip() or task_input
        required_skills = [safe_id(str(s)) for s in (project_brief.get("required_skills") or []) if str(s).strip()]
        grouped = {
            "frontend_dev": [],
            "backend_dev": [],
            "game_logic_dev": [],
            "designer": [],
            "qa_engineer": [],
        }

        for sid in required_skills:
            if any(token in sid for token in ("ui", "front", "react", "css", "html", "layout", "browser", "page")):
                grouped["frontend_dev"].append(sid)
            elif any(token in sid for token in ("api", "server", "db", "backend", "storage", "auth", "data")):
                grouped["backend_dev"].append(sid)
            elif any(token in sid for token in ("game", "logic", "rule", "state", "deck", "hand", "turn", "evaluator", "workflow")):
                grouped["game_logic_dev"].append(sid)
            elif any(token in sid for token in ("design", "ux", "visual", "wireframe", "prototype")):
                grouped["designer"].append(sid)
            elif any(token in sid for token in ("test", "qa", "guard", "validate")):
                grouped["qa_engineer"].append(sid)

        if "game" in goal.lower() and not grouped["game_logic_dev"]:
            grouped["game_logic_dev"].append("gameplay_core")
        if any(token in goal.lower() for token in ("web", "ui", "page", "screen")) and not grouped["frontend_dev"]:
            grouped["frontend_dev"].append("frontend_game_ui")
        if not grouped["qa_engineer"]:
            grouped["qa_engineer"].append("integration_test_guard")

        roles = []
        objectives = {
            "frontend_dev": "사용자 화면과 상호작용 레이어를 구현한다.",
            "backend_dev": "서버, 데이터, 외부 연동 레이어를 구현한다.",
            "game_logic_dev": "핵심 규칙과 상태 전이를 구현한다.",
            "designer": "정보 구조와 화면 흐름, 비주얼 방향을 설계한다.",
            "qa_engineer": "핵심 플로우와 회귀 시나리오를 검증한다.",
        }
        titles = {
            "frontend_dev": "Frontend Dev",
            "backend_dev": "Backend Dev",
            "game_logic_dev": "Game Logic Dev",
            "designer": "Product Designer",
            "qa_engineer": "QA Engineer",
        }

        for role_id, skills in grouped.items():
            if not skills and role_id not in {"qa_engineer"}:
                continue
            roles.append(
                {
                    "id": role_id,
                    "name": titles[role_id],
                    "objective": objectives[role_id],
                    "required_skills": list(dict.fromkeys(skills)),
                }
            )

        if not roles:
            roles.append(
                {
                    "id": "general_dev",
                    "name": "General Dev",
                    "objective": "프로젝트를 단일 구현 경로로 완성한다.",
                    "required_skills": required_skills[:4],
                }
            )

        return {
            "execution_strategy": "parallel",
            "roles": roles[:5],
            "todo_items": [f"{role['name']}: {role['objective']}" for role in roles[:5]],
        }

    def _normalize_roles(self, payload: dict) -> list[dict]:
        roles = payload.get("roles", [])
        if not isinstance(roles, list) or not roles:
            raise ValueError("planner_roles_missing")
        normalized = []
        for item in roles[:5]:
            if not isinstance(item, dict):
                continue
            role_id = safe_id(str(item.get("id") or item.get("name") or "role"))
            if not role_id:
                continue
            normalized.append(
                {
                    "id": role_id,
                    "name": str(item.get("name") or role_id),
                    "objective": str(item.get("objective") or "").strip(),
                    "required_skills": [
                        safe_id(str(s))
                        for s in (item.get("required_skills") or [])
                        if str(s).strip()
                    ],
                    "owned_modules": [
                        safe_id(str(s))
                        for s in (item.get("owned_modules") or [])
                        if str(s).strip()
                    ],
                }
            )
        if not normalized:
            raise ValueError("planner_roles_empty")
        return normalized

    def _normalize_planning_steps(self, payload: dict) -> list[dict]:
        raw_steps = payload.get("planning_steps", [])
        if not isinstance(raw_steps, list):
            return []
        steps = []
        for index, item in enumerate(raw_steps, start=1):
            if not isinstance(item, dict):
                continue
            step_id = safe_id(str(item.get("id") or item.get("name") or f"step_{index}"))
            if not step_id:
                continue
            steps.append(
                {
                    "id": step_id,
                    "name": str(item.get("name") or step_id).strip(),
                    "objective": str(item.get("objective") or "").strip(),
                    "exit_criteria": [
                        str(x).strip() for x in (item.get("exit_criteria") or []) if str(x).strip()
                    ],
                }
            )
        return steps

    def _normalize_modules(self, payload: dict) -> list[dict]:
        raw_modules = payload.get("modules", [])
        if not isinstance(raw_modules, list):
            return []
        modules = []
        for index, item in enumerate(raw_modules, start=1):
            if not isinstance(item, dict):
                continue
            module_id = safe_id(str(item.get("id") or item.get("name") or f"module_{index}"))
            if not module_id:
                continue
            tasks = []
            for task_index, raw_task in enumerate(item.get("tasks") or [], start=1):
                if not isinstance(raw_task, dict):
                    continue
                task_id = safe_id(str(raw_task.get("id") or f"{module_id}_task_{task_index}"))
                if not task_id:
                    continue
                tasks.append(
                    {
                        "id": task_id,
                        "title": str(raw_task.get("title") or raw_task.get("instruction") or task_id).strip(),
                        "instruction": str(raw_task.get("instruction") or raw_task.get("title") or task_id).strip(),
                        "owner_role": safe_id(str(raw_task.get("owner_role") or item.get("owner_role") or "")),
                        "phase": safe_id(str(raw_task.get("phase") or "build")) or "build",
                        "depends_on": [
                            safe_id(str(dep)) for dep in (raw_task.get("depends_on") or []) if str(dep).strip()
                        ],
                        "acceptance": [
                            str(x).strip() for x in (raw_task.get("acceptance") or []) if str(x).strip()
                        ],
                        "artifacts": [
                            str(x).strip() for x in (raw_task.get("artifacts") or []) if str(x).strip()
                        ],
                    }
                )
            modules.append(
                {
                    "id": module_id,
                    "name": str(item.get("name") or module_id).strip(),
                    "summary": str(item.get("summary") or "").strip(),
                    "owner_role": safe_id(str(item.get("owner_role") or "")),
                    "depends_on": [
                        safe_id(str(dep)) for dep in (item.get("depends_on") or []) if str(dep).strip()
                    ],
                    "deliverables": [
                        str(x).strip() for x in (item.get("deliverables") or []) if str(x).strip()
                    ],
                    "feature_slices": [
                        str(x).strip() for x in (item.get("feature_slices") or []) if str(x).strip()
                    ],
                    "tasks": tasks,
                }
            )
        return modules

    def _ensure_qa_role(self, payload: dict) -> None:
        """QA 역할이 없으면 강제로 추가한다. QA는 필수."""
        roles = payload.get("roles") or []
        qa_ids = {"qa_engineer", "qa", "tester", "quality_assurance"}
        has_qa = any(
            safe_id(str(r.get("id", ""))) in qa_ids
            for r in roles
            if isinstance(r, dict)
        )
        if has_qa:
            return

        # policy.yaml의 required_roles에서 QA 정의를 로드
        policy = _load_task_decomposition_policy()
        required_roles = policy.get("required_roles") or []
        qa_def = next(
            (r for r in required_roles if safe_id(str(r.get("id", ""))) == "qa_engineer"),
            None,
        )

        qa_role = {
            "id": "qa_engineer",
            "name": qa_def.get("name", "QA Engineer") if qa_def else "QA Engineer",
            "objective": qa_def.get("objective", "핵심 플로우와 회귀 시나리오를 검증한다.") if qa_def else "핵심 플로우와 회귀 시나리오를 검증한다.",
            "required_skills": (qa_def.get("required_skills") or ["integration_test_guard"]) if qa_def else ["integration_test_guard"],
            "owned_modules": [],
        }
        payload["roles"].append(qa_role)

        # verify 단계 모듈이 없으면 QA 전용 검증 모듈도 추가
        modules = payload.get("modules") or []
        has_verify_module = any(
            any(
                str(t.get("phase", "")).strip() == "verify"
                for t in (m.get("tasks") or [])
                if isinstance(t, dict)
            )
            for m in modules
            if isinstance(m, dict)
        )
        if not has_verify_module:
            verify_module = {
                "id": "qa_verification",
                "name": "QA Verification",
                "summary": "전체 통합 검증 및 회귀 테스트",
                "owner_role": "qa_engineer",
                "depends_on": [
                    safe_id(str(m.get("id", "")))
                    for m in modules
                    if isinstance(m, dict) and safe_id(str(m.get("id", "")))
                ],
                "deliverables": ["테스트 결과 리포트"],
                "feature_slices": ["통합 테스트", "회귀 테스트"],
                "tasks": [
                    {
                        "id": "qa_integration_verify",
                        "title": "통합 검증",
                        "instruction": "QA Engineer: 모든 모듈의 통합 동작을 검증하고 테스트 결과를 리포트로 남겨라.",
                        "owner_role": "qa_engineer",
                        "phase": "verify",
                        "depends_on": [
                            safe_id(str(m.get("id", "")))
                            for m in modules
                            if isinstance(m, dict) and safe_id(str(m.get("id", "")))
                        ],
                        "acceptance": ["전체 통합 테스트 통과", "테스트 리포트 생성"],
                        "artifacts": ["tests/", "test_report.md"],
                    }
                ],
            }
            payload.setdefault("modules", []).append(verify_module)
            qa_role["owned_modules"] = ["qa_verification"]

        # todo_items에 QA 항목 추가
        todo_items = payload.get("todo_items") or []
        if not any("qa" in str(item).lower() or "검증" in str(item) for item in todo_items):
            payload.setdefault("todo_items", []).append(
                "QA Engineer: 핵심 플로우와 회귀 시나리오를 검증한다."
            )

    def plan(self, task_input: str, project_brief: dict) -> dict:
        prompt = f"""
You are a project planning director.
User task: {task_input}
Research brief(JSON): {json.dumps(project_brief, ensure_ascii=False)}

Return JSON only:
{{
  "execution_strategy": "parallel|sequential",
  "planning_steps": [
    {{
      "id": "scope_contracts",
      "name": "Stage name",
      "objective": "What this stage accomplishes",
      "exit_criteria": ["observable completion rule"]
    }}
  ],
  "roles": [
    {{
      "id": "snake_case_role",
      "name": "Short Role Name",
      "objective": "Role objective",
      "required_skills": ["skill_a", "skill_b"],
      "owned_modules": ["module_a"]
    }}
  ],
  "modules": [
    {{
      "id": "snake_case_module",
      "name": "Module name",
      "summary": "Why this module exists",
      "owner_role": "snake_case_role",
      "depends_on": ["other_module_id"],
      "deliverables": ["user-visible deliverable"],
      "feature_slices": ["small implementation slice"],
      "tasks": [
        {{
          "id": "task_id",
          "title": "Task title",
          "instruction": "Role-prefixed actionable instruction",
          "owner_role": "snake_case_role",
          "phase": "scope|build|integrate|verify",
          "depends_on": ["task_or_module_id"],
          "acceptance": ["completion signal"],
          "artifacts": ["path/or/output"]
        }}
      ]
    }}
  ],
  "todo_items": ["short actionable item"]
}}

Rules:
{_build_policy_rules()}

MANDATORY: You MUST always include a "qa_engineer" role. QA is non-negotiable.
The qa_engineer must own at least one module with verify-phase tasks.
""".strip()

        try:
            response = self.llm.generate_json(prompt)
            payload = response if isinstance(response, dict) else safe_json_load(response)
            if not isinstance(payload, dict):
                raise ValueError("planner_payload_not_dict")
            normalized_payload = {
                "execution_strategy": str(payload.get("execution_strategy") or "parallel"),
                "planning_steps": self._normalize_planning_steps(payload),
                "roles": self._normalize_roles(payload),
                "modules": self._normalize_modules(payload),
                "todo_items": [str(x).strip() for x in (payload.get("todo_items") or []) if str(x).strip()],
            }
            self._ensure_qa_role(normalized_payload)
            return normalized_payload
        except Exception:
            return self._fallback_roles(task_input, project_brief)
