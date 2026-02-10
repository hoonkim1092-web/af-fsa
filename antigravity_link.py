import os
import sys
import subprocess
import google.generativeai as genai

# --- 1. 설정 ---
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ [오류] GEMINI_API_KEY가 없습니다.")
    sys.exit(1)

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-2.0-flash')

# --- 2. AI 번역기 (한국어 -> 영어 역할명) ---
def interpret_command(user_input):
    print(f"🧠 [Antigravity] 명령 분석 중...")
    
    prompt = f"""
    User Request: "{user_input}"
    
    Task:
    1. Analyze the user's request.
    2. Extract the most suitable 'Agent Role Name' in English.
    3. Output ONLY the role name. (e.g., 'Stock Trader', 'News Scraper')
    4. Do not add any explanation.
    """
    
    try:
        response = model.generate_content(prompt)
        role_name = response.text.strip()
        # 특수문자나 불필요한 공백 제거
        role_name = role_name.replace('"', '').replace("'", "").replace(".", "")
        return role_name
    except Exception as e:
        print(f"❌ 분석 실패: {e}")
        return None

# --- 3. 메인 실행 루프 ---
def main():
    print("\n" + "="*40)
    print("🚀 [Antigravity Link] 시스템 대기 중")
    print("   (종료하려면 'exit' 입력)")
    print("="*40 + "\n")

    while True:
        try:
            # 사용자 입력 대기
            user_input = input("💬 명령(자연어): ").strip()
            
            if user_input.lower() in ['exit', 'quit', '종료']:
                print("👋 시스템을 종료합니다.")
                break
            
            if not user_input:
                continue

            # 1. 의도 파악
            role_name = interpret_command(user_input)
            
            if role_name:
                print(f"🎯 목표 설정: '{role_name}'")
                print(f"🏭 공장 가동 요청 보내는 중...\n")
                
                # 2. 공장장(factory_manager.py) 실행
                # 여기서 factory_manager가 리서치->제작->Git전송을 수행합니다.
                subprocess.run(["python", "factory_manager.py", role_name])
                
                print("\n✅ 에이전트 생성 완료. 다음 명령을 주세요.")
                
            else:
                print("⚠️ 명령을 이해하지 못했습니다.")

        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()