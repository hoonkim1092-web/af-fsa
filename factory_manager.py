import os
import sys
import glob
import subprocess
import re
import shutil
import google.generativeai as genai
from dotenv import load_dotenv
from model_utils import get_best_model

FACTORY_ROOT = os.getcwd()
AGENTS_DIR = os.path.join(FACTORY_ROOT, "agents")
WAREHOUSE_DIR = os.path.join(FACTORY_ROOT, "skills", "warehouse")
FORGE_DIR = os.path.join(FACTORY_ROOT, "skills", "forge")

role = sys.argv[1] if len(sys.argv) > 1 else "General Assistant"
selected_model_name = sys.argv[2] if len(sys.argv) > 2 else "gemini-3-flash-preview"

ANTIGRAVITY_REPO_URL = "https://github.com/guanyang/antigravity-skills.git"

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv() # Search for .env automatically
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("?좑툘 [寃쎄퀬] GOOGLE_API_KEY媛 ?ㅼ젙?섏? ?딆븯?듬땲?? (?쒖뒪???몄쬆???쒕룄?⑸땲??")
else:
    genai.configure(api_key=api_key)

def log(step, msg):
    print(f"[{step}] {msg}")

def get_random_signature(agent_config: dict) -> str:
    """YAML ?ㅼ젙?먯꽌 臾댁옉???쒓렇?덉쿂 ??щ? 諛섑솚?⑸땲??"""
    import random
    lines = agent_config.get("signature_lines")
    if not lines and "persona" in agent_config:
        lines = agent_config["persona"].get("signature_lines")
    
    if lines and isinstance(lines, list):
        return random.choice(lines)
    return ""

if len(sys.argv) > 2:
    selected_model_name = sys.argv[2]
else:
    selected_model_name = get_best_model()

log("SYSTEM", f"??{selected_model_name} ?붿쭊?쇰줈 {role} ?쒖옉 怨듭젙 ?쒖옉")

model = genai.GenerativeModel(
    model_name=selected_model_name,
    tools=[{'google_search_retrieval': {}}]
)

SENSITIVE_PATTERNS = [
    r"sk-[a-zA-Z0-9]{20,}", r"AIza[0-9A-Za-z-_]{35}", 
    r"ghp_[a-zA-Z0-9]{20,}", r"xoxb-[a-zA-Z0-9-]{10,}"
]

def security_scan(directory):
    log("SECURITY", f"?뵇 蹂댁븞 寃??以? {directory}")
    is_safe = True
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith((".py", ".md", ".yaml", ".txt", ".json", ".sh")):
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        for pattern in SENSITIVE_PATTERNS:
                            if re.search(pattern, content):
                                log("SECURITY", f"?슚 誘쇨컧 ?뺣낫 諛쒓껄! ?뚯씪: {file}")
                                is_safe = False
                except: pass
    if not is_safe:
        log("SECURITY", "??蹂댁븞 ?꾧퇋 ?ы빆 諛쒖깮! (Git Push 以묐떒??")
        return False
    return True

def sync_warehouse():
    log("WAREHOUSE", "?벀 理쒖떊 ?ㅽ궗 ??μ냼 ?숆린??以?..")
    if not os.path.exists(WAREHOUSE_DIR):
        try:
            subprocess.run(["git", "clone", ANTIGRAVITY_REPO_URL, WAREHOUSE_DIR], check=True)
            log("WAREHOUSE", "???ㅽ궗 李쎄퀬 ?ㅼ슫濡쒕뱶 ?꾨즺")
        except Exception as e:
            log("WAREHOUSE", f"?좑툘 ?ㅼ슫濡쒕뱶 ?ㅽ뙣: {e}")
    else:
        try:
            subprocess.run(["git", "-C", WAREHOUSE_DIR, "pull"], check=True)
            log("WAREHOUSE", "??理쒖떊 ?ㅽ궗 ?낅뜲?댄듃 ?꾨즺")
        except Exception as e:
            log("WAREHOUSE", f"?좑툘 ?낅뜲?댄듃 ?ㅽ뙣(濡쒖뺄 紐⑤뱶): {e}")

def find_existing_agent(role):
    agent_name = role.replace(" ", "-").lower() + "-agent"
    if os.path.exists(os.path.join(AGENTS_DIR, agent_name)): return agent_name
    return None

def load_agent_config(agent_name):
    config_path = os.path.join(AGENTS_DIR, f"{agent_name}.yaml")
    if not os.path.exists(config_path):
        return None
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        log("SYSTEM", "?좑툘 PyYAML not installed. Returning None.")
        return None
    except Exception as e:
        log("SYSTEM", f"?좑툘 Error loading agent config: {e}")
        return None

def _query_notebooklm(query: str) -> str:
    """
    NotebookLM CLI瑜??듯빐 吏덈Ц???섑뻾?⑸땲?? (Factory 踰꾩쟾)
    湲곕낯 ?명듃遺?ID: eaa34a54-a898-46a0-835a-cdb6024887f0 (Google Antigravity Guide)
    """
    try:
        target_notebook_id = "eaa34a54-a898-46a0-835a-cdb6024887f0"
        
        cmd = [
            sys.executable, "-m", "notebooklm_tools.cli.main",
            "query", "notebook",
            target_notebook_id,
            query
        ]
        
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            env=env,
            timeout=60
        )
        
        if p.returncode != 0:
            return ""
            
        return p.stdout.strip()
        
    except Exception as e:
        log("RESEARCH", f"?좑툘 NotebookLM ?곌껐 ?ㅻ쪟: {e}")
        return ""

def research_required_skills(role):
    with open("debug.log", "a", encoding="utf-8") as f:
        f.write(f"LOG: sys.argv: {sys.argv}\n")
        f.write(f"LOG: Model Name: {selected_model_name}\n")
    log("RESEARCH", f"'{role}'???꾩슂???듭떖 ?ㅽ궗 遺꾩꽍 以?..")
    
    himari_config = load_agent_config("himari")
    
    system_instruction = ""
    if himari_config:
        sig = get_random_signature(himari_config)
        if sig:
            print(f"\n[RESEARCH] Himari \"{sig}\"")
        log("RESEARCH", "???덈쭏由?Himari)媛 遺꾩꽍???쒖옉?⑸땲??")
        system_instruction = himari_config.get("prompt", {}).get("system_ko", "")

        log("RESEARCH", "?뵊 [鍮꾨? ?쒓퀬] NotebookLM?먯꽌 愿??吏?앹쓣 ?먯깋?⑸땲??..")
        query = f"""
        Target Role/Domain: {role}
        Based on our strategic guidelines & business constraints, please provide:
        1. Mission Context: Exact responsibilities, KPIs, and goals for this role (applicable to ANY industry: IT, Sales, Logistics, HR, Marketing, etc.).
        2. Domain Logic & Process: What specific knowledge, industry standards, workflows, OR non-IT strategic methodologies must this role command? (Focus on cost-efficiency, process optimization, and value creation).
        3. Essential Skills: What specific tools, frameworks, skills, operations, or even soft-skill methodologies are required? (Completely unconstrained from software development).
        Return highly specific, actionable insights tailored to the exact domain context.
        """.strip()
        notebook_insight = _query_notebooklm(query)
        
        if notebook_insight:
            log("RESEARCH", f"?뮕 ?쒓퀬?먯꽌 ?좎쓽誘명븳 湲곕줉??諛쒓껄?덉뒿?덈떎.")
            system_instruction += f"\n\n[NotebookLM Secret Archive Constraint]:\n{notebook_insight[:1000]}\n(???뺣낫瑜?諛뷀깢?쇰줈 ?먮떒?섏떗?쒖삤.)"
    
    try:
        if system_instruction:
            prompt = f"""
            {system_instruction}
            
            [?ъ슜???붿껌]
            Target Role/Domain: {role}
            
            이 역할(또는 부서/산업군)의 목표를 완벽하게 달성하기 위해 필요한 **핵심 스킬/도구/실무 방법론(Skill) 2~3개**를 추천해줘.
            (IT/SW 개발에 절대 국한되지 않음. 물류 최적화, 영업 전략, 마케팅 자동화, 재무 분석 등 해당 도메인 본연의 필수 역량을 제안할 것.)
            
            [출력 형식]
            ?덉쓽 遺꾩꽍 寃곌낵(JSON)?먯꽌 `recommended_tools` 由ъ뒪?몃쭔 異붿텧?댁꽌 ?ъ슜??嫄곗빞.
            ?섏?留??덉쓽 洹?"珥덉쿿?ъ쟻??遺꾩꽍"???ｊ퀬 ?띠쑝?덇퉴, **JSON 釉붾줉**?쇰줈 寃곌낵瑜?以?
            
            ```json
            {{
                "thought_process": "히마리의 분석 내용 (한국어, 반말, 압도적으로)",
                "recommended_tools": ["industry_skill_a", "industry_skill_b"],
                "coding_engine": "gemini-3.1-pro-preview | codex-5.3"
            }}
            ```
            스킬 이름은 반드시 **영어, snake_case** 형태로 추상화할 것 (예: market_trend_analysis, inventory_control).
            `coding_engine`은 이 스킬을 작동 방식을 시뮬레이션 하거나 자동화할 때 사용할 엔진.
            """
        else:
            prompt = f"Role/Domain: {role}. Analyze this non-IT or general business role and recommend 2-3 essential tools/skills/methodologies (comma separated, English snake_case only). Absolutely not limited to IT; could be sales, logistics, HR, etc. Example: inventory_optimization, negotiation_strategy. **紐⑤뱺 遺꾩꽍 寃곌낵? 異붿쿿 ?ъ쑀??諛섎뱶???쒓뎅?대줈 ?묒꽦??**"

        response = model.generate_content(prompt)
        text = response.text

        if system_instruction and "```json" in text:
            import json
            try:
                json_block = text.split("```json")[1].split("```")[0].strip()
                data = json.loads(json_block)
                tools = data.get("recommended_tools", [])
                thought = data.get("thought_process", "")
                coding_engine = data.get("coding_engine", "gemini-3.1-pro-preview")
                if thought:
                    log("HIMARI", f"💭 {thought}")
                log("HIMARI", f"🛠️ Coding Engine selected: {coding_engine}")
                return [s.strip() for s in tools], coding_engine
            except Exception as e:
                log("RESEARCH", f"❌ JSON 파싱 실패, 텍스트에서 추출 시도: {e}")
        
        if "," in text:
            return [s.strip() for s in text.split(',')], "gemini-3.1-pro-preview"
        else:
            return [s.strip() for s in text.split('\n') if s.strip() and not s.startswith("```")], "gemini-3.1-pro-preview"
            
    except Exception as e: 
        log("RESEARCH", f"🆘 [히마리가 뻗었습니다!] 원인: {e}")
        with open("debug.log", "a", encoding="utf-8") as f:
            f.write(f"ERROR (HIMARI DOWN): {e}\n")
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
    found = glob.glob(os.path.join(WAREHOUSE_DIR, "**", f"{skill_name}.py"), recursive=True)
    if found: return found[0]
    
    forge_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
    if os.path.exists(forge_path): return forge_path

    # Extract coding_engine through arguments or context if needed
    # For now, we assume provide_skill is called within a context that knows coding_engine
    return forge_new_skill(skill_name, role)

def forge_new_skill(skill_name, role, coding_engine="gemini-3.1-pro-preview"):
    log("FORGE", f"🔨 스킬 직접 제작: '{skill_name}' (Engine: {coding_engine})")
    os.makedirs(FORGE_DIR, exist_ok=True)
    output_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
    
    # Use selected engine for coding
    coding_model = genai.GenerativeModel(model_name=get_best_model([coding_engine]))
    
    prompt = f"Write a professional Python CLI tool '{skill_name}.py' for the role '{role}'. Use argparse. Provide clean, robust code only. **코드 내의 닥스트링(Docstring)과 사용자에게 보여지는 출력 메시지는 반드시 한국어로 작성함.**"
    try:
        response = coding_model.generate_content(prompt)
        code = response.text.replace("```python", "").replace("```", "").strip()
        with open(output_path, "w", encoding="utf-8") as f: f.write(code)
        log("FORGE", f"✅ 제작 완료: {output_path}")
        return output_path
    except Exception as e:
        log("FORGE", f"❌ 제작 실패: {e}")
        return None

def assemble_and_push(agent_name, role, skill_paths):
    target_dir = os.path.join(AGENTS_DIR, agent_name)
    tools_dir = os.path.join(target_dir, "tools")
    os.makedirs(tools_dir, exist_ok=True)
    
    padding_protocol = ""
    PROTOCOL_PATH = os.path.join(FACTORY_ROOT, "skills", "core", "learning_protocol.md")
    if os.path.exists(PROTOCOL_PATH):
        with open(PROTOCOL_PATH, "r", encoding="utf-8") as pf:
            padding_protocol = pf.read()

    with open(os.path.join(target_dir, "profile.md"), "w", encoding="utf-8") as f:
        f.write(f"# Agent Role: {role}\nEngine: {selected_model_name}\n\nGenerated by Logi-Mind Factory Manager.\n\n{padding_protocol}")

    CORE_SKILLS_DIR = os.path.join(FACTORY_ROOT, "skills", "core")
    if os.path.exists(CORE_SKILLS_DIR):
        log("ASSEMBLE", f"?쭬 Cortex(Core Skills) ?묒옱 以?..")
        for core_skill in glob.glob(os.path.join(CORE_SKILLS_DIR, "*.py")):
            shutil.copy2(core_skill, tools_dir)

    for src in skill_paths:
        if src and os.path.exists(src): shutil.copy2(src, tools_dir)

    log("ASSEMBLE", f"???먯씠?꾪듃 議곕┰ ?꾨즺: {target_dir}")
    
    if not security_scan(target_dir): return 

    log("GIT", "GitHub ??μ냼濡??꾩넚 以?..")
    try:
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", f"feat: Factory generated {agent_name} using {selected_model_name}"], check=True)
        subprocess.run(["git", "push"], check=True)
        log("GIT", "?? ?꾩넚 ?깃났!")
    except: log("GIT", "?좑툘 蹂寃??ы빆???녾굅???몄떆 ?ㅽ뙣")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("?ъ슜踰? python factory_manager.py '??븷紐? '紐⑤뜽紐?")
        sys.exit(1)

    sync_warehouse()

    agent_id = role.replace(" ", "-").lower() + "-agent"
    if find_existing_agent(role):
        log("SYSTEM", f"?대? '{agent_id}'媛 議댁옱?⑸땲?? ?ㅽ궗 ?낅뜲?댄듃瑜?怨꾩냽 吏꾪뻻?⑸땲??")
    else:
        log("SYSTEM", f"'{agent_id}' ?좉퇋 ?앹꽦??吏꾪뻾?⑸땲??")
        
    required_skills, coding_engine = research_required_skills(role)
    missing_skills = get_missing_skills(agent_id, required_skills)
    if not missing_skills:
        log("SYSTEM", "탈락 스킬이 없어 업데이트를 종료합니다.")
        sys.exit(0)

    paths = [procure_skill(s, role) if glob.glob(os.path.join(WAREHOUSE_DIR, "**", f"{s}.py"), recursive=True) or os.path.exists(os.path.join(FORGE_DIR, f"{s}.py")) else forge_new_skill(s, role, coding_engine) for s in missing_skills]
    assemble_and_push(agent_id, role, paths)
