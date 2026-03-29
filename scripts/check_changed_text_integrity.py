from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from core.text_integrity import inspect_text_bytes, inspect_text_file, is_supported_text_path


@dataclass(frozen=True)
class IntegrityIssue:
    path: str
    code: str
    message: str


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def resolve_repo_root(repo_root: str | Path | None = None) -> Path:
    if repo_root:
        return Path(repo_root).resolve()

    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode != 0:
        raise RuntimeError("not_a_git_repository")
    return Path(probe.stdout.strip()).resolve()


def list_changed_paths(repo_root: Path, against: str) -> list[Path]:
    result = _run_git(repo_root, ["diff", "--name-only", "--diff-filter=ACMRTUXB", against, "--"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip() or "git_diff_failed")

    paths: list[Path] = []
    for raw_line in result.stdout.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        target = (repo_root / line).resolve()
        if target.exists() and target.is_file() and is_supported_text_path(target):
            paths.append(target)
    return paths


def load_revision_bytes(repo_root: Path, against: str, path: Path) -> bytes | None:
    rel = PurePosixPath(path.resolve().relative_to(repo_root).as_posix())
    result = _run_git(repo_root, ["show", f"{against}:{rel.as_posix()}"])
    if result.returncode != 0:
        return None
    return bytes(result.stdout)


def check_paths_against_revision(repo_root: Path, against: str, paths: list[Path]) -> list[IntegrityIssue]:
    issues: list[IntegrityIssue] = []
    root = repo_root.resolve()

    for path in paths:
        current_path = path.resolve()
        rel = current_path.relative_to(root).as_posix()

        try:
            current = inspect_text_file(current_path)
        except UnicodeDecodeError as exc:
            issues.append(
                IntegrityIssue(
                    path=rel,
                    code="invalid_utf8",
                    message=f"invalid UTF-8 at byte {exc.start}",
                )
            )
            continue

        previous_bytes = load_revision_bytes(root, against, current_path)
        previous = None
        if previous_bytes is not None:
            try:
                previous = inspect_text_bytes(previous_bytes)
            except UnicodeDecodeError:
                previous = None

        if previous is not None:
            if previous.format.has_utf8_bom != current.format.has_utf8_bom:
                issues.append(
                    IntegrityIssue(
                        path=rel,
                        code="bom_changed",
                        message="UTF-8 BOM state changed",
                    )
                )
            allow_windows_script_eol = current_path.suffix.lower() in {".bat", ".cmd", ".ps1"}
            if (
                not allow_windows_script_eol
                and previous.format.newline in {"lf", "crlf"}
                and current.format.newline in {"lf", "crlf"}
                and previous.format.newline != current.format.newline
            ):
                issues.append(
                    IntegrityIssue(
                        path=rel,
                        code="newline_changed",
                        message=f"line endings changed from {previous.format.newline} to {current.format.newline}",
                    )
                )

        new_markers = set(current.suspicious_markers)
        old_markers = set(previous.suspicious_markers) if previous is not None else set()
        for marker in sorted(new_markers - old_markers):
            issues.append(
                IntegrityIssue(
                    path=rel,
                    code="suspicious_text",
                    message=f"new suspicious mojibake marker: {marker}",
                )
            )

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check changed text files for encoding drift and mojibake.")
    parser.add_argument("--repo-root", default="", help="git repository root")
    parser.add_argument("--against", default="HEAD", help="revision used as the baseline")
    parser.add_argument("--path", action="append", default=[], help="explicit path to check; may be repeated")
    parser.add_argument("--changed-only", action="store_true", help="check git-changed files instead of explicit paths")
    args = parser.parse_args(argv)

    repo_root = resolve_repo_root(args.repo_root or None)
    if args.changed_only:
        paths = list_changed_paths(repo_root, args.against)
    else:
        raw_paths = [str(item).strip() for item in args.path if str(item).strip()]
        if not raw_paths:
            parser.error("provide --changed-only or at least one --path")
        paths = [Path(item).resolve() for item in raw_paths if is_supported_text_path(item)]

    issues = check_paths_against_revision(repo_root, args.against, paths)
    if not issues:
        print("OK")
        return 0

    for issue in issues:
        print(f"{issue.path}: {issue.code}: {issue.message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())