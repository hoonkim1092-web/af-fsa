from __future__ import annotations

import re
from pathlib import Path

DESTRUCTIVE_GUARD_MARKER = "[Destructive Action Guard]"

_CLAUDE_DENY_RULES = (
    "Bash(rm:*)",
    "Bash(del:*)",
    "Bash(erase:*)",
    "Bash(Remove-Item:*)",
    "Bash(cmd /c del:*)",
    "Bash(powershell -Command Remove-Item:*)",
    "Bash(pwsh -Command Remove-Item:*)",
    "Bash(git clean:*)",
    "Bash(git reset --hard:*)",
    "Bash(git checkout --:*)",
    "Bash(git restore --worktree:*)",
    "Bash(git restore --staged:*)",
)

_GEMINI_RULES = (
    ("Block rm", "rm", None),
    ("Block del", "del", None),
    ("Block erase", "erase", None),
    ("Block Remove-Item", "Remove-Item", None),
    ("Block cmd del", "cmd", r"(^|\s)/c\s+del(\s|$)"),
    ("Block powershell Remove-Item", "powershell", r"(^|\s)-Command\s+Remove-Item(\s|$)"),
    ("Block pwsh Remove-Item", "pwsh", r"(^|\s)-Command\s+Remove-Item(\s|$)"),
    ("Block git clean", "git", r"(^|\s)clean(\s|$)"),
    ("Block git reset --hard", "git", r"(^|\s)reset(\s|$).*--hard"),
    ("Block git checkout --", "git", r"(^|\s)checkout(\s|$).*--"),
    ("Block git restore --worktree", "git", r"(^|\s)restore(\s|$).*--worktree"),
    ("Block git restore --staged", "git", r"(^|\s)restore(\s|$).*--staged"),
)

_SHELL_BLOCK_PATTERNS = (
    ("rm", re.compile(r"(^|[\s;&|()])rm(\s|$)", re.IGNORECASE)),
    ("del", re.compile(r"(^|[\s;&|()])del(\s|$)", re.IGNORECASE)),
    ("erase", re.compile(r"(^|[\s;&|()])erase(\s|$)", re.IGNORECASE)),
    ("Remove-Item", re.compile(r"(^|[\s;&|()])Remove-Item(\s|$)", re.IGNORECASE)),
    ("git clean", re.compile(r"(^|[\s;&|()])git\s+clean(\s|$)", re.IGNORECASE)),
    ("git reset --hard", re.compile(r"(^|[\s;&|()])git\s+reset\b.*--hard(\s|$)", re.IGNORECASE)),
    ("git checkout --", re.compile(r"(^|[\s;&|()])git\s+checkout\b.*\s--(\s|$)", re.IGNORECASE)),
    ("git restore --worktree", re.compile(r"(^|[\s;&|()])git\s+restore\b.*--worktree(\s|$)", re.IGNORECASE)),
    ("git restore --staged", re.compile(r"(^|[\s;&|()])git\s+restore\b.*--staged(\s|$)", re.IGNORECASE)),
)


def inject_destructive_guard_contract(system_prompt: str) -> str:
    base = str(system_prompt or "").strip()
    if DESTRUCTIVE_GUARD_MARKER in base:
        return base

    contract = (
        f"{DESTRUCTIVE_GUARD_MARKER}\n"
        "Never execute destructive delete/reset shell actions unless the human explicitly asks for that exact action in the current message and the runtime policy is changed to allow it.\n"
        "Forbidden operations include `rm`, `del`, `erase`, `Remove-Item`, `git clean`, `git reset --hard`, `git checkout --`, `git restore --worktree`, `git restore --staged`, and equivalent delete/reset commands.\n"
        "If cleanup seems necessary, stop and explain the safer non-destructive alternative instead of running the command."
    )
    return f"{base}\n\n{contract}".strip()


def merge_claude_destructive_guard(settings: dict) -> dict:
    data = dict(settings) if isinstance(settings, dict) else {}
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
    deny = permissions.get("deny", [])
    if not isinstance(deny, list):
        deny = []
    merged = list(dict.fromkeys([*(str(item) for item in deny), *_CLAUDE_DENY_RULES]))
    permissions["deny"] = merged
    data["permissions"] = permissions
    return data


def write_gemini_destructive_policy(path: Path) -> Path:
    lines = [
        "# Agent Factory destructive command guard",
        "# Blocks delete/reset shell commands while keeping normal edits available.",
        "",
    ]
    for description, command_prefix, args_pattern in _GEMINI_RULES:
        lines.extend(
            [
                "[[rules]]",
                f'description = "{description}"',
                'include_tools = ["run_shell_command"]',
                'decision = "deny"',
                "",
                "[[rules.matchers]]",
                'field = "commandPrefix"',
                f'pattern = "{command_prefix}"',
                "",
            ]
        )
        if args_pattern:
            lines.extend(
                [
                    "[[rules.matchers]]",
                    'field = "argsPattern"',
                    f'pattern = """{args_pattern}"""',
                    "",
                ]
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def attach_gemini_policy_path(settings: dict, policy_path: Path) -> dict:
    data = dict(settings) if isinstance(settings, dict) else {}
    paths = data.get("policyPaths", [])
    if not isinstance(paths, list):
        paths = []
    policy_text = str(policy_path)
    merged = list(dict.fromkeys([*(str(item) for item in paths), policy_text]))
    data["policyPaths"] = merged
    return data


def blocked_command_message(reason: str) -> str:
    return f"Agent Factory destructive guard blocked command: {reason}"


def detect_destructive_shell_text(text: str) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    for reason, pattern in _SHELL_BLOCK_PATTERNS:
        if pattern.search(raw):
            return reason
    return None


def detect_destructive_process(program: str, argv: list[str]) -> str | None:
    base = Path(str(program or "").strip()).name.lower()
    args = [str(part or "").strip() for part in argv[1:]]
    args_lower = [part.lower() for part in args]

    if base in {"rm", "rm.exe", "rm.cmd"}:
        return "rm"
    if base in {"del", "del.exe", "del.cmd"}:
        return "del"
    if base in {"erase", "erase.exe", "erase.cmd"}:
        return "erase"
    if base in {"git", "git.exe", "git.cmd"}:
        if args_lower and args_lower[0] == "clean":
            return "git clean"
        if args_lower and args_lower[0] == "reset" and "--hard" in args_lower:
            return "git reset --hard"
        if args_lower and args_lower[0] == "checkout" and "--" in args:
            return "git checkout --"
        if args_lower and args_lower[0] == "restore" and "--worktree" in args_lower:
            return "git restore --worktree"
        if args_lower and args_lower[0] == "restore" and "--staged" in args_lower:
            return "git restore --staged"
        return None
    if base in {"powershell", "powershell.exe", "powershell.cmd", "pwsh", "pwsh.exe", "pwsh.cmd"}:
        return detect_destructive_shell_text(" ".join(args))
    if base in {"cmd", "cmd.exe", "cmd.cmd"}:
        for switch in ("/c", "/k"):
            if switch in args_lower:
                index = args_lower.index(switch)
                return detect_destructive_shell_text(" ".join(args[index + 1 :]))
        return detect_destructive_shell_text(" ".join(args))
    return detect_destructive_shell_text(" ".join([base, *args]))
