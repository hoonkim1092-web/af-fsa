from pathlib import Path

from scripts.project_context_sync import collect_snapshot


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_collect_snapshot_excludes_docs_task_by_default(tmp_path: Path):
    _write(tmp_path / "docs" / "task.md", "should_be_excluded")
    _write(tmp_path / "docs" / "keep.md", "should_remain")

    payload = collect_snapshot(tmp_path, "agent_factory", None, 10)
    files = payload.get("files", {})

    assert "docs/task.md" not in files
    assert "docs/keep.md" in files


def test_collect_snapshot_applies_env_excludes(tmp_path: Path, monkeypatch):
    _write(tmp_path / "docs" / "keep.md", "exclude_me")
    monkeypatch.setenv("CONTEXT_SYNC_EXCLUDE", "docs/keep.md")

    payload = collect_snapshot(tmp_path, "agent_factory", None, 10)
    files = payload.get("files", {})

    assert "docs/keep.md" not in files


def test_collect_snapshot_applies_env_exclude_glob(tmp_path: Path, monkeypatch):
    _write(tmp_path / "docs" / "tmp.plan.md", "exclude_glob")
    _write(tmp_path / "docs" / "guide.md", "keep")
    monkeypatch.setenv("CONTEXT_SYNC_EXCLUDE", "docs/*.plan.md")

    payload = collect_snapshot(tmp_path, "agent_factory", None, 10)
    files = payload.get("files", {})

    assert "docs/tmp.plan.md" not in files
    assert "docs/guide.md" in files
