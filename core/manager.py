import os
import google.generativeai as genai
from core.utils import (
    safe_id, read_yaml, write_yaml, now_iso, get_random_signature,
    print_agent_msg, safe_json_load, apply_agent_overrides, safe_generate
)
from core.config_paths import AGENTS_DIR, GLOBAL_AGENTS_DIR

class AgentManager:
    """Manages Agent YAML files and storage."""
    def __init__(self, mr):
        self.mr = mr

    def _agent_path(self, role_spec: str) -> str:
        return os.path.join(AGENTS_DIR, f"{safe_id(role_spec)}.yaml")

    def get_or_create(self, role_spec: str) -> dict:
        path = self._agent_path(role_spec)
        if os.path.exists(path):
            return apply_agent_overrides(read_yaml(path), role_spec)

        # Reuse global agent template first, then local project copy.
        global_path = os.path.join(GLOBAL_AGENTS_DIR, f"{safe_id(role_spec)}.yaml")
        if os.path.exists(global_path):
            data = read_yaml(global_path)
            data["updated_at"] = now_iso()
            write_yaml(path, data)
            return apply_agent_overrides(data, role_spec)

        # Use Gemini 3.0 for Agent Creation (V22.0 Gold Standard)
        model = genai.GenerativeModel(self.mr.pick("agent_create"))
        prompt = f"""
ROLE_SPEC: "{role_spec}"
JSON 출력:
{{"name":"...", "role":"...", "tone":"...", "traits":["..."], "system_ko": "...", "signature_lines": ["...", "..."]}}

[필수 규칙]
1. 모든 출력(tone, traits, system_ko, signature_lines)은 반드시 **한국어**로 작성해야 합니다.
2. **system_ko**: 에이전트의 페르소나와 행동 지침을 상세한 한국어로 작성하세요.
3. **signature_lines**: 에이전트가 대화를 시작할 때 사용할 시그니처 대사(한국어)를 3~5개 작성하세요. 캐릭터의 성격을 잘 드러내야 합니다.
"""
        res = safe_generate(model, prompt, generation_config={"response_mime_type": "application/json"})
        data = safe_json_load(res.text)
        data["name"] = data.get("name") or f"agent_{safe_id(role_spec)}"
        data["role"] = data.get("role") or role_spec
        data["created_at"] = now_iso()
        write_yaml(path, data)
        return apply_agent_overrides(data, role_spec)

    def install_skills(self, role_spec: str, skill_ids: list[str]) -> list[str]:
        if not skill_ids:
            return []
        path = self._agent_path(role_spec)
        agent = read_yaml(path) if os.path.exists(path) else self.get_or_create(role_spec)
        current = [safe_id(str(s)) for s in (agent.get("skills") or []) if str(s).strip()]
        merged = list(dict.fromkeys(current + [safe_id(s) for s in skill_ids]))
        agent["skills"] = merged
        agent["updated_at"] = now_iso()
        write_yaml(path, agent)
        return merged

class RequirementAnalyzer:
    """Analyzes task requirements and identifies missing skills."""
    def __init__(self, mr):
        self.mr = mr

    def _fallback_missing_skills(self, task_input: str, role_text: str) -> list[str]:
        text = f"{task_input} {role_text}".lower()
        picks: list[str] = []
        rules = [
            ("issue_tracker", ["이슈", "추적", "ticket", "issue", "책임", "audit", "로그"]),
            ("data_visualize", ["시각화", "대시보드", "차트", "그래프", "요약"]),
        ]
        for sid, kws in rules:
            if any(k in text for k in kws):
                picks.append(sid)
        return list(dict.fromkeys([safe_id(s) for s in picks]))[:5]

    def analyze(self, agent: dict, task_input: str) -> dict:
        sig = get_random_signature(agent)
        print_agent_msg(agent.get("name", "Agent"), f"태스크 분석을 시작합니다... \"{task_input}\"", sig)
        
        # Use Gemini 3.0 for Requirement Analysis (V22.0 Gold Standard)
        model = genai.GenerativeModel(self.mr.pick("requirement"))
        prompt = f"""
AgentRole: {agent.get("role")}
Task: {task_input}

JSON 출력:
{{
  "goal": "한 문장",
  "missing_skills": ["snake_case_0to5"],
  "constraints": ["network_allowed", "no_system_tools", "data_io_allowed"],
  "risk_level": "normal|elevated|strict"
}}

규칙:
- missing_skills 0~5개
- 스킬명 snake_case
- data 분석이면 needs_pandas 추가
"""
        try:
            res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            data = safe_json_load(res.text)
        except Exception as e:
            data = {
                "goal": task_input,
                "missing_skills": self._fallback_missing_skills(task_input, str(agent.get("role", ""))),
                "constraints": ["network_allowed", "no_system_tools", "data_io_allowed"],
                "risk_level": "normal",
                "analysis_fallback": f"llm_unavailable:{type(e).__name__}",
            }
        data.setdefault("goal", task_input)
        data.setdefault("missing_skills", [])
        data.setdefault("constraints", ["network_allowed", "no_system_tools", "data_io_allowed"])
        rl = data.get("risk_level", "normal")
        if rl not in ("normal", "elevated", "strict"):
            data["risk_level"] = "normal"

        data["missing_skills"] = [safe_id(str(s)) for s in (data["missing_skills"] or []) if str(s).strip()]
        return data
