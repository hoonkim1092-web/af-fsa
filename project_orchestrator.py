import argparse
import json
import os
import subprocess
import sys
import re
import concurrent.futures
from typing import List

from core.llm_engine import LLMEngine
from core.swarm_council import SwarmCouncil

# [P0] 중앙 설정 검증기 도입 (Zod -> Pydantic 패턴)
# 이 모듈이 임포트되는 순간 모델/DB 키/정책(policy)이 완벽하지 않으면 팩토리는 즉시 중단(Fail-Fast)됩니다.
from config.schema import factory_config
from core.intent import IntentGate
from core.hooks.base import TodoContinuationEnforcer

from factory_manager import research_required_skills, load_policy
from utils.audit_logger import log_audit_event

FACTORY_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BOARD_PATH = os.path.join(FACTORY_DIR, "project_board_state.json")


def print_message(actor: str, message: str):
    print(f"[{actor}] {message}")


def parse_mentions(text: str) -> List[str]:
    # 사용자의 프롬프트에서 @oracle 형태의 명시적 역할을 추출
    matches = re.findall(r"@([a-zA-Z0-9_가-힣]+)", text)
    return list(set(matches))


def decompose_roles(project_description: str, model_name: str = "gemini-3.0-flash") -> List[str]:
    llm = LLMEngine(model_name=model_name)
    prompt = f"""
You are Lilith, a PM orchestrator for a multi-agent factory.
Project description: {project_description}

Return JSON:
{{
  "roles": ["role_a", "role_b", "role_c"]
}}

Rules:
- Provide 2 to 5 practical roles.
- Keep role names short.
- Avoid duplicates.
""".strip()

    try:
        data = llm.generate_json(prompt)
    except Exception as exc:
        print_message("Lilith", f"Role decomposition failed: {exc}")
        return []

    if not isinstance(data, dict):
        return []

    raw_roles = data.get("roles", [])
    if not isinstance(raw_roles, list):
        return []

    roles: List[str] = []
    seen = set()
    for item in raw_roles:
        role = str(item).strip()
        key = role.lower()
        if not role or key in seen:
            continue
        seen.add(key)
        roles.append(role)

    return roles[:5]


def forge_roles(roles: List[str], target_dir: str | None = None, is_ghost_pilot: bool = False, enforce_todo: bool = False) -> List[str]:
    success = []
    env = os.environ.copy()
    if target_dir:
        env["AGENT_PROJECT_ROOT"] = os.path.abspath(target_dir)

    policy = load_policy()
    skill_defs = policy.get("skills", {})
    risk_mapping = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

    def _forge_single_role(role_arg) -> bool:
        role_name, is_gp, enforce = role_arg
        print_message("System", f"--- Analyzing Role: {role_name} ---")
        # [STEP 1] 지시 흡수 (임시 스킬셋 파싱)
        required_skills, _ = research_required_skills(role_name, selected_model_name="gemini-3.0-flash", skip_research=True)
        
        max_risk = 1
        skill_risk_tags = []
        for s in required_skills:
            r = skill_defs.get(s, {}).get("risk", "LOW")
            r_val = risk_mapping.get(r.upper(), 1)
            if r_val > max_risk: 
                max_risk = r_val
            skill_risk_tags.append(f"{s}[{r}]")
            
        # [STEP 2] 가이드 템플릿 출력 (Ghost-Pilot이 아닐 경우)
        if not is_gp:
            print_message("Wizard", f">>> [에이전트 초안] 역할: {role_name}")
            print_message("Wizard", f">>> [필요 스킬 셋] {', '.join(skill_risk_tags)}")
            
        # [STEP 4] 3단 리스크 통제 정책
        if max_risk == 4: # CRITICAL
            print_message("Security", f"⚠️ 치명적 스킬이 포함되었습니다 (Target: {role_name}). 강제 생성하시겠습니까? Y/N")
            ans = input("[System] Y/N: ").strip().upper()
            if ans != 'Y':
                print_message("Security", f"에이전트 '{role_name}' 생성(Forge)이 취소되었습니다.")
                return False
            log_audit_event(f"USER CREATED AGENT '{role_name}' WITH CRITICAL SKILLS: {required_skills}")
        elif max_risk == 3: # HIGH
            log_audit_event(f"USER CREATED AGENT '{role_name}' WITH HIGH SKILLS: {required_skills}")

        print_message("Himari", f"Forging role: {role_name}")
        cmd = [sys.executable, "-u", "factory_manager.py", role_name]
        if enforce:
            cmd.append("--enforce")
            
        try:
            # capture_output을 False로 두어 터미널로 실시간 송출
            proc = subprocess.run(
                cmd,
                cwd=FACTORY_DIR,
                env=env,
                text=True,
                encoding="utf-8",
                capture_output=False,
                timeout=300,
            )
        except Exception as exc:
            print_message("Himari", f"Forge failed for {role_name}: {exc}")
            return False

        if proc.returncode == 0:
            print_message("Himari", f"Forge success: {role_name}")
            return True
        else:
            print_message("Himari", f"Forge failed: {role_name} (code={proc.returncode})")
            return False

    # [Ghost-Pilot 모드 병렬 실행 지원]
    if is_ghost_pilot:
        print_message("System", "🚀 Ghost-Pilot: 멀티 스레딩 기반 다중 에이전트 동시 병렬(Parallel) 생성을 시작합니다.")
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(roles), 5)) as executor:
            args = [(r, is_ghost_pilot, enforce_todo) for r in roles]
            results = list(executor.map(_forge_single_role, args))
            for i, res in enumerate(results):
                if res:
                    success.append(roles[i])
    else:
        for role in roles:
            if _forge_single_role((role, is_ghost_pilot, enforce_todo)):
                success.append(role)

    return success


def parse_roles(raw: str | None) -> List[str]:
    if not raw:
        return []
    out = []
    seen = set()
    for item in raw.split(","):
        role = item.strip()
        key = role.lower()
        if not role or key in seen:
            continue
        seen.add(key)
        out.append(role)
    return out


def main():
    parser = argparse.ArgumentParser(description="Logi-Mind Swarm Council V2 Orchestrator")
    parser.add_argument("--project", "-p", type=str, help="Project description")
    parser.add_argument("--roles", "-r", type=str, help="Comma-separated role list")
    parser.add_argument("--dir", "-d", type=str, help="Target project root for generated skills")
    parser.add_argument("--board-path", type=str, default=DEFAULT_BOARD_PATH, help="Project board JSON output path")
    parser.add_argument("--max-loops", type=int, default=3, help="Swarm council max iterations")
    parser.add_argument("--skip-forge", action="store_true", help="Skip factory_manager forging before council run")
    args = parser.parse_args()

    project_desc = (args.project or "").strip()
    if not project_desc:
        project_desc = input("Project description: ").strip()
    if not project_desc:
        print_message("Lilith", "Project description is required.")
        return

    roles = parse_roles(args.roles)
    
    mentions = parse_mentions(project_desc)
    for m in mentions:
        if m.lower() not in [r.lower() for r in roles]:
            roles.append(m)
            print_message("System", f"🎯 명시적 역할 호출 감지: @{m} 라우팅 추가됨.")

    is_ghost_pilot = False
    enforce_todo = False
    
    if "고스트-파일럿" in project_desc:
        is_ghost_pilot = True
        enforce_todo = True
        print_message("System", "⚡ [Ghost-Pilot] 모드 감지: 사용자의 개입 없이 병렬 파이프라인으로 전면 자율화합니다!")
    else:
        # P1 IntentGate Integration
        print_message("IntentGate", "Classifying user intent to determine optimal workflow...")
        classifier = IntentGate()
        intent_res = classifier.classify(project_desc)
        intent = intent_res.get("intent", "trivial")
        print_message("IntentGate", f"Diagnosed Intent: {intent.upper()} (Confidence: {intent_res.get('confidence', 0)}%)")
        print_message("IntentGate", f"Reasoning: {intent_res.get('reasoning', '')}")

        if intent in ["refactoring", "greenfield"]:
            print_message("System", f"⚠️ Complex Intent ({intent}) detected. Enforcing Todo-Enforced Planning workflow.")
            enforce_todo = True
        else:
            print_message("Wizard", "작업 강제 완수 (Enforce): 에이전트가 도중에 질문하지 않고 자율적으로 끝까지 완수하도록 할까요? (기본:Y) [Y/N]")
            ans = input("[System] Y/N: ").strip().upper()
            if ans != 'N':
                enforce_todo = True
                print_message("System", "🛡️ Todo Continuation Enforcer 활성화: 핑퐁 멈춤 방지.")

    if not roles:
        print_message("Lilith", "No explicit roles given. Decomposing roles with LLM.")
        roles = decompose_roles(project_desc)
    if not roles:
        print_message("Lilith", "No roles available. Stop.")
        return

    print_message("Lilith", f"Roles: {', '.join(roles)}")

    if not args.skip_forge:
        built = forge_roles(roles, target_dir=args.dir, is_ghost_pilot=is_ghost_pilot, enforce_todo=enforce_todo)
        if len(built) != len(roles):
            print_message("Lilith", f"Forge partial success ({len(built)}/{len(roles)}). Continue council run.")
        else:
            print_message("Lilith", "All roles forged.")

    if enforce_todo:
        checker = TodoContinuationEnforcer()
        state = {
            "intent": intent if 'intent' in locals() else "greenfield",
            "workspace": args.dir or FACTORY_DIR
        }
        if not checker.pre_execute(state):
            print_message("Lilith", "Task blocked by Todo Enforcer. Please generate a plan first.")
            return

    council = SwarmCouncil(factory_dir=FACTORY_DIR)
    board = council.run(
        project_desc=project_desc,
        roles=roles,
        board_path=args.board_path,
        target_dir=args.dir,
        max_loops=max(1, int(args.max_loops)),
    )

    status = board.get("current_status", "unknown")
    print_message("Lilith", f"Swarm Council finished: {status}")
    print_message("System", f"Board saved: {args.board_path}")
    print(json.dumps({"status": status, "roles": roles}, ensure_ascii=False))


if __name__ == "__main__":
    main()
