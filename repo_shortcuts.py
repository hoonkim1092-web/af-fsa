import os
import subprocess
from pathlib import Path
from typing import Optional


SHORTCUT_TARGETS = {
    "lm": "logi-mind-v22",
    "af": "agent-factory",
}


def _nearest_git_root(start_path: Path) -> Optional[Path]:
    try:
        cursor = start_path.resolve()
    except OSError:
        return None

    while True:
        if (cursor / ".git").exists():
            return cursor
        if cursor.parent == cursor:
            return None
        cursor = cursor.parent


def resolve_repo_path(repo_name: str, start_path: Path) -> Optional[Path]:
    roots = []

    env_hoon = os.environ.get("HOON_PROJECTS_HOME")
    env_projects = os.environ.get("PROJECTS_HOME")
    if env_hoon:
        roots.append(Path(env_hoon))
    if env_projects:
        roots.append(Path(env_projects))

    git_root = _nearest_git_root(start_path)
    if git_root:
        roots.append(git_root)
        roots.append(git_root.parent)

    cursor = start_path
    while True:
        roots.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent

    roots.extend(
        [
            Path.home() / "hoonProJect",
            Path.home() / "projects",
            Path(r"D:\hoonProJect"),
            Path(r"C:\hoonProJect"),
        ]
    )

    unique_roots = []
    seen = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(root)

    for root in unique_roots:
        candidates = [
            root / repo_name,
            root / "hoonProJect" / repo_name,
            root / "projects" / repo_name,
        ]
        for candidate in candidates:
            if (candidate / ".git").exists():
                return candidate
    return None


def handle_repo_shortcut(user_input: str) -> bool:
    text = user_input.strip()
    if not text:
        return False

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    if cmd not in SHORTCUT_TARGETS:
        return False

    repo_name = SHORTCUT_TARGETS[cmd]
    target = resolve_repo_path(repo_name, Path.cwd())
    if not target:
        print(f"[{cmd}] Could not find '{repo_name}'. Set HOON_PROJECTS_HOME or move under a matching parent.")
        return True

    if len(parts) == 1:
        os.chdir(target)
        print(target)
        return True

    result = subprocess.run(parts[1], cwd=str(target), shell=True)
    if result.returncode != 0:
        print(f"[{cmd}] Command failed (exit {result.returncode})")
    return True
