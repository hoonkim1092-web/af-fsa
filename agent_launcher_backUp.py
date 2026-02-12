import os
import sys
import re
import time
import json
import yaml  # pip install pyyaml 필요
import google.generativeai as genai
from dotenv import load_dotenv

# =============================================================================
# 1. 환경 설정
# =============================================================================
def configure_api():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=True)
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("❌ [Error] .env 파일에 GOOGLE_API_KEY가 없습니다.")
        sys.exit(1)
    genai.configure(api_key=api_key)
    return api_key

# =============================================================================
# 2. 페르소나 엔진 (단일 폴더 'agents' 관리)
# =============================================================================
class PersonaEngine:
    def __init__(self):
        # [원칙] 오직 'agents' 폴더 하나만 사용합니다.
        self.agents_dir = "agents"
        if not os.path.exists(self.agents_dir):
            os.makedirs(self.agents_dir)

    def load_or_create_agent(self, agent_name, model):
        """
        1. agents 폴더에 파일이 있으면 -> 로드 (Load)
        2. 없으면 -> AI가 생성 후 저장 (Create & Save) -> 로드
        """
        file_name = f"{agent_name.lower()}.yaml"
        file_path = os.path.join(self.agents_dir, file_name)

        # [Case A] 파일이 존재함 (Load)
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                print(f"📜 [Load] 설정 파일 로드 완료: {file_path}")
                return self._build_system_prompt(data)
            except Exception as e:
                print(f"⚠️ 파일 읽기 오류: {e}")
                return f"당신은 {agent_name}입니다. 전문가처럼 행동하세요."

        # [Case B] 파일이 없음 (Create & Save)
        else:
            print(f"🏭 [Factory] '{agent_name}' 에이전트가 없습니다. 새로 생성합니다...")
            new_persona = self._generate_persona_data(agent_name, model)
            
            # 생성된 설정을 파일로 저장 (영구 보존)
            with open(file_path, 'w', encoding='utf-8') as f:
                yaml.dump(new_persona, f, allow_unicode=True, default_flow_style=False)
            
            print(f"💾 [Save] 새 에이전트가 '{file_path}'에 저장되었습니다.")
            return self._build_system_prompt(new_persona)

    def _generate_persona_data(self, agent_name, model):
        """LLM에게 페르소나 JSON 생성을 요청"""
        prompt = f"""
        당신은 AI 에이전트 설계자입니다.
        사용자가 '{agent_name}'라는 이름의 새로운 에이전트를 요청했습니다.
        이 이름의 어원이나 서브컬처적 배경을 분석하여, 'Logi-Mind 물류 프로젝트'에 투입될
        가상의 전문가 페르소나를 기획하세요.

        [필수 조건]
        1. 결과는 반드시 JSON 형식이어야 합니다.
        2. 키(Key): name, role, tone, traits, skills(리스트)
        3. tone과 traits는 위트 있고 덕력 넘치는 컨셉 권장.
        4. skills는 이름에 어울리는 기상천외한 비즈니스 스킬 3~4개.
        
        응답은 JSON만 출력하세요.
        """
        try:
            # 창작은 1.5-flash가 빠르고 적합함
            response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            return json.loads(response.text)
        except:
            # 실패 시 기본값 리턴
            return {
                "name": agent_name,
                "role": "범용 에이전트",
                "tone": "평범함",
                "traits": "자동 생성 실패로 인한 기본 모드",
                "skills": ["기본 대화"]
            }

    def _build_system_prompt(self, data):
        """YAML 데이터를 시스템 프롬프트 문장으로 변환"""
        return f"""
        당신은 {data.get('role', '요원')} '{data.get('name', agent_name)}'입니다.
        
        [성격 및 말투]
        {data.get('tone', '정중함')}
        
        [특징]
        {data.get('traits', '없음')}
        
        [보유 스킬]
        {', '.join(data.get('skills', []))}
        
        위 설정을 완벽히 연기하며 사용자의 질문에 한국어로 답하세요.
        자신의 스킬을 활용해 비즈니스/물류 문제를 해결하는 척 연기하세요.
        """

# =============================================================================
# 3. 모델 스카우터 (우선순위 큐 생성)
# =============================================================================
def get_model_priority_queue():
    try:
        all_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 모델 버전(숫자) 추출 및 정렬 로직
        def extract_version(name):
            match = re.search(r'gemini-(\d+\.?\d*)', name)
            return float(match.group(1)) if match else 1.0

        # 높은 버전 우선, 그 다음 Pro 우선
        return sorted(all_models, key=lambda x: (extract_version(x), "pro" in x), reverse=True)
    except Exception as e:
        print(f"⚠️ 모델 목록 조회 실패: {e}")
        return ["models/gemini-1.5-flash"]

# =============================================================================
# 4. 메인 실행 (에이전트 가동)
# =============================================================================
def launch_agent(agent_name):
    configure_api()
    
    # [Step 1] 페르소나 준비 (로드 or 생성)
    # 생성을 위한 가벼운 모델 준비
    factory_model = genai.GenerativeModel("gemini-1.5-flash")
    engine = PersonaEngine()
    
    # 여기서 시스템 프롬프트를 확정합니다.
    system_prompt = engine.load_or_create_agent(agent_name, factory_model)
    
    # [Step 2] 대화용 고성능 모델 대기열 확보
    model_queue = get_model_priority_queue()
    current_model_idx = 0
    
    print(f"📋 '{agent_name}' 가동 준비 완료. (가용 두뇌: {len(model_queue)}개)")

    # [Step 3] 대화 루프
    while True:
        user_msg = input("\n💬 나: ").strip()
        if not user_msg: continue
        if user_msg.lower() in ["exit", "종료"]: break

        success = False
        retry_count = 0 

        # 모델 하향 지원 로직 (Fallback Loop)
        while not success and current_model_idx < len(model_queue):
            target_model = model_queue[current_model_idx]
            
            try:
                print(f"🧠 {target_model} 장착 중...", end="\r")
                model = genai.GenerativeModel(target_model)
                
                # 시스템 프롬프트 + 사용자 질문 결합
                full_prompt = f"{system_prompt}\n\n[사용자 질문]: {user_msg}"
                
                response = model.generate_content(
                    full_prompt,
                    generation_config={"max_output_tokens": 1000}
                )
                
                print(f"\r🤖 {agent_name} [{target_model}]: {response.text}")
                success = True
                
            except Exception as e:
                error_info = str(e).lower()
                
                # 치명적 오류(할당량 부족, 권한 없음 등) -> 즉시 다음 모델
                if any(x in error_info for x in ["not found", "404", "permission", "403", "exhausted", "429", "limit"]):
                    # print(f"\r⚠️ {target_model} 불가. 다음 모델로 교체합니다.") # 너무 시끄러우면 주석 처리
                    current_model_idx += 1 
                    retry_count = 0
                
                # 일시적 오류 -> 재시도
                else:
                    retry_count += 1
                    if retry_count <= 1:
                        time.sleep(2)
                    else:
                        current_model_idx += 1
                        retry_count = 0

        if current_model_idx >= len(model_queue):
            print("\n❌ [최종 실패] 모든 모델이 응답하지 않습니다. API 키를 확인하세요.")
            break

if __name__ == "__main__":
    # 실행 시 인자가 없으면 기본값 'Lilith' 사용
    name = sys.argv[1] if len(sys.argv) > 1 else "Lilith"
    launch_agent(name)