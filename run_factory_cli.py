import argparse
import os
import sys

# Agent Factory 경로를 sys.path에 추가 (현재 스크립트 위치 기준)
FACTORY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(FACTORY_DIR)

# Windows Console Encoding Fix
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

from agent_launcher import AgentFactory, AgentManager, ModelRouter

def main():
    parser = argparse.ArgumentParser(description="Logi-Mind Agent Factory CLI")
    parser.add_argument("--role", "-r", type=str, help="에이전트 역할 (예: 'Saiba Midori', 'Backend Dev')")
    parser.add_argument("--task", "-t", type=str, help="에이전트에게 요청할 작업 내용")
    parser.add_argument("--model", "-m", type=str, default="gemini-2.0-flash", help="사용할 AI 모델")
    
    args = parser.parse_args()

    # Interactive Prompt
    role = args.role
    if not role:
        print("\n🤖 [Agent Setup]")
        role = input("   에이전트 역할 (기본값: 'General Assistant'): ").strip()
        if not role:
            role = "General Assistant"
            
    task = args.task
    if not task:
        # If task is not provided, prompt for it
        if args.role: # If role WAS provided but task wasn't, print header
             pass 
        else: # Header already printed above
             pass
             
        print(f"\n📝 [Task for '{role}']")
        task = input("   요청할 작업 내용을 입력하세요: ").strip()
        
    if not task:
        print("❌ 작업 내용이 입력되지 않아 종료합니다.")
        return
    
    # 현재 실행 위치를 프로젝트 루트로 설정
    cwd = os.getcwd()
    os.environ["AGENT_PROJECT_ROOT"] = cwd
    
    print(f"\n🏭 [Logi-Mind Agent Factory] 가동")
    print(f"📂 Project Root: {cwd}")
    print(f"🤖 Role: {role}")
    print("-" * 50)
    
    # Factory 실행
    try:
        factory = AgentFactory()
         # force run even if args were missing initially
        factory.run(task_input=task, role_spec=role)
    except KeyboardInterrupt:
        print("\n🛑 사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"\n⚠️ 오류 발생: {e}")

if __name__ == "__main__":
    main()
