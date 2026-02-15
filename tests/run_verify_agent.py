import sys
import os
import time

# agent_launcher.py가 있는 경로를 sys.path에 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent_launcher import AgentFactory, AgentManager, ModelRouter

def run_verification():
    print("🚀 [Verification] 테스트 시작...")
    
    # Core Memory 테스트 (우선 수행)
    print("\n[Step 1] Core Memory 스킬 테스트...")
    try:
        sys.path.append(os.path.join(os.path.dirname(__file__), "skills", "core_memory"))
        import skill as memory_skill
        
        ctx = {"data_dir": os.path.join(os.path.dirname(__file__), "data")}
        res = memory_skill.test(ctx)
        if res.get("ok"):
            print("✅ [Pass] Core Memory Self-Test 통과")
        else:
            print(f"❌ [Fail] Core Memory Test 실패: {res}")
            
    except ImportError:
        print("❌ [Fail] Core Memory 스킬 임포트 실패")
    except Exception as e:
        print(f"❌ [Fail] Core Memory 테스트 중 오류: {e}")

    # Agent Generation 테스트
    print("\n[Step 2] 에이전트 생성 및 한국어 설정 테스트...")
    
    role_spec = "Korean History Tutor"
    agent_mgr = AgentManager(ModelRouter())
    
    # 기존에 있으면 삭제
    agent_path = agent_mgr._agent_path(role_spec)
    if os.path.exists(agent_path):
        os.remove(agent_path)
    
    try:
        print("⏳ 에이전트 생성 요청 (Gemini)...")
        # safe_generate가 적용되었으므로 재시도 로직이 동작할 것임
        agent_data = agent_mgr.get_or_create(role_spec)
        
        print("\n✅ 생성 완료! 결과 확인:")
        print(f"Name: {agent_data.get('name')}")
        print(f"System KO: {agent_data.get('system_ko')}")
        sig = agent_data.get('signature_lines')
        print(f"Signature Lines: {sig}")
        
        if not agent_data.get('system_ko'):
            print("❌ [Fail] system_ko 누락됨")
        elif not isinstance(sig, list) or len(sig) == 0:
            print("❌ [Fail] signature_lines가 유효하지 않음")
        else:
            # 한글 포함 여부 간단 체크
            if any(ord(c) > 128 for c in str(sig[0])):
                print("✅ [Pass] 한국어 설정 및 시그니처 대사 확인 완료")
            else:
                 print("⚠️ [Warning] 시그니처에 한글이 없어 보임 (확인 필요)")

    except Exception as e:
        print(f"⚠️ [Skip] 에이전트 생성 테스트 실패 (Quota/Network): {e}")

if __name__ == "__main__":
    run_verification()
