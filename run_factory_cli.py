import argparse
import os
import re
import sys

FACTORY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(FACTORY_DIR)

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')


def _safe_project_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_\\-]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t


def main():
    parser = argparse.ArgumentParser(description="Agent Factory CLI")
    parser.add_argument("--project", "-p", type=str, required=True, help="프로젝트 ID (필수)")
    parser.add_argument("--role", "-r", type=str, help="에이전트 역할 (예: 'Saiba Midori', 'Backend Dev')")
    parser.add_argument("--task", "-t", type=str, help="에이전트에게 요청할 작업 내용")
    parser.add_argument("--model", "-m", type=str, default=None, help="사용할 AI 모델")
    parser.add_argument("--workflow", "-w", type=str, help="워크플로우 YAML 경로")
    parser.add_argument("--agents", "-a", type=str, help="워크플로우 실행 에이전트 목록(쉼표 구분)")
    parser.add_argument("--mode", choices=["approval", "fsa"], default="approval", help="실행 모드 (기본: approval, 자율: fsa)")
    parser.add_argument("--fsa", action="store_true", help="풀 셀프 자동화(Full Self Automation) 모드 활성화 단축키")
    parser.add_argument("--build", action="store_true", help="Build missing skills before run")
    parser.add_argument("--no-cli-auto-install", action="store_true", help="누락된 Claude/Gemini/Codex CLI 자동 설치 비활성화")
    parser.add_argument("--pipeline", choices=["auto", "single", "project"], default="auto", help="실행 파이프라인 선택")
    args = parser.parse_args()
    execution_mode = "fsa" if (args.fsa or args.mode == "fsa") else "approval"

    project_id = _safe_project_id(args.project)
    if not project_id:
        print("유효한 프로젝트 ID를 입력하세요.")
        return

    task = args.task
    if not task:
        print("\n[Task Input]")
        task = input("  요청할 작업 내용을 입력하세요: ").strip()

    if not task:
        print("작업 내용이 비어 있어 종료합니다.")
        return

    role = (args.role or "").strip() or "General Assistant"

    project_root = os.path.join(FACTORY_DIR, "projects", project_id)
    os.makedirs(project_root, exist_ok=True)
    os.environ["AGENT_PROJECT_ID"] = project_id
    os.environ["AGENT_PROJECT_ROOT"] = project_root
    if args.model:
        os.environ["AGENT_CHAT_MODEL"] = args.model.strip()
    if args.no_cli_auto_install:
        os.environ["AGENT_AUTO_INSTALL_CLI"] = "0"
    else:
        os.environ.setdefault("AGENT_AUTO_INSTALL_CLI", "1")
    os.environ.setdefault("AGENT_DISABLE_ENGINE_API_KEYS", "1")

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
            factory.run(
                task_input=task,
                role_spec=role,
                enable_build=bool(args.build),
                execution_mode=execution_mode,
                pipeline_mode=args.pipeline,
            )
    except KeyboardInterrupt:
        print("\n사용자가 실행을 중단했습니다.")
    except Exception as e:
        print(f"\n오류 발생: {e}")


if __name__ == "__main__":
    main()
