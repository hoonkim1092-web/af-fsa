import argparse
import json
import os
import subprocess
import sys
from typing import List

from core.llm_engine import LLMEngine
from core.swarm_council import SwarmCouncil
from dotenv import load_dotenv

load_dotenv()

FACTORY_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BOARD_PATH = os.path.join(FACTORY_DIR, "project_board_state.json")


def print_message(actor: str, message: str):
    print(f"[{actor}] {message}")


def decompose_roles(project_description: str, model_name: str = "gemini-2.0-flash") -> List[str]:
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


def forge_roles(roles: List[str], target_dir: str | None = None) -> List[str]:
    success = []
    env = os.environ.copy()
    if target_dir:
        env["AGENT_PROJECT_ROOT"] = os.path.abspath(target_dir)

    for role in roles:
        print_message("Himari", f"Forging role: {role}")
        cmd = [sys.executable, "-u", "factory_manager.py", role]
        try:
            proc = subprocess.run(
                cmd,
                cwd=FACTORY_DIR,
                env=env,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=300,
            )
        except Exception as exc:
            print_message("Himari", f"Forge failed for {role}: {exc}")
            continue

        if proc.returncode == 0:
            success.append(role)
            print_message("Himari", f"Forge success: {role}")
        else:
            print_message("Himari", f"Forge failed: {role} (code={proc.returncode})")

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
    if not roles:
        print_message("Lilith", "No explicit roles given. Decomposing roles with LLM.")
        roles = decompose_roles(project_desc)
    if not roles:
        print_message("Lilith", "No roles available. Stop.")
        return

    print_message("Lilith", f"Roles: {', '.join(roles)}")

    if not args.skip_forge:
        built = forge_roles(roles, target_dir=args.dir)
        if len(built) != len(roles):
            print_message("Lilith", f"Forge partial success ({len(built)}/{len(roles)}). Continue council run.")
        else:
            print_message("Lilith", "All roles forged.")

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
