import argparse
import json
import sys

from core.providers.session_adapter import handle_hook_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge CLI hook events into agent-factory continuity.")
    parser.add_argument("--provider", required=True, choices=("claude", "gemini"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo-root", default="")
    args = parser.parse_args(argv)

    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    result = handle_hook_event(
        args.provider,
        payload,
        workspace=args.workspace,
        run_id=args.run_id,
        repo_root=args.repo_root,
    )
    if result:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
