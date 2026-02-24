# agent_factory_lite_secure.py
import os
import re
import time
import json
import ast
import yaml
import hashlib
import subprocess
import sys
import importlib.util
import inspect
import functools
import shutil
from datetime import datetime
import getpass
from config.schema import factory_config

from core.executor import run_skill_safely
from core.policy import resolve_quality_gate_policy

import google.generativeai as genai
try:
    from openai import OpenAI
except Exception:
    OpenAI = None
from model_utils import get_best_model
from core.registry import ToolRegistry
from core.hooks.base import TodoContinuationEnforcer

# Reconfigure stdout for Windows
try:
    sys.stdin.reconfigure(encoding='utf-8')
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
try:
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

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

# =============================================================================
# config.schema is loaded at top
global_project_root = os.getenv("AGENT_PROJECT_ROOT", None)
GOOGLE_API_KEY = factory_config.google_api_key
OPENAI_API_KEY = factory_config.openai_api_key
if not GOOGLE_API_KEY and not OPENAI_API_KEY:
    raise RuntimeError("Neither GOOGLE_API_KEY nor OPENAI_API_KEY found in env/.env")
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# [Project Specific Path Resolution]
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
GLOBAL_AGENTS_DIR = os.path.join(BASE_DIR, "agents")
SKILLS_DIR = os.path.join(BASE_DIR, "skills")
GLOBAL_RUNS_DIR = os.path.join(BASE_DIR, "runs")

def _boot_safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "default"

_proj_id_env = _boot_safe_id(os.environ.get("AGENT_PROJECT_ID", "").strip()) if os.environ.get("AGENT_PROJECT_ID") else ""
_proj_root_env = os.environ.get("AGENT_PROJECT_ROOT", "").strip()
if _proj_root_env:
    PROJECT_ROOT = os.path.abspath(_proj_root_env)
    PROJECT_ID = _proj_id_env or _boot_safe_id(os.path.basename(PROJECT_ROOT))
else:
    PROJECT_ID = _proj_id_env or "default"
    PROJECT_ROOT = os.path.abspath(os.path.join(PROJECTS_DIR, PROJECT_ID))

AGENTS_DIR = os.path.join(PROJECT_ROOT, "agents")
RUNS_DIR = os.path.join(PROJECT_ROOT, "runs")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
ARTIFACTS_DIR = os.path.join(PROJECT_ROOT, "artifacts")
PROJECT_SKILLS_DIR = os.path.join(PROJECT_ROOT, "skills")
EXTERNAL_CACHE_DIR = os.path.join(SKILLS_DIR, "_external_cache")
PROJECT_SETTINGS_PATH = os.path.join(PROJECT_ROOT, "settings.yaml")
POLICIES_PATH = os.path.join(PROJECT_ROOT, "policies.yaml")
CONTEXT_SCHEMA_PATH = os.path.join(PROJECT_ROOT, "context_schema.yaml")
SKILL_LOCK_PATH = os.path.join(PROJECT_ROOT, "skill-lock.yaml")
DASHBOARD_PATH = os.path.join(PROJECT_ROOT, "dashboard.json")
PROJECT_WORKFLOW_PATH = os.path.join(PROJECT_ROOT, "workflow.yaml")

REGISTRY_PATH = os.path.join(SKILLS_DIR, "registry.yaml")
WORKFLOW_PATH = os.path.join(SKILLS_DIR, "workflow_registry.yaml")

for d in [PROJECTS_DIR, GLOBAL_AGENTS_DIR, GLOBAL_RUNS_DIR, AGENTS_DIR, SKILLS_DIR, RUNS_DIR, DATA_DIR, ARTIFACTS_DIR, PROJECT_SKILLS_DIR, EXTERNAL_CACHE_DIR]:
    os.makedirs(d, exist_ok=True)

if not os.path.exists(POLICIES_PATH):
    with open(POLICIES_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "project_id": PROJECT_ID,
                "workflow": {"default_template": "workflows/two_week_webapp_delivery.yaml", "role_map": {}},
                "quality_gate": {"default_stage_on_build": "candidate", "auto_promote_sequence": ["canary", "active"]},
                "approval_policy": {"default_require_approval": False, "require_skill_change_approval": False},
                "autonomy": {"max_stage_retries": 2, "strict_quality_gate": True, "stop_on_stage_failure": True},
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )
if not os.path.exists(CONTEXT_SCHEMA_PATH):
    with open(CONTEXT_SCHEMA_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "required_keys": ["agent", "data_dir", "artifacts_dir"],
                "types": {"agent": "dict", "data_dir": "str", "artifacts_dir": "str"},
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )
if not os.path.exists(SKILL_LOCK_PATH):
    with open(SKILL_LOCK_PATH, "w", encoding="utf-8") as f:
        yaml.dump({"skills": {}}, f, allow_unicode=True, default_flow_style=False)
if not os.path.exists(DASHBOARD_PATH):
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        json.dump({"project_id": PROJECT_ID, "runs": []}, f, ensure_ascii=False, indent=2)
if not os.path.exists(PROJECT_WORKFLOW_PATH):
    with open(PROJECT_WORKFLOW_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            {"owner_agent": "General", "stages": [{"id": "MAIN", "name": "Main", "objective": "기본 워크플로우"}]},
            f,
            allow_unicode=True,
            default_flow_style=False,
        )
if not os.path.exists(PROJECT_SETTINGS_PATH):
    with open(PROJECT_SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "agent_overrides": {},
                "skill_overrides": {
                    "prefer_project_skills": True,
                },
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )

MAX_ITERATIONS = 3
TEST_TIMEOUT_SEC = 10
CHILD_ENV_PASSTHROUGH = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}

# =============================================================================
# Utils
# =============================================================================
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
# 1) Model Router (Lite)
# =============================================================================
class ModelRouter:
    def pick(self, stage: str, agent_config: dict = None) -> str:
        if stage == "chat":
            forced = (os.getenv("AGENT_CHAT_MODEL") or "").strip()
            if forced:
                return forced
            provider_raw = (os.getenv("AGENT_CHAT_PROVIDER") or "").strip().lower()
            providers = [p.strip() for p in provider_raw.split(",") if p.strip()]
            for provider in providers:
                if provider == "codex" and OPENAI_API_KEY:
                    return "codex-5.3"
                if provider == "claude":
                    return "claude-4.6"
        
        # Check Agent-specific high-end preference
        if agent_config and agent_config.get("preferred_model"):
            return get_best_model([agent_config["preferred_model"], "gemini-3.1-pro-preview", "gemini-1.5-pro"])

        # Stage 3 (Requirement/Reasoning) -> 3.1 Pro
        if stage in ("requirement", "reasoning"):
            return get_best_model(["gemini-3.1-pro-preview", "gemini-2.0-pro", "gemini-1.5-pro"])
        
        # Stage 2 (Flash/Normalization) -> 3 Flash
        return get_best_model(["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"])

# =============================================================================
# 2) Quick Guard (AST) - 移섎챸 ?꾧뎄 ?뺤닔
# =============================================================================
BANNED_IMPORT_TOPS = {
    "os", "sys", "subprocess", "shutil", "importlib",
    "pathlib", "glob",
    "ctypes",
    "multiprocessing", "threading", "concurrent", "asyncio",
}

BANNED_CALLS = {"eval", "exec", "__import__", "compile", "input"}
# open? ?덉슜(???runner?먯꽌 寃쎈줈 ?듭젣)

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
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in BANNED_CALLS:
                vios.append(f"Forbidden call: {name}")

    return (len(vios) == 0), vios

def build_child_env() -> dict:
    child = {}
    for k in CHILD_ENV_PASSTHROUGH:
        v = os.environ.get(k)
        if v:
            child[k] = v
    return child

# =============================================================================
# 3) Isolated Run (Lite) - -I ?좎?, -S ?쒓굅(pandas ?덉슜)
# =============================================================================
def run_isolated(skill_py_path: str, timeout_sec: int = TEST_TIMEOUT_SEC) -> tuple[bool, dict, str]:
    skill_abs = os.path.abspath(skill_py_path)
    data_abs = os.path.abspath(DATA_DIR)
    art_abs = os.path.abspath(ARTIFACTS_DIR)

    # ??Windows ?ы븿 ?덉쟾 二쇱엯: repr ?ъ슜
    SKILL_PATH = repr(skill_abs)
    DATA_ROOT = repr(data_abs)
    ART_ROOT = repr(art_abs)

    runner = f"""
import json, os, sys, builtins, importlib.util

SKILL_PATH = {SKILL_PATH}
DATA_ROOT  = {DATA_ROOT}
ART_ROOT   = {ART_ROOT}

# 1) sys.path?먯꽌 CWD ?쒓굅(紐⑤뱢 ??꾩엵 諛⑹?)
try:
    cwd = os.getcwd()
    sys.path = [p for p in sys.path if p not in ("", ".", cwd)]
except Exception:
    pass

try:
    import socket as _socket
    AUDIT_PATH = os.path.join(ART_ROOT, "network_audit.log")

    def _audit(line: str):
        try:
            ts = __import__("datetime").datetime.utcnow().isoformat()
            with builtins.open(AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {line}\\n")
        except Exception:
            pass

    _orig_connect = _socket.socket.connect
    def _blocked_connect(self, address):
        _audit(f"BLOCKED socket.connect address={address}")
        raise PermissionError(f"Network access is blocked in the sandbox. (address={address})")
    _socket.socket.connect = _blocked_connect

    _orig_create_connection = _socket.create_connection
    def _blocked_create_connection(address, *args, **kwargs):
        _audit(f"BLOCKED socket.create_connection address={address}")
        raise PermissionError(f"Network access is blocked in the sandbox. (address={address})")
    _socket.create_connection = _blocked_create_connection
except Exception:
    pass

# 3) ?뚯씪 寃쎈줈 ?듭젣(Chroot-ish): data/artifacts 諛?open 李⑤떒
_real_open = builtins.open
BLOCK_EXT = (".py", ".pth", ".so", ".dll", ".exe")

def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))

DATA_N = _norm(DATA_ROOT)
ART_N  = _norm(ART_ROOT)

def _is_within(path: str, root_norm: str) -> bool:
    p = _norm(path)
    return p == root_norm or p.startswith(root_norm + os.sep)

def _safe_open(file, mode="r", *args, **kwargs):
    if isinstance(file, int):
        raise PermissionError("fd open blocked")
    path = _norm(str(file))

    # read: data/artifacts留?
    if not (_is_within(path, DATA_N) or _is_within(path, ART_N)):
        raise PermissionError(f"open blocked: {path}")

    # write: artifacts留?+ ?ㅽ뻾???뺤옣??李⑤떒
    if any(x in mode for x in ("w","a","x","+")):
        if not _is_within(path, ART_N):
            raise PermissionError(f"write blocked: {path}")
        if path.endswith(BLOCK_EXT):
            raise PermissionError(f"write ext blocked: {path}")

    return _real_open(path, mode, *args, **kwargs)

builtins.open = _safe_open

# 4) Load skill
try:
    spec = importlib.util.spec_from_file_location("skill", SKILL_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
except Exception as e:
    print(json.dumps({{"ok": False, "reason": "import_failed", "error": str(e)}}, ensure_ascii=False))
    raise SystemExit(1)

missing = [fn for fn in ("propose","apply","test") if not hasattr(m, fn)]
if missing:
    print(json.dumps({{"ok": False, "reason": "missing_interface", "missing": missing}}, ensure_ascii=False))
    raise SystemExit(2)

ctx = {{"dry_run": True, "data_dir": DATA_ROOT, "artifacts_dir": ART_ROOT}}

try:
    res = m.test(ctx)
    if not isinstance(res, dict):
        res = {{"ok": False, "reason": "return_not_dict"}}
    print(json.dumps(res, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"ok": False, "reason": "runtime_error", "error": str(e)}}, ensure_ascii=False))
"""

    try:
        # Save runner script to a temporary file
        temp_runner_path = os.path.join(ARTIFACTS_DIR, f"temp_runner_{int(time.time())}.py")
        with open(temp_runner_path, "w", encoding="utf-8") as f:
            f.write(runner)

        # Execute using Safe Action Executor
        exec_result = run_skill_safely(
            role="Skill_Test",
            skill_path=temp_runner_path,
            args=[],
            timeout=timeout_sec,
            workdir=ARTIFACTS_DIR,
        )
        
        # Clean up temp file
        if os.path.exists(temp_runner_path):
            os.remove(temp_runner_path)

        if exec_result["status"] == "failed" and "timeout" in exec_result["error"].lower():
            return False, {"ok": False, "reason": "timeout"}, "timeout"
        elif exec_result["status"] == "failed":
            return False, {"ok": False, "reason": "runner_error", "error": exec_result["error"]}, exec_result["error"]
            
        p_stdout = exec_result["stdout"]
        p_stderr = exec_result["stderr"]
        
    except Exception as e:
        return False, {"ok": False, "reason": "runner_error", "error": str(e)}, str(e)

    out = p_stdout.strip()
    err = p_stderr.strip()
    if not out:
        return False, {"ok": False, "reason": "empty_output"}, err

    try:
        j = json.loads(out.splitlines()[-1])
        return bool(j.get("ok")), j, err
    except Exception:
        return False, {"ok": False, "reason": "non_json_output", "stdout": out}, err

# =============================================================================
# 4) Agent / Requirements
# =============================================================================
class AgentManager:
    def __init__(self, mr: ModelRouter):
        self.mr = mr

    def _agent_path(self, role_spec: str) -> str:
        return os.path.join(AGENTS_DIR, f"{safe_id(role_spec)}.yaml")

    def get_or_create(self, role_spec: str) -> dict:
        path = self._agent_path(role_spec)
        if os.path.exists(path):
            return apply_agent_overrides(read_yaml(path), role_spec)

        # Reuse global agent template first, then local project copy.
        global_path = os.path.join(GLOBAL_AGENTS_DIR, f"{safe_id(role_spec)}.yaml")
        if os.path.exists(global_path):
            data = read_yaml(global_path)
            data["updated_at"] = now_iso()
            write_yaml(path, data)
            return apply_agent_overrides(data, role_spec)

        model = genai.GenerativeModel(self.mr.pick("agent_create"))
        prompt = f"""
ROLE_SPEC: "{role_spec}"
JSON 출력:
{{"name":"...", "role":"...", "tone":"...", "traits":["..."], "system_ko": "...", "signature_lines": ["...", "..."]}}

[필수 규칙]
1. 모든 출력(tone, traits, system_ko, signature_lines)은 반드시 **한국어**로 작성해야 합니다.
2. **system_ko**: 에이전트의 페르소나와 행동 지침을 상세한 한국어로 작성하세요.
3. **signature_lines**: 에이전트가 대화를 시작할 때 사용할 시그니처 대사(한국어)를 3~5개 작성하세요. 캐릭터의 성격을 잘 드러내야 합니다.

"""
        res = safe_generate(model, prompt, generation_config={"response_mime_type": "application/json"})
        data = safe_json_load(res.text)
        data["name"] = data.get("name") or f"agent_{safe_id(role_spec)}"
        data["role"] = data.get("role") or role_spec
        data["created_at"] = now_iso()
        write_yaml(path, data)
        return apply_agent_overrides(data, role_spec)

    def install_skills(self, role_spec: str, skill_ids: list[str]) -> list[str]:
        if not skill_ids:
            return []
        path = self._agent_path(role_spec)
        agent = read_yaml(path) if os.path.exists(path) else self.get_or_create(role_spec)
        current = [safe_id(str(s)) for s in (agent.get("skills") or []) if str(s).strip()]
        merged = list(dict.fromkeys(current + [safe_id(s) for s in skill_ids]))
        agent["skills"] = merged
        agent["updated_at"] = now_iso()
        write_yaml(path, agent)
        return merged

class RequirementAnalyzer:
    def __init__(self, mr: ModelRouter):
        self.mr = mr

    def _fallback_missing_skills(self, task_input: str, role_text: str) -> list[str]:
        text = f"{task_input} {role_text}".lower()
        picks: list[str] = []
        rules = [
            ("issue_tracker", ["이슈", "추적", "ticket", "issue", "책임", "audit", "로그"]),
            ("data_visualize", ["시각화", "대시보드", "차트", "그래프", "요약"]),
        ]
        for sid, kws in rules:
            if any(k in text for k in kws):
                picks.append(sid)
        return list(dict.fromkeys([safe_id(s) for s in picks]))[:5]

    def analyze(self, agent: dict, task_input: str) -> dict:
        sig = get_random_signature(agent)
        print_agent_msg(agent.get("name", "Agent"), f"태스크 분석을 시작합니다... \"{task_input}\"", sig)
        
        model = genai.GenerativeModel(self.mr.pick("requirement"))
        prompt = f"""
AgentRole: {agent.get("role")}
Task: {task_input}

JSON留?異쒕젰:
{{
  "goal": "??臾몄옣",
  "missing_skills": ["snake_case_0to5"],
  "constraints": ["network_allowed", "no_system_tools", "data_io_allowed"],
  "risk_level": "normal|elevated|strict"
}}

洹쒖튃:
- missing_skills 0~5媛?
- ?ㅽ궗紐?snake_case
- data 遺꾩꽍?대㈃ needs_pandas 異붽?
"""
        try:
            res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            data = safe_json_load(res.text)
        except Exception as e:
            data = {
                "goal": task_input,
                "missing_skills": self._fallback_missing_skills(task_input, str(agent.get("role", ""))),
                "constraints": ["network_allowed", "no_system_tools", "data_io_allowed"],
                "risk_level": "normal",
                "analysis_fallback": f"llm_unavailable:{type(e).__name__}",
            }
        data.setdefault("goal", task_input)
        data.setdefault("missing_skills", [])
        data.setdefault("constraints", ["network_allowed", "no_system_tools", "data_io_allowed"])
        rl = data.get("risk_level", "normal")
        if rl not in ("normal", "elevated", "strict"):
            data["risk_level"] = "normal"

        data["missing_skills"] = [safe_id(str(s)) for s in (data["missing_skills"] or []) if str(s).strip()]
        return data

class HimariResearchAgent:
    def __init__(self, mr: ModelRouter):
        self.mr = mr

    def _approve_notebooklm_insight(self, insight: str) -> bool:
        preview = (insight or "").strip()
        if not preview:
            return False
        print("\n[Himari][디버그] NotebookLM 응답 미리보기")
        print("-" * 50)
        print(preview[:1200])
        print("-" * 50)
        try:
            ans = input("[Himari] 위 응답을 리서치 근거로 반영할까요? (yes/no): ").strip().lower()
            return ans in ("y", "yes")
        except Exception:
            return False

    def _registry_skill_index(self) -> dict:
        reg = read_yaml(REGISTRY_PATH)
        items = reg.get("skills", {}) if isinstance(reg, dict) else {}
        idx: dict = {}
        for sid, meta in items.items():
            key = safe_id(str(sid))
            caps = [safe_id(str(c)) for c in (meta.get("capabilities") or [])]
            idx[key] = {
                "id": key,
                "name": meta.get("name") or sid,
                "capabilities": caps,
                "meta": meta,
            }
        return idx

    def _fallback_match(self, need: str, idx: dict) -> list[str]:
        need_tokens = set(t for t in safe_id(need).split("_") if t)
        picked: list[str] = []
        for sid, item in idx.items():
            corpus = " ".join([sid, safe_id(item.get("name", ""))] + item.get("capabilities", []))
            tokens = set(t for t in corpus.split("_") if t)
            if need_tokens and (need_tokens & tokens):
                picked.append(sid)
        return picked[:3]

    def _score_candidate(self, need: str, item: dict) -> tuple[int, dict]:
        need_tokens = set(t for t in safe_id(need).split("_") if t)
        caps = [safe_id(str(c)) for c in (item.get("capabilities") or [])]
        corpus = " ".join([safe_id(item.get("id", "")), safe_id(item.get("name", ""))] + caps)
        tokens = set(t for t in corpus.split("_") if t)
        overlap = sorted(list(need_tokens & tokens))

        meta = item.get("meta", {}) if isinstance(item.get("meta"), dict) else {}
        path = str(meta.get("path", ""))
        meta_path = str(meta.get("meta_path", ""))
        resolved_py, resolved_meta = resolve_skill_paths(item["id"])
        exists_py = bool(resolve_existing_path(path)) if path else bool(resolved_py)
        exists_meta = bool(resolve_existing_path(meta_path)) if meta_path else bool(resolved_meta)
        last_test_ok = bool(meta.get("last_test_ok", False))

        score = 0
        score += min(len(overlap) * 25, 60)
        if exists_py:
            score += 20
        if exists_meta:
            score += 10
        if last_test_ok:
            score += 10
        score = max(0, min(100, score))
        verify = {
            "exists_skill_py": exists_py,
            "exists_meta_yaml": exists_meta,
            "last_test_ok": last_test_ok,
            "token_overlap": overlap,
        }
        return score, verify

    def _notebooklm_cmd(self, query: str) -> list[str]:
        target_notebook_id = "eaa34a54-a898-46a0-835a-cdb6024887f0"
        return [
            sys.executable, "-m", "notebooklm_tools.cli.main",
            "query", "notebook",
            target_notebook_id,
            query,
        ]

    def _notebooklm_login_cmd(self) -> list[str]:
        return [sys.executable, "-m", "notebooklm_tools.cli.main", "login"]

    def _is_notebooklm_auth_error(self, stderr_text: str) -> bool:
        s = (stderr_text or "").lower()
        flags = [
            "authentication expired",
            "rpc error 16",
            "clientauthenticationerror",
            "run 'nlm login'",
        ]
        return any(f in s for f in flags)

    def _extract_notebooklm_answer(self, raw_text: str) -> str:
        text = (raw_text or "").strip()
        if not text:
            return ""
        payload = safe_json_load(text)
        if isinstance(payload, dict):
            value = payload.get("value")
            if isinstance(value, dict):
                ans = str(value.get("answer") or "").strip()
                if ans:
                    return ans
                raw_response = str(value.get("raw_response") or "").strip()
                if raw_response:
                    # Fallback: keep response non-empty even when answer is blank.
                    return raw_response[:2000]
        return text

    def _query_notebooklm_once(self, query: str, timeout: int = 60) -> tuple[int, str, str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        p = subprocess.run(
            self._notebooklm_cmd(query),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=timeout,
        )
        return p.returncode, (p.stdout or ""), (p.stderr or "")

    def _reauth_notebooklm(self) -> bool:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            print("🔐 [Himari] NotebookLM 인증 만료 감지. 재인증을 시도합니다...")
            p = subprocess.run(
                self._notebooklm_login_cmd(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=180,
            )
            if p.returncode == 0:
                print("✅ [Himari] NotebookLM 재인증 성공.")
                return True
            print(f"⚠️ [Himari] NotebookLM 재인증 실패: {(p.stderr or '').strip()}")
            return False
        except Exception as e:
            print(f"⚠️ [Himari] NotebookLM 재인증 예외: {e}")
            return False

    def _query_notebooklm(self, query: str) -> str:
        """
        NotebookLM CLI를 통해 질문을 수행합니다.
        인증 만료가 감지되면 자동 재인증 후 1회 재시도합니다.
        """
        try:
            rc, out, err = self._query_notebooklm_once(query, timeout=60)
            if rc != 0 and self._is_notebooklm_auth_error(err):
                if self._reauth_notebooklm():
                    rc, out, err = self._query_notebooklm_once(query, timeout=60)
            if rc != 0:
                print(f"⚠️ [Himari] NotebookLM 쿼리 실패: {err.strip()}")
                return ""
            return self._extract_notebooklm_answer(out)
        except Exception as e:
            print(f"⚠️ [Himari] NotebookLM 연결 오류: {e}")
            return ""

    def research(self, agent: dict, reqs: dict, build_targets: list[str] | None = None) -> dict:
        missing = [safe_id(str(s)) for s in (build_targets or reqs.get("missing_skills") or []) if str(s).strip()]
        idx = self._registry_skill_index()
        if not missing:
            return {"suggestions": {}, "all_candidates": [], "evidence_pack": {"targets": {}}}

        skill_catalog = []
        for sid, item in idx.items():
            skill_catalog.append({
                "id": sid,
                "name": item["name"],
                "capabilities": item["capabilities"],
            })

        # --- NotebookLM 리서치 수행 (필요 시) ---
        notebook_insight = ""
        if missing:
            # 히마리 셋업 및 시그니처 출력
            himari_cfg = read_yaml(os.path.join(AGENTS_DIR, "himari.yaml"))
            sig = get_random_signature(himari_cfg)
            print_agent_msg("Himari", f"비밀 서고(NotebookLM)에서 '{missing[0]}' 관련 지식을 탐색합니다...", sig)
            
            # 첫 번째 미싱 스킬에 대해 힌트를 얻어봄
            query = f"Python skill implementation for: {missing[0]}. requirements: {reqs.get('goal')}"
            insight = self._query_notebooklm(query)
            if insight and self._approve_notebooklm_insight(insight):
                notebook_insight = f"\n[NotebookLM Secret Archive Constraint]: {insight[:1000]}"
                print("💡 [Himari] 승인된 NotebookLM 근거를 반영합니다.")
            elif insight:
                print("⏭️ [Himari] NotebookLM 근거 반영이 보류되었습니다. 로컬 근거만 사용합니다.")

        model = genai.GenerativeModel(self.mr.pick("requirement"))
        prompt = f"""
너는 리서치 에이전트 Himari다.
목표: missing_skills에 대해 설치 가능한 로컬 스킬 후보를 추천한다.

AgentRole: {agent.get("role")}
Goal: {reqs.get("goal")}
MissingSkills: {missing}
LocalSkillCatalog(JSON): {json.dumps(skill_catalog, ensure_ascii=False)}
{notebook_insight}

출력은 JSON만:
{{
  "suggestions": {{
    "missing_skill_id": ["candidate_skill_id_1", "candidate_skill_id_2"]
  }}
}}
"""
        suggestions: dict[str, list[str]] = {}
        try:
            res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            payload = safe_json_load(res.text)
            raw = payload.get("suggestions", {}) if isinstance(payload, dict) else {}
            if isinstance(raw, dict):
                for need, cands in raw.items():
                    k = safe_id(str(need))
                    values = [safe_id(str(c)) for c in (cands or []) if safe_id(str(c)) in idx]
                    if values:
                        suggestions[k] = list(dict.fromkeys(values))
        except Exception:
            suggestions = {}

        for need in missing:
            if need not in suggestions:
                fallback = self._fallback_match(need, idx)
                if fallback:
                    suggestions[need] = fallback

        all_candidates = []
        for arr in suggestions.values():
            for sid in arr:
                if sid not in all_candidates:
                    all_candidates.append(sid)

        targets: dict = {}
        for need in missing:
            ranked = []
            for sid in suggestions.get(need, []):
                item = idx.get(sid)
                if not item:
                    continue
                score, verify = self._score_candidate(need, item)
                ranked.append({
                    "candidate_skill_id": sid,
                    "candidate_name": item.get("name", sid),
                    "score": score,
                    "verification": verify,
                    "capabilities": item.get("capabilities", []),
                })
            ranked.sort(key=lambda x: x["score"], reverse=True)
            best = ranked[0] if ranked else None
            targets[need] = {
                "need_skill_id": need,
                "top_candidate": (best or {}).get("candidate_skill_id"),
                "top_score": (best or {}).get("score", 0),
                "verified": bool(best and best["verification"]["exists_skill_py"]),
                "candidates": ranked,
            }

        evidence_pack = {
            "generated_at": now_iso(),
            "agent_role": agent.get("role"),
            "goal": reqs.get("goal"),
            "targets": targets,
            "notebook_insight": notebook_insight # 결과에 포함
        }
        return {"suggestions": suggestions, "all_candidates": all_candidates, "evidence_pack": evidence_pack}

    def notify_candidates(self, candidates: list[str], idx: dict):
        if not candidates:
            print("\n[Himari] 설치 추천 후보가 없습니다.")
            return
        print("\n[Himari] 리서치 결과 - 설치 후보")
        for i, sid in enumerate(candidates, start=1):
            item = idx.get(sid, {})
            caps = item.get("capabilities", [])
            print(f"  {i}. {sid} | name={item.get('name', sid)} | capabilities={caps}")

    def search_external_and_install(self, needs: list[str], reqs: dict, registry) -> dict[str, str]:
        needs = [safe_id(str(n)) for n in (needs or []) if str(n).strip()]
        if not needs:
            return {}
        himari_cfg = read_yaml(os.path.join(AGENTS_DIR, "himari.yaml"))
        sig = get_random_signature(himari_cfg)
        print_agent_msg("Himari", f"외부 스킬 소스에서 설치 가능한 후보를 탐색합니다: {needs}", sig)
        installed = registry.resolve_and_install_external(needs, reqs=reqs)
        if installed:
            print(f"💡 [Himari] 외부 소스 설치 성공: {list(installed.keys())}")
        else:
            print("⏭️ [Himari] 외부 소스에서 설치 가능한 후보를 찾지 못했습니다.")
        return installed

# =============================================================================
# 5) Builder
# =============================================================================
class SandboxedBuilder:
    def __init__(self, mr: ModelRouter):
        self.mr = mr

    def build_skill(
        self,
        agent: dict,
        skill_name: str,
        reqs: dict,
        run_id: str,
        evidence_pack: dict,
    ) -> tuple[bool, str | None, dict]:
        skill_id = safe_id(skill_name)
        skill_dir = os.path.join(SKILLS_DIR, skill_id)
        os.makedirs(skill_dir, exist_ok=True)
        code_path = os.path.join(skill_dir, "skill.py")
        meta_path = os.path.join(skill_dir, "meta.yaml")

        run_dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(run_dir, exist_ok=True)

        evidence_targets = (evidence_pack or {}).get("targets", {}) if isinstance(evidence_pack, dict) else {}
        target_evidence = evidence_targets.get(skill_id)
        if not target_evidence:
            fail_meta = {
                "id": skill_id,
                "name": skill_name,
                "status": "disabled",
                "version": "0.1.0",
                "capabilities": [skill_name],
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "last_test_ok": False,
                "last_test_detail": {"ok": False, "reason": "missing_evidence_pack"},
            }
            write_yaml(meta_path, fail_meta)
            return False, None, fail_meta

        model = genai.GenerativeModel(self.mr.pick("builder"))
        base_prompt = f"""
?덈뒗 ?뚯씠???ㅽ궗 紐⑤뱢???묒꽦?쒕떎.
Skill: "{skill_name}"
AgentRole: {agent.get("role")}
Goal: {reqs.get("goal")}
Constraints: {reqs.get("constraints")}
Evidence(JSON): {json.dumps(target_evidence, ensure_ascii=False)}

?꾩닔:
- ?⑥닔 3媛? propose(ctx)->dict, apply(ctx)->dict, test(ctx)->dict(諛섎뱶??ok ???ы븿)
- ?곗씠???낅젰: ctx["data_dir"] ?꾨옒 ?뚯씪???쎈뒗??
- ?곗텧臾???? ctx["artifacts_dir"] ?꾨옒濡???ν빐???섏?留? 媛?ν븯硫?dict濡?諛섑솚.
湲덉?:
- os/sys/subprocess/shutil/importlib/pathlib/glob/ctypes ???ъ슜 湲덉?
- eval/exec/__import__/compile/input 湲덉?
異쒕젰:
- 留덊겕?ㅼ슫 ?놁씠 ?뚯씠??肄붾뱶留?
"""

        last = {"ok": False, "reason": "not_started"}
        for i in range(MAX_ITERATIONS):
            res = model.generate_content(base_prompt)
            code = strip_code_fences(res.text)

            ok, vios = quick_guard(code)
            if not ok:
                last = {"ok": False, "reason": "guard_block", "violations": vios}
                continue

            write_text(code_path, code)

            t_ok, t_json, t_err = run_isolated(code_path, timeout_sec=TEST_TIMEOUT_SEC)
            last = {"test_ok": t_ok, "test_json": t_json, "stderr": (t_err or "")[:500]}

            if t_ok:
                meta = {
                    "id": skill_id,
                    "name": skill_name,
                    "status": "active",
                    "version": "0.1.0",
                    "capabilities": [skill_name],
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "code_hash": sha256_text(code),
                    "last_test_ok": True,
                    "last_test_detail": t_json,
                }
                write_yaml(meta_path, meta)
                write_text(os.path.join(run_dir, f"{skill_id}_skill.py"), code)
                write_yaml(os.path.join(run_dir, f"{skill_id}_meta.yaml"), meta)
                return True, code_path, meta

        fail_meta = {
            "id": skill_id,
            "name": skill_name,
            "status": "disabled",
            "version": "0.1.0",
            "capabilities": [skill_name],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "last_test_ok": False,
            "last_test_detail": last,
        }
        write_yaml(meta_path, fail_meta)
        return False, None, fail_meta

    def get_model_priority_queue(self):
        # 1순위부터 순차적으로 시도할 모델 목록 (2026.02 Update)
        # model_utils를 통해 동적으로 가져오진 않지만, 캐시된 유효 모델을 우선순위에 따라 반환하도록 유도
        # 여기서는 단순화를 위해 best model 하나를 리스트로 반환하거나, Known list를 반환
        return [get_best_model(["gemini-2.0-flash", "gemini-1.5-flash"])]

# =============================================================================
# 6) Registry / Workflow / Git
# =============================================================================
class RegistryManager:
    def __init__(self):
        ensure_registry_files()
        self._normalize_registry_paths()

    def _read_registry(self) -> dict:
        reg = read_yaml(REGISTRY_PATH)
        if not isinstance(reg, dict):
            reg = {}
        reg.setdefault("skills", {})
        reg.setdefault("install_candidates", {})
        return reg

    def _write_registry(self, reg: dict):
        reg = reg if isinstance(reg, dict) else {}
        reg.setdefault("skills", {})
        reg.setdefault("install_candidates", {})
        write_yaml(REGISTRY_PATH, reg)

    def _tokenize(self, text: str) -> set[str]:
        return {t for t in safe_id(text).split("_") if t}

    def _score_need_match(self, need: str, candidate_text: str) -> int:
        need_tokens = self._tokenize(need)
        cand_tokens = self._tokenize(candidate_text)
        if not need_tokens or not cand_tokens:
            return 0
        overlap = len(need_tokens & cand_tokens)
        if overlap == 0:
            return 0
        base = overlap * 20
        if safe_id(need) == safe_id(candidate_text):
            base += 40
        return min(100, base)

    def _resolve_path(self, path_text: str) -> str | None:
        return resolve_existing_path(path_text)

    def _normalize_registry_paths(self):
        reg = self._read_registry()
        skills = reg.get("skills", {}) if isinstance(reg, dict) else {}
        changed = False
        for sid, item in list((skills or {}).items()):
            if not isinstance(item, dict):
                continue
            key = safe_id(str(sid))
            resolved_py, resolved_meta = resolve_skill_paths(key)
            if resolved_py:
                item["path"] = to_portable_path(resolved_py)
                changed = True
            elif item.get("path"):
                p = self._resolve_path(str(item.get("path")))
                if p:
                    item["path"] = to_portable_path(p)
                    changed = True
            if resolved_meta:
                item["meta_path"] = to_portable_path(resolved_meta)
                changed = True
            elif item.get("meta_path"):
                mp = self._resolve_path(str(item.get("meta_path")))
                if mp:
                    item["meta_path"] = to_portable_path(mp)
                    changed = True
        if changed:
            reg["skills"] = skills
            self._write_registry(reg)

        # Enforce portable relative paths for install_candidates as well.
        raw_cands = reg.get("install_candidates", {})
        normalized: dict = {}
        if isinstance(raw_cands, dict):
            for cid, item in raw_cands.items():
                sid = safe_id(str(cid))
                if isinstance(item, str):
                    rp = to_portable_path(item)
                    if is_portable_rel_path(rp):
                        normalized[sid] = rp
                        changed = True
                    continue
                if not isinstance(item, dict):
                    continue
                n_item = dict(item)
                p = str(item.get("path") or "").strip()
                if p:
                    rp = to_portable_path(p)
                    if not is_portable_rel_path(rp):
                        changed = True
                        continue
                    n_item["path"] = rp
                n_item["id"] = safe_id(str(item.get("id") or sid))
                normalized[sid] = n_item
                if str(item) != str(n_item):
                    changed = True
        elif isinstance(raw_cands, list):
            # Convert list shape to map keyed by id.
            for i, item in enumerate(raw_cands):
                if not isinstance(item, dict):
                    continue
                sid = safe_id(str(item.get("id") or f"cand_{i}"))
                n_item = dict(item)
                p = str(item.get("path") or "").strip()
                if p:
                    rp = to_portable_path(p)
                    if not is_portable_rel_path(rp):
                        changed = True
                        continue
                    n_item["path"] = rp
                n_item["id"] = sid
                normalized[sid] = n_item
                changed = True
        if raw_cands != normalized:
            reg["install_candidates"] = normalized
            changed = True
        if changed:
            self._write_registry(reg)

    def _iter_install_candidates(self) -> list[dict]:
        reg = self._read_registry()
        raw = reg.get("install_candidates", {})
        out: list[dict] = []
        if isinstance(raw, dict):
            for cid, item in raw.items():
                sid = safe_id(str(cid))
                if isinstance(item, str):
                    path = str(item).strip()
                    if not is_portable_rel_path(path):
                        continue
                    out.append({"id": sid, "path": path, "capabilities": [sid]})
                    continue
                if isinstance(item, dict):
                    path = str(item.get("path") or "").strip()
                    if path and not is_portable_rel_path(path):
                        continue
                    out.append(
                        {
                            "id": safe_id(str(item.get("id") or sid)),
                            "name": str(item.get("name") or sid),
                            "path": path,
                            "source_url": str(item.get("source_url") or ""),
                            "capabilities": [safe_id(str(x)) for x in (item.get("capabilities") or []) if str(x).strip()],
                        }
                    )
        elif isinstance(raw, list):
            for i, item in enumerate(raw):
                if not isinstance(item, dict):
                    continue
                sid = safe_id(str(item.get("id") or f"cand_{i}"))
                path = str(item.get("path") or "").strip()
                if path and not is_portable_rel_path(path):
                    continue
                out.append(
                    {
                        "id": sid,
                        "name": str(item.get("name") or sid),
                        "path": path,
                        "source_url": str(item.get("source_url") or ""),
                        "capabilities": [safe_id(str(x)) for x in (item.get("capabilities") or []) if str(x).strip()],
                    }
                )
        return out

    def _sync_external_sources(self):
        policies = read_project_policies()
        urls: list[str] = []
        pol_urls = policies.get("external_skill_sources", []) if isinstance(policies, dict) else []
        if isinstance(pol_urls, list):
            urls.extend([str(x).strip() for x in pol_urls if str(x).strip()])

        env_urls = [x.strip() for x in str(os.getenv("AGENT_EXTERNAL_SKILL_REPOS", "")).split(",") if x.strip()]
        urls.extend(env_urls)
        urls = list(dict.fromkeys(urls))
        if not urls:
            return

        for url in urls:
            repo_name = safe_id(os.path.basename(url).replace(".git", "")) or "external_repo"
            dst = os.path.join(EXTERNAL_CACHE_DIR, repo_name)
            try:
                if os.path.exists(dst):
                    subprocess.run(["git", "-C", dst, "pull", "--ff-only"], check=False, capture_output=True, text=True, timeout=20)
                else:
                    subprocess.run(["git", "clone", "--depth", "1", url, dst], check=False, capture_output=True, text=True, timeout=45)
            except Exception:
                continue

    def _scan_cache_candidates(self) -> list[dict]:
        out: list[dict] = []
        if not os.path.exists(EXTERNAL_CACHE_DIR):
            return out
        for root, _dirs, files in os.walk(EXTERNAL_CACHE_DIR):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                if fn.startswith("_") or fn.startswith("test_"):
                    continue
                py_path = os.path.join(root, fn)
                sid = safe_id(os.path.splitext(fn)[0])
                out.append({"id": sid, "path": py_path, "source": "external_cache"})
        return out

    def _install_skill_file(self, need_id: str, source_path: str, source_label: str = "external") -> tuple[bool, str]:
        sid = safe_id(need_id)
        if not sid:
            return False, "invalid_skill_id"
        src = self._resolve_path(source_path)
        if not src:
            return False, "source_not_found"

        skill_py: str | None = None
        if os.path.isdir(src):
            cand = os.path.join(src, "skill.py")
            if os.path.exists(cand):
                skill_py = cand
        elif os.path.isfile(src):
            if src.endswith(".py"):
                skill_py = src
            elif src.endswith(".yaml") or src.endswith(".yml"):
                data = read_yaml(src)
                p = self._resolve_path(str(data.get("path", "")))
                if p and p.endswith(".py") and os.path.exists(p):
                    skill_py = p
        if not skill_py:
            return False, "no_python_skill_file"

        target_dir = os.path.join(SKILLS_DIR, sid)
        os.makedirs(target_dir, exist_ok=True)
        target_py = os.path.join(target_dir, "skill.py")
        target_meta = os.path.join(target_dir, "meta.yaml")
        try:
            shutil.copy2(skill_py, target_py)
        except Exception as e:
            return False, f"copy_failed:{type(e).__name__}"

        meta = {
            "id": sid,
            "name": sid,
            "version": "1.0.0",
            "capabilities": [sid],
            "status": "active",
            "updated_at": now_iso(),
            "source": source_label,
            "source_path": skill_py,
        }
        write_yaml(target_meta, meta)

        reg = self._read_registry()
        reg.setdefault("skills", {})
        reg["skills"][sid] = {
            "id": sid,
            "name": sid,
            "status": "active",
            "version": "1.0.0",
            "capabilities": [sid],
            "path": to_portable_path(target_py),
            "meta_path": to_portable_path(target_meta),
            "updated_at": now_iso(),
            "last_test_ok": True,
        }
        self._write_registry(reg)
        lock_skill_state(sid, {"version": "1.0.0", "status": "active"})
        return True, sid

    def resolve_and_install_external(self, needs: list[str], reqs: dict | None = None) -> dict[str, str]:
        installed: dict[str, str] = {}
        needs = [safe_id(str(n)) for n in (needs or []) if str(n).strip()]
        if not needs:
            return installed

        # Stage 2-A: local external pools
        local_pool: list[dict] = []
        for n in needs:
            local_pool.append({"id": n, "path": os.path.join(SKILLS_DIR, "warehouse", n)})
            local_pool.append({"id": n, "path": os.path.join(SKILLS_DIR, "warehouse", f"{n}.py")})
            local_pool.append({"id": n, "path": os.path.join(SKILLS_DIR, "forge", f"{n}.py")})

        # Stage 2-B: registry install_candidates
        reg_pool = self._iter_install_candidates()

        # Stage 2-C: best-effort remote sync + cache scan
        self._sync_external_sources()
        cache_pool = self._scan_cache_candidates()

        for need in needs:
            if resolve_skill_paths(need)[0]:
                installed[need] = need
                continue

            chosen_path: str | None = None
            chosen_src = "external_local"

            # 1) exact local hits first
            for item in local_pool:
                if safe_id(item.get("id", "")) != need:
                    continue
                p = self._resolve_path(str(item.get("path", "")))
                if p:
                    chosen_path = p
                    break

            # 2) registry candidates (token score)
            if not chosen_path:
                best_score = -1
                for cand in reg_pool:
                    path = self._resolve_path(str(cand.get("path", "")))
                    if not path:
                        continue
                    corpus = " ".join(
                        [str(cand.get("id", "")), str(cand.get("name", ""))]
                        + [str(x) for x in (cand.get("capabilities") or [])]
                    )
                    sc = self._score_need_match(need, corpus)
                    if sc > best_score:
                        best_score = sc
                        chosen_path = path
                        chosen_src = "install_candidates"

            # 3) external cache (downloaded repos)
            if not chosen_path:
                best_score = -1
                for cand in cache_pool:
                    path = self._resolve_path(str(cand.get("path", "")))
                    if not path:
                        continue
                    sc = self._score_need_match(need, str(cand.get("id", "")))
                    if sc > best_score:
                        best_score = sc
                        chosen_path = path
                        chosen_src = str(cand.get("source", "external_cache"))

            if not chosen_path:
                continue
            ok, _msg = self._install_skill_file(need, chosen_path, source_label=chosen_src)
            if ok:
                installed[need] = need
        return installed

    def _quality_gate_policy(self) -> dict:
        qg = resolve_quality_gate_policy(read_project_policies())
        return {
            "default_stage_on_build": str(qg.get("default_stage_on_build", "candidate")),
            "auto_promote_sequence": [safe_id(str(s)) for s in (qg.get("auto_promote_sequence") or ["canary", "active"])],
            "installable_statuses": [safe_id(str(s)) for s in (qg.get("installable_statuses") or ["active"])],
        }

    def apply_quality_gate(self, meta: dict) -> dict:
        qg = self._quality_gate_policy()
        stage = safe_id(qg.get("default_stage_on_build", "candidate"))
        sequence = [s for s in qg.get("auto_promote_sequence", []) if s in ("candidate", "canary", "active")]
        
        # If it's a valid default stage, keep it instead of overwriting loops
        if stage not in ("candidate", "canary", "active"):
            stage = "candidate"
            
        # In a real pipeline, stage advancement happens progressively via tests.
        # Since this applies the INITIAL gate, we just lock it to the default
        # or the first step of the promotion sequence.
        if sequence and stage not in sequence:
            stage = sequence[0]
            
        patched = dict(meta or {})
        patched["status"] = stage
        patched["quality_stage"] = stage
        patched["quality_updated_at"] = now_iso()
        return patched

    def is_installable(self, skill_id: str) -> bool:
        lock = read_skill_lock()
        item = (lock.get("skills", {}) or {}).get(safe_id(skill_id), {})
        status = safe_id(str(item.get("status", "")))
        qg = self._quality_gate_policy()
        installable = set(qg.get("installable_statuses", ["active"]))
        return status in installable

    def ensure_lock_for_existing_skill(self, skill_id: str):
        sid = safe_id(skill_id)
        lock = read_skill_lock()
        if sid in (lock.get("skills", {}) or {}):
            return
        reg = read_yaml(REGISTRY_PATH)
        item = ((reg.get("skills", {}) if isinstance(reg, dict) else {}) or {}).get(sid, {})
        status = safe_id(str(item.get("status", "active") or "active"))
        lock_skill_state(sid, {"version": item.get("version", "1.0.0"), "status": status or "active"})

    def register_built(self, meta: dict, skill_dir: str):
        gated = self.apply_quality_gate(meta)
        reg = read_yaml(REGISTRY_PATH)
        reg.setdefault("skills", {})
        reg["skills"][gated["id"]] = {
            "id": gated["id"],
            "name": gated.get("name"),
            "status": gated.get("status"),
            "version": gated.get("version"),
            "capabilities": gated.get("capabilities", []),
            "path": to_portable_path(os.path.join(skill_dir, "skill.py")),
            "meta_path": to_portable_path(os.path.join(skill_dir, "meta.yaml")),
            "updated_at": now_iso(),
            "last_test_ok": bool(gated.get("last_test_ok", False)),
        }
        write_yaml(REGISTRY_PATH, reg)
        lock_skill_state(gated["id"], gated)

    def workflow_apply(self, metas: list[dict]):
        wf = read_yaml(WORKFLOW_PATH)
        wf.setdefault("capability_to_skill", {})
        mapping = wf["capability_to_skill"]
        for meta in metas:
            sid = meta["id"]
            for cap in meta.get("capabilities", []):
                k = safe_id(str(cap))
                mapping.setdefault(k, [])
                if sid not in mapping[k]:
                    mapping[k].append(sid)
        wf["updated_at"] = now_iso()
        write_yaml(WORKFLOW_PATH, wf)

class GitManager:
    def push_gate(self) -> bool:
        ans = input("\n?뵶 Git commit ?좊옒? (yes/no): ").strip().lower()
        return ans == "yes"

    def commit(self):
        # ?섍꼍留덈떎 ?ㅻⅤ??理쒖냼留?
        subprocess.run(["git", "add", "skills/", "runs/"], check=False)
        subprocess.run(["git", "commit", "-m", "feat: auto-generated skills"], check=False)

class AgentRunner:
    def __init__(self, model_router: ModelRouter):
        self.mr = model_router

    def _resolve_system_prompt(self, agent: dict) -> str:
        direct = str(agent.get("system_ko", "")).strip()
        if direct:
            return direct
        prompt_obj = agent.get("prompt", {}) if isinstance(agent.get("prompt"), dict) else {}
        nested = str(prompt_obj.get("system_ko", "")).strip()
        if nested:
            return nested
        legacy = str(agent.get("system_prompt", "")).strip()
        if legacy:
            return legacy
        return "당신은 유용한 AI 어시스턴트입니다."

    def _resolve_signature_lines(self, agent: dict) -> list[str]:
        lines = agent.get("signature_lines")
        if not lines and isinstance(agent.get("persona"), dict):
            lines = agent["persona"].get("signature_lines")
        if isinstance(lines, list):
            return [str(x) for x in lines if str(x).strip()]
        return []

    def _build_policy(self, agent: dict, loaded_skill_ids: list[str]) -> dict:
        rr = agent.get("runtime_rules", {}) if isinstance(agent, dict) else {}
        if not isinstance(rr, dict):
            rr = {}

        loaded = {safe_id(s) for s in loaded_skill_ids if safe_id(s)}
        declared = {safe_id(str(x)) for x in (agent.get("skills") or []) if str(x).strip()} if isinstance(agent, dict) else set()

        allowed_skills_raw = {safe_id(str(x)) for x in (rr.get("allowed_skills") or []) if str(x).strip()}
        approval_skills_raw = {safe_id(str(x)) for x in (rr.get("approval_required_skills") or []) if str(x).strip()}
        explicit_approval_tools = {safe_id(str(x)) for x in (rr.get("explicit_approval_required") or []) if str(x).strip()}
        allowed_tools = {safe_id(str(x)) for x in (rr.get("allowed_tools") or []) if str(x).strip()}
        approval_tools = {safe_id(str(x)) for x in (rr.get("approval_required_tools") or []) if str(x).strip()}

        known_local = loaded | declared
        allowed_local = allowed_skills_raw & known_local
        approval_local = approval_skills_raw & known_local

        default_deny = bool(rr.get("default_deny", False))
        enforce_allow = default_deny and bool(loaded or allowed_tools or allowed_local)

        # Safe baseline: when default_deny is on, allow only canonical skill entrypoints
        # unless explicitly opened via allowed_tools or allow_all_local.
        baseline_tools = {"propose", "apply", "test"}
        allow_all_local = bool(rr.get("allow_all_local", False))

        if default_deny and allowed_skills_raw and not allowed_local and not allowed_tools:
            unresolved = sorted([x for x in allowed_skills_raw if x and x not in known_local])
            if unresolved:
                # Fallback: Treat unresolved allowed_skills as allowed_tools (function names)
                allowed_tools.update(unresolved)

        if default_deny and approval_skills_raw and not approval_local and not approval_tools:
            unresolved_app = sorted([x for x in approval_skills_raw if x and x not in known_local])
            if unresolved_app:
                approval_tools.update(unresolved_app)

        return {
            "enforce_allow": enforce_allow,
            "allowed_local": allowed_local,
            "approval_local": approval_local,
            "allowed_tools": allowed_tools,
            "approval_tools": approval_tools | explicit_approval_tools,
            "loaded_local": loaded,
            "baseline_tools": baseline_tools,
            "allow_all_local": allow_all_local,
        }

    def _is_tool_allowed(self, policy: dict, skill_id: str, tool_name: str) -> bool:
        if not policy.get("enforce_allow", False):
            return True

        sid = safe_id(skill_id)
        tname = safe_id(tool_name)
        allowed_tools = policy.get("allowed_tools", set())
        baseline_tools = policy.get("baseline_tools", {"propose", "apply", "test"})
        allow_all_local = bool(policy.get("allow_all_local", False))
        allowed_local = policy.get("allowed_local", set())

        # Global tool allow-list has highest priority.
        if tname and tname in allowed_tools:
            return True

        # Explicitly allowed local skills unlock all their functions.
        if sid and sid in allowed_local:
            return True

        # Safe fallback: loaded skills can execute canonical entrypoints only
        # when no explicit allow-list was configured.
        if (
            sid
            and sid in policy.get("loaded_local", set())
            and tname in baseline_tools
            and not allowed_local
            and not allowed_tools
        ):
            return True

        return False

    def _requires_tool_approval(self, policy: dict, skill_id: str, tool_name: str) -> bool:
        sid = safe_id(skill_id)
        tname = safe_id(tool_name)
        if sid and sid in policy.get("approval_local", set()):
            return True
        if tname and tname in policy.get("approval_tools", set()):
            return True
        return False

    def _ask_tool_approval(self, fname: str, skill_id: str) -> bool:
        try:
            print("\n[승인 요청]")
            print(f"- 도구: {fname}")
            print(f"- 스킬: {skill_id if skill_id else 'unknown'}")
            ans = input("위 도구 실행을 허용할까요? (yes/no): ").strip().lower()
            return ans in ("y", "yes")
        except Exception:
            return False

    def _list_approval_required_tools(self, tool_functions: list, policy: dict):
        needs = []
        for fn in tool_functions:
            fname = str(getattr(fn, "__name__", "unknown"))
            sid = safe_id(str(getattr(fn, "_skill_id", "")))
            if self._requires_tool_approval(policy, sid, fname):
                needs.append((sid or "unknown", fname))
        if not needs:
            return
        print("\n[정책 안내] 사용자 승인이 필요한 도구 목록")
        for sid, fname in needs:
            print(f"- 스킬 `{sid}` / 도구 `{fname}`")
        print("실행 시마다 yes/y로 승인해야 진행됩니다.")

    def _make_tool_wrapper(self, func, ctx: dict):
        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        takes_ctx = bool(params) and params[0].name == "ctx"

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if takes_ctx and len(params) == 1:
                # apply(ctx) style: merge model args into context
                call_ctx = dict(ctx)
                call_ctx.update(kwargs)
                return func(call_ctx)
            if takes_ctx:
                return func(ctx, *args, **kwargs)
            return func(*args, **kwargs)

        return wrapper

    def _build_tool_functions(self, modules: list, ctx: dict, policy: dict) -> list:
        tool_functions = []
        for mod in modules:
            sid = safe_id(str(getattr(mod, "__skill_id__", "")))
            for attr_name in dir(mod):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(mod, attr_name)
                if not inspect.isfunction(attr):
                    continue
                # Expose only functions declared in the skill module itself.
                if getattr(attr, "__module__", "") != mod.__name__:
                    continue
                if not self._is_tool_allowed(policy, sid, attr_name):
                    print(f"⛔ [Policy] 도구 차단: {sid}.{attr_name}")
                    continue
                wrapped = self._make_tool_wrapper(attr, ctx)
                wrapped.__name__ = attr.__name__
                wrapped.__doc__ = attr.__doc__
                setattr(wrapped, "_skill_id", sid)
                tool_functions.append(wrapped)
        return tool_functions

    def _agent_prefers_codex(self, agent: dict, model_name: str) -> bool:
        if is_codex_model(model_name):
            return True
        if is_claude_model(model_name):
            # Claude 설정 시에도 실행 가능한 Codex 경로를 우선 시도한다.
            return True
        engine = str(agent.get("engine", "")).strip().lower()
        if "codex" in engine:
            return True
        runtime_rules = agent.get("runtime_rules", {}) if isinstance(agent, dict) else {}
        if bool(runtime_rules.get("codex_enabled", False)):
            return True
        directive = str(runtime_rules.get("codex_directive", "")).strip().lower()
        return "codex" in directive

    def _run_with_codex(self, model_name: str, sys_prompt: str, task_input: str, tool_functions: list) -> bool:
        if not OPENAI_API_KEY:
            print("⚠️ [Runner] OPENAI_API_KEY가 없어 Codex 경로를 사용할 수 없습니다.")
            return False
        if OpenAI is None:
            print("⚠️ [Runner] openai 패키지가 없어 Codex 경로를 사용할 수 없습니다.")
            return False

        codex_model = model_name if is_codex_model(model_name) else "codex-5.3"
        tools = ", ".join(sorted({t.__name__ for t in tool_functions})) if tool_functions else "none"
        prompt = (
            f"{sys_prompt}\n\n"
            f"[Task]\n{task_input}\n\n"
            f"[Available Tools]\n{tools}\n"
            "도구 호출은 현재 Codex 경로에서 비활성화되어 있으니, 실행 가능한 지시와 설계안을 우선 제시하세요."
        )

        client = OpenAI(api_key=OPENAI_API_KEY)
        for i in range(3):
            try:
                resp = client.responses.create(model=codex_model, input=prompt)
                text = getattr(resp, "output_text", "") or ""
                if not text:
                    try:
                        chunks = []
                        for item in getattr(resp, "output", []) or []:
                            if getattr(item, "type", "") != "message":
                                continue
                            for c in getattr(item, "content", []) or []:
                                c_type = getattr(c, "type", "")
                                if c_type in ("output_text", "text"):
                                    chunks.append(getattr(c, "text", ""))
                        text = "\n".join([c for c in chunks if c])
                    except Exception:
                        text = ""

                if text.strip():
                    print(f"🤖 {text.strip()}")
                    return True
                return False
            except Exception as e:
                msg = str(e).lower()
                if "429" in msg or "rate" in msg or "quota" in msg:
                    wait = 5 * (i + 1)
                    print(f"⏳ [Quota] Codex API 사용량 제한. {wait}초 대기 중... ({i+1}/3)")
                    time.sleep(wait)
                    continue
                print(f"⚠️ [Runner] Codex 실행 오류: {e}")
                return False
        return False

    def load_skills(self, agent: dict) -> list:
        # Legacy support
        loaded_skills = []
        skill_ids = agent.get("skills", [])
        for sid in skill_ids:
            sid = safe_id(str(sid))
            skill_py, _skill_meta = resolve_skill_paths(sid)
            if not skill_py:
                 continue
            try:
                spec = importlib.util.spec_from_file_location(f"skills.{sid}", skill_py)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[f"skills.{sid}"] = module
                    spec.loader.exec_module(module)
                    setattr(module, "__skill_id__", sid)
                    loaded_skills.append(module)
                    print(f"✅ [Runner] 스킬 로드 성공: {sid}")
            except Exception as e:
                print(f"⚠️ [Runner] 스킬 로드 실패 ({sid}): {e}")
        return loaded_skills

    def build_tool_registry(self, module_list: list, ctx: dict, policy: dict) -> ToolRegistry:
        """Adapts legacy modules into the precise V2 Tool Registry"""
        registry = ToolRegistry(agent_name=ctx["agent"].get("name", "unknown"), factory_root=BASE_DIR)
        
        legacy_tool_funcs = self._build_tool_functions(module_list, ctx, policy)
        for fn in legacy_tool_funcs:
            registry.mount_tool(fn.__name__, fn)
            
        return registry

    def run(self, agent: dict, task_input: str, run_id: str | None = None):
        print(f"\n🚀 [Runner] 에이전트 실행 시작: {agent.get('name')}")
        started = time.time()
        run_id = run_id or f"run_{int(started)}"
        run_dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(run_dir, exist_ok=True)
        transcript = []

        def _append_trace(kind: str, payload: dict):
            transcript.append({
                "ts": now_iso(),
                "kind": str(kind),
                "payload": payload if isinstance(payload, dict) else {"value": str(payload)},
            })

        def _flush_trace(result: dict):
            data = {
                "run_id": run_id,
                "project_id": PROJECT_ID,
                "agent_name": str(agent.get("name", "")),
                "agent_role": str(agent.get("role", "")),
                "task": str(task_input or ""),
                "transcript": transcript,
                "result": result,
                "updated_at": now_iso(),
            }
            _safe_write_json(os.path.join(run_dir, "chat_trace.json"), data)
        approval_rejects = 0
        
        # 1. Load Skills
        modules = self.load_skills(agent)
        
        # 2. Context Setup
        # Inject context into modules if they have a 'ctx' global or similar
        ctx = {
            "agent": agent,
            "data_dir": DATA_DIR,
            "artifacts_dir": ARTIFACTS_DIR
        }
        ok_ctx, msg_ctx = validate_context_with_schema(ctx)
        if not ok_ctx:
            print(f"⚠️ [ContextSchema] 컨텍스트 검증 실패: {msg_ctx}")
            print("에이전트 실행을 중단합니다.")
            result = {"ok": False, "reason": f"context_schema:{msg_ctx}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "context_schema", "message": str(msg_ctx)})
            _flush_trace(result)
            return result
        loaded_skill_ids = [safe_id(str(getattr(m, "__skill_id__", ""))) for m in modules]
        policy = self._build_policy(agent, loaded_skill_ids)
        registry = self.build_tool_registry(modules, ctx, policy)
        tool_functions = registry.get_active_tools()
        self._list_approval_required_tools(tool_functions, policy)
        
        # Continuation Hook Enforcement
        enforcer = TodoContinuationEnforcer()
        if not enforcer.pre_execute({"intent": "refactoring", "has_approved_plan": True}): # Hardcoded mock context for now
            result = {"ok": False, "reason": "continuation_hook_blocked"}
            _flush_trace(result)
            return result

        # 3. Chat Session
        model_name = self.mr.pick("chat") or "gemini-2.0-flash"
        
        # System Prompt construction
        sys_prompt = self._resolve_system_prompt(agent)
        
        # Proactive Memory Instruction
        skill_ids = [safe_id(str(s)) for s in agent.get("skills", [])]
        if "core_memory" in skill_ids:
            sys_prompt += (
                "\n\n[Memory Instruction]\n"
                "당신은 `core_memory` 스킬을 장착하고 있습니다.\n"
                "대화 중 **중요한 정보**(프로젝트 명세, 사용자 선호, 일정, 결정 사항 등)가 등장하면, "
                "사용자가 명시적으로 '기억해'라고 말하지 않아도 `core_memory.store` 도구를 사용하여 **스스로 저장**하세요.\n"
                "저장할 때는 맥락에 맞는 적절한 키(key)와 카테고리(category)를 판단하여 저장합니다."
            )

        sigs = self._resolve_signature_lines(agent)
        if sigs:
            import random
            greeting = random.choice(sigs)
            print(f"💬 [Agent] {greeting}")
            sys_prompt += f"\n\n[Signature]\n{greeting}"

        if self._agent_prefers_codex(agent, model_name):
            codex_ok = self._run_with_codex(model_name, sys_prompt, task_input, tool_functions)
            if codex_ok:
                print("✅ Agent Execution Finished.")
                result = {"ok": True, "reason": "codex", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
                _append_trace("assistant", {"channel": "codex", "note": "codex path completed"})
                _flush_trace(result)
                return result
            print("⚠️ [Runner] Codex 경로 실패, Gemini 경로로 폴백합니다.")

        if not GOOGLE_API_KEY:
            print("⚠️ [Runner] GOOGLE_API_KEY가 없어 Gemini 경로를 사용할 수 없습니다.")
            print("에이전트가 응답을 생성하지 못했습니다.")
            result = {"ok": False, "reason": "missing_google_api_key", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "bootstrap", "message": "missing_google_api_key"})
            _flush_trace(result)
            return result

        gemini_model = model_name if not (is_codex_model(model_name) or is_claude_model(model_name)) else get_best_model(["gemini-2.0-flash", "gemini-1.5-flash"])
        model = genai.GenerativeModel(gemini_model, tools=tool_functions)

        chat = model.start_chat(history=[
            {"role": "user", "parts": [sys_prompt + f"\n\nTask: {task_input}"]}
        ])
        _append_trace("user", {"text": f"Task: {task_input}"})
        _append_trace("system", {"model": str(gemini_model), "skills": [str(s) for s in skill_ids]})
        
        # Helper for safe sending
        def safe_send(msg, **kwargs):
            max_retries = 3
            for i in range(max_retries):
                try:
                    return chat.send_message(msg, **kwargs)
                except Exception as e:
                    if "429" in str(e) or "quota" in str(e).lower() or "resource exhausted" in str(e).lower():
                        wait = 5 * (i + 1) # 5s, 10s, 15s
                        print(f"⏳ [Quota] API 사용량 초과 (429). {wait}초 대기 중... ({i+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    raise e
            raise Exception("API 호출 실패 (Quota Exceeded)")

        try:
            # We send an empty message to trigger the model to start working
            response = safe_send("작업을 시작해주세요. 필요한 도구가 있다면 사용하세요.", tool_config={'function_calling_config': 'AUTO'})
            
            # Basic ReAct Loop
            for _ in range(10): # Max 10 turns
                if not response.parts:
                    break
                part = response.parts[0]
                
                # 1. Output Text
                if part.text:
                    print(f"🤖 {part.text}")
                    _append_trace("assistant", {"text": str(part.text)})
                    # If model thinks it's done or asking question, we might stop
                    # But if it also has function call (rare in Gemini part[0]), check that.
                
                # 2. Function Call
                if part.function_call:
                    fc = part.function_call
                    fname = fc.name
                    fargs = dict(fc.args)
                    print(f"🛠️ [Tool] {fname}({fargs})")
                    _append_trace("tool_call", {"name": str(fname), "args": fargs})
                    
                    # Find tool wrapper
                    tool_func = next((t for t in tool_functions if t.__name__ == fname), None)
                    if tool_func:
                        try:
                            skill_id = safe_id(str(getattr(tool_func, "_skill_id", "")))
                            if self._requires_tool_approval(policy, skill_id, fname):
                                if not self._ask_tool_approval(fname, skill_id):
                                    print(f"⏭️ [Policy] 사용자 미승인으로 도구 실행을 건너뜁니다: {fname}")
                                    approval_rejects += 1
                                    _append_trace("tool_reject", {"name": str(fname), "skill_id": str(skill_id)})
                                    response = safe_send("해당 도구는 승인되지 않았습니다. 다른 방법으로 진행하세요.")
                                    continue
                            # Execute
                            res_obj = tool_func(**fargs)
                            print(f"  -> Result: {str(res_obj)[:100]}...")
                            _append_trace("tool_result", {"name": str(fname), "result": str(res_obj)[:800]})
                            
                            # Send result back
                            response = safe_send(
                                genai.prototypes.Part(function_response=genai.prototypes.FunctionResponse(
                                    name=fname,
                                    response={'result': res_obj}
                                ))
                            )
                            continue # Continue loop with new response
                        except Exception as e:
                            print(f"⚠️ Tool Execution Error: {e}")
                            _append_trace("error", {"stage": "tool_execution", "tool": str(fname), "message": str(e)})
                            break
                    else:
                        print(f"⚠️ Tool not found: {fname}")
                        _append_trace("error", {"stage": "tool_lookup", "tool": str(fname), "message": "not_found"})
                        break
                
                # If no function call and simple text, we assume turn is done for this prompt
                if not part.function_call:
                    break
            
            print("✅ Agent Execution Finished.")
            result = {"ok": True, "reason": "gemini", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _flush_trace(result)
            return result

        except Exception as e:
            print(f"⚠️ [Runner] 실행 중 오류: {e}")
            # Fallback output
            print("에이전트가 응답을 생성하지 못했습니다.")
            result = {"ok": False, "reason": f"runner_error:{type(e).__name__}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "runner", "message": str(e)})
            _flush_trace(result)
            return result

# =============================================================================
# 7) Factory
# =============================================================================
class AgentFactory:
    def __init__(self):
        self.mr = ModelRouter()
        self.agent_mgr = AgentManager(self.mr)
        self.req = RequirementAnalyzer(self.mr)
        self.research = HimariResearchAgent(self.mr)
        self.builder = SandboxedBuilder(self.mr)
        self.registry = RegistryManager()
        self.git = GitManager()
        self.runner = AgentRunner(self.mr) # Added Runner

    def _missing_local_skill_files(self, agent: dict) -> list[str]:
        missing: list[str] = []
        for sid_raw in (agent.get("skills") or []):
            sid = safe_id(str(sid_raw))
            if not sid:
                continue
            skill_py, _meta = resolve_skill_paths(sid)
            if not skill_py:
                missing.append(sid)
        return list(dict.fromkeys(missing))

    def _read_autonomy_policy(self) -> dict:
        policies = read_project_policies()
        ap = policies.get("autonomy", {}) if isinstance(policies.get("autonomy"), dict) else {}
        return {
            "max_stage_retries": int(ap.get("max_stage_retries", 2) or 2),
            "strict_quality_gate": bool(ap.get("strict_quality_gate", True)),
            "stop_on_stage_failure": bool(ap.get("stop_on_stage_failure", True)),
        }

    def _read_approval_policy(self) -> dict:
        policies = read_project_policies()
        ap = policies.get("approval_policy", {}) if isinstance(policies.get("approval_policy"), dict) else {}
        return {
            "require_skill_change_approval": bool(ap.get("require_skill_change_approval", False))
        }

    def _ask_skill_change_approval(self, role_spec: str, skills: list[str], action: str) -> bool:
        if not skills:
            return True
        print("\n[승인 요청] 스킬 변경")
        print(f"- 대상 에이전트: {role_spec}")
        print(f"- 작업: {action}")
        print(f"- 스킬 목록: {skills}")
        ans = input("위 스킬 변경을 허용할까요? (yes/no): ").strip().lower()
        return ans in ("y", "yes")

    def _create_workflow_state(self, run_id: str, workflow_path: str, stages: list, base_roles: list[str]) -> str:
        run_dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(run_dir, exist_ok=True)
        state_path = os.path.join(run_dir, "state.json")
        data = {
            "run_id": run_id,
            "project_id": PROJECT_ID,
            "workflow_path": workflow_path,
            "status": "running",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "base_roles": base_roles,
            "stages": [
                {
                    "id": str(s.get("id", "STAGE")),
                    "name": str(s.get("name", s.get("id", "STAGE"))),
                    "status": "pending",
                    "attempts": 0,
                    "last_error": "",
                    "updated_at": now_iso(),
                }
                for s in stages
            ],
        }
        _safe_write_json(state_path, data)
        return state_path

    def _update_workflow_state(self, state_path: str, stage_id: str, status: str, attempts: int = 0, last_error: str = ""):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        for s in data.get("stages", []):
            if str(s.get("id")) == str(stage_id):
                s["status"] = status
                if attempts:
                    s["attempts"] = attempts
                if last_error:
                    s["last_error"] = last_error[:300]
                s["updated_at"] = now_iso()
                break
        data["updated_at"] = now_iso()
        _safe_write_json(state_path, data)

    def _finish_workflow_state(self, state_path: str, status: str):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        data["status"] = status
        data["updated_at"] = now_iso()
        _safe_write_json(state_path, data)

    def run(self, task_input: str, role_spec: str = "General", enable_build: bool = False):
        run_id = f"run_{int(time.time())}"
        print(f"\nRUN={run_id}")
        print(f"- Role: {role_spec}")
        print(f"- Task: {task_input}")

        agent = self.agent_mgr.get_or_create(role_spec)
        reqs = self.req.analyze(agent, task_input)
        file_missing = self._missing_local_skill_files(agent)

        skills = reqs.get("missing_skills", [])
        initial_targets = list(dict.fromkeys([safe_id(s) for s in skills] + file_missing))

        skipped_build_targets: list[str] = []
        if initial_targets and not enable_build:
            skipped_build_targets = list(initial_targets)
            print(f"\n[RunOnly] build disabled, skipping: {skipped_build_targets}")

        if initial_targets and enable_build:
            print(f"\n[Build] Needed skills: {initial_targets}")
            built_metas: list[dict] = []

            research = self.research.research(agent, reqs, build_targets=initial_targets)
            evidence_pack = research.get("evidence_pack", {}) if isinstance(research, dict) else {}
            targets = evidence_pack.get("targets", {}) if isinstance(evidence_pack, dict) else {}

            reusable: list[str] = []
            resolved_needs: set[str] = set()
            for need in initial_targets:
                target = targets.get(need, {}) if isinstance(targets, dict) else {}
                top_sid = safe_id(str(target.get("top_candidate", "")))
                verified = bool(target.get("verified", False))
                if top_sid and verified:
                    reusable.append(top_sid)
                    resolved_needs.add(need)

            reusable = list(dict.fromkeys(reusable))
            if reusable:
                for sid in reusable:
                    self.registry.ensure_lock_for_existing_skill(sid)
                installable_reuse = [sid for sid in reusable if self.registry.is_installable(sid)]
                blocked_reuse = [sid for sid in reusable if sid not in installable_reuse]
                if blocked_reuse:
                    print(f"[QualityGate] install blocked: {blocked_reuse}")
                approval_policy = self._read_approval_policy()
                allow_skill_change = True
                if installable_reuse and approval_policy.get("require_skill_change_approval", False):
                    allow_skill_change = self._ask_skill_change_approval(role_spec, installable_reuse, "reuse skill install")
                installed = self.agent_mgr.install_skills(role_spec, installable_reuse) if (installable_reuse and allow_skill_change) else []
                if installable_reuse and not allow_skill_change:
                    print("[Approval] reuse install skipped by user.")
                print(f"[Factory] reused install: {installable_reuse} -> agent.skills={installed}")
                agent = self.agent_mgr.get_or_create(role_spec)

            unresolved = [need for need in initial_targets if need not in resolved_needs]

            unresolved_for_external = []
            for need in unresolved:
                target = targets.get(need, {}) if isinstance(targets, dict) else {}
                cands = target.get("candidates", []) if isinstance(target, dict) else []
                if isinstance(cands, list) and cands:
                    unresolved_for_external.append(need)
            if unresolved_for_external:
                print(f"[Factory] external lookup targets: {unresolved_for_external}")
                ext_installed_map = self.research.search_external_and_install(
                    unresolved_for_external, reqs=reqs, registry=self.registry
                )
                ext_skill_ids = list(dict.fromkeys([safe_id(str(sid)) for sid in ext_installed_map.values() if str(sid).strip()]))
                if ext_skill_ids:
                    installable_ext = [sid for sid in ext_skill_ids if self.registry.is_installable(sid)]
                    blocked_ext = [sid for sid in ext_skill_ids if sid not in installable_ext]
                    if blocked_ext:
                        print(f"[QualityGate] external install blocked: {blocked_ext}")
                    approval_policy = self._read_approval_policy()
                    allow_skill_change = True
                    if installable_ext and approval_policy.get("require_skill_change_approval", False):
                        allow_skill_change = self._ask_skill_change_approval(role_spec, installable_ext, "external skill install")
                    installed = self.agent_mgr.install_skills(role_spec, installable_ext) if (installable_ext and allow_skill_change) else []
                    if installable_ext and not allow_skill_change:
                        print("[Approval] external install skipped by user.")
                    print(f"[Factory] external install: {installable_ext} -> agent.skills={installed}")
                    for need in unresolved_for_external:
                        if need in ext_installed_map:
                            resolved_needs.add(need)
                    agent = self.agent_mgr.get_or_create(role_spec)
                else:
                    print("[Factory] no installable external candidates.")

            unresolved = [need for need in initial_targets if need not in resolved_needs]
            if unresolved:
                print(f"[Factory] new build targets: {unresolved}")

            built_skill_ids: list[str] = []
            for need in unresolved:
                ok, _code_path, meta = self.builder.build_skill(
                    agent=agent,
                    skill_name=need,
                    reqs=reqs,
                    run_id=run_id,
                    evidence_pack=evidence_pack,
                )
                if ok:
                    sid = safe_id(str(meta.get("id", need)))
                    skill_dir = os.path.join(SKILLS_DIR, sid)
                    self.registry.register_built(meta, skill_dir)
                    built_metas.append(meta)
                    built_skill_ids.append(sid)
                    print(f"[Factory] build success: {sid}")
                else:
                    print(f"[Factory] build failed: {need} | detail={meta.get('last_test_detail')}")

            if built_metas:
                self.registry.workflow_apply(built_metas)

            if built_skill_ids:
                installable_new = [sid for sid in built_skill_ids if self.registry.is_installable(sid)]
                blocked_new = [sid for sid in built_skill_ids if sid not in installable_new]
                if blocked_new:
                    print(f"[QualityGate] new install blocked: {blocked_new}")
                approval_policy = self._read_approval_policy()
                allow_skill_change = True
                if installable_new and approval_policy.get("require_skill_change_approval", False):
                    allow_skill_change = self._ask_skill_change_approval(role_spec, installable_new, "new skill install")
                installed = self.agent_mgr.install_skills(role_spec, installable_new) if (installable_new and allow_skill_change) else []
                if installable_new and not allow_skill_change:
                    print("[Approval] new install skipped by user.")
                print(f"[Factory] new install: {installable_new} -> agent.skills={installed}")
                agent = self.agent_mgr.get_or_create(role_spec)

        try:
            run_metrics = self.runner.run(agent, task_input, run_id=run_id) or {}
        except TypeError as e:
            if "unexpected keyword argument 'run_id'" in str(e):
                run_metrics = self.runner.run(agent, task_input) or {}
            else:
                raise

        append_dashboard_run(
            {
                "ts": now_iso(),
                "type": "single_run",
                "project_id": PROJECT_ID,
                "role": role_spec,
                "task": (task_input or "")[:300],
                "skills_loaded": list(agent.get("skills", []) if isinstance(agent, dict) else []),
                "ok": bool(run_metrics.get("ok", False)),
                "reason": str(run_metrics.get("reason", "")),
                "latency_ms": int(run_metrics.get("latency_ms", 0) or 0),
                "approval_rejects": int(run_metrics.get("approval_rejects", 0) or 0),
                "build_enabled": bool(enable_build),
                "missing_skills_detected": skipped_build_targets,
            }
        )
        return {
            "run_id": run_id,
            "ok": bool(run_metrics.get("ok", False)),
            "reason": str(run_metrics.get("reason", "")),
            "latency_ms": int(run_metrics.get("latency_ms", 0) or 0),
            "approval_rejects": int(run_metrics.get("approval_rejects", 0) or 0),
            "build_enabled": bool(enable_build),
            "missing_skills_detected": skipped_build_targets,
        }

    def run_workflow(self, task_input: str, workflow_path: str | None = None, role_specs: list[str] | None = None):
        if not workflow_path:
            policies = read_project_policies()
            wf_cfg = policies.get("workflow", {}) if isinstance(policies, dict) else {}
            default_tpl = str(wf_cfg.get("default_template", "")).strip()
            if default_tpl:
                cand = os.path.join(BASE_DIR, default_tpl)
                workflow_path = cand if os.path.exists(cand) else PROJECT_WORKFLOW_PATH
            else:
                workflow_path = PROJECT_WORKFLOW_PATH

        wf = read_yaml(workflow_path)
        if not wf:
            print(f"⚠️ [Workflow] 워크플로우를 읽을 수 없습니다: {workflow_path}")
            return

        owner = str(wf.get("owner_agent", "")).strip()
        stages = wf.get("stages", []) if isinstance(wf.get("stages"), list) else []
        picked_roles = [r.strip() for r in (role_specs or []) if str(r).strip()]
        if not picked_roles:
            picked_roles = [owner] if owner else ["General"]

        if not stages:
            stages = [{"id": "MAIN", "name": "Main", "objective": task_input}]

        policies = read_project_policies()
        role_map = {}
        if isinstance(policies, dict):
            wf_cfg = policies.get("workflow", {}) if isinstance(policies.get("workflow"), dict) else {}
            role_map = wf_cfg.get("role_map", {}) if isinstance(wf_cfg.get("role_map"), dict) else {}

        print(f"\n🧭 [Workflow] 시작: {workflow_path}")
        print(f"👥 [Workflow] 대상 에이전트: {picked_roles}")
        workflow_run_id = f"wf_{int(time.time())}"
        state_path = self._create_workflow_state(workflow_run_id, workflow_path, stages, picked_roles)
        autonomy = self._read_autonomy_policy()
        max_stage_retries = max(1, int(autonomy.get("max_stage_retries", 2)))
        strict_quality_gate = bool(autonomy.get("strict_quality_gate", True))
        stop_on_stage_failure = bool(autonomy.get("stop_on_stage_failure", True))
        run_count = 0
        failed = False
        for stage in stages:
            sid = str(stage.get("id", "STAGE"))
            sname = str(stage.get("name", sid))
            objective = str(stage.get("objective", "")).strip()
            mapped_roles = role_map.get(sid)
            stage_roles = [r.strip() for r in mapped_roles if str(r).strip()] if isinstance(mapped_roles, list) else picked_roles
            stage_task = (
                f"{task_input}\n"
                f"[Workflow Stage] id={sid}, name={sname}\n"
                f"[Stage Objective] {objective if objective else 'N/A'}"
            )
            print(f"\n📍 [Workflow] Stage {sid}: {sname}")
            self._update_workflow_state(state_path, sid, "in_progress")
            stage_ok = True
            stage_error = ""
            stage_attempts = 0
            for role in stage_roles:
                print(f"🤝 [Workflow] 실행 에이전트: {role}")
                role_ok = False
                role_reason = ""
                for attempt in range(1, max_stage_retries + 1):
                    stage_attempts = max(stage_attempts, attempt)
                    result = self.run(task_input=stage_task, role_spec=role) or {}
                    run_count += 1
                    role_ok = bool(result.get("ok", False))
                    role_reason = str(result.get("reason", ""))
                    if role_ok:
                        break
                    print(f"⚠️ [Workflow] 재시도 예정: stage={sid}, role={role}, attempt={attempt}/{max_stage_retries}, reason={role_reason}")
                if not role_ok:
                    stage_ok = False
                    stage_error = f"role={role}, reason={role_reason}"
                    print(f"❌ [Workflow] 단계 실패: {stage_error}")
                    break
            if stage_ok:
                self._update_workflow_state(state_path, sid, "completed", attempts=stage_attempts)
            else:
                self._update_workflow_state(state_path, sid, "failed", attempts=stage_attempts, last_error=stage_error)
                failed = True
                if strict_quality_gate or stop_on_stage_failure:
                    print(f"🛑 [QualityGate] Stage `{sid}` 실패로 다음 단계를 중단합니다.")
                    break

        self._finish_workflow_state(state_path, "failed" if failed else "completed")
        append_dashboard_run(
            {
                "ts": now_iso(),
                "type": "workflow_run",
                "project_id": PROJECT_ID,
                "workflow_path": workflow_path,
                "stage_count": len(stages),
                "run_count": run_count,
                "base_roles": picked_roles,
                "ok": not failed,
                "state_path": state_path,
            }
        )
    
# =============================================================================
# Example
# =============================================================================
if __name__ == "__main__":
    AgentFactory().run(
        task_input="Check current skills",
        role_spec="General",
    )





