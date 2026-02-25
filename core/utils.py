import os
import re
import json
import yaml
import hashlib
import threading
import ast
import time
import subprocess
import sys
import importlib.util
import copy
from collections import OrderedDict
from datetime import datetime
from core.config_paths import *
from core.executor import run_skill_safely

_YAML_CACHE: "OrderedDict[str, tuple[int, int, str, dict]]" = OrderedDict()
_YAML_CACHE_LOCK = threading.Lock()
_DASHBOARD_CACHE: tuple[int, int, dict] | None = None

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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y")


def _yaml_cache_max_entries() -> int:
    raw = str(os.getenv("YAML_CACHE_MAX_ENTRIES", "256")).strip()
    try:
        val = int(raw)
        return max(1, val)
    except Exception:
        return 256


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _yaml_cache_put(path: str, mtime_ns: int, size: int, content_hash: str, data: dict):
    with _YAML_CACHE_LOCK:
        _YAML_CACHE[path] = (mtime_ns, size, content_hash, data)
        _YAML_CACHE.move_to_end(path, last=True)
        max_entries = _yaml_cache_max_entries()
        while len(_YAML_CACHE) > max_entries:
            _YAML_CACHE.popitem(last=False)

def read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        st = os.stat(path)
        mtime_ns = int(st.st_mtime_ns)
        size = int(st.st_size)
    except Exception:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    verify_hash = _env_flag("YAML_CACHE_VERIFY_HASH", default=False)
    with _YAML_CACHE_LOCK:
        cached = _YAML_CACHE.get(path)
        if cached and cached[0] == mtime_ns and cached[1] == size:
            cached_hash = str(cached[2] or "")
            if not verify_hash:
                _YAML_CACHE.move_to_end(path, last=True)
                return copy.deepcopy(cached[3])
            if cached_hash:
                cur_hash = _sha256_file(path)
                if cur_hash == cached_hash:
                    _YAML_CACHE.move_to_end(path, last=True)
                    return copy.deepcopy(cached[3])
            # stale or unverifiable entry
            _YAML_CACHE.pop(path, None)

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        data = {}
    content_hash = _sha256_file(path) if verify_hash else ""
    _yaml_cache_put(path, mtime_ns, size, content_hash, data)
    return copy.deepcopy(data)

def write_yaml(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    try:
        st = os.stat(path)
        verify_hash = _env_flag("YAML_CACHE_VERIFY_HASH", default=False)
        content_hash = _sha256_file(path) if verify_hash else ""
        _yaml_cache_put(
            path,
            int(st.st_mtime_ns),
            int(st.st_size),
            content_hash,
            copy.deepcopy(data if isinstance(data, dict) else {}),
        )
    except Exception:
        with _YAML_CACHE_LOCK:
            _YAML_CACHE.pop(path, None)

def write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

# --- Core Constants (Moved from agent_launcher) ---
MAX_ITERATIONS = 3
TEST_TIMEOUT_SEC = 10
CHILD_ENV_PASSTHROUGH = {
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
    "PYTHONIOENCODING", "PYTHONUTF8",
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
}
BANNED_IMPORT_TOPS = {
    "os", "sys", "subprocess", "shutil", "importlib",
    "pathlib", "glob", "ctypes",
    "multiprocessing", "threading", "concurrent", "asyncio",
}
BANNED_CALLS = {"eval", "exec", "__import__", "compile", "input"}

def safe_generate(model, prompt, **kwargs):
    for i in range(5):
        try:
            return model.generate_content(prompt, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "quota" in msg:
                wait_sec = 20 * (i + 1)
                print(f"[Warn] Quota hit. Waiting {wait_sec}s... ({i+1}/5)")
                time.sleep(wait_sec)
                continue
            raise e
    raise RuntimeError("Quota exceeded after retries")

def quick_guard(code: str) -> tuple[bool, list[str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"SyntaxError: {e}"]

    vios: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in BANNED_IMPORT_TOPS:
                    vios.append(f"Forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in BANNED_IMPORT_TOPS:
                vios.append(f"Forbidden import: {node.module}")
        elif isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name): name = node.func.id
            elif isinstance(node.func, ast.Attribute): name = node.func.attr
            if name in BANNED_CALLS:
                vios.append(f"Forbidden call: {name}")
    return (len(vios) == 0), vios

def run_isolated(skill_py_path: str, timeout_sec: int = TEST_TIMEOUT_SEC) -> tuple[bool, dict, str]:
    skill_abs = os.path.abspath(skill_py_path)
    data_abs = os.path.abspath(DATA_DIR)
    art_abs = os.path.abspath(ARTIFACTS_DIR)
    
    SKILL_PATH = repr(skill_abs)
    DATA_ROOT = repr(data_abs)
    ART_ROOT = repr(art_abs)

    runner = f"""
import json, os, sys, builtins, importlib.util

SKILL_PATH = {SKILL_PATH}
DATA_ROOT  = {DATA_ROOT}
ART_ROOT   = {ART_ROOT}

try:
    cwd = os.getcwd()
    sys.path = [p for p in sys.path if p not in ("", ".", cwd)]
except Exception: pass

try:
    import socket as _socket
    AUDIT_PATH = os.path.join(ART_ROOT, "network_audit.log")
    def _audit(line: str):
        try:
            ts = __import__("datetime").datetime.utcnow().isoformat()
            with builtins.open(AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{{ts}}] {{line}}\\n")
        except Exception: pass
    def _blocked_connect(self, address):
        _audit(f"BLOCKED socket.connect address={{address}}")
        raise PermissionError(f"Network access blocked: {{address}}")
    _socket.socket.connect = _blocked_connect
except Exception: pass

_real_open = builtins.open
def _norm(p: str) -> str: return os.path.normcase(os.path.realpath(os.path.abspath(p)))
DATA_N = _norm(DATA_ROOT)
ART_N  = _norm(ART_ROOT)

def _is_within(path: str, root_norm: str) -> bool:
    p = _norm(path)
    return p == root_norm or p.startswith(root_norm + os.sep)

def _safe_open(file, mode="r", *args, **kwargs):
    path = _norm(str(file))
    if not (_is_within(path, DATA_N) or _is_within(path, ART_N)):
        raise PermissionError(f"open blocked: {{path}}")
    if any(x in mode for x in ("w","a","x","+")) and not _is_within(path, ART_N):
        raise PermissionError(f"write blocked: {{path}}")
    return _real_open(path, mode, *args, **kwargs)

builtins.open = _safe_open

try:
    spec = importlib.util.spec_from_file_location("skill", SKILL_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    res = m.test({{"dry_run": True, "data_dir": DATA_ROOT, "artifacts_dir": ART_ROOT}})
    print(json.dumps(res, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"ok": False, "reason": "runtime_error", "error": str(e)}}, ensure_ascii=False))
"""
    temp_runner_path = os.path.join(ARTIFACTS_DIR, f"temp_runner_{int(time.time())}.py")
    write_text(temp_runner_path, runner)
    try:
        exec_result = run_skill_safely("Skill_Test", temp_runner_path, [], timeout=timeout_sec, workdir=ARTIFACTS_DIR)
        if os.path.exists(temp_runner_path): os.remove(temp_runner_path)
        if exec_result["status"] == "failed": return False, {"ok": False}, exec_result["error"]
        out = exec_result["stdout"].strip()
        j = json.loads(out.splitlines()[-1])
        return bool(j.get("ok")), j, exec_result["stderr"]
    except Exception as e:
        return False, {"ok": False}, str(e)

def build_child_env() -> dict:
    return {k: os.environ.get(k) for k in CHILD_ENV_PASSTHROUGH if os.environ.get(k)}

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
    global _DASHBOARD_CACHE
    path = DASHBOARD_PATH
    stat_sig = None
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
    # Keep recent 300 entries to avoid unbounded growth.
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

# =============================================================================

__all__ = [name for name in dir() if not name.startswith('__')]
