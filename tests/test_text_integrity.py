from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.text_integrity import (
    TextFileFormat,
    detect_newline_style,
    find_suspicious_markers,
    inspect_text_file,
    write_text_preserving_format,
)
from scripts.check_changed_text_integrity import check_paths_against_revision


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_detect_newline_style():
    assert detect_newline_style(b"a\nb\n") == "lf"
    assert detect_newline_style(b"a\r\nb\r\n") == "crlf"
    assert detect_newline_style(b"a\r\nb\n") == "mixed"
    assert detect_newline_style(b"abc") == "none"


def test_write_text_preserving_format_keeps_bom_and_crlf(tmp_path: Path):
    target = tmp_path / "sample.md"
    target.write_bytes(b"\xef\xbb\xbfline1\r\nline2\r\n")

    file_format = write_text_preserving_format(target, "line1\nline2\nline3\n")
    snapshot = inspect_text_file(target)

    assert file_format == TextFileFormat(has_utf8_bom=True, newline="crlf")
    assert snapshot.format == TextFileFormat(has_utf8_bom=True, newline="crlf")
    assert snapshot.text == "line1\r\nline2\r\nline3\r\n"
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")


def test_find_suspicious_markers_detects_common_mojibake():
    assert "question_mark_before_hangul" in find_suspicious_markers("?\ud55c\uae00")
    assert "replacement_character" in find_suspicious_markers("bad\ufffdtext")
    assert "cp1252_utf8_mojibake" in find_suspicious_markers("\u00C3\u00A9")


def test_check_paths_against_revision_flags_new_mojibake(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    assert _git(repo, "init").returncode == 0
    assert _git(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _git(repo, "config", "user.name", "Test User").returncode == 0

    target = repo / "sample.py"
    target.write_text('message = "\ud55c\uae00\\n"\n', encoding="utf-8", newline="\n")
    assert _git(repo, "add", "sample.py").returncode == 0
    assert _git(repo, "commit", "-m", "init").returncode == 0

    target.write_text('message = "?\ud55c\uae00\\n"\n', encoding="utf-8", newline="\n")

    issues = check_paths_against_revision(repo, "HEAD", [target])

    assert any(issue.code == "suspicious_text" for issue in issues)


def test_check_script_returns_nonzero_for_new_mojibake(tmp_path: Path):
    repo = tmp_path / "repo_script"
    repo.mkdir(parents=True, exist_ok=True)

    assert _git(repo, "init").returncode == 0
    assert _git(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _git(repo, "config", "user.name", "Test User").returncode == 0

    target = repo / "note.md"
    target.write_text("# ok\n", encoding="utf-8", newline="\n")
    assert _git(repo, "add", "note.md").returncode == 0
    assert _git(repo, "commit", "-m", "init").returncode == 0

    target.write_text("# ?\ud55c\uae00\n", encoding="utf-8", newline="\n")

    script = Path(__file__).resolve().parents[1] / "scripts" / "check_changed_text_integrity.py"
    result = subprocess.run(
        [sys.executable, str(script), "--repo-root", str(repo), "--against", "HEAD", "--changed-only"],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 2
    assert "suspicious_text" in result.stderr