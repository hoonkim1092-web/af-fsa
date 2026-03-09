import importlib
import os
import time


def _load_utils(monkeypatch, project_root):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "utils_cache")
    import core.config_paths
    importlib.reload(core.config_paths)
    import core.file_io
    importlib.reload(core.file_io)
    import core.dashboard
    importlib.reload(core.dashboard)
    import core.utils
    return importlib.reload(core.utils)


def test_read_yaml_cache_returns_copy_and_refreshes(monkeypatch, tmp_path):
    u = _load_utils(monkeypatch, tmp_path / "proj")
    path = tmp_path / "proj" / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a: 1\n", encoding="utf-8")

    first = u.read_yaml(str(path))
    first["a"] = 999
    second = u.read_yaml(str(path))
    assert second["a"] == 1

    time.sleep(1.1)
    path.write_text("a: 2\n", encoding="utf-8")
    os.utime(path, None)
    third = u.read_yaml(str(path))
    assert third["a"] == 2


def test_append_dashboard_run_keeps_recent_300(monkeypatch, tmp_path):
    u = _load_utils(monkeypatch, tmp_path / "proj2")
    assert os.path.basename(os.path.dirname(u.DASHBOARD_PATH)) == ".af_runtime"
    for i in range(305):
        u.append_dashboard_run({"idx": i})

    with open(u.DASHBOARD_PATH, "r", encoding="utf-8") as f:
        data = u.json.load(f)
    runs = data.get("runs", [])
    assert len(runs) == 300
    assert runs[0]["idx"] == 5
    assert runs[-1]["idx"] == 304


def test_append_dashboard_run_normalizes_path_fields(monkeypatch, tmp_path):
    u = _load_utils(monkeypatch, tmp_path / "proj_paths")
    state_path = os.path.join(u.PROJECT_ROOT, "runs", "wf_123", "state.json")
    workflow_path = os.path.join(u.BASE_DIR, "tests", "_tmp", "wf.yaml")

    u.append_dashboard_run(
        {
            "state_path": state_path,
            "workflow_path": workflow_path,
            "nested": {"artifact_path": os.path.join(u.PROJECT_ROOT, "artifacts", "out.txt")},
        }
    )

    with open(u.DASHBOARD_PATH, "r", encoding="utf-8") as f:
        data = u.json.load(f)
    row = data["runs"][-1]

    assert row["state_path"] == os.path.relpath(state_path, u.BASE_DIR).replace("\\", "/")
    assert row["workflow_path"] == os.path.relpath(workflow_path, u.BASE_DIR).replace("\\", "/")
    assert row["nested"]["artifact_path"] == os.path.relpath(
        os.path.join(u.PROJECT_ROOT, "artifacts", "out.txt"),
        u.BASE_DIR,
    ).replace("\\", "/")


def test_append_dashboard_run_uses_current_config_after_config_reload(monkeypatch, tmp_path):
    first_root = tmp_path / "proj_a"
    second_root = tmp_path / "proj_b"
    u = _load_utils(monkeypatch, first_root)
    dashboard_module = importlib.import_module("core.dashboard")

    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(second_root))
    monkeypatch.setenv("AGENT_PROJECT_ID", "utils_cache")
    import core.config_paths

    importlib.reload(core.config_paths)

    dashboard_module.append_dashboard_run({"idx": 1})

    expected_path = second_root / ".af_runtime" / "dashboard.json"
    assert expected_path.exists()
    assert not (first_root / ".af_runtime" / "dashboard.json").exists()


def test_read_yaml_cache_hash_verify_detects_same_stat_change(monkeypatch, tmp_path):
    monkeypatch.setenv("YAML_CACHE_VERIFY_HASH", "1")
    u = _load_utils(monkeypatch, tmp_path / "proj3")
    path = tmp_path / "proj3" / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a: 1\n", encoding="utf-8")

    _ = u.read_yaml(str(path))
    st = path.stat()
    original_ns = int(st.st_mtime_ns)

    # Keep file size and mtime the same while changing content.
    path.write_text("a: 9\n", encoding="utf-8")
    os.utime(path, ns=(original_ns, original_ns))

    refreshed = u.read_yaml(str(path))
    assert refreshed["a"] == 9


def test_read_yaml_cache_eviction_lru(monkeypatch, tmp_path):
    monkeypatch.setenv("YAML_CACHE_MAX_ENTRIES", "2")
    u = _load_utils(monkeypatch, tmp_path / "proj4")
    import core.file_io
    p1 = tmp_path / "proj4" / "a.yaml"
    p2 = tmp_path / "proj4" / "b.yaml"
    p3 = tmp_path / "proj4" / "c.yaml"
    for i, p in enumerate([p1, p2, p3], start=1):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"v: {i}\n", encoding="utf-8")
        _ = u.read_yaml(str(p))

    assert len(core.file_io._YAML_CACHE) == 2
    assert str(p1) not in core.file_io._YAML_CACHE
