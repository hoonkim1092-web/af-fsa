"""
core/role_decomposer.py
========================
역할 기반 엔진 매핑 + 스킬 리서치 전담 모듈.
factory_manager.py 에서 추출.

주요 함수:
  - resolve_agent_engine(skills): 스킬 목록 → 최적 엔진 ID
  - get_engine_model(engine_id): 엔진 ID → 최종 LLM 모델명
  - research_required_skills(role, ...): Himari 리서치 → 필요 스킬 목록
"""

import os
import yaml

from model_utils import resolve_dynamic_model
from core.llm_engine import LLMEngine
from core.research_engine import query_notebooklm, generate_deep_research_prompt


def log(step, msg):
    print(f"[{step}] {msg}")


def get_random_signature(agent_config: dict) -> str:
    import random
    lines = agent_config.get("signature_lines")
    if not lines and "persona" in agent_config:
        lines = agent_config["persona"].get("signature_lines")
    if lines and isinstance(lines, list):
        return random.choice(lines)
    return ""


FACTORY_ROOT = os.getcwd()


def load_policy():
    policy_path = os.path.join(FACTORY_ROOT, "policy.yaml")
    if os.path.exists(policy_path):
        try:
            with open(policy_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            log("SYSTEM", f"Policy load error: {e}")
    return {}


def resolve_agent_engine(skills):
    """skills 목록을 바탕으로 policy.yaml을 스캔해 최적의 engine_id를 반환"""
    policy = load_policy()
    skill_defs = policy.get("skills", {})
    engine_defs = policy.get("engines", {})

    engine_counts = {}
    max_tier = 0
    max_risk_level = 0
    risk_mapping = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

    for s in skills:
        s_data = skill_defs.get(s, {})
        eid = s_data.get("engine_id", "gemini_flash")
        engine_counts[eid] = engine_counts.get(eid, 0) + 1
        tier = engine_defs.get(eid, {}).get("tier", 1)
        if tier > max_tier:
            max_tier = tier
        risk_str = s_data.get("risk", "LOW")
        risk_val = risk_mapping.get(risk_str.upper(), 1)
        if risk_val > max_risk_level:
            max_risk_level = risk_val

    if not skills:
        return "gemini_flash"

    selected_eid = "gemini_flash"
    current_best_tier = 0

    for eid, count in engine_counts.items():
        tier = engine_defs.get(eid, {}).get("tier", 1)
        if tier > current_best_tier:
            current_best_tier = tier
            selected_eid = eid
        elif tier == current_best_tier:
            if engine_counts.get(eid, 0) > engine_counts.get(selected_eid, 0):
                selected_eid = eid

    # 고위험 스킬 감지 시 최소 Tier 2 엔진 강제 보장
    if max_risk_level >= 3:
        target_tier = engine_defs.get(selected_eid, {}).get("tier", 1)
        if target_tier < 2:
            selected_eid = "codex"

    return selected_eid


def get_engine_model(engine_id: str) -> str:
    """엔진 식별자를 바탕으로 최종 LLM 모델명을 반환"""
    return resolve_dynamic_model(engine_id)


def research_required_skills(role, selected_model_name=None, skip_research=False):
    """Himari 리서치: 역할에 필요한 스킬 목록과 최적 코딩 엔진을 반환"""
    if selected_model_name is None:
        selected_model_name = resolve_dynamic_model("research_pro")

    llm = LLMEngine(model_name=selected_model_name)

    # load_agent_config을 순환 import 방지를 위해 여기서 직접 로드
    AGENTS_DIR = os.path.join(os.getenv("AGENT_PROJECT_ROOT") or FACTORY_ROOT, "agents")
    himari_path = os.path.join(AGENTS_DIR, "himari.yaml")
    himari_config = None
    if os.path.exists(himari_path):
        try:
            with open(himari_path, "r", encoding="utf-8") as f:
                himari_config = yaml.safe_load(f)
        except Exception:
            pass

    system_instruction = ""

    if himari_config:
        sig = get_random_signature(himari_config)
        if sig: print(f"\n[RESEARCH] Himari \"{sig}\"")
        log("RESEARCH", "Himari analysis starting...")
        system_instruction = himari_config.get("prompt", {}).get("system_ko", "")

        if not skip_research:
            log("RESEARCH", "Querying NotebookLM for Domain Deep-Dive...")
            from core.research_engine import classify_research_depth
            query = generate_deep_research_prompt(role)
            # 역할 텍스트 분석을 통한 자율 모드 선택
            target_mode = classify_research_depth(query)
            notebook_insight = query_notebooklm(query, mode=target_mode)

            if notebook_insight:
                log("RESEARCH", "Found valuable Deep-Dive insights from NotebookLM.")
                system_instruction += f"\n\n[NotebookLM Core Strategy/Recipe]:\n{notebook_insight}\n"

    try:
        prompt = f"""
        {system_instruction}
        
        [Target Request]
        Target Role/Domain: {role}
        
        Using the provided Deep-Dive insights (seasonal context, pricing, tools, step-by-step recipes), 
        recommend 2-3 essential tools/skills/methodologies.
        
        [CRITICAL]
        At least one skill MUST be a highly concrete 'Recipe' or 'Playbook' that the agent can execute immediately based on the Deep-Dive data.
        If the logic is complex (e.g., game engines, core algorithms), ALWAYS propose decomposing them into 'Atomic Modules' (separate files) to minimize token costs.
        
        [Output Format]
        Return purely a JSON block:
        ```json
        {{
            "thought_process": "Himari's tactical thought process in Korean (informal/assertive)",
            "recommended_tools": ["industry_skill_a", "industry_skill_b"],
            "coding_engine": "gemini-3.1-pro-preview"
        }}
        ```
        Skill names must be English snake_case.
        """

        data = llm.generate_json(prompt)

        tools = data.get("recommended_tools", ["core_module"])
        thought = data.get("thought_process", "")
        coding_engine = data.get("coding_engine", "gemini-3.1-pro-preview")

        if thought: log("HIMARI", f"thought: {thought}")
        log("HIMARI", f"Selected Execution Engine: {coding_engine}")

        return [s.strip() for s in tools], coding_engine

    except Exception as e:
        log("RESEARCH", f"[Himari Error] Failed to generate skills: {e}")
        return ["core_module"], "gemini-3.1-pro-preview"
