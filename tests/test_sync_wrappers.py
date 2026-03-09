import os
import subprocess
from pathlib import Path


def _copy_wrapper_tree(repo_root: Path, sandbox_root: Path) -> None:
    for rel in ("start_db.cmd", "start_sync.cmd", "sync.cmd"):
        source = repo_root / rel
        target = sandbox_root / rel
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    scripts_dir = sandbox_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "sync_easy.ps1").write_text("# test placeholder\n", encoding="utf-8")


def test_start_db_passes_repo_alias_to_powershell(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    sandbox_root = tmp_path / "wrapper"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    _copy_wrapper_tree(repo_root, sandbox_root)

    args_file = sandbox_root / "powershell_args.txt"
    fake_powershell = sandbox_root / "powershell.cmd"
    fake_powershell.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "> \"%SYNC_WRAPPER_ARGS_FILE%\" echo %*\r\n"
        "exit /b 0\r\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = str(sandbox_root) + os.pathsep + env.get("PATH", "")
    env["SYNC_WRAPPER_ARGS_FILE"] = str(args_file)

    result = subprocess.run(
        ["cmd", "/c", "start_db.cmd", "@repo"],
        cwd=sandbox_root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    assert result.returncode == 0
    captured = args_file.read_text(encoding="utf-8").strip()
    assert '-Action "down"' in captured or "-Action down" in captured
    assert '-Backend "db"' in captured or "-Backend db" in captured
    assert '-Target "@repo"' in captured or "-Target @repo" in captured
    assert "-Target all" not in captured


def test_powershell_start_db_preserves_repo_alias_via_repo_keyword(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    sandbox_root = tmp_path / "wrapper_ps"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    _copy_wrapper_tree(repo_root, sandbox_root)

    args_file = sandbox_root / "powershell_args.txt"
    fake_powershell = sandbox_root / "powershell.cmd"
    fake_powershell.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "> \"%SYNC_WRAPPER_ARGS_FILE%\" echo %*\r\n"
        "exit /b 0\r\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = str(sandbox_root) + os.pathsep + env.get("PATH", "")
    env["SYNC_WRAPPER_ARGS_FILE"] = str(args_file)

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ".\\start_db.cmd repo"],
        cwd=sandbox_root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    assert result.returncode == 0
    captured = args_file.read_text(encoding="utf-8").strip()
    assert '-Action "down"' in captured or "-Action down" in captured
    assert '-Backend "db"' in captured or "-Backend db" in captured
    assert '-Target "repo"' in captured or "-Target repo" in captured
    assert "-Target all" not in captured
