import os
import sys
import glob
import subprocess
import re
import shutil
from dotenv import load_dotenv

from model_utils import get_best_model
from core.llm_engine import LLMEngine
from core.research_engine import query_notebooklm, generate_deep_research_prompt
from core.git_manager import git_configure_and_push
from core.security_scanner import security_scan
from core.skill_registry import check_skill_exists, register_skill, rebuild_registry_from_disk

FACTORY_ROOT = os.getcwd()
AGENT_PROJECT_ROOT = os.getenv("AGENT_PROJECT_ROOT")

if AGENT_PROJECT_ROOT:
    AGENTS_DIR = os.path.join(AGENT_PROJECT_ROOT, "agents")
    FORGE_DIR = os.path.join(AGENT_PROJECT_ROOT, "skills", "forge")
else:
    AGENTS_DIR = os.path.join(FACTORY_ROOT, "agents")
    FORGE_DIR = os.path.join(FACTORY_ROOT, "skills", "forge")

WAREHOUSE_DIR = os.path.join(FACTORY_ROOT, "skills", "warehouse")

ANTIGRAVITY_REPO_URL = "https://github.com/guanyang/antigravity-skills.git"

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

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

def sync_warehouse():
    log("WAREHOUSE", "코어 최신 스킬 저장소 동기화 중...")
    if not os.path.exists(WAREHOUSE_DIR):
        try:
            subprocess.run(["git", "clone", ANTIGRAVITY_REPO_URL, WAREHOUSE_DIR], check=True)
            log("WAREHOUSE", "코어 스킬 창고 다운로드 완료")
        except Exception as e:
            log("WAREHOUSE", f"스킬 다운로드 실패: {e}")
    else:
        try:
            subprocess.run(["git", "-C", WAREHOUSE_DIR, "pull"], check=True)
            log("WAREHOUSE", "코어 최신 스킬 업데이트 완료")
        except Exception as e:
            log("WAREHOUSE", f"스킬 업데이트 실패(로컬 모드): {e}")

def find_existing_agent(role):
    agent_name = role.replace(" ", "-").lower() + "-agent"
    if os.path.exists(os.path.join(AGENTS_DIR, agent_name)): return agent_name
    return None

def load_agent_config(agent_name):
    config_path = os.path.join(AGENTS_DIR, f"{agent_name}.yaml")
    if not os.path.exists(config_path):
        global_path = os.path.join(FACTORY_ROOT, "agents", f"{agent_name}.yaml")
        if os.path.exists(global_path):
            config_path = global_path
        else:
            return None
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        log("SYSTEM", "스킬 PyYAML not installed. Returning None.")
        return None
    except Exception as e:
        log("SYSTEM", f"스킬 Error loading agent config: {e}")
        return None


def research_required_skills(role, selected_model_name="gemini-2.0-flash", skip_research=False):
    llm = LLMEngine(model_name=selected_model_name)
    
    himari_config = load_agent_config("himari")
    system_instruction = ""
    
    if himari_config:
        sig = get_random_signature(himari_config)
        if sig: print(f"\n[RESEARCH] Himari \"{sig}\"")
        log("RESEARCH", "Himari analysis starting...")
        system_instruction = himari_config.get("prompt", {}).get("system_ko", "")

        if not skip_research:
            log("RESEARCH", "Querying NotebookLM for Domain Deep-Dive...")
            query = generate_deep_research_prompt(role)
            notebook_insight = query_notebooklm(query)
            
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
        
        if thought: log("HIMARI", f"💭 {thought}")
        log("HIMARI", f"🛠️ Selected Execution Engine: {coding_engine}")
        
        return [s.strip() for s in tools], coding_engine
            
    except Exception as e: 
        log("RESEARCH", f"🆘 [Himari Error] Failed to generate skills: {e}")
        return ["core_module"], "gemini-3.1-pro-preview"

def normalize_skill_id(value):
    base = os.path.splitext(os.path.basename(str(value)))[0].strip().lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return base

def get_installed_skill_ids(agent_name):
    tools_dir = os.path.join(AGENTS_DIR, agent_name, "tools")
    if not os.path.exists(tools_dir):
        return set()
    installed = set()
    for path in glob.glob(os.path.join(tools_dir, "*.py")):
        sid = normalize_skill_id(path)
        if sid:
            installed.add(sid)
    return installed

def get_missing_skills(agent_name, required_skills):
    normalized_required = []
    for raw in required_skills:
        sid = normalize_skill_id(raw)
        if sid and sid not in normalized_required:
            normalized_required.append(sid)

    installed = get_installed_skill_ids(agent_name)
    missing = [sid for sid in normalized_required if sid not in installed]

    log("CHECK", f"required={normalized_required}")
    log("CHECK", f"installed={sorted(installed)}")
    log("CHECK", f"missing={missing}")
    return missing

def procure_skill(skill_name, role):
    # Check Registry first using semantic matching
    purpose_desc = f"Skill intended for {role} to handle {skill_name}"
    existing_skill_path = check_skill_exists(skill_name, purpose_desc)
    
    if existing_skill_path and os.path.exists(existing_skill_path):
        log("REGISTRY", f"Reusing existing skill from registry: {existing_skill_path}")
        return existing_skill_path

    found = glob.glob(os.path.join(WAREHOUSE_DIR, "**", f"{skill_name}.py"), recursive=True)
    if found: 
        register_skill(skill_name, purpose_desc, found[0])
        return found[0]
    
    forge_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
    if os.path.exists(forge_path): 
        register_skill(skill_name, purpose_desc, forge_path)
        return forge_path

    return forge_new_skill(skill_name, role)

def forge_new_skill(skill_name, role, coding_engine="gemini-2.0-flash"):
    log("FORGE", f"🔨 Forging new skill: '{skill_name}' (Engine: {coding_engine})")
    os.makedirs(FORGE_DIR, exist_ok=True)
    output_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
    
    llm = LLMEngine(model_name=get_best_model([coding_engine]))
    
    prompt = f"Write a professional Python CLI tool '{skill_name}.py' for the role '{role}'. Use argparse. Provide clean, robust code only. Code docstrings and user output MUST be in Korean. Return ONLY the python code."
    try:
        code = llm.generate(prompt)
        code = code.replace("```python", "").replace("```", "").strip()
        with open(output_path, "w", encoding="utf-8") as f: f.write(code)
        log("FORGE", f"✅ Forge complete: {output_path}")
        
        # Register the new skill
        register_skill(skill_name, f"Dynamically forged skill for {role}", output_path)
        
        return output_path
    except Exception as e:
        log("FORGE", f"❌ Forge failed: {e}")
        return None

def assemble_and_push(agent_name, role, skill_paths, selected_model="gemini-2.0-flash"):
    target_dir = os.path.join(AGENTS_DIR, agent_name)
    tools_dir = os.path.join(target_dir, "tools")
    os.makedirs(tools_dir, exist_ok=True)
    
    padding_protocol = ""
    PROTOCOL_PATH = os.path.join(FACTORY_ROOT, "skills", "core", "learning_protocol.md")
    if os.path.exists(PROTOCOL_PATH):
        with open(PROTOCOL_PATH, "r", encoding="utf-8") as pf:
            padding_protocol = pf.read()

    with open(os.path.join(target_dir, "profile.md"), "w", encoding="utf-8") as f:
        f.write(f"# Agent Role: {role}\nEngine: {selected_model}\n\nGenerated by Logi-Mind Factory Manager.\n\n{padding_protocol}")

    CORE_SKILLS_DIR = os.path.join(FACTORY_ROOT, "skills", "core")
    if os.path.exists(CORE_SKILLS_DIR):
        log("ASSEMBLE", f"🧠 Cortex(Core Skills) 탑재 중...")
        for core_skill in glob.glob(os.path.join(CORE_SKILLS_DIR, "*.py")):
            shutil.copy2(core_skill, tools_dir)

    for src in skill_paths:
        if src and os.path.exists(src): shutil.copy2(src, tools_dir)

    log("ASSEMBLE", f"Assembly complete: {target_dir}")
    
    if not security_scan(target_dir, logger=print): 
        return 

    git_configure_and_push(FACTORY_ROOT, target_dir, agent_name, selected_model)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python factory_manager.py 'role_name' ['model_name']")
        sys.exit(1)

    role = sys.argv[1]
    selected_model = sys.argv[2] if len(sys.argv) > 2 else "gemini-2.0-flash"

    # Auto-initialize registry if empty
    rebuild_registry_from_disk(FORGE_DIR, WAREHOUSE_DIR)

    sync_warehouse()

    agent_id = role.replace(" ", "-").lower() + "-agent"
    log("SYSTEM", f"Starting factory process for '{agent_id}'")
        
    required_skills, coding_engine = research_required_skills(role, selected_model_name=selected_model)
    missing_skills = get_missing_skills(agent_id, required_skills)
    
    if not missing_skills:
        log("SYSTEM", "No missing skills. Update complete.")
        sys.exit(0)

    paths = [procure_skill(s, role) for s in missing_skills]
    assemble_and_push(agent_id, role, paths, selected_model)
