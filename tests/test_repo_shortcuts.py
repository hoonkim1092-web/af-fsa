from pathlib import Path

from repo_shortcuts import resolve_repo_path


def test_resolve_repo_path_prefers_sibling_repo(tmp_path):
    parent = tmp_path / "workspace"
    current_repo = parent / "agent-factory"
    target_repo = parent / "logi-mind-v22"
    (current_repo / ".git").mkdir(parents=True)
    (target_repo / ".git").mkdir(parents=True)

    found = resolve_repo_path("logi-mind-v22", start_path=current_repo)

    assert found == target_repo


def test_resolve_repo_path_falls_back_to_env_root(tmp_path, monkeypatch):
    project_root = tmp_path / "projects-root"
    target_repo = project_root / "logi-mind-v22"
    (target_repo / ".git").mkdir(parents=True)

    start = tmp_path / "other" / "agent-factory"
    (start / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOON_PROJECTS_HOME", str(project_root))

    found = resolve_repo_path("logi-mind-v22", start_path=start)

    assert found == target_repo
