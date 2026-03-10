from core.external_skill_sources import (
    CodexOfficialSkillSource,
    ExternalSkillCandidate,
    ExternalSkillResolver,
    RepoCacheSkillSource,
)


def test_external_resolver_prefers_claude_before_codex():
    installs = []

    def _install(need_id, path, source_label="external"):
        installs.append((need_id, path, source_label))
        return True, need_id

    resolver = ExternalSkillResolver(
        project_policies={"external_skill_source_priority": ["claude_repo", "codex_repo", "registry"]},
        install_candidates=[
            {"id": "priority_demo_skill", "path": "claude_skill.py", "source_id": "claude_repo"},
            {"id": "priority_demo_skill", "path": "codex_skill.py", "source_id": "codex_repo"},
        ],
        install_fn=_install,
        path_resolver=lambda path: path,
        match_fn=lambda need, text: 100 if need == text else 0,
    )

    result = resolver.resolve_and_install(
        ["priority_demo_skill"],
        evidence_pack={
            "targets": {
                "priority_demo_skill": {
                    "top_candidate": "priority_demo_skill",
                    "candidates": [{"candidate_skill_id": "priority_demo_skill"}],
                }
            }
        },
    )

    assert installs == [("priority_demo_skill", "claude_skill.py", "claude_repo")]
    assert result["installed"] == {"priority_demo_skill": "priority_demo_skill"}
    assert result["results"]["priority_demo_skill"]["installed_from"] == "claude_repo"


def test_external_resolver_records_miss_before_falling_through():
    resolver = ExternalSkillResolver(
        project_policies={"external_skill_source_priority": ["claude_repo", "codex_repo"]},
        install_candidates=[
            {"id": "fallback_demo_skill", "path": "codex_skill.py", "source_id": "codex_repo"},
        ],
        install_fn=lambda need_id, path, source_label="external": (True, need_id),
        path_resolver=lambda path: path,
        match_fn=lambda need, text: 100 if need == text else 0,
    )

    result = resolver.resolve_and_install(["fallback_demo_skill"])
    attempts = result["results"]["fallback_demo_skill"]["attempts"]

    assert attempts[0]["source_id"] == "claude_repo"
    assert attempts[0]["status"] == "miss"
    assert attempts[1]["source_id"] == "codex_repo"
    assert attempts[1]["status"] == "installed"


def test_external_resolver_treats_prepare_error_as_source_error_then_falls_through():
    class _BrokenSource:
        source_id = "claude_repo"

        def prepare(self):
            raise PermissionError("cache locked")

        def iter_candidates(self):
            raise AssertionError("iter_candidates should not run after prepare failure")

    installs = []

    resolver = ExternalSkillResolver(
        project_policies={"external_skill_source_priority": ["claude_repo", "codex_repo"]},
        install_candidates=[],
        install_fn=lambda need_id, path, source_label="external": (
            installs.append((need_id, path, source_label)) or (True, need_id)
        ),
        path_resolver=lambda path: path,
        match_fn=lambda need, text: 100 if need == text else 0,
    )
    resolver._build_sources = lambda: [
        _BrokenSource(),
        type(
            "_ManifestSource",
            (),
            {
                "source_id": "codex_repo",
                "prepare": lambda self: None,
                "iter_candidates": lambda self: [
                    ExternalSkillCandidate(source_id="codex_repo", skill_id="error_demo_skill", path="codex_skill.py")
                ],
            },
        )(),
    ]

    result = resolver.resolve_and_install(["error_demo_skill"])
    attempts = result["results"]["error_demo_skill"]["attempts"]

    assert installs == [("error_demo_skill", "codex_skill.py", "codex_repo")]
    assert attempts[0]["source_id"] == "claude_repo"
    assert attempts[0]["status"] == "source_error"
    assert "PermissionError" in attempts[0]["reason"]
    assert attempts[1]["source_id"] == "codex_repo"
    assert attempts[1]["status"] == "installed"


def test_repo_cache_source_reads_imported_candidates(monkeypatch):
    calls = []

    def _fake_discover(root_dir, cache_dir, source_ids=None, scan_python=False):
        calls.append(
            {
                "root_dir": root_dir,
                "cache_dir": cache_dir,
                "source_ids": source_ids,
                "scan_python": scan_python,
            }
        )
        return (
            {
                "claude_repo:manifest_demo": {
                    "id": "manifest_demo",
                    "path": "skills/_external_cache/claude/repo/manifest_demo.py",
                    "source_id": "claude_repo",
                    "source_repo": "repo",
                    "source_url": "https://example.com/repo.git",
                    "capabilities": ["demo"],
                }
            },
            [],
        )

    monkeypatch.setattr("core.external_skill_sources.discover_external_candidates", _fake_discover)

    source = RepoCacheSkillSource("claude_repo", [])
    candidates = source.iter_candidates()

    assert len(candidates) == 1
    assert candidates[0].skill_id == "manifest_demo"
    assert candidates[0].source_repo == "repo"
    assert calls[0]["source_ids"] == ["claude_repo"]
    assert calls[0]["scan_python"] is False


def test_repo_cache_source_falls_back_to_python_scan_when_manifest_missing(monkeypatch):
    calls = []

    def _fake_discover(root_dir, cache_dir, source_ids=None, scan_python=False):
        calls.append(scan_python)
        if not scan_python:
            return ({}, [])
        return (
            {
                "claude_repo:raw_demo": {
                    "id": "raw_demo",
                    "path": "skills/_external_cache/claude/repo/raw_demo.py",
                    "source_id": "claude_repo",
                    "source_repo": "repo",
                    "capabilities": ["raw_demo"],
                }
            },
            [],
        )

    monkeypatch.setattr("core.external_skill_sources.discover_external_candidates", _fake_discover)

    source = RepoCacheSkillSource("claude_repo", [])
    candidates = source.iter_candidates()

    assert len(candidates) == 1
    assert candidates[0].skill_id == "raw_demo"
    assert calls == [False, True]


def test_external_resolver_surfaces_repo_sync_failure_as_source_error(monkeypatch, tmp_path):
    class _Completed:
        returncode = 1
        stderr = "auth failed"
        stdout = ""

    monkeypatch.setattr("core.external_skill_sources.subprocess.run", lambda *args, **kwargs: _Completed())
    monkeypatch.setattr(
        "core.external_skill_sources.discover_external_candidates",
        lambda root_dir, cache_dir, source_ids=None, scan_python=False: ({}, []),
    )

    source = RepoCacheSkillSource("claude_repo", ["https://example.com/repo.git"])
    source.root_dir = str(tmp_path / "claude_repo")

    resolver = ExternalSkillResolver(
        project_policies={"external_skill_source_priority": ["claude_repo"]},
        install_candidates=[],
        install_fn=lambda need_id, path, source_label="external": (True, need_id),
        path_resolver=lambda path: path,
        match_fn=lambda need, text: 100 if need == text else 0,
    )
    resolver._build_sources = lambda: [source]

    result = resolver.resolve_and_install(["repo_demo_skill"])
    attempts = result["results"]["repo_demo_skill"]["attempts"]

    assert attempts[0]["source_id"] == "claude_repo"
    assert attempts[0]["status"] == "source_error"
    assert "auth failed" in attempts[0]["reason"]


def test_official_codex_source_reads_skill_directories(tmp_path):
    skills_root = tmp_path / ".agents" / "skills"
    skill_dir = skills_root / "review_guide"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Review Guide\ndescription: Review workflow\n---\n\n# Steps\n",
        encoding="utf-8",
    )

    source = CodexOfficialSkillSource([str(skills_root)])
    candidates = source.iter_candidates()

    assert len(candidates) == 1
    assert candidates[0].source_id == "codex_official"
    assert candidates[0].skill_id == "review_guide"
    assert candidates[0].path == str(skill_dir)


def test_external_resolver_prefers_official_codex_before_repo_cache(tmp_path):
    skills_root = tmp_path / ".agents" / "skills"
    skill_dir = skills_root / "review_guide"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Review Guide\ndescription: Review workflow\n---\n\n# Steps\n",
        encoding="utf-8",
    )

    installs = []

    resolver = ExternalSkillResolver(
        project_policies={
            "official_codex_skill_roots": [str(skills_root)],
            "external_skill_source_priority": ["codex_official", "codex_repo"],
        },
        install_candidates=[
            {"id": "review_guide", "path": "codex_repo/review_guide", "source_id": "codex_repo"},
        ],
        install_fn=lambda need_id, path, source_label="external": (
            installs.append((need_id, path, source_label)) or (True, need_id)
        ),
        path_resolver=lambda path: path,
        match_fn=lambda need, text: 100 if need == text else 0,
    )

    result = resolver.resolve_and_install(["review_guide"])

    assert installs == [("review_guide", str(skill_dir), "codex_official")]
    assert result["results"]["review_guide"]["installed_from"] == "codex_official"
