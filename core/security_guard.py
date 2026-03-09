"""
core/security_guard.py
======================
코드 보안 검사 + 격리 실행 전담 모듈.
core/utils.py 에서 추출.
"""

import os
import ast
import time
import json
import subprocess
import sys

from core.config_paths import DATA_DIR, ARTIFACTS_DIR
from core.providers.registry import strip_engine_api_keys
from core.file_io import write_text
from core.executor import run_skill_safely

# =============================================================================
# Security Constants
# =============================================================================
MAX_ITERATIONS = 3
TEST_TIMEOUT_SEC = max(1, int(str(os.getenv("AGENT_TEST_TIMEOUT_SEC", "10") or "10").strip()))
CHILD_ENV_PASSTHROUGH = {
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
}
BANNED_IMPORT_TOPS = {
    "os", "sys", "subprocess", "shutil", "importlib",
    "pathlib", "glob", "ctypes",
    "multiprocessing", "threading", "concurrent", "asyncio",
}
BANNED_CALLS = {"eval", "exec", "__import__", "compile", "input"}


# =============================================================================
# LLM Retry Helper
# =============================================================================
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
# Code Security Guard
# =============================================================================
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


# =============================================================================
# Isolated Skill Execution (Sandbox)
# =============================================================================
def run_isolated(skill_py_path: str, timeout_sec: int = 10) -> tuple[bool, dict, str]:
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
    env = {k: os.environ.get(k) for k in CHILD_ENV_PASSTHROUGH if os.environ.get(k)}
    return strip_engine_api_keys(env)
