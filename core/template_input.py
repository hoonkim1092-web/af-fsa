import inquirer
import sys

def prompt_mission_template(agent_name: str) -> str:
    """
    Shows an interactive TUI to structure vague user goals into concrete 
    Goal/Constraints/Done definitions.
    """
    print(f"\n🚀 {agent_name} 에이전트에게 보낼 임무 명세서 양식입니다.")
    print("작업의 방향성(Goal), 제약조건(Constraint), 성공기준(DoD)을 명확히 할수록 오작동 확률이 줄어듭니다.\n")
    
    questions = [
        inquirer.Text('goal', message="[1/3] 무엇을 만들거나 해결하고 싶으신가요? (핵심 목표)"),
        inquirer.Text('constraint', message="[2/3] 지켜야 할 주요 제약사항은 무엇인가요? (언어, 프레임워크, 기한 등)"),
        inquirer.Text('dod', message="[3/3] 성공 기준(Definition of Done)은 무엇인가요? (ex: pytest 100% 통과, UI 깨짐 없음)")
    ]
    
    try:
        answers = inquirer.prompt(questions)
        if not answers:
            print("입력이 취소되었습니다. 강제 종료합니다.")
            sys.exit(1)
            
        goal = answers.get('goal', '').strip()
        constraint = answers.get('constraint', '').strip()
        dod = answers.get('dod', '').strip()
        
        if not goal:
            print("핵심 목표(Goal)는 필수입니다. 강제 종료합니다.")
            sys.exit(1)
            
        formatted_prompt = f"Goal:\n{goal}\n\nConstraints:\n{constraint if constraint else 'N/A'}\n\nDefinition of Done:\n{dod if dod else 'N/A'}"
        return formatted_prompt
    except KeyboardInterrupt:
        print("\n입력이 취소되었습니다. 강제 종료합니다.")
        sys.exit(1)

if __name__ == "__main__":
    res = prompt_mission_template("Lilith")
    print("\n--- 결과 ---")
    print(res)
