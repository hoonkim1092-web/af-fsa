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


def _get_memory_path(ctx):
    data_dir = ctx.get("data_dir", ".")
    agent_id = _agent_id_from_ctx(ctx)
    return os.path.join(data_dir, "memory", agent_id)


def _legacy_memory_path(ctx):
    data_dir = ctx.get("data_dir", ".")
    return os.path.join(data_dir, "memory")


def store(ctx, key, value, category="general"):
    memory_dir = _get_memory_path(ctx)
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
        "created_at": ts,
        "updated_at": ts,
    }

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": file_path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def retrieve(ctx, key, category="general"):
    safe_key = "".join([c for c in key if c.isalnum() or c in (" ", "_", "-")]).strip()
    if not safe_key:
        safe_key = "unnamed_memory"

    primary = os.path.join(_get_memory_path(ctx), category, f"{safe_key}.json")
    legacy = os.path.join(_legacy_memory_path(ctx), category, f"{safe_key}.json")

    for idx, file_path in enumerate([primary, legacy]):
        if not os.path.exists(file_path):
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"ok": True, "data": data, "legacy": bool(idx == 1)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "not_found"}


def search(ctx, query, category="general"):
    query_lower = str(query).lower()
    results = []
    seen = set()

    candidate_dirs = [
        os.path.join(_get_memory_path(ctx), category),
        os.path.join(_legacy_memory_path(ctx), category),
    ]
    for category_dir in candidate_dirs:
        if not os.path.exists(category_dir):
            continue
        for file_path in glob.glob(os.path.join(category_dir, "*.json")):
            if file_path in seen:
                continue
            seen.add(file_path)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if query_lower in str(data.get("key", "")).lower() or query_lower in str(data.get("value", "")).lower():
                    results.append(data)
            except Exception:
                continue

    return {"ok": True, "results": results}


def propose(ctx):
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
    }


def apply(ctx):
    payload = ctx if isinstance(ctx, dict) else {}
    operation = str(payload.get("operation") or payload.get("op") or "").strip().lower()
    category = str(payload.get("category") or "general")

    if operation == "store":
        key = str(payload.get("key") or "").strip()
        if not key:
            return {"ok": False, "reason": "missing_key"}
        if "value" not in payload:
            return {"ok": False, "reason": "missing_value"}
        return store(payload, key, payload.get("value"), category)

    if operation == "retrieve":
        key = str(payload.get("key") or "").strip()
        if not key:
            return {"ok": False, "reason": "missing_key"}
        return retrieve(payload, key, category)

    if operation == "search":
        query = str(payload.get("query") or payload.get("key") or "").strip()
        if not query:
            return {"ok": False, "reason": "missing_query"}
        return search(payload, query, category)

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
