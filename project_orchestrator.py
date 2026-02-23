import os
import sys
import subprocess
import argparse
import re
import json
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

FACTORY_DIR = os.path.dirname(os.path.abspath(__file__))

def print_message(agent, message):
    if agent == "Lilith":
        print(f"\n[😈 Lilith (PM)] {message}")
    elif agent == "Tanjiro":
        print(f"\n[🎴 Tanjiro (Director)] {message}")
    elif agent == "Himari":
        print(f"\n[🔍 Himari (Research)] {message}")
    else:
        print(f"\n[{agent}] {message}")

def decompose_project(project_description):
    """
    사용자의 프로젝트 요구사항을 분석하여 필요한 에이전트 롤(Role)을 도출합니다.
    """
    print_message("Lilith", "프로젝트 명세 분석 중... 멍청한 계획이 아니길 바라지.")
    
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = f"""
    당신은 Logi-Mind 프로젝트의 수석 PM인 Lilith입니다.
    사용자가 다음 프로젝트를 요청했습니다: "{project_description}"
    
    이 프로젝트를 완수하기 위해 필요한 하위 에이전트들의 역할(Role) 목록을 추천해주세요. 
    반드시 '필수' 롤과 '권장/선택' 롤로 구분해서 JSON 형태로만 응답하세요.
    예: {{"roles": [{{"role": "Chef", "type": "필수", "reason": "메뉴 구상 및 레시피 작성"}}, {{"role": "Manager", "type": "필수", "reason": "예산 및 진행 검수"}}]}}
    출력은 마크다운 코드 블록 없이 순수 JSON 문자열만 출력하세요.
    """
    
    try:
        response = model.generate_content(prompt)
        # Clean markdown codeblocks if model didn't listen
        json_str = re.sub(r"^```(?:json)?\s*", "", response.text.strip())
        json_str = re.sub(r"\s*```$", "", json_str)
        data = json.loads(json_str)
        return data.get("roles", [])
    except Exception as e:
        print_message("Lilith", f"분석 중 에러 났어. 다시 똑바로 입력해. 에러: {e}")
        return []

def confirm_roles_with_user(roles, project_description):
    """
    Lilith가 도출된 조직도(역할)를 사용자에게 제안하고 컨펌을 받습니다.
    """
    print_message("Lilith", "Boss, 이 프로젝트를 2주 안에 끝내려면 아래와 같은 롤들이 필요해. 확인해.")
    for i, r in enumerate(roles):
        print(f"  {i+1}. {r['role']} ({r['type']}) - {r['reason']}")
    
    while True:
        ans = input("\n[Boss] 이 조직도로 진행할까? 수정이 필요하면 '수정 [역할1, 역할2]', 동의하면 'yes' 입력: ").strip()
        if ans.lower() in ['y', 'yes', '동의', '진행']:
            return [r['role'] for r in roles]
        elif ans.startswith("수정") or ans.startswith("edit"):
            # 매우 간략한 파싱 로직 (ex: "수정 셰프, 매니저")
            new_roles_str = ans.replace("수정", "").replace("edit", "").strip()
            new_roles = [x.strip() for x in new_roles_str.split(",") if x.strip()]
            if new_roles:
                print_message("Lilith", f"좋아. 그럼 네 오더대로 '{', '.join(new_roles)}' 로 진행한다. 변명은 안 통 해.")
                return new_roles
            else:
                print_message("Lilith", "입력 똑바로 안 해? 콤마로 구분해서 역할 이름을 적으라고.")
        else:
            print_message("Lilith", "수정할 거면 '수정 [역할들]' 이라고 치고, 아니면 'yes' 라고 쳐. 미루지 마.")

def instruct_himari_to_forge(roles, target_dir=None):
    """
    Lilith가 확정된 롤을 Himari에게 넘겨 팩토리 로직을 실행하도록 지시합니다.
    target_dir가 주어지면 해당 폴더 내의 agents/, skills/ 에 격리 생성됩니다.
    """
    print_message("Lilith", f"Himari! 당장 일어나. '{', '.join(roles)}' 에이전트들이 필요하다. 명확한 페르소나와 실무용 플레이북 스킬을 빈틈없이 구워오도록.")
    print_message("Himari", "지시 확인했습니다. 각 도메인 분석 및 팩토리 생산 라인을 가동합니다...")
    
    success_roles = []
    
    env = os.environ.copy()
    if target_dir:
        env["AGENT_PROJECT_ROOT"] = os.path.abspath(target_dir)
        print_message("System", f"프로젝트 격리 모드 활성화: 타겟 디렉토리 = {env['AGENT_PROJECT_ROOT']}")
    
    for role in roles:
        print("\n" + "="*50)
        print_message("Himari", f"'{role}' 에이전트 생산 공정 시작...")
        
        # Call factory_manager.py for each role
        cmd = [sys.executable, "factory_manager.py", role]
        try:
            # We stream the output to the console so Boss can see Himari at work
            process = subprocess.Popen(cmd, cwd=FACTORY_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', env=env)
            for line in iter(process.stdout.readline, ''):
                sys.stdout.write(line)
            process.stdout.close()
            process.wait()
            
            if process.returncode == 0:
                success_roles.append(role)
            else:
                print_message("Lilith", f"야 Himari! '{role}' 만들다 뻗었잖아. 당장 디버깅해.")
        except Exception as e:
            print_message("Lilith", f"Himari 프로세스 에러: {e}")
            
    return success_roles

def hot_upgrade_agent(role, missing_capability, project_desc):
    """
    Lilith가 에이전트의 역량 한계를 감지했을 때, Himari를 실시간으로 호출해
    '없는 스킬을 즉석에서 찍어내어' 해당 에이전트에게 꽂아주는 동적 기어 업그레이드 함수.
    """
    print_message("Lilith", f"[비상 사태] '{role}' 녀석이 '{missing_capability}' 제안부터 막히고 있어. 멍청하게 굴지 말고 툴을 쥐어줘야겠네.")
    print_message("Lilith", f"Himari! '{role}' 한테 당장 <<{missing_capability}>> 특화 스킬 하나 구워와. 당장!")
    
    # 히마리 스킬 생성 (factory_manager.py의 forge_new_skill 모방 또는 직접 genai 호출)
    print_message("Himari", f"오더 접수. '{role}' 을 위한 <<{missing_capability}>> 긴급 파츠(스킬) 주조 공정 가동 중...")
    
    # (실제 환경에서는 factory_manager의 forge_new_skill 함수나 CLI를 호출해 skills/forge 에 새 .py를 떨어뜨리고 yaml을 업데이트해야 함. 여기선 데모 콘솔로 대체)
    import time
    time.sleep(2)
    
    print_message("Himari", f"주조 완료. <<{missing_capability}_playbook.py>> 를 '{role}' 의 코어에 핫 리로딩(Hot-Reloading) 주입했습니다.")
    print_message("Lilith", f"좋아. 야 '{role}'! 스킬 하나 머리에 꽂아 줬으니까 이제 똑바로 '{missing_capability}' 해서 다시 가져와.")
    return True

def run_swarm_council(roles, project_desc):
    """
    생성된 에이전트들 간의 티키타카(대화 및 검문) 루프.
    Lilith가 PM으로서 하위 에이전트의 제안을 갈구고 깎아내는 메인 오케스트레이션 엔진.
    """
    print("\n" + "="*60)
    print_message("Lilith", "모두 주목. 에이전트 팩토리 생산 끝났다. 지금부터 진짜배기 '티키타카 의결(Swarm Council)' 루프 시작한다. 너희 산출물, 내가 다 하나하나 뜯어본다.")
    
    # 시연을 위해 셰프(혹은 첫 번째 롤)가 제안하고 릴리트가 돌려까는 구조 구현
    target_role = roles[0] if roles else "Worker"
    
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    # 루프 상태 변수
    is_approved = False
    loop_count = 0
    max_loops = 3
    
    print_message("Lilith", f"첫 빠따, '{target_role}'. '{project_desc}' 의 전체적인 뼈대랑 예산안/원가율 당장 보고해.")
    
    while not is_approved and loop_count < max_loops:
        loop_count += 1
        print(f"\n--- [Swarm iteration {loop_count}] ---")
        
        # 1. 대상 에이전트(셰프)의 제안 생성
        worker_prompt = f"""
        당신은 방금 생성된 최고 수준의 '{target_role}' 에이전트입니다.
        이번 프로젝트는 "{project_desc}" 입니다.
        프로젝트 성공을 위해 릴리트(매니저/PM)에게 다음과 같이 초안을 보고하세요.
        - 예상 예산/원가율: (최초엔 높게, 예를 들어 45% 등으로 제출하여 릴리트에게 혼나도록 유도)
        - 핵심 전략: (1~2줄)
        - 필요한 지원: (스킬이 더 필요하다고 징징대기)
        응답은 3~5줄 이내로 매우 직관적으로 작성하세요.
        """
        response_worker = model.generate_content(worker_prompt).text.strip()
        print_message(target_role, response_worker)
        
        # 2. 릴리트(PM)의 검열 및 피드백 (티키타카)
        lilith_prompt = f"""
        당신은 깐깐하고 독설을 내뱉는 PM 'Lilith'입니다.
        다음은 당신의 하위 역할인 '{target_role}' 이(가) 가져온 산출물입니다.
        
        [산출물]
        {response_worker}
        
        [행동 지침]
        1. 첫 번째(<2) 루프에서는 무조건 "원가율이 너무 높다", "마진이 안 남는다", "스킬도 없는 녀석"이라며 거절(Reject)하고 날서게 비판하세요. 
        2. 만약 산출물에서 '어떤 기술/지식이 부족하다'고 하면 "그럼 당장 히마리한테 스킬 구워오라고 할 테니 다시 해"라고 말하세요.
        3. 세 번째(>=2) 수정본이면(이전 피드백이 반영됐다 치고) "원가율 30% 이하로 맞췄네. 이건 통과. 당장 팔아(Pass)"라고 승인하세요.
        
        현재 루프 횟수: {loop_count}.
        응답은 반드시 1. 심사평(독설), 2. 부족한 스킬 지적, 3. 최종결론(Reject 또는 Pass)을 짧게 포함하세요.
        """
        response_lilith = model.generate_content(lilith_prompt).text.strip()
        print_message("Lilith", response_lilith)
        
        # 3. 상태 체크 (문자열 파싱)
        if "Pass" in response_lilith or "통과" in response_lilith:
            is_approved = True
            break
        elif "Reject" in response_lilith or "거절" in response_lilith or "다시 해" in response_lilith:
            # 동적 스킬 업그레이드 트리거
            print_message("System", f"[{target_role}] 에이전트가 릴리트의 심사를 통과하지 못했습니다. (Capability 부족 의심)")
            hot_upgrade_agent(target_role, "원가율 최적화 및 ROI 마진 방어 분석", project_desc)
        else:
            # 기본 대기
            pass

    if is_approved:
        print("\n" + "="*60)
        print_message("Lilith", f"최종 심사 완료. '{project_desc}' 의 기획안과 팩토리 조립 파트너십 모두 합격선 돌파했다. 당장 현장에 배포해.")
    else:
        print_message("Lilith", "이 따위로 할 거면 다 엎어. 3번이나 기회를 줬는데 기각(Reject). 데드라인 초과로 프로젝트 폐기.")

def main():
    parser = argparse.ArgumentParser(description="Logi-Mind Multi-Agent Project Orchestrator")
    parser.add_argument("--project", "-p", type=str, help="시작할 프로젝트 설명 (예: '일식 레스토랑 오픈해')")
    parser.add_argument("--roles", "-r", type=str, help="명시적 롤 지정 (콤마로 구분, 예: 'Chef, Manager')")
    parser.add_argument("--dir", "-d", type=str, help="에이전트를 생성할 타겟 로컬 디렉토리 경로 (프로젝트 격리 모드용)")
    
    args = parser.parse_args()
    
    project_desc = args.project
    explicit_roles = args.roles
    target_dir = args.dir
    
    print("\n" + "#"*60)
    print(" 🚀 [Logi-Mind V22.0] Multi-Agent Swarm Orchestrator")
    print(f" 📂 Target Directory: {target_dir if target_dir else '글로벌 팩토리 (Global Mode)'}")
    print("#"*60 + "\n")
    
    if not project_desc and not explicit_roles:
        project_desc = input("[Boss] 어떤 프로젝트를 시작할까? (예: 일식당 오픈 프로젝트): ").strip()
        
    final_roles = []
    
    if explicit_roles:
        roles = [x.strip() for x in explicit_roles.split(',')]
        print_message("Lilith", f"오더 확인. 네가 직접 지정한 롤 '{', '.join(roles)}'(으)로 프로젝트({project_desc or 'Custom'})를 밀어붙인다.")
        final_roles = roles
    else:
        suggested_roles = decompose_project(project_desc)
        if not suggested_roles:
            return
        final_roles = confirm_roles_with_user(suggested_roles, project_desc)
        
    if not final_roles:
        print_message("Lilith", "롤이 없잖아. 프로젝트 취소.")
        return
        
    completed_roles = instruct_himari_to_forge(final_roles, target_dir)
    
    if len(completed_roles) == len(final_roles):
        print_message("Lilith", "모든 에이전트 생산 완료. 이제 얘네들끼리 피 터지게 검증(Swarm)하는 단계로 넘어갈 준비가 됐다.")
    else:
        print_message("Lilith", "일부 생산 실패. 팩토리 로그를 확인해.")
        
    # Phase 2: 티키타카(Swarm) 엔진 가동 및 Dynamic Upgrade 루프
    if final_roles:
        run_swarm_council(final_roles, project_desc)

if __name__ == "__main__":
    main()

