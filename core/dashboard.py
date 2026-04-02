"""
core/dashboard.py
=================
프로젝트 대시보드 JSON 관리 전담 모듈.
core/utils.py 에서 추출.
"""

import os
import json
import copy
import threading

from core.file_io import read_yaml

# =============================================================================
# Dashboard Cache
# =============================================================================
_DASHBOARD_CACHE: tuple[int, int, dict] | None = None
_DASHBOARD_LOCK = threading.Lock()


def _current_dashboard_config() -> tuple[str, str, str]:
    from core import config_paths as cfg

    return cfg.BASE_DIR, cfg.DASHBOARD_PATH, cfg.PROJECT_ID


def _normalize_dashboard_path(path_text: str, base_dir: str) -> str:
    p = str(path_text or "").strip()
    if not p:
        return p
    if not os.path.isabs(p):
        return p.replace("\\", "/")
    try:
        rel = os.path.relpath(os.path.abspath(p), base_dir)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except Exception:
        pass
    return p.replace("\\", "/")


def _normalize_dashboard_entry(value, base_dir: str):
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if isinstance(key, str) and key.endswith("_path") and isinstance(item, str):
                normalized[key] = _normalize_dashboard_path(item, base_dir)
            else:
                normalized[key] = _normalize_dashboard_entry(item, base_dir)
        return normalized
    if isinstance(value, list):
        return [_normalize_dashboard_entry(item, base_dir) for item in value]
    return value


def append_dashboard_run(entry: dict):
    global _DASHBOARD_CACHE
    with _DASHBOARD_LOCK:
        _append_dashboard_run_locked(entry)


def _append_dashboard_run_locked(entry: dict):
    global _DASHBOARD_CACHE
    base_dir, path, project_id = _current_dashboard_config()
    try:
        st = os.stat(path)
        stat_sig = (int(st.st_mtime_ns), int(st.st_size))
    except Exception:
        stat_sig = None

    if _DASHBOARD_CACHE is not None and stat_sig is not None:
        c_mtime_ns, c_size, c_data = _DASHBOARD_CACHE
        if (c_mtime_ns, c_size) == stat_sig:
            data = copy.deepcopy(c_data)
        else:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {"project_id": project_id, "runs": []}
    else:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"project_id": project_id, "runs": []}

    data.setdefault("project_id", project_id)
    data.setdefault("runs", [])
    data["runs"] = [_normalize_dashboard_entry(copy.deepcopy(item), base_dir) for item in data["runs"]]
    data["runs"].append(_normalize_dashboard_entry(copy.deepcopy(entry), base_dir))
    data["runs"] = data["runs"][-300:]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    try:
        st2 = os.stat(path)
        _DASHBOARD_CACHE = (int(st2.st_mtime_ns), int(st2.st_size), copy.deepcopy(data))
    except Exception:
        _DASHBOARD_CACHE = None


def _safe_write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def validate_context_with_schema(ctx: dict) -> tuple[bool, str]:
    from core.config_paths import CONTEXT_SCHEMA_PATH

    schema = read_yaml(CONTEXT_SCHEMA_PATH)
    reqs = schema.get("required_keys", []) if isinstance(schema, dict) else []
    types = schema.get("types", {}) if isinstance(schema, dict) else {}
    for key in reqs:
        if key not in ctx:
            return False, f"missing context key: {key}"
    type_map = {"str": str, "dict": dict, "list": list, "int": int, "bool": bool}
    for key, tname in (types or {}).items():
        if key not in ctx:
            continue
        py_t = type_map.get(str(tname).strip().lower())
        if py_t and not isinstance(ctx[key], py_t):
            return False, f"context type mismatch: {key} expected {tname}"
    return True, "ok"
