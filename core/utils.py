import os
import re
import json
import yaml
import hashlib
from datetime import datetime
from core.config_paths import *

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return (t[:60] if t else "skill")

def strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json|python)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()

def safe_json_load(s: str) -> dict:
    s = strip_code_fences(s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        return json.loads(m.group(0)) if m else {}

def read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def write_yaml(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

def write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def ensure_registry_files():
    if not os.path.exists(REGISTRY_PATH):
        write_yaml(REGISTRY_PATH, {"skills": {}, "install_candidates": {}})
    if not os.path.exists(WORKFLOW_PATH):
        write_yaml(WORKFLOW_PATH, {"capability_to_skill": {}, "updated_at": now_iso()})

# (Duplicate utility block removed by Audit Remediation)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def ensure_registry_files():
    if not os.path.exists(REGISTRY_PATH):
        write_yaml(REGISTRY_PATH, {"skills": {}, "install_candidates": {}})
    if not os.path.exists(WORKFLOW_PATH):
        write_yaml(WORKFLOW_PATH, {"capability_to_skill": {}, "updated_at": now_iso()})

def get_random_signature(agent_config: dict) -> str:
    """YAML 설정에서 무작위 시그니처 대사를 반환합니다."""
    import random
    # persona 하위 혹은 최상위에 signature_lines가 있을 수 있음 (표준화 진행됨)
    lines = agent_config.get("signature_lines")
    if not lines and "persona" in agent_config:
        lines = agent_config["persona"].get("signature_lines")
    
    if lines and isinstance(lines, list):
        return random.choice(lines)
    return ""

def print_agent_msg(name: str, msg: str, signature: str = ""):
    """에이전트 이름과 메시지, 그리고 시그니처 대사를 출력합니다."""
    header = f"[{name}]"
    if signature:
        print(f"\n{header} \"{signature}\"")
        print(f"{header} {msg}")
    else:
        print(f"{header} {msg}")

def is_codex_model(model_name: str) -> bool:
    m = (model_name or "").strip().lower()
    return m.startswith("codex") or m.startswith("gpt-5")

def is_claude_model(model_name: str) -> bool:
    m = (model_name or "").strip().lower()
    return m.startswith("claude")

def read_project_policies() -> dict:
    data = read_yaml(POLICIES_PATH)
    return data if isinstance(data, dict) else {}

def read_project_settings() -> dict:
    data = read_yaml(PROJECT_SETTINGS_PATH)
    return data if isinstance(data, dict) else {}

def resolve_existing_path(path_text: str) -> str | None:
    p = str(path_text or "").strip()
    if not p:
        return None
    if os.path.isabs(p) and os.path.exists(p):
        return os.path.normpath(p)
    for root in (BASE_DIR, SKILLS_DIR, PROJECT_ROOT):
        cand = os.path.normpath(os.path.join(root, p))
        if os.path.exists(cand):
            return cand
    return None

def to_portable_path(path_text: str) -> str:
    p = str(path_text or "").strip()
    if not p:
        return p
    abs_p = resolve_existing_path(p)
    if not abs_p:
        return p
    try:
        rel = os.path.relpath(abs_p, BASE_DIR)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except Exception:
        pass
    return abs_p.replace("\\", "/")

def is_portable_rel_path(path_text: str) -> bool:
    p = str(path_text or "").strip()
    if not p:
        return False
    # Reject absolute paths (e.g., D:\..., /home/...)
    if os.path.isabs(p):
        return False
    # Reject drive-letter style even if os.path.isabs misses it in edge cases.
    if re.match(r"^[a-zA-Z]:[/\\\\]", p):
        return False
    return True

def resolve_skill_paths(skill_id: str) -> tuple[str | None, str | None]:
    sid = safe_id(skill_id)
    settings = read_project_settings()
    pref = settings.get("skill_overrides", {}) if isinstance(settings.get("skill_overrides"), dict) else {}
    prefer_project = bool(pref.get("prefer_project_skills", True))
    project_py = os.path.join(PROJECT_SKILLS_DIR, sid, "skill.py")
    project_meta = os.path.join(PROJECT_SKILLS_DIR, sid, "meta.yaml")
    global_py = os.path.join(SKILLS_DIR, sid, "skill.py")
    global_meta = os.path.join(SKILLS_DIR, sid, "meta.yaml")
    ordered = (
        [(project_py, project_meta), (global_py, global_meta)]
        if prefer_project
        else [(global_py, global_meta), (project_py, project_meta)]
    )
    for py_path, meta_path in ordered:
        if os.path.exists(py_path):
            return py_path, (meta_path if os.path.exists(meta_path) else None)
    # Backward compatibility: allow legacy forge single-file skills.
    forge_py = os.path.join(SKILLS_DIR, "forge", f"{sid}.py")
    if os.path.exists(forge_py):
        return forge_py, None
    return None, None

def _merge_dict(base: dict, override: dict) -> dict:
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = v
    return out

def apply_agent_overrides(agent: dict, role_spec: str) -> dict:
    settings = read_project_settings()
    over = settings.get("agent_overrides", {}) if isinstance(settings.get("agent_overrides"), dict) else {}
    key = safe_id(role_spec)
    cfg = over.get(key, {}) if isinstance(over.get(key), dict) else {}
    if not cfg:
        return agent
    merged = _merge_dict(agent, cfg)
    extra = [safe_id(str(s)) for s in (cfg.get("skills_add") or []) if str(s).strip()]
    if extra:
        base_skills = [safe_id(str(s)) for s in (merged.get("skills") or []) if str(s).strip()]
        merged["skills"] = list(dict.fromkeys(base_skills + extra))
    return merged

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

def read_skill_lock() -> dict:
    data = read_yaml(SKILL_LOCK_PATH)
    if not isinstance(data, dict):
        return {"skills": {}}
    data.setdefault("skills", {})
    return data

def lock_skill_state(skill_id: str, meta: dict):
    lock = read_skill_lock()
    lock.setdefault("skills", {})
    lock["skills"][safe_id(skill_id)] = {
        "version": str(meta.get("version", "0.1.0")),
        "status": str(meta.get("status", "candidate")),
        "updated_at": now_iso(),
    }
    write_yaml(SKILL_LOCK_PATH, lock)

def append_dashboard_run(entry: dict):
    try:
        with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"project_id": PROJECT_ID, "runs": []}
    data.setdefault("project_id", PROJECT_ID)
    data.setdefault("runs", [])
    data["runs"].append(entry)
    # Keep recent 300 entries to avoid unbounded growth.
    data["runs"] = data["runs"][-300:]
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _safe_write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =============================================================================

__all__ = [name for name in dir() if not name.startswith('__')]
