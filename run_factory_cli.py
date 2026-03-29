import argparse
import os
import re
import sys

# PyInstaller 환경에서 stdout 버퍼링 해제
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

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
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 시작 즉시 CLI 프로바이더 자동 탐색 및 런타임 레지스트리 설정
# AGENT_CHAT_PROVIDER 환경변수 불필요: 설치된 CLI를 자동 감지해 우선순위 배정
try:
    from core.engine_auth import auto_configure_cli_provider
    auto_configure_cli_provider()
except Exception:
    pass  # 임포트 실패 시 무시 (이후 check_llm_available()에서 재시도)



def _safe_project_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_\-]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t



def _resolve_projects_root(override: str | None = None) -> str:
    raw = str(override or os.getenv("AGENT_PROJECTS_DIR", "") or "").strip()
    if not raw:
        raw = os.path.join(FACTORY_DIR, "projects")
    return os.path.abspath(os.path.expanduser(raw))



def _run_skill_creator(argv: list[str] | None = None):
    """skill-create subcommand."""
    from core.skill_creator import cli_main

    cli_main(argv)



def _run_skill_spec(argv: list[str] | None = None):
    """skill-spec subcommand."""
    from core.skill_spec_synthesizer import cli_main

    cli_main(argv)



def _run_preflight(argv: list[str] | None = None):
    """preflight subcommand."""
    from core.skill_preflight import cli_main

    cli_main(argv)



def _run_skill_eval(argv: list[str] | None = None):
    """skill-eval subcommand."""
    from core.skill_eval_harness import cli_main

    cli_main(argv)



def _run_skill_promote(argv: list[str] | None = None):
    """skill-promote subcommand."""
    from core.skill_promotion import cli_main

    cli_main(argv)



def _launch_interactive_mode(
    projects_root: str,
    *,
    role: str = "General Assistant",
    model: str = "",
    execution_mode: str = "approval",
    pipeline_mode: str = "auto",
    enable_build: bool = False,
):
    """대화형 모드 진입점 (인자 없이 af 실행 시) — 바로 채팅 시작."""
    from core.interactive_chat import run_interactive

    project_id = "default"
    workspace = os.path.join(projects_root, project_id)
    os.makedirs(workspace, exist_ok=True)
    os.environ["AGENT_PROJECTS_DIR"] = projects_root
    os.environ["AGENT_PROJECT_ID"] = project_id
    os.environ["AGENT_PROJECT_ROOT"] = workspace
    os.environ.setdefault("AGENT_AUTO_INSTALL_CLI", "1")

    run_interactive(
        project_id=project_id,
        workspace=workspace,
        role=role,
        model=model,
        auto_approve=(execution_mode == "fsa"),
        execution_mode=execution_mode,
        pipeline_mode=pipeline_mode,
        enable_build=enable_build,
    )


def main(argv: list[str] | None = None):
    effective_argv = argv if argv is not None else sys.argv[1:]

    # ── 인자 없이 실행 → 대화형 PDCA 모드 ──
    if not effective_argv or effective_argv == ["--interactive"]:
        projects_root = _resolve_projects_root()
        _launch_interactive_mode(projects_root)
        return

    if effective_argv and effective_argv[0] == "skill-create":
        _run_skill_creator(effective_argv[1:])
        return
    if effective_argv and effective_argv[0] == "skill-spec":
        _run_skill_spec(effective_argv[1:])
        return
    if effective_argv and effective_argv[0] == "preflight":
        _run_preflight(effective_argv[1:])
        return
    if effective_argv and effective_argv[0] == "skill-eval":
        _run_skill_eval(effective_argv[1:])
        return
    if effective_argv and effective_argv[0] == "skill-promote":
        _run_skill_promote(effective_argv[1:])
        return

    parser = argparse.ArgumentParser(description="Agent Factory CLI")
    parser.add_argument("--interactive", action="store_true", help="대화형 PDCA 모드 시작")
    parser.add_argument("--project", "-p", type=str, required=False, help="Project id")
    parser.add_argument("--role", "-r", type=str, help="Agent role")
    parser.add_argument("--task", "-t", type=str, help="Task input")
    parser.add_argument("--model", "-m", type=str, default=None, help="Model override")
    parser.add_argument("--provider", choices=CLI_PROVIDER_CHOICES, help="Force CLI provider")
    parser.add_argument("--provider-command", type=str, help="CLI provider command path")
    parser.add_argument("--projects-root", type=str, help="Projects root override")
    parser.add_argument("--workflow", "-w", type=str, help="Workflow YAML path")
    parser.add_argument("--agents", "-a", type=str, help="Comma-separated workflow roles")
    parser.add_argument("--mode", choices=["approval", "fsa", "ise"], default="approval", help="Execution mode")
    parser.add_argument("--fsa", action="store_true", help="Shortcut for full self automation mode")
    parser.add_argument("--ise", action="store_true", help="Infinite Self-Evolution mode (무한 자기진화 루프)")
    parser.add_argument("--build", action="store_true", help="Build missing skills before run")
    parser.add_argument("--no-cli-auto-install", action="store_true", help="Disable missing CLI auto install")
    parser.add_argument("--pipeline", choices=["auto", "single", "project"], default="auto", help="Pipeline mode")
    parser.add_argument("--chat", action="store_true", help="Interactive chat mode (continuous conversation)")
    args = parser.parse_args(argv)
    execution_mode = "ise" if (args.ise or args.mode == "ise") else ("fsa" if (args.fsa or args.mode == "fsa") else "approval")

    # --interactive 또는 --project 미입력 → 대화형 PDCA 모드
    if getattr(args, "interactive", False) or not args.project:
        projects_root = _resolve_projects_root(args.projects_root if hasattr(args, "projects_root") else None)
        _launch_interactive_mode(
            projects_root,
            role=(args.role or "").strip() or "General Assistant",
            model=args.model or "",
            execution_mode=execution_mode,
            pipeline_mode=args.pipeline,
            enable_build=bool(args.build),
        )
        return

    if args.provider_command and not args.provider:
        parser.error("--provider-command requires --provider")

    project_id = _safe_project_id(args.project)
    if not project_id:
        print("Enter a valid project id.")
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
        try:
            from core.providers.registry import configure_providers
            configure_providers([args.provider])
        except Exception:
            os.environ["AGENT_CHAT_PROVIDER"] = args.provider  # 폴백
    if args.provider_command:
        os.environ[CLI_PROVIDER_COMMAND_ENVS[args.provider]] = args.provider_command.strip()
    if args.no_cli_auto_install:
        os.environ["AGENT_AUTO_INSTALL_CLI"] = "0"
    else:
        os.environ.setdefault("AGENT_AUTO_INSTALL_CLI", "1")

    # ── 대화형 채팅 모드 ──
    if args.chat:
        from core.interactive_chat import run_interactive
        run_interactive(
            project_id=project_id,
            workspace=project_root,
            role=role,
            model=args.model or "",
            auto_approve=(execution_mode == "fsa"),
            execution_mode=execution_mode,
            pipeline_mode=args.pipeline,
            enable_build=bool(args.build),
        )
        return

    # ── 기존 태스크 실행 모드 ──
    task = args.task
    if not task:
        print("\n[Task Input]")
        task = input("  Enter the task: ").strip()

    if not task:
        print("Task input is empty. Exiting.")
        return

    from agent_launcher import AgentFactory

    print("\n[Logi-Mind Agent Factory] start")
    print(f"Project ID: {project_id}")
    print(f"Projects Root: {projects_root}")
    print(f"Project Root: {project_root}")
    print(f"Role: {role}")
    print("-" * 50)

    try:
        factory = AgentFactory()
        if args.workflow:
            roles = [item.strip() for item in (args.agents or "").split(",") if item.strip()]
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
        print("\nExecution interrupted by user.")
    except Exception as exc:
        print(f"\nError: {exc}")


if __name__ == "__main__":
    main()
