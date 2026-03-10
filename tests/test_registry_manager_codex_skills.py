import importlib


def _load_registry_manager(monkeypatch, project_root):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "codex_registry_test")
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    importlib.reload(core.utils)
    import core.registry_manager
    return importlib.reload(core.registry_manager)


def test_registry_manager_installs_codex_markdown_skill_directory(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    mod = _load_registry_manager(monkeypatch, project_root)

    skills_dir = tmp_path / "factory_skills"
    registry_path = skills_dir / "registry.yaml"
    monkeypatch.setattr(mod, "SKILLS_DIR", str(skills_dir))
    monkeypatch.setattr(mod, "REGISTRY_PATH", str(registry_path))

    src_dir = tmp_path / "source" / "review_guide"
    (src_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (src_dir / "SKILL.md").write_text(
        "---\nname: Review Guide\ndescription: Review workflow\n---\n\n# Steps\n",
        encoding="utf-8",
    )
    (src_dir / "scripts" / "helper.py").write_text("print('ok')\n", encoding="utf-8")

    mgr = mod.RegistryManager()
    ok, sid = mgr._install_skill_file("review_guide", str(src_dir), source_label="codex_official")

    installed_dir = skills_dir / "review_guide"
    assert ok is True
    assert sid == "review_guide"
    assert (installed_dir / "skill.md").exists()
    assert (installed_dir / "scripts" / "helper.py").exists()

    registry = mgr._read_registry()
    entry = registry["skills"]["review_guide"]
    assert entry["type"] == "knowledge"
    assert entry["path"].endswith("factory_skills/review_guide/skill.md")


def test_registry_manager_init_falls_back_to_read_only_on_permission_error(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    mod = _load_registry_manager(monkeypatch, project_root)

    def _boom():
        raise PermissionError("denied")

    monkeypatch.setattr(mod, "ensure_registry_files", _boom)

    mgr = mod.RegistryManager()

    assert mgr._read_only is True
