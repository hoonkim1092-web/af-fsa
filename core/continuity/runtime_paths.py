from __future__ import annotations

from pathlib import Path


RUNTIME_DIRNAME = ".af_runtime"


def workspace_runtime_dir(workspace: str | Path) -> Path:
    path = Path(workspace).resolve() / RUNTIME_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_runtime_file(workspace: str | Path, filename: str) -> Path:
    return workspace_runtime_dir(workspace) / filename
