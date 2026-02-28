"""
core/memory.py
==============
에이전트 메모리 읽기/쓰기 전담 모듈.
core/utils.py 에서 추출.
"""

import os
import json
from datetime import datetime


def _to_epoch(ts: object, fallback: float = 0.0) -> float:
    raw = str(ts or "").strip()
    if not raw:
        return fallback
    try:
        raw = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return fallback


def _iter_recent_json_files(root: str, max_files: int) -> list[str]:
    if not root or not os.path.isdir(root):
        return []
    rows: list[tuple[float, str]] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not str(name).lower().endswith(".json"):
                continue
            p = os.path.join(dirpath, name)
            try:
                mt = float(os.path.getmtime(p))
            except Exception:
                mt = 0.0
            rows.append((mt, p))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [p for _mt, p in rows[: max(1, int(max_files or 300))]]


def _memory_value_to_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _load_core_json(path: str) -> tuple[dict, float]:
    if not os.path.exists(path):
        return {}, 0.0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}, 0.0
    except Exception:
        return {}, 0.0
    try:
        mt = float(os.path.getmtime(path))
    except Exception:
        mt = 0.0
    return data, mt


def read_core_memory(agent_id: str | None = None, max_items: int = 10, max_files_per_root: int = 300) -> dict:
    """
    Reads structured memory records with Local -> Global priority.
    Returns a compact dict suitable for prompt briefing.
    """
    from core.config_paths import DATA_DIR, GLOBAL_MEMORY_DIR

    def _safe_id(text: str) -> str:
        import re
        t = (text or "").strip().lower()
        t = re.sub(r"[^a-z0-9_]+", "_", t)
        t = re.sub(r"_+", "_", t).strip("_")
        return t[:60] if t else "skill"

    aid = _safe_id(agent_id) if agent_id else "general"
    selected: dict[str, tuple[str, str, float, str]] = {}

    def absorb_record(scope: str, file_path: str):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
        except Exception:
            return

        key_raw = str(data.get("key") or os.path.splitext(os.path.basename(file_path))[0]).strip()
        value_raw = data.get("value")
        if not key_raw or value_raw is None:
            return

        category = str(data.get("category") or os.path.basename(os.path.dirname(file_path)) or "general").strip() or "general"
        token = f"{category.lower()}::{key_raw.lower()}"
        display_key = key_raw if category.lower() == "general" else f"{category}.{key_raw}"
        value_text = _memory_value_to_text(value_raw)
        try:
            mt = float(os.path.getmtime(file_path))
        except Exception:
            mt = 0.0
        epoch = _to_epoch(data.get("updated_at") or data.get("created_at"), fallback=mt)

        prev = selected.get(token)
        if prev is None:
            selected[token] = (display_key, value_text, epoch, scope)
            return

        prev_epoch, prev_scope = prev[2], prev[3]
        if scope == "local" and prev_scope == "global":
            selected[token] = (display_key, value_text, epoch, scope)
            return
        if scope == prev_scope and epoch >= prev_epoch:
            selected[token] = (display_key, value_text, epoch, scope)

    # core.json maps
    global_core, gcore_ts = _load_core_json(os.path.join(GLOBAL_MEMORY_DIR, aid, "core.json"))
    local_core, lcore_ts = _load_core_json(os.path.join(DATA_DIR, "memory", aid, "core.json"))
    merged_core = dict(global_core)
    merged_core.update(local_core)
    for k, v in merged_core.items():
        key = str(k).strip()
        if not key:
            continue
        token = f"core::{key.lower()}"
        scope = "local" if key in local_core else "global"
        epoch = lcore_ts if scope == "local" else gcore_ts
        selected[token] = (key, _memory_value_to_text(v), epoch, scope)

    # Structured memory records
    root_plan = [
        ("global", os.path.join(GLOBAL_MEMORY_DIR, aid)),
        ("global", os.path.join(GLOBAL_MEMORY_DIR, "general")),
        ("local", os.path.join(DATA_DIR, "memory", aid)),
        ("local", os.path.join(DATA_DIR, "memory", "general")),
    ]
    seen_files: set[str] = set()
    for scope, root in root_plan:
        for p in _iter_recent_json_files(root, max_files=max_files_per_root):
            abs_p = os.path.abspath(p)
            if abs_p in seen_files:
                continue
            seen_files.add(abs_p)
            absorb_record(scope, abs_p)

    rows = list(selected.values())
    rows.sort(key=lambda x: x[2], reverse=True)
    out: dict[str, str] = {}
    for display_key, value_text, _epoch, _scope in rows[: max(1, int(max_items or 10))]:
        out[display_key] = value_text[:500]
    return out
