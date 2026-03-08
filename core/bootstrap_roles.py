import json

from core.agent_runner import ModelRouter
from core.llm_engine import LLMEngine
from core.utils import now_iso, safe_id, safe_json_load


BOOTSTRAP_ROLES = {
    "research_director": {
        "name": "Himari Bootstrap",
        "role": "Project Research Director",
        "tone": "precise",
        "traits": ["research", "planning", "evidence"],
        "system_ko": (
            "당신은 프로젝트 착수 전담 리서처다. 필요한 스킬, 구현 제약, 위험 요소, 역할 분해 근거를 "
            "짧고 구조적으로 정리한다."
        ),
        "signature_lines": ["근거부터 고정한다.", "자료를 모은 뒤 설계한다."],
    },
    "pd_director": {
        "name": "Lilith Bootstrap",
        "role": "Project Planning Director",
        "tone": "directive",
        "traits": ["orchestration", "planning", "delivery"],
        "system_ko": (
            "당신은 프로젝트 PD다. 리서치 브리프를 바탕으로 역할을 분해하고 각 역할의 목표와 필요한 "
            "스킬을 설계한다."
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
            elif any(token in sid for token in ("game", "logic", "rule", "state", "deck", "hand", "turn", "evaluator")):
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
            "backend_dev": "서버, 데이터, 외부 연동이 필요하면 해당 레이어를 구현한다.",
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

        todo_items = [f"{role['name']}: {role['objective']}" for role in roles]
        return {
            "execution_strategy": "parallel",
            "roles": roles[:5],
            "todo_items": todo_items,
        }

    def plan(self, task_input: str, project_brief: dict) -> dict:
        prompt = f"""
You are a project planning director.
User task: {task_input}
Research brief(JSON): {json.dumps(project_brief, ensure_ascii=False)}

Return JSON only:
{{
  "execution_strategy": "parallel|sequential",
  "roles": [
    {{
      "id": "snake_case_role",
      "name": "Short Role Name",
      "objective": "Role objective",
      "required_skills": ["skill_a", "skill_b"]
    }}
  ],
  "todo_items": ["short actionable item"]
}}

Rules:
- 2 to 5 roles only.
- Prefer practical implementation roles.
- required_skills must be English snake_case.
- Keep objectives concrete.
""".strip()

        try:
            payload = self.llm.generate_json(prompt)
            if not isinstance(payload, dict):
                raise ValueError("planner_payload_not_dict")
            roles = payload.get("roles", [])
            if not isinstance(roles, list) or not roles:
                raise ValueError("planner_roles_missing")
            normalized = []
            for item in roles[:5]:
                if not isinstance(item, dict):
                    continue
                role_id = safe_id(str(item.get("id") or item.get("name") or "role"))
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
                    }
                )
            if not normalized:
                raise ValueError("planner_roles_empty")
            todo_items = [str(x).strip() for x in (payload.get("todo_items") or []) if str(x).strip()]
            return {
                "execution_strategy": str(payload.get("execution_strategy") or "parallel"),
                "roles": normalized,
                "todo_items": todo_items,
            }
        except Exception:
            return self._fallback_roles(task_input, project_brief)
