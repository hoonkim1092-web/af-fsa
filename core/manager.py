import inspect
import os
from core.engine_auth import get_engine_api_key, supports_cli_bootstrap
from core.requirement_llm import execute_requirement_prompt
from core.utils import (
    safe_id, read_yaml, write_yaml, now_iso, get_random_signature,
    print_agent_msg, safe_json_load, apply_agent_overrides, safe_generate
)
from core.config_paths import AGENTS_DIR, GLOBAL_AGENTS_DIR

class AgentManager:
    """Manages Agent YAML files and storage."""
    def __init__(self, mr):
        self.mr = mr

    def _build_fallback_agent(self, role_spec: str) -> dict:
        role_text = str(role_spec or "").strip() or "General Assistant"
        agent_id = safe_id(role_text) or "agent"
        return {
            "name": f"agent_{agent_id}",
            "role": role_text,
            "tone": "calm, direct, pragmatic",
            "traits": ["practical", "concise", "execution-focused"],
            "system_ko": (
                f"당신은 {role_text} 역할의 실행 에이전트다. "
                "현재 워크스페이스 안에서 필요한 파일을 직접 만들거나 수정해 작업 결과를 남겨라."
            ),
            "signature_lines": [
                f"[{role_text}] 바로 실행합니다.",
                f"[{role_text}] 작업 결과를 파일로 남깁니다.",
            ],
        }

    def _agent_path(self, role_spec: str, workspace: str | None = None) -> str:
        base_dir = os.path.join(workspace, "agents") if workspace else AGENTS_DIR
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, f"{safe_id(role_spec)}.yaml")

    def get_or_create(self, role_spec: str, workspace: str | None = None) -> dict:
        path = self._agent_path(role_spec, workspace)
        if os.path.exists(path):
            return apply_agent_overrides(read_yaml(path), role_spec)

        # Reuse global agent template first, then local project copy.
        global_path = os.path.join(GLOBAL_AGENTS_DIR, f"{safe_id(role_spec)}.yaml")
        if os.path.exists(global_path):
            data = read_yaml(global_path)
            data["updated_at"] = now_iso()
            write_yaml(path, data)
            return apply_agent_overrides(data, role_spec)

        # [New SDK] Client 기반 에이전트 생성 (Triad: agent_create = Gemini Pro)
        _api_key = get_engine_api_key("google")
        if _api_key:
            from google import genai
            from model_utils import normalize_model_name, generate_content_with_self_heal
            _client = genai.Client(api_key=_api_key)
            _model_name = normalize_model_name(self.mr.pick("agent_create"))
        else:
            _client = None
            _model_name = ""
        prompt = f"""
ROLE_SPEC: "{role_spec}"
JSON 출력:
{{"name":"...", "role":"...", "tone":"...", "traits":["..."], "system_ko": "...", "signature_lines": ["...", "..."]}}

[필수 규칙]
1. 모든 출력(tone, traits, system_ko, signature_lines)은 반드시 **한국어**로 작성해야 합니다.
2. **system_ko**: 에이전트의 페르소나와 행동 지침을 상세한 한국어로 작성하세요.
3. **signature_lines**: 에이전트가 대화를 시작할 때 사용할 시그니처 대사(한국어)를 3~5개 작성하세요. 캐릭터의 성격을 잘 드러내야 합니다.
"""
        res = generate_content_with_self_heal(_client, _model_name, prompt) if _client else None
        data = safe_json_load(res.text if res else "{}")
        if not data and supports_cli_bootstrap():
            data = self._build_fallback_agent(role_spec)
        data["name"] = data.get("name") or f"agent_{safe_id(role_spec)}"
        data["role"] = data.get("role") or role_spec
        data["tone"] = data.get("tone") or "calm, direct, pragmatic"
        data["traits"] = data.get("traits") or ["practical", "concise", "execution-focused"]
        fallback = self._build_fallback_agent(role_spec)
        data["system_ko"] = data.get("system_ko") or fallback["system_ko"]
        data["signature_lines"] = data.get("signature_lines") or fallback["signature_lines"]
        data["created_at"] = now_iso()
        write_yaml(path, data)
        return apply_agent_overrides(data, role_spec)

    def install_skills(self, role_spec: str, skill_ids: list[str], workspace: str | None = None) -> list[str]:
        if not skill_ids:
            return []
        path = self._agent_path(role_spec, workspace)
        if os.path.exists(path):
            agent = read_yaml(path)
        else:
            params = inspect.signature(self.get_or_create).parameters
            if "workspace" in params:
                agent = self.get_or_create(role_spec, workspace=workspace)
            else:
                agent = self.get_or_create(role_spec)
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

    def analyze(self, agent: dict, task_input: str, workspace: str | None = None) -> dict:
        sig = get_random_signature(agent)
        print_agent_msg(agent.get("name", "Agent"), f"태스크 분석을 시작합니다... \"{task_input}\"", sig)
        
        # [New SDK] 요구사항 분석 — client는 아래 try 블록에서 생성
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
            result = execute_requirement_prompt(prompt, workspace=workspace)
            if not result.get("ok"):
                raise RuntimeError("requirement_llm_unavailable")
            data = safe_json_load(result.get("text") or "{}")
            if not isinstance(data, dict):
                raise ValueError("requirement_response_not_dict")
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
