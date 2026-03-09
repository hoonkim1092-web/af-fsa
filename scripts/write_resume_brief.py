from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.continuity.resume_brief import write_resume_brief
from scripts.project_context_sync import load_dotenv_simple, parse_project_inputs, resolve_project_root


def refresh_resume_briefs(
    repo_root: str | Path | None = None,
    project_inputs: list[str] | None = None,
    trigger: str = "sync",
) -> list[dict[str, str]]:
    repo_root_path = Path(repo_root).resolve() if repo_root else REPO_ROOT
    load_dotenv_simple(repo_root_path)
    results: list[dict[str, str]] = []

    for project_input in project_inputs or []:
        project_id, project_root = resolve_project_root(repo_root_path, project_input)
        resume_path = write_resume_brief(project_root, trigger=trigger)
        results.append(
            {
                "project_input": str(project_input),
                "project_id": project_id,
                "project_root": project_root.as_posix(),
                "resume_brief_path": resume_path.as_posix(),
            }
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic resume briefs for synced projects.")
    parser.add_argument("--project", "-p", default="", help="single project id")
    parser.add_argument("--projects", default="", help="multiple project ids (comma separated)")
    parser.add_argument("--trigger", default="sync", help="generation trigger label")
    args = parser.parse_args()

    repo_root = REPO_ROOT
    project_inputs = parse_project_inputs(args.project, args.projects)
    if not project_inputs:
        raise SystemExit("provide --project or --projects")

    results = refresh_resume_briefs(repo_root=repo_root, project_inputs=project_inputs, trigger=args.trigger)
    print(json.dumps({"ok": True, "items": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
