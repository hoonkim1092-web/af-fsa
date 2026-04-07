"""
core/clarification.py
=====================
Clarification 단계: Brief 분석 → 질문 생성 → 사용자 답변 → Brief 병합.

Brief 생성 직후, RolePlan 이전에 삽입되어 사용자 입력의 모호성을 제거한다.
"""
from __future__ import annotations

import json
from typing import Any

from core.requirement_llm import execute_requirement_prompt


# ── 질문 생성 ────────────────────────────────────────────────────────

_CLARIFICATION_PROMPT_TEMPLATE = """\
You are a requirements analyst. Analyze the project brief below and generate
targeted clarification questions for ambiguous or missing requirements.

## Project Brief
{brief_json}

## Rules
- Generate 3-5 questions that would MOST impact the quality of implementation
- Do NOT ask about things already answered in the brief or evidence
- Each question must target a specific gap: UI preference, data source, deployment,
  performance requirement, user flow, scope boundary, etc.
- Provide 2-3 suggested options per question to reduce user effort
- Questions must be in Korean

## Output Format (JSON)
{{
  "questions": [
    {{
      "id": "Q1",
      "category": "ui|data|deployment|scope|performance|integration",
      "question": "질문 텍스트",
      "why": "이 질문이 왜 중요한지 한줄 설명",
      "options": ["옵션A", "옵션B", "옵션C"],
      "default": "옵션A"
    }}
  ]
}}
"""


def _safe_json_load(text: str) -> dict:
    """LLM 응답에서 JSON 추출. 코드블록 래핑 처리."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def generate_clarification_questions(
    project_brief: dict[str, Any],
    *,
    workspace: str | None = None,
    run_id: str = "",
) -> list[dict[str, Any]]:
    """Brief를 분석하여 모호한 부분에 대한 질문 리스트를 생성한다.

    Returns:
        질문 리스트. 각 항목: {id, category, question, why, options, default}
        LLM 실패 시 빈 리스트.
    """
    prompt = _CLARIFICATION_PROMPT_TEMPLATE.format(
        brief_json=json.dumps(project_brief, ensure_ascii=False, indent=2),
    )
    try:
        result = execute_requirement_prompt(prompt, workspace=workspace, run_id=run_id)
        if not result.get("ok"):
            return []
        parsed = _safe_json_load(result.get("text", ""))
        questions = parsed.get("questions") or []
        if not isinstance(questions, list):
            return []
        # 최소 필드 검증
        valid = []
        for q in questions:
            if isinstance(q, dict) and q.get("question") and q.get("options"):
                q.setdefault("id", f"Q{len(valid) + 1}")
                q.setdefault("category", "scope")
                q.setdefault("why", "")
                q.setdefault("default", q["options"][0] if q["options"] else "")
                valid.append(q)
        return valid
    except Exception:
        return []


def should_skip_clarification(
    project_brief: dict[str, Any],
    *,
    pipeline: str = "project",
    execution_mode: str = "approval",
) -> bool:
    """Clarification을 스킵해야 하는지 판단한다.

    스킵 조건:
    - single 파이프라인 (단순 태스크)
    - Brief의 주요 필드가 충분히 채워져 있을 때 (빈 필드 2개 이하)
    """
    if pipeline == "single":
        return True
    # Brief 핵심 필드 중 비어있는 것 체크
    check_fields = ["tech_stack", "deliverables", "user_flows", "constraints", "architecture_style"]
    filled_count = 0
    for f in check_fields:
        value = project_brief.get(f)
        if isinstance(value, list) and len(value) > 0:
            filled_count += 1
        elif isinstance(value, str) and value.strip():
            filled_count += 1
    # 5개 중 3개 이상 채워져 있으면 충분히 구체적 → skip
    if filled_count >= 3:
        return True
    return False


# ── Brief 병합 ───────────────────────────────────────────────────────

def merge_clarification(
    project_brief: dict[str, Any],
    questions: list[dict[str, Any]],
    answers: list[str],
) -> dict[str, Any]:
    """사용자 답변을 Brief에 병합하여 enriched_brief 반환."""
    enriched = dict(project_brief)
    clarification_log: list[dict] = []

    for q, answer in zip(questions, answers):
        selected = answer.strip() if answer.strip() else q.get("default", "")
        clarification_log.append({
            "question": q["question"],
            "answer": selected,
            "category": q.get("category", "scope"),
        })

        category = q.get("category", "")
        if category == "ui":
            enriched["architecture_style"] = selected
        elif category == "deployment":
            enriched.setdefault("constraints", [])
            if isinstance(enriched["constraints"], list):
                enriched["constraints"].append(f"배포: {selected}")
        elif category == "data":
            enriched.setdefault("constraints", [])
            if isinstance(enriched["constraints"], list):
                enriched["constraints"].append(f"데이터 소스: {selected}")
        elif category == "scope":
            if any(kw in selected for kw in ["불필요", "없", "제외"]):
                enriched.setdefault("non_goals", [])
                if isinstance(enriched["non_goals"], list):
                    enriched["non_goals"].append(selected)
            else:
                enriched.setdefault("deliverables", [])
                if isinstance(enriched["deliverables"], list):
                    enriched["deliverables"].append(selected)
        elif category == "performance":
            enriched.setdefault("constraints", [])
            if isinstance(enriched["constraints"], list):
                enriched["constraints"].append(f"성능: {selected}")
        elif category == "integration":
            enriched.setdefault("constraints", [])
            if isinstance(enriched["constraints"], list):
                enriched["constraints"].append(f"연동: {selected}")

    enriched["clarification_log"] = clarification_log
    return enriched


def auto_apply_defaults(
    project_brief: dict[str, Any],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """FSA 모드: 모든 질문에 기본값 자동 적용."""
    answers = [q.get("default", "") for q in questions]
    return merge_clarification(project_brief, questions, answers)
