import argparse
import os
import re
import sys

FACTORY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(FACTORY_DIR)

# Windows Console Encoding Fix
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')


def _safe_project_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_\\-]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t


def main():
    parser = argparse.ArgumentParser(description="Logi-Mind Agent Factory CLI")
    parser.add_argument("--project", "-p", type=str, required=True, help="프로젝트 ID (필수)")
    parser.add_argument("--role", "-r", type=str, help="에이전트 역할 (예: 'Saiba Midori', 'Backend Dev')")
    parser.add_argument("--task", "-t", type=str, help="에이전트에게 요청할 작업 내용")
    parser.add_argument("--model", "-m", type=str, default="gemini-2.0-flash", help="사용할 AI 모델")
    parser.add_argument("--workflow", "-w", type=str, help="워크플로우 YAML 경로")
    parser.add_argument("--agents", "-a", type=str, help="워크플로우 실행 에이전트 목록(쉼표 구분)")

    args = parser.parse_args()

    project_id = _safe_project_id(args.project)
    if not project_id:
        print("유효한 프로젝트 ID를 입력하세요.")
        return

    role = args.role
    if not role:
        print("\n[Agent Setup]")
        role = input("  에이전트 역할 (기본값 'General Assistant'): ").strip()
        if not role:
            role = "General Assistant"

    task = args.task
    if not task:
        print(f"\n[Task for '{role}']")
        task = input("  요청할 작업 내용을 입력하세요: ").strip()

    if not task:
        print("작업 내용이 비어 있어 종료합니다.")
        return

    project_root = os.path.join(FACTORY_DIR, "projects", project_id)
    os.makedirs(project_root, exist_ok=True)
    os.environ["AGENT_PROJECT_ID"] = project_id
    os.environ["AGENT_PROJECT_ROOT"] = project_root

    # Import after project env is fixed.
    from agent_launcher import AgentFactory

    print("\n[Logi-Mind Agent Factory] 시작")
    print(f"Project ID: {project_id}")
    print(f"Project Root: {project_root}")
    print(f"Role: {role}")
    print("-" * 50)

    try:
        factory = AgentFactory()
        if args.workflow:
            roles = [x.strip() for x in (args.agents or "").split(",") if x.strip()]
            factory.run_workflow(task_input=task, workflow_path=args.workflow, role_specs=roles)
        else:
            factory.run(task_input=task, role_spec=role)
    except KeyboardInterrupt:
        print("\n사용자가 실행을 중단했습니다.")
    except Exception as e:
        print(f"\n오류 발생: {e}")


if __name__ == "__main__":
    main()
