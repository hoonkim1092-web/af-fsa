import glob
import json
import os
from datetime import datetime


def _safe_id(text):
    t = str(text or "").strip().lower()
    t = "".join([c if (c.isalnum() or c in ("_", "-")) else "_" for c in t])
    while "__" in t:
        t = t.replace("__", "_")
    return t.strip("_")


def _agent_id_from_ctx(ctx):
    agent = ctx.get("agent", {}) if isinstance(ctx, dict) else {}
    if isinstance(agent, dict):
        name = agent.get("name") or agent.get("role")
        sid = _safe_id(name)
        if sid:
            return sid
    return "general"


def _resolve_scope(value, default="hybrid"):
    v = str(value or "").strip().lower()
    if v in ("local", "global", "hybrid"):
        return v
    return default


def _default_store_scope(ctx):
    if not isinstance(ctx, dict):
        return "local"
    project_id = str(ctx.get("project_id") or "").strip().lower()
    if project_id == "agent-factory":
        return "global"
    return "local"


def _get_local_data_dir(ctx):
    data_dir = ctx.get("data_dir", ".")
    return os.path.abspath(str(data_dir))


def _get_global_data_dir(ctx):
    if not isinstance(ctx, dict):
        return None
    raw = str(ctx.get("global_data_dir") or "").strip()
    if raw:
        return os.path.abspath(raw)
    return None


def _memory_roots(ctx, scope):
    scope = _resolve_scope(scope, default="hybrid")
    agent_id = _agent_id_from_ctx(ctx)
    roots = []

    if scope in ("local", "hybrid"):
        local_data = _get_local_data_dir(ctx)
        roots.append({"root": os.path.join(local_data, "memory", agent_id), "source": "local", "legacy": False})
        roots.append({"root": os.path.join(local_data, "memory"), "source": "local", "legacy": True})

    if scope in ("global", "hybrid"):
        global_data = _get_global_data_dir(ctx)
        if global_data:
            roots.append({"root": os.path.join(global_data, "memory", agent_id), "source": "global", "legacy": False})
            roots.append({"root": os.path.join(global_data, "memory"), "source": "global", "legacy": True})

    return roots


def _category_dirs(ctx, category, scope):
    dirs = []
    for item in _memory_roots(ctx, scope):
        dirs.append(
            {
                "dir": os.path.join(item["root"], category),
                "source": item["source"],
                "legacy": item["legacy"],
            }
        )
    return dirs


def _legacy_memory_path(ctx):
    data_dir = _get_local_data_dir(ctx)
    return os.path.join(data_dir, "memory")


def _store_root(ctx, scope):
    scope = _resolve_scope(scope, default=_default_store_scope(ctx))
    if scope == "global":
        global_data = _get_global_data_dir(ctx)
        if not global_data:
            return None, "global"
        return os.path.join(global_data, "memory", _agent_id_from_ctx(ctx)), "global"
    local_data = _get_local_data_dir(ctx)
    return os.path.join(local_data, "memory", _agent_id_from_ctx(ctx)), "local"


def store(ctx, key, value, category="general", scope=None):
    memory_dir, store_scope = _store_root(ctx, scope)
    if not memory_dir:
        return {"ok": False, "error": "global_memory_unavailable"}

    category_dir = os.path.join(memory_dir, category)
    os.makedirs(category_dir, exist_ok=True)

    safe_key = "".join([c for c in key if c.isalnum() or c in (" ", "_", "-")]).strip()
    if not safe_key:
        safe_key = "unnamed_memory"

    file_path = os.path.join(category_dir, f"{safe_key}.json")
    ts = datetime.now().isoformat()
    record = {
        "key": key,
        "value": value,
        "category": category,
        "agent_id": _agent_id_from_ctx(ctx),
        "memory_scope": store_scope,
        "created_at": ts,
        "updated_at": ts,
    }

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": file_path, "scope": store_scope}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def retrieve(ctx, key, category="general", scope="hybrid"):
    safe_key = "".join([c for c in key if c.isalnum() or c in (" ", "_", "-")]).strip()
    if not safe_key:
        safe_key = "unnamed_memory"

    for entry in _category_dirs(ctx, category, scope):
        file_path = os.path.join(entry["dir"], f"{safe_key}.json")
        if not os.path.exists(file_path):
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "ok": True,
                "data": data,
                "source": entry["source"],
                "legacy": bool(entry["legacy"]),
                "scope": _resolve_scope(scope, default="hybrid"),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "not_found", "scope": _resolve_scope(scope, default="hybrid")}


def search(ctx, query, category="general", scope="hybrid"):
    query_lower = str(query).lower()
    results = []
    seen = set()

    for entry in _category_dirs(ctx, category, scope):
        category_dir = entry["dir"]
        if not os.path.exists(category_dir):
            continue
        for file_path in glob.glob(os.path.join(category_dir, "*.json")):
            key_path = os.path.abspath(file_path)
            if key_path in seen:
                continue
            seen.add(key_path)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if query_lower in str(data.get("key", "")).lower() or query_lower in str(data.get("value", "")).lower():
                    row = dict(data)
                    row["source"] = entry["source"]
                    row["legacy"] = bool(entry["legacy"])
                    results.append(row)
            except Exception:
                continue

    return {"ok": True, "results": results, "scope": _resolve_scope(scope, default="hybrid")}


def propose(ctx):
    store_default_scope = _default_store_scope(ctx)
    operation = str((ctx or {}).get("operation") or (ctx or {}).get("op") or "retrieve").strip().lower()
    return {
        "ok": True,
        "skill": "core_memory",
        "operation": operation,
        "supported_operations": ["store", "retrieve", "search"],
        "required_fields": {
            "store": ["key", "value"],
            "retrieve": ["key"],
            "search": ["query"],
        },
        "optional_fields": {
            "scope": (
                "local|global|hybrid "
                f"(store default={store_default_scope}; "
                "project_id=agent-factory -> global, otherwise local; "
                "retrieve/search default=hybrid)"
            )
        },
    }


def apply(ctx):
    payload = ctx if isinstance(ctx, dict) else {}
    operation = str(payload.get("operation") or payload.get("op") or "").strip().lower()
    category = str(payload.get("category") or "general")
    scope = str(payload.get("scope") or "").strip().lower()

    if operation == "store":
        key = str(payload.get("key") or "").strip()
        if not key:
            return {"ok": False, "reason": "missing_key"}
        if "value" not in payload:
            return {"ok": False, "reason": "missing_value"}
        return store(payload, key, payload.get("value"), category, scope=(scope or None))

    if operation == "retrieve":
        key = str(payload.get("key") or "").strip()
        if not key:
            return {"ok": False, "reason": "missing_key"}
        return retrieve(payload, key, category, scope=(scope or "hybrid"))

    if operation == "search":
        query = str(payload.get("query") or payload.get("key") or "").strip()
        if not query:
            return {"ok": False, "reason": "missing_query"}
        return search(payload, query, category, scope=(scope or "hybrid"))

    return {
        "ok": False,
        "reason": "invalid_operation",
        "supported_operations": ["store", "retrieve", "search"],
    }


def test(ctx):
    res_store = store(ctx, "test_key_123", "test_value_456", "test_cat")
    if not res_store.get("ok"):
        return {"ok": False, "reason": "store_failed", "detail": res_store}

    res_retr = retrieve(ctx, "test_key_123", "test_cat")
    if not res_retr.get("ok") or res_retr["data"]["value"] != "test_value_456":
        return {"ok": False, "reason": "retrieve_mismatch", "detail": res_retr}

    res_search = search(ctx, "value_456", "test_cat")
    if not res_search.get("ok") or len(res_search["results"]) == 0:
        return {"ok": False, "reason": "search_failed", "detail": res_search}

    return {"ok": True}
