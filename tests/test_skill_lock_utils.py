import importlib


def _load_utils(monkeypatch, project_root):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_skill_lock")
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.utils
    return importlib.reload(core.utils)


def test_read_skill_lock_defaults_when_missing(monkeypatch, tmp_path):
    utils = _load_utils(monkeypatch, tmp_path / "proj")
    lock = utils.read_skill_lock()
    assert isinstance(lock, dict)
    assert lock == {"skills": {}}


def test_lock_skill_state_sets_defaults_and_normalizes_id(monkeypatch, tmp_path):
    utils = _load_utils(monkeypatch, tmp_path / "proj")
    utils.lock_skill_state("My Skill", {"status": "canary"})
    lock = utils.read_skill_lock()
    item = lock["skills"]["my_skill"]
    assert item["status"] == "canary"
    assert item["version"] == "1.0.0"
    assert isinstance(item.get("updated_at"), str) and item["updated_at"]


def test_lock_skill_state_merges_patch_without_dropping_existing_fields(monkeypatch, tmp_path):
    utils = _load_utils(monkeypatch, tmp_path / "proj")
    utils.lock_skill_state("issue_tracker", {"status": "active", "version": "2.0.0", "quality_stage": "canary"})
    before = utils.read_skill_lock()["skills"]["issue_tracker"]
    before_updated_at = before.get("updated_at")

    utils.lock_skill_state("issue_tracker", {"status": "disabled"})
    after = utils.read_skill_lock()["skills"]["issue_tracker"]

    assert after["status"] == "disabled"
    assert after["version"] == "2.0.0"
    assert after["quality_stage"] == "canary"
    assert isinstance(after.get("updated_at"), str) and after["updated_at"]
    assert after["updated_at"] >= before_updated_at
