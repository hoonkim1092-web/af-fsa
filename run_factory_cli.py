import argparse
import os
import re
import sys

FACTORY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(FACTORY_DIR)

CLI_PROVIDER_CHOICES = ("claude_cli", "gemini_cli", "codex_cli")
CLI_PROVIDER_COMMAND_ENVS = {
    "claude_cli": "AGENT_CLAUDE_CLI_COMMAND",
    "gemini_cli": "AGENT_GEMINI_CLI_COMMAND",
    "codex_cli": "AGENT_CODEX_CLI_COMMAND",
}

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _safe_project_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_\\-]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t


def _resolve_projects_root(override: str | None = None) -> str:
    raw = str(override or os.getenv("AGENT_PROJECTS_DIR", "") or "").strip()
    if not raw:
        raw = os.path.join(FACTORY_DIR, "projects")
    return os.path.abspath(os.path.expanduser(raw))


def _run_skill_creator(argv: list[str] | None = None):
    """skill-create 서브커맨드 — Claude Code 스타일 스킬 생성기"""
    from core.skill_creator import cli_main
    cli_main(argv)


def main(argv: list[str] | None = None):
    # skill-create 서브커맨드 감지: 첫 인자가 "skill-create" 이면 스킬 생성기로 분기
    effective_argv = argv if argv is not None else sys.argv[1:]
    if effective_argv and effective_argv[0] == "skill-create":
        _run_skill_creator(effective_argv[1:])
        return

    parser = argparse.ArgumentParser(description="Agent Factory CLI")
    parser.add_argument("--project", "-p", type=str, required=True, help="프로젝트 ID (필수)")
    parser.add_argument("--role", "-r", type=str, help="에이전트 역할 (예: 'Saiba Midori', 'Backend Dev')")
    parser.add_argument("--task", "-t", type=str, help="에이전트에게 요청할 작업 내용")
    parser.add_argument("--model", "-m", type=str, default=None, help="사용할 AI 모델")
    parser.add_argument("--provider", choices=CLI_PROVIDER_CHOICES, help="CLI provider 강제 지정")
    parser.add_argument("--provider-command", type=str, help="선택한 CLI provider 실행 경로/명령")
    parser.add_argument("--projects-root", type=str, help="프로젝트 루트 상위 디렉터리 override")
    parser.add_argument("--workflow", "-w", type=str, help="워크플로우 YAML 경로")
    parser.add_argument("--agents", "-a", type=str, help="워크플로우 실행 에이전트 목록(쉼표 구분)")
    parser.add_argument("--mode", choices=["approval", "fsa"], default="approval", help="실행 모드 (기본: approval, 자율: fsa)")
    parser.add_argument("--fsa", action="store_true", help="풀 셀프 자동화(Full Self Automation) 모드 활성화 단축키")
    parser.add_argument("--build", action="store_true", help="Build missing skills before run")
    parser.add_argument("--no-cli-auto-install", action="store_true", help="누락된 Claude/Gemini/Codex CLI 자동 설치 비활성화")
    parser.add_argument("--pipeline", choices=["auto", "single", "project"], default="auto", help="실행 파이프라인 선택")
    args = parser.parse_args(argv)
    execution_mode = "fsa" if (args.fsa or args.mode == "fsa") else "approval"

    if args.provider_command and not args.provider:
        parser.error("--provider-command requires --provider")

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

    projects_root = _resolve_projects_root(args.projects_root)
    project_root = os.path.join(projects_root, project_id)
    os.makedirs(project_root, exist_ok=True)
    os.environ["AGENT_PROJECTS_DIR"] = projects_root
    os.environ["AGENT_PROJECT_ID"] = project_id
    os.environ["AGENT_PROJECT_ROOT"] = project_root
    if args.model:
        os.environ["AGENT_CHAT_MODEL"] = args.model.strip()
    if args.provider:
        os.environ["AGENT_CHAT_PROVIDER"] = args.provider
    if args.provider_command:
        os.environ[CLI_PROVIDER_COMMAND_ENVS[args.provider]] = args.provider_command.strip()
    if args.no_cli_auto_install:
        os.environ["AGENT_AUTO_INSTALL_CLI"] = "0"
    else:
        os.environ.setdefault("AGENT_AUTO_INSTALL_CLI", "1")

    from agent_launcher import AgentFactory

    print("\n[Logi-Mind Agent Factory] 시작")
    print(f"Project ID: {project_id}")
    print(f"Projects Root: {projects_root}")
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
