import os

import yaml

from core import external_skill_candidate_importer as importer_mod
from core.external_skill_candidate_importer import (
    discover_external_candidates,
    import_external_candidates,
    merge_install_candidates,
)


def test_import_external_candidates_from_manifest(tmp_path):
    root = tmp_path
    registry_path = root / "skills" / "registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("skills: {}\ninstall_candidates: {}\n", encoding="utf-8")

    repo_root = root / "skills" / "_external_cache" / "claude" / "repo_alpha"
    skill_dir = repo_root / "skills" / "issue_tracker"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.py").write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    (repo_root / "skill_candidates.yaml").write_text(
        "install_candidates:\n"
        "  issue_tracker:\n"
        "    path: skills/issue_tracker/skill.py\n"
        "    name: Issue Tracker\n"
        "    capabilities: [issue_tracker, issue_read]\n",
        encoding="utf-8",
    )

    result = import_external_candidates(
        root_dir=str(root),
        registry_path=str(registry_path),
        cache_dir=str(root / "skills" / "_external_cache"),
    )

    assert result["candidate_count"] == 1
    content = registry_path.read_text(encoding="utf-8")
    assert "claude_repo_issue_tracker" in content
    assert "source_id: claude_repo" in content
    assert "skills/_external_cache/claude/repo_alpha/skills/issue_tracker/skill.py" in content


def test_import_external_candidates_can_fallback_to_python_scan(tmp_path):
    root = tmp_path
    registry_path = root / "skills" / "registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("skills: {}\ninstall_candidates: {}\n", encoding="utf-8")

    repo_root = root / "skills" / "_external_cache" / "codex" / "repo_beta"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "debug_helper.py").write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")

    result = import_external_candidates(
        root_dir=str(root),
        registry_path=str(registry_path),
        cache_dir=str(root / "skills" / "_external_cache"),
        source_ids=["codex_repo"],
        scan_python=True,
    )

    assert result["candidate_count"] == 1
    content = registry_path.read_text(encoding="utf-8")
    assert "codex_repo_debug_helper" in content
    assert "source_id: codex_repo" in content


def test_import_external_candidates_can_fallback_to_codex_markdown_scan(tmp_path):
    root = tmp_path
    registry_path = root / "skills" / "registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("skills: {}\ninstall_candidates: {}\n", encoding="utf-8")

    repo_root = root / "skills" / "_external_cache" / "codex" / "repo_gamma"
    skill_dir = repo_root / "review_guide"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Review Guide\ndescription: Review workflow\n---\n\n# Steps\n",
        encoding="utf-8",
    )

    result = import_external_candidates(
        root_dir=str(root),
        registry_path=str(registry_path),
        cache_dir=str(root / "skills" / "_external_cache"),
        source_ids=["codex_repo"],
        scan_python=True,
    )

    assert result["candidate_count"] == 1
    content = registry_path.read_text(encoding="utf-8")
    assert "codex_repo_review_guide" in content
    assert "source_id: codex_repo" in content
    assert "skills/_external_cache/codex/repo_gamma/review_guide" in content


def test_import_external_candidates_blocks_duplicate_skill_ids_within_same_source(tmp_path):
    root = tmp_path
    registry_path = root / "skills" / "registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("skills: {}\ninstall_candidates: {}\n", encoding="utf-8")

    repo_a = root / "skills" / "_external_cache" / "claude" / "repo_alpha"
    repo_b = root / "skills" / "_external_cache" / "claude" / "repo_beta"

    skill_a = repo_a / "skills" / "issue_tracker"
    skill_b = repo_b / "skills" / "issue_tracker"
    skill_a.mkdir(parents=True, exist_ok=True)
    skill_b.mkdir(parents=True, exist_ok=True)
    (skill_a / "skill.py").write_text("def apply(ctx):\n    return {'repo': 'a'}\n", encoding="utf-8")
    (skill_b / "skill.py").write_text("def apply(ctx):\n    return {'repo': 'b'}\n", encoding="utf-8")

    manifest = (
        "install_candidates:\n"
        "  issue_tracker:\n"
        "    path: skills/issue_tracker/skill.py\n"
        "    name: Issue Tracker\n"
        "    capabilities: [issue_tracker]\n"
    )
    (repo_a / "skill_candidates.yaml").write_text(manifest, encoding="utf-8")
    (repo_b / "skill_candidates.yaml").write_text(manifest, encoding="utf-8")

    result = import_external_candidates(
        root_dir=str(root),
        registry_path=str(registry_path),
        cache_dir=str(root / "skills" / "_external_cache"),
        source_ids=["claude_repo"],
    )

    assert result["candidate_count"] == 1
    assert result["duplicate_count"] == 1
    assert result["duplicates"][0]["candidate_key"] == "claude_repo_issue_tracker"
    assert result["duplicates"][0]["kept_repo"] == "repo_alpha"
    assert result["duplicates"][0]["skipped_repo"] == "repo_beta"

    content = registry_path.read_text(encoding="utf-8")
    assert "claude_repo_issue_tracker" in content
    assert "source_repo: repo_alpha" in content
    assert "repo_beta" not in content


def test_import_external_candidates_replaces_legacy_source_keys(tmp_path):
    root = tmp_path
    registry_path = root / "skills" / "registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        "skills: {}\n"
        "install_candidates:\n"
        "  claude_issue_tracker:\n"
        "    id: issue_tracker\n"
        "    name: Issue Tracker\n"
        "    path: skills/_external_cache/claude/legacy_repo/skills/issue_tracker/skill.py\n"
        "    source_id: claude\n",
        encoding="utf-8",
    )

    repo_root = root / "skills" / "_external_cache" / "claude" / "repo_alpha"
    skill_dir = repo_root / "skills" / "issue_tracker"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.py").write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    (repo_root / "skill_candidates.yaml").write_text(
        "install_candidates:\n"
        "  issue_tracker:\n"
        "    path: skills/issue_tracker/skill.py\n"
        "    name: Issue Tracker\n",
        encoding="utf-8",
    )

    result = import_external_candidates(
        root_dir=str(root),
        registry_path=str(registry_path),
        cache_dir=str(root / "skills" / "_external_cache"),
        source_ids=["claude_repo"],
    )

    assert result["candidate_count"] == 1
    content = registry_path.read_text(encoding="utf-8")
    assert "claude_repo_issue_tracker" in content
    assert "claude_issue_tracker" not in content


def test_merge_install_candidates_preserves_legacy_list_form_entries(tmp_path):
    registry_path = tmp_path / "skills" / "registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        yaml.safe_dump(
            {
                "skills": {},
                "install_candidates": [
                    {
                        "id": "Issue Tracker",
                        "path": "skills/_external_cache/claude/repo_alpha/skills/issue_tracker/skill.py",
                        "source": "Claude",
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    merged = merge_install_candidates(
        registry_path=str(registry_path),
        candidates={},
        check_only=False,
    )

    install_candidates = merged["install_candidates"]
    assert "claude_repo_issue_tracker" in install_candidates
    assert install_candidates["claude_repo_issue_tracker"]["source_id"] == "claude_repo"


def test_discover_external_candidates_sweeps_custom_sources_when_unspecified(tmp_path):
    root = tmp_path
    cache_dir = root / "skills" / "_external_cache"
    repo_root = cache_dir / "partner_repo" / "repo_delta"
    skill_dir = repo_root / "skills" / "triage_helper"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.py").write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    (repo_root / "skill_candidates.yaml").write_text(
        "install_candidates:\n"
        "  triage_helper:\n"
        "    path: skills/triage_helper/skill.py\n",
        encoding="utf-8",
    )

    discovered, duplicates = discover_external_candidates(
        root_dir=str(root),
        cache_dir=str(cache_dir),
        source_ids=None,
    )

    assert duplicates == []
    assert "partner_repo_triage_helper" in discovered
    assert discovered["partner_repo_triage_helper"]["source_id"] == "partner_repo"


def test_discover_external_candidates_prefers_manifest_before_python_fallback(tmp_path):
    root = tmp_path
    cache_dir = root / "skills" / "_external_cache"
    repo_root = cache_dir / "claude" / "repo_alpha"
    skill_dir = repo_root / "skills" / "issue_tracker"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.py").write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    (repo_root / "debug_helper.py").write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    (repo_root / "skill_candidates.yaml").write_text(
        "install_candidates:\n"
        "  issue_tracker:\n"
        "    path: skills/issue_tracker/skill.py\n",
        encoding="utf-8",
    )

    discovered, _duplicates = discover_external_candidates(
        root_dir=str(root),
        cache_dir=str(cache_dir),
        source_ids=["claude_repo"],
        scan_python=True,
    )

    assert "claude_repo_issue_tracker" in discovered
    assert "claude_repo_debug_helper" not in discovered


def test_main_uses_default_paths_for_check_only(monkeypatch, tmp_path):
    captured = {}

    def _fake_import_external_candidates(**kwargs):
        captured.update(kwargs)
        return {
            "candidate_count": 0,
            "duplicate_count": 0,
            "install_candidate_count": 0,
        }

    monkeypatch.setattr(importer_mod, "import_external_candidates", _fake_import_external_candidates)
    monkeypatch.setattr(importer_mod, "__file__", str(tmp_path / "core" / "external_skill_candidate_importer.py"))

    rc = importer_mod.main(["--check"])

    assert rc == 0
    assert captured["root_dir"] == os.path.abspath(str(tmp_path))
    assert captured["registry_path"] == os.path.join(os.path.abspath(str(tmp_path)), "skills", "registry.yaml")
    assert captured["cache_dir"] == os.path.join(os.path.abspath(str(tmp_path)), "skills", "_external_cache")
    assert captured["check_only"] is True



def test_main_accepts_legacy_cli_aliases(monkeypatch, tmp_path):
    captured = {}
    custom_registry = tmp_path / "custom_registry.yaml"
    custom_cache = tmp_path / "custom_cache"

    def _fake_import_external_candidates(**kwargs):
        captured.update(kwargs)
        return {
            "candidate_count": 0,
            "duplicate_count": 0,
            "install_candidate_count": 0,
        }

    monkeypatch.setattr(importer_mod, "import_external_candidates", _fake_import_external_candidates)

    rc = importer_mod.main(
        [
            "--root", str(tmp_path),
            "--registry", str(custom_registry),
            "--cache-dir", str(custom_cache),
            "--source", "Claude",
            "--check",
        ]
    )

    assert rc == 0
    assert captured["root_dir"] == os.path.abspath(str(tmp_path))
    assert captured["registry_path"] == os.path.abspath(str(custom_registry))
    assert captured["cache_dir"] == os.path.abspath(str(custom_cache))
    assert captured["source_ids"] == ["Claude"]
    assert captured["check_only"] is True
