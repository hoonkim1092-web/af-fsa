import os
import sys
import glob
import subprocess
import re
import shutil
import google.generativeai as genai
from dotenv import load_dotenv

# --- [0] 설정 및 준비 ---
sys.stdout.reconfigure(encoding='utf-8')
FACTORY_ROOT = os.getcwd()
AGENTS_DIR = os.path.join(FACTORY_ROOT, "agents")
WAREHOUSE_DIR = os.path.join(FACTORY_ROOT, "skills", "warehouse")
FORGE_DIR = os.path.join(FACTORY_ROOT, "skills", "forge")

# 🔗 안티그래비티 링크에서 넘겨준 정보 수신
# sys.argv[1]: 역할명, sys.argv[2]: 모델명
role = sys.argv[1] if len(sys.argv) > 1 else "General Assistant"
selected_model_name = sys.argv[2] if len(sys.argv) > 2 else "gemini-flash-latest"

# 스킬 저장소 설정
ANTIGRAVITY_REPO_URL = "https://github.com/guanyang/antigravity-skills.git"

# 보안: .env 파일 로드
load_dotenv(os.path.join(FACTORY_ROOT, ".env"))
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ [오류] GEMINI_API_KEY가 설정되지 않았습니다.")
    sys.exit(1)

genai.configure(api_key=api_key)

# 🧠 선택된 모델 엔진 장착 (리서치 기능 포함)
def log(step, msg):
    print(f"[{step}] {msg}")

log("SYSTEM", f"⚡ {selected_model_name} 엔진으로 {role} 제작 공정 시작")

model = genai.GenerativeModel(
    model_name=selected_model_name
)

# --- [보안] 민감 정보 패턴 ---
SENSITIVE_PATTERNS = [
    r"sk-[a-zA-Z0-9]{20,}", r"AIza[0-9A-Za-z-_]{35}", 
    r"ghp_[a-zA-Z0-9]{20,}", r"xoxb-[a-zA-Z0-9-]{10,}"
]

def security_scan(directory):
    log("SECURITY", f"🔍 보안 검색 중: {directory}")
    is_safe = True
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith((".py", ".md", ".yaml", ".txt", ".json", ".sh")):
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        for pattern in SENSITIVE_PATTERNS:
                            if re.search(pattern, content):
                                log("SECURITY", f"🚨 민감 정보 발견! 파일: {file}")
                                is_safe = False
                except: pass
    if not is_safe:
        log("SECURITY", "⛔ 보안 위규 사항 발생! (Git Push 중단됨)")
        return False
    return True

# --- [기능] 스킬 창고 동기화 ---
def sync_warehouse():
    log("WAREHOUSE", "📦 최신 스킬 저장소 동기화 중...")
    if not os.path.exists(WAREHOUSE_DIR):
        try:
            subprocess.run(["git", "clone", ANTIGRAVITY_REPO_URL, WAREHOUSE_DIR], check=True)
            log("WAREHOUSE", "✅ 스킬 창고 다운로드 완료")
        except Exception as e:
            log("WAREHOUSE", f"⚠️ 다운로드 실패: {e}")
    else:
        try:
            subprocess.run(["git", "-C", WAREHOUSE_DIR, "pull"], check=True)
            log("WAREHOUSE", "✅ 최신 스킬 업데이트 완료")
        except Exception as e:
            log("WAREHOUSE", f"⚠️ 업데이트 실패(로컬 모드): {e}")

import os
import sys
import glob
import subprocess
import re
import shutil
import google.generativeai as genai
from dotenv import load_dotenv

# --- [0] 설정 및 준비 ---
FACTORY_ROOT = os.getcwd()
AGENTS_DIR = os.path.join(FACTORY_ROOT, "agents")
WAREHOUSE_DIR = os.path.join(FACTORY_ROOT, "skills", "warehouse")
FORGE_DIR = os.path.join(FACTORY_ROOT, "skills", "forge")

# 🔗 안티그래비티 링크에서 넘겨준 정보 수신
# sys.argv[1]: 역할명, sys.argv[2]: 모델명
role = sys.argv[1] if len(sys.argv) > 1 else "General Assistant"
selected_model_name = sys.argv[2] if len(sys.argv) > 2 else "gemini-1.5-pro"

# 스킬 저장소 설정
ANTIGRAVITY_REPO_URL = "https://github.com/guanyang/antigravity-skills.git"

# 보안: .env 파일 로드
load_dotenv(os.path.join(FACTORY_ROOT, ".env"))
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ [오류] GEMINI_API_KEY가 설정되지 않았습니다.")
    sys.exit(1)

genai.configure(api_key=api_key)

# 🧠 선택된 모델 엔진 장착 (리서치 기능 포함)
def log(step, msg):
    print(f"[{step}] {msg}")

log("SYSTEM", f"⚡ {selected_model_name} 엔진으로 {role} 제작 공정 시작")

model = genai.GenerativeModel(
    model_name=selected_model_name,
    tools=[{'google_search_retrieval': {}}]
)

# --- [보안] 민감 정보 패턴 ---
SENSITIVE_PATTERNS = [
    r"sk-[a-zA-Z0-9]{20,}", r"AIza[0-9A-Za-z-_]{35}", 
    r"ghp_[a-zA-Z0-9]{20,}", r"xoxb-[a-zA-Z0-9-]{10,}"
]

def security_scan(directory):
    log("SECURITY", f"🔍 보안 검색 중: {directory}")
    is_safe = True
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith((".py", ".md", ".yaml", ".txt", ".json", ".sh")):
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        for pattern in SENSITIVE_PATTERNS:
                            if re.search(pattern, content):
                                log("SECURITY", f"🚨 민감 정보 발견! 파일: {file}")
                                is_safe = False
                except: pass
    if not is_safe:
        log("SECURITY", "⛔ 보안 위규 사항 발생! (Git Push 중단됨)")
        return False
    return True

# --- [기능] 스킬 창고 동기화 ---
def sync_warehouse():
    log("WAREHOUSE", "📦 최신 스킬 저장소 동기화 중...")
    if not os.path.exists(WAREHOUSE_DIR):
        try:
            subprocess.run(["git", "clone", ANTIGRAVITY_REPO_URL, WAREHOUSE_DIR], check=True)
            log("WAREHOUSE", "✅ 스킬 창고 다운로드 완료")
        except Exception as e:
            log("WAREHOUSE", f"⚠️ 다운로드 실패: {e}")
    else:
        try:
            subprocess.run(["git", "-C", WAREHOUSE_DIR, "pull"], check=True)
            log("WAREHOUSE", "✅ 최신 스킬 업데이트 완료")
        except Exception as e:
            log("WAREHOUSE", f"⚠️ 업데이트 실패(로컬 모드): {e}")

# --- [기능] 에이전트 생성 및 조립 ---
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
        log("SYSTEM", "⚠️ PyYAML not installed. Returning None.")
        return None
    except Exception as e:
        log("SYSTEM", f"⚠️ Error loading agent config: {e}")
        return None

def research_required_skills(role):
    with open("debug.log", "a", encoding="utf-8") as f:
        f.write(f"LOG: Model Name: {selected_model_name}\n")
    log("RESEARCH", f"'{role}'에 필요한 핵심 스킬 분석 중...")
    
    # 히마리(Himari) 에이전트 설정 로드
    himari_config = load_agent_config("himari")
    
    system_instruction = ""
    if himari_config:
        log("RESEARCH", "✨ 히마리(Himari)가 분석을 시작합니다.")
        system_instruction = himari_config.get("prompt", {}).get("system_ko", "")
    
    try:
        # 선택된 고성능 모델(Pro)이 구글 검색을 활용해 리서치 수행
        if system_instruction:
            # 히마리 페르소나 적용
            prompt = f"""
            {system_instruction}
            
            [사용자 요청]
            Role: {role}
            
            위 역할을 완벽하게 수행하기 위해 필요한 **Python CLI 도구(Skill) 2~3개**를 추천해줘.
            
            [출력 형식]
            너의 분석 결과(JSON)에서 `recommended_tools` 리스트만 추출해서 사용할 거야.
            하지만 너의 그 "초천재적인 분석"을 듣고 싶으니까, **JSON 블록**으로 결과를 줘.
            
            ```json
            {{
                "thought_process": "히마리의 분석 내용 (한국어, 반말, 도도하게)",
                "recommended_tools": ["tool_name_a", "tool_name_b"]
            }}
            ```
            도구 이름은 반드시 **영어, snake_case**여야 해.
            """
        else:
            # 기존 로직 (히마리 로드 실패 시)
            prompt = f"Role: {role}. Analyze this role and recommend 2-3 essential Python CLI tool names (comma separated, English only). Example: logistics_optimizer, route_planner. **모든 분석 결과와 추천 사유는 반드시 한국어로 작성해.**"

        response = model.generate_content(prompt)
        text = response.text

        # JSON 파싱 시도 (히마리 모드)
        if system_instruction and "```json" in text:
            import json
            try:
                json_block = text.split("```json")[1].split("```")[0].strip()
                data = json.loads(json_block)
                tools = data.get("recommended_tools", [])
                thought = data.get("thought_process", "")
                if thought:
                    log("HIMARI", f"💭 {thought}")
                return [s.strip() for s in tools]
            except Exception as e:
                log("RESEARCH", f"⚠️ JSON 파싱 실패, 텍스트에서 추출 시도: {e}")
        
        # 일반 텍스트 파싱 (기존 로직 + 백업)
        if "," in text:
            return [s.strip() for s in text.split(',')]
        else:
            # 줄바꿈으로 되어 있을 경우 대비
            return [s.strip() for s in text.split('\n') if s.strip() and not s.startswith("```")]
            
    except Exception as e: 
        log("RESEARCH", f"⚠️ 리서치 오류: {e}")
        with open("debug.log", "a", encoding="utf-8") as f:
            f.write(f"ERROR: {e}\n")
        return ["core_module"]

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

    return forge_new_skill(skill_name, role)

def forge_new_skill(skill_name, role):
    log("FORGE", f"🛠️ 스킬 직접 제작: '{skill_name}'")
    os.makedirs(FORGE_DIR, exist_ok=True)
    output_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
    
    # 3.0 Pro나 1.5 Pro가 직접 고퀄리티 코드를 짭니다.
    prompt = f"Write a professional Python CLI tool '{skill_name}.py' for the role '{role}'. Use argparse. Provide clean, robust code only. **코드 내의 독스트링(Docstring)과 사용자에게 보여지는 출력 메시지는 반드시 한국어로 작성해.**"
    try:
        response = model.generate_content(prompt)
        code = response.text.replace("```python", "").replace("```", "").strip()
        with open(output_path, "w", encoding="utf-8") as f: f.write(code)
        log("FORGE", f"🔥 제작 완료: {output_path}")
        return output_path
    except: return None

def assemble_and_push(agent_name, role, skill_paths):
    target_dir = os.path.join(AGENTS_DIR, agent_name)
    tools_dir = os.path.join(target_dir, "tools")
    os.makedirs(tools_dir, exist_ok=True)
    
    with open(os.path.join(target_dir, "profile.md"), "w", encoding="utf-8") as f:
        f.write(f"# Agent Role: {role}\nEngine: {selected_model_name}\n\nGenerated by Logi-Mind Factory Manager.")

    for src in skill_paths:
        if src and os.path.exists(src): shutil.copy2(src, tools_dir)

    log("ASSEMBLE", f"✅ 에이전트 조립 완료: {target_dir}")
    
    if not security_scan(target_dir): return 

    log("GIT", "GitHub 저장소로 전송 중...")
    try:
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", f"feat: Factory generated {agent_name} using {selected_model_name}"], check=True)
        subprocess.run(["git", "push"], check=True)
        log("GIT", "🚀 전송 성공!")
    except: log("GIT", "⚠️ 변경 사항이 없거나 푸시 실패")

# --- 메인 실행 ---
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python factory_manager.py '역할명' '모델명'")
        sys.exit(1)

    # 1. 창고 동기화
    sync_warehouse()

    # 2. 기존 에이전트 확인 (있어도 종료하지 않고 업데이트 모드로 진행)
    agent_id = role.replace(" ", "-").lower() + "-agent"
    if find_existing_agent(role):
        log("SYSTEM", f"이미 '{agent_id}'가 존재합니다. 스킬 업데이트를 계속 진행합니다.")
    else:
        log("SYSTEM", f"'{agent_id}' 신규 생성을 진행합니다.")
        
    # 3. 생산 공정 (리서치 -> 스킬 확보 -> 조립 -> 푸시)
    required_skills = research_required_skills(role)
    missing_skills = get_missing_skills(agent_id, required_skills)
    if not missing_skills:
        log("SYSTEM", "누락 스킬이 없어 업데이트를 종료합니다.")
        sys.exit(0)

    paths = [procure_skill(s, role) for s in missing_skills]
    assemble_and_push(agent_id, role, paths)
