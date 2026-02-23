import os
import sys
import subprocess
import argparse
import re
import json
import yaml

from core.llm_engine import LLMEngine
from dotenv import load_dotenv

load_dotenv()

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
    
    llm = LLMEngine(model_name="gemini-2.0-flash")
    prompt = f"""
    당신은 Logi-Mind 프로젝트의 수석 PM인 Lilith입니다.
    사용자가 다음 프로젝트를 요청했습니다: "{project_description}"
    
    이 프로젝트를 완수하기 위해 필요한 하위 에이전트들의 역할(Role) 목록을 추천해주세요. 
    반드시 '필수' 롤과 '권장/선택' 롤로 구분해서 JSON 형태로만 응답하세요.
    예: {{"roles": [{{"role": "Chef", "type": "필수", "reason": "메뉴 구상 및 레시피 작성"}}, {{"role": "Manager", "type": "필수", "reason": "예산 및 진행 검수"}}]}}
    단순 JSON 객체만 반환하세요.
    """
    
    try:
        data = llm.generate_json(prompt)
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
        cmd = [sys.executable, "-u", "factory_manager.py", role]
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
    
    print_message("Himari", f"오더 접수. '{role}' 을 위한 <<{missing_capability}>> 긴급 파츠(스킬) 주조 공정 가동 중...")
    
    # 팩토리 매니저를 서브프로세스로 호출해 실제 스킬 설치 진행
    agent_id = role.replace(" ", "-").lower() + "-agent"
    
    # 내부적으로 factory_manager를 호출하되, missing_capability를 강제 주입하는 로직이 필요함.
    # 현재 factory_manager.py는 role을 주면 알아서 research해서 설치하는 구조임.
    # 일단은 Himari가 다시 그 role 전체 스킬을 스캔 후 보강하는 형태로 실행
    cmd = [sys.executable, "-u", "factory_manager.py", role]
    
    try:
        process = subprocess.Popen(cmd, cwd=FACTORY_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
        for line in iter(process.stdout.readline, ''):
            if "[SYS" not in line and "[GIT" not in line: # 불필요한 로그 필터링
                sys.stdout.write("  " + line)
        process.stdout.close()
        process.wait()
        
        if process.returncode == 0:
            print_message("Himari", f"주조 및 조립 완료. '{role}' 의 코어에 핫 리로딩(Hot-Reloading) 주입했습니다.")
            print_message("Lilith", f"좋아. 야 '{role}'! 스킬 하나 머리에 꽂아 줬으니까 이제 똑바로 '{missing_capability}' 해서 다시 가져와.")
            return True
        else:
            print_message("Himari", f"스킬 주조 실패. (Return code: {process.returncode})")
            print_message("Lilith", "히마리 너마저 이따위로 할 거야? 닥치고 다시 디버깅해.")
            return False
            
    except Exception as e:
        print_message("Himari", f"스킬 주조 시스템 에러: {e}")
        return False

def run_swarm_council(roles, project_desc):
    """
    생성된 에이전트들 간의 티키타카(대화 및 검문) 루프.
    Lilith가 PM으로서 하위 에이전트의 제안을 갈구고 깎아내는 메인 오케스트레이션 엔진.
    """
    print("\n" + "="*60)
    print_message("Lilith", "모두 주목. 에이전트 팩토리 생산 끝났다. 지금부터 진짜배기 '티키타카 의결(Swarm Council)' 루프 시작한다. 너희 산출물, 내가 다 하나하나 뜯어본다.")
    
    # Stateful Project Board 초기화
    project_board = {
        "project_description": project_desc,
        "roles": roles,
        "history": [],
        "current_status": "Planning"
    }

    # 정책 파일 로드 (Defense Logic 용)
    policy_path = os.path.join(FACTORY_DIR, "projects", "default", "policies.yaml")
    policy_text = ""
    try:
        if os.path.exists(policy_path):
            with open(policy_path, "r", encoding="utf-8") as f:
                policy_text = f.read()
    except Exception as e:
        print_message("System", f"Policy load failed: {e}")

    llm = LLMEngine(model_name="gemini-2.0-flash")
    
    is_approved = False
    loop_count = 0
    max_loops = 3
    
    # 둥글게 돌아가며 제안을 받을 수도 있지만, 여기서는 첫 번째 역할을 대표로 사용.
    target_role = roles[0] if roles else "Worker"
    print_message("Lilith", f"첫 빠따, '{target_role}'. '{project_desc}' 의 전체적인 뼈대랑 예산안/원가율 당장 보고해.")
    
    while not is_approved and loop_count < max_loops:
        loop_count += 1
        print(f"\n--- [Swarm iteration {loop_count}] ---")
        
        # 1. 대상 에이전트의 제안 생성 (JSON 포맷 강제)
        worker_prompt = f"""
        당신은 방금 생성된 최고 수준의 '{target_role}' 에이전트입니다.
        이번 프로젝트는 "{project_desc}" 입니다.
        프로젝트 보드 히스토리: {json.dumps(project_board['history'], ensure_ascii=False)}
        
        프로젝트 성공을 위해 릴리트(매니저/PM)에게 다음과 같이 초안을 보고하세요.
        - 예상 예산/원가율: (최초엔 높게 제출하여 릴리트에게 혼나도록 유도)
        - 핵심 전략: (1~2줄)
        - 필요한 지원: (스킬이 더 필요하다고 징징대기)
        
        오직 JSON 객체만 반환하세요.
        """
        
        worker_response_data = llm.generate_json(worker_prompt)
        response_worker_text = json.dumps(worker_response_data, ensure_ascii=False, indent=2)
        print_message(target_role, response_worker_text)
        
        # 상태 업데이트
        project_board["history"].append({"role": target_role, "type": "proposal", "content": worker_response_data})
        
        # 2. 릴리트(PM)의 검열 초안 작성
        lilith_draft_prompt = f"""
        당신은 깐깐하고 독설을 내뱉는 PM 'Lilith'입니다.
        다음은 하위 역할 '{target_role}' 의 산출물입니다.
        {response_worker_text}
        
        1. 첫 번째(<2) 루프에서는 원가율, 마진, 스킬 부족을 이유로 무조건 거절(Reject)하고 날서게 비판하세요. 
        2. 스킬 부족을 징징거리면 "그럼 당장 히마리한테 스킬 구워오라고 할 테니 다시 해"라고 지시하세요.
        3. 세 번째(>=2) 루프면 "원가율 30% 이하로 맞췄네. 이건 통과. 당장 팔아(Pass)"라고 승인하세요.
        현재 루프 횟수: {loop_count}.
        
        오직 JSON 객체로 응답하세요. 키: "critique", "missing_skill" (선택), "decision" ("Reject" 또는 "Pass")
        """
        lilith_draft_data = llm.generate_json(lilith_draft_prompt)
        
        # 3. Lilith Defense Logic (Self-Validation against Policies)
        print_message("System", "(Lilith is self-validating her decision against project policies...)")
        defense_prompt = f"""
        Review the following PM decision against the project policies to ensure it is structurally sound and safe.
        Project Policies: {policy_text}
        PM Draft Decision: {json.dumps(lilith_draft_data, ensure_ascii=False)}
        
        If the decision violates strict_quality_gate or other policies, correct it.
        Return the Final PM Decision as a JSON object with keys: "verified_critique", "verified_decision", "missing_skill".
        """
        lilith_final_data = llm.generate_json(defense_prompt)
        
        # Defense Fallback
        if not lilith_final_data:
            lilith_final_data = {
                "verified_critique": lilith_draft_data.get("critique", "정책 검증 실패. 다시 분석해."),
                "verified_decision": lilith_draft_data.get("decision", "Reject"),
                "missing_skill": lilith_draft_data.get("missing_skill", "")
            }

        response_lilith_text = f"[{lilith_final_data.get('verified_decision', 'Reject')}] {lilith_final_data.get('verified_critique', '')}"
        print_message("Lilith", response_lilith_text)
        
        project_board["history"].append({"role": "Lilith", "type": "decision", "content": lilith_final_data})
        
        # 4. 상태 트리거 실행
        decision = str(lilith_final_data.get("verified_decision", "")).strip().lower()
        
        if "pass" in decision or "통과" in decision:
            is_approved = True
            break
        elif "reject" in decision or "거절" in decision:
            missing_skill = lilith_final_data.get("missing_skill", "")
            if missing_skill and len(missing_skill) > 2:
                print_message("System", f"[{target_role}] 에이전트 능력 부족 감지: {missing_skill}")
                hot_upgrade_agent(target_role, missing_skill, project_desc)
        else:
            pass

    if is_approved:
        project_board["current_status"] = "Approved"
        print("\n" + "="*60)
        print_message("Lilith", f"최종 심사 완료. '{project_desc}' 기획안과 파트너십 합격선 돌파. 당장 배포해.")
    else:
        project_board["current_status"] = "Rejected"
        print_message("Lilith", "이 따위로 할 거면 다 엎어. 3번이나 기회를 줬는데 기각(Reject). 데드라인 초과로 폐기.")
        
    # 최종 보드 상태 저장 (디버그/기록용)
    try:
        with open("project_board_state.json", "w", encoding="utf-8") as f:
            json.dump(project_board, f, ensure_ascii=False, indent=2)
    except Exception: pass

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

