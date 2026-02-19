import os
import sys
import subprocess
import json
import google.generativeai as genai
from dotenv import load_dotenv
from model_utils import get_best_model, get_available_models

# Reconfigure stdout to utf-8 for Windows console
sys.stdout.reconfigure(encoding='utf-8')

# --- 1. 환경 설정 및 보안 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, ".env")
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    print("❌ [오류] .env 파일에서 API 키를 찾을 수 없습니다.")
    sys.exit(1)

genai.configure(api_key=api_key)


def resolve_python_exec() -> str:
    """Prefer a real Python binary over WindowsApps shim."""
    candidates = [
        sys.executable,
        os.path.expandvars(r"%LOCALAPPDATA%\Python\bin\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return "python"

# --- 2. 지능형 오케스트레이터 클래스 ---
class SmartLinker:
    def __init__(self):
        # 상황 판단용 가벼운 모델
        self.gatekeeper = genai.GenerativeModel(get_best_model(["gemini-2.0-flash", "gemini-1.5-flash"]))

    def analyze_and_route(self, user_input):
        """질문을 분석하여 최적의 모델과 역할명을 결정"""
        print(f"🧠 [Antigravity] 의도 분석 및 모델 선정 중...")
        
        prompt = f"""
        User Request: "{user_input}"
        
        당신은 AI 자원 관리자입니다. 다음 규칙에 따라 요청을 분석하세요.
        1. 전략/기획/게임/아이디어 -> GEMINI_3_PRO
        2. 물류/분석/리서치/문서화 -> GEMINI_1_5_PRO
        3. 단순 작업/요약 -> GEMINI_2_FLASH
        
        다음 JSON 형식으로만 답하세요:
        {{
            "role_name": "영문 역할명 (예: Logistics Manager)",
            "model_choice": "모델명",
            "reason": "선택 사유"
        }}
        """
        
        try:
            response = self.gatekeeper.generate_content(prompt)
            # JSON 파싱
            res_text = response.text.replace('```json', '').replace('```', '').strip()
            data = json.loads(res_text)
            
            # 모델 매핑 (동적 검색 결과 활용)
            choice = data.get('model_choice', '').upper()
            
            # 우선순위 부여
            if "GEMINI_3" in choice:
                priority = ["gemini-3.0-pro", "gemini-2.0-pro", "gemini-1.5-pro"]
            elif "GEMINI_1_5" in choice:
                priority = ["gemini-1.5-pro", "gemini-2.0-flash"]
            else: # FLASH or default
                priority = ["gemini-2.0-flash", "gemini-1.5-flash"]
                
            data['actual_model'] = get_best_model(priority)
            return data
        except Exception as e:
            print(f"⚠️ 분석 중 오류 발생, 기본 모델을 사용합니다: {e}")
            return {"role_name": "General Assistant", "actual_model": get_best_model()}

# --- 3. 메인 실행 루프 ---
def main():
    linker = SmartLinker()
    
    print("\n" + "="*50)
    print("🚀 [Logi-Mind Intelligent Link] 가동 중")
    print("   모델 자율 선택 모드가 활성화되었습니다.")
    print("="*50 + "\n")

    while True:
        try:
            user_input = input("💬 명령(자연어): ").strip()
            if user_input.lower() in ['exit', 'quit', '종료']: break
            if not user_input: continue

            # 1. AI가 직접 모델과 역할 결정
            plan = linker.analyze_and_route(user_input)
            role_name = plan['role_name']
            selected_model = plan['actual_model']

            print(f"🎯 목표: '{role_name}'")
            print(f"🧠 선택된 뇌: {selected_model}")
            print(f"🏭 공장장에게 제작 요청을 보냅니다...\n")

            # --- antigravity_link.py의 메인 루프 안 ---
            # 2. 공장장 실행 (모델 정보를 두 번째 인자로 넘깁니다)
            print(f"🚀 {selected_model} 엔진을 장착하고 공장을 가동합니다...")
            
            # 기존: ["python", "factory_manager.py", role_name]
            # 변경: 모델명(selected_model)을 추가로 전달
            subprocess.run([resolve_python_exec(), "factory_manager.py", role_name, selected_model])         
            
            print("\n✅ 작업 완료. 다음 명령을 주세요.")

        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()
