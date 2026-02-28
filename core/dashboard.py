"""
core/dashboard.py
=================
프로젝트 대시보드 JSON 관리 전담 모듈.
core/utils.py 에서 추출.
"""

import os
import json
import copy

from core.config_paths import DASHBOARD_PATH, PROJECT_ID, CONTEXT_SCHEMA_PATH
from core.file_io import read_yaml

# =============================================================================
# Dashboard Cache
# =============================================================================
_DASHBOARD_CACHE: tuple[int, int, dict] | None = None


def append_dashboard_run(entry: dict):
    global _DASHBOARD_CACHE
    path = DASHBOARD_PATH
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
                data = {"project_id": PROJECT_ID, "runs": []}
    else:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"project_id": PROJECT_ID, "runs": []}

    data.setdefault("project_id", PROJECT_ID)
    data.setdefault("runs", [])
    data["runs"].append(entry)
    data["runs"] = data["runs"][-300:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        st2 = os.stat(path)
        _DASHBOARD_CACHE = (int(st2.st_mtime_ns), int(st2.st_size), copy.deepcopy(data))
    except Exception:
        _DASHBOARD_CACHE = None


def _safe_write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def validate_context_with_schema(ctx: dict) -> tuple[bool, str]:
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
