from __future__ import annotations

import argparse
import subprocess
import sys

from core.destructive_guard import blocked_command_message, detect_destructive_process


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proxy launcher that blocks destructive commands.")
    parser.add_argument("--target", required=True)
    parser.add_argument("--delegate", required=True)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(list(argv or sys.argv[1:]))
    args = list(ns.args)
    if args and args[0] == "--":
        args = args[1:]

    reason = detect_destructive_process(str(ns.target), [str(ns.target), *args])
    if reason:
        print(blocked_command_message(reason), file=sys.stderr, flush=True)
        return 126

    try:
        completed = subprocess.run([str(ns.delegate), *args], check=False)
    except FileNotFoundError:
        print(f"Agent Factory destructive guard could not find delegate executable: {ns.delegate}", file=sys.stderr, flush=True)
        return 127
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
