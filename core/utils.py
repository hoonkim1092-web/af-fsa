"""
core/utils.py
=============
범용 유틸리티 + 하위 호환 재수출 허브.

[모듈화 2026-03-01]
기능별 전담 모듈로 분해:
  - core.file_io        : YAML/Text 파일 I/O + 캐시
  - core.security_guard : 코드 보안 검사 + 격리 실행
  - core.dashboard      : 대시보드 JSON 관리
  - core.memory         : 에이전트 메모리

기존 `from core.utils import read_yaml` 등의 import를 깨뜨리지 않도록
모든 공개 심볼을 여기서 re-export 합니다.
"""

import os
import re
import json
import random
from datetime import datetime
from core.config_paths import *

# =============================================================================
# [Re-Export] 하위 호환 — 기존 import 경로 유지
# =============================================================================
# file_io
from core.file_io import (
    read_yaml, write_yaml, write_text, sha256_text,
    _env_flag, _sha256_file, _yaml_cache_put,
)

# security_guard
from core.security_guard import (
    safe_generate, quick_guard, run_isolated, build_child_env,
    MAX_ITERATIONS, TEST_TIMEOUT_SEC,
    CHILD_ENV_PASSTHROUGH, BANNED_IMPORT_TOPS, BANNED_CALLS,
)

# dashboard
from core.dashboard import (
    append_dashboard_run, _safe_write_json, validate_context_with_schema,
)

# memory
from core.memory import read_core_memory

# executor (기존 re-export 유지)
from core.executor import run_skill_safely


# =============================================================================
# [Local] 범용 유틸리티 — 여기에만 존재하는 함수들
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


# =============================================================================
# [Local] 에이전트 헬퍼
# =============================================================================
def get_random_signature(agent_config: dict) -> str:
    """무작위 시그니처 대사를 반환합니다."""
    lines = agent_config.get("signature_lines")
    if not lines and "persona" in agent_config:
        lines = agent_config["persona"].get("signature_lines")
    if lines and isinstance(lines, list):
        return random.choice(lines)
    return ""


def print_agent_msg(name: str, msg: str, signature: str = ""):
    """에이전트 이름과 메시지, 그리고 시그니처 대사를 출력합니다."""
    header = f"[{name}]"
    
    def _safe_out(text):
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("utf-8", "replace").decode("cp949", "replace"))
            
    if signature:
        _safe_out(f"\n{header} \"{signature}\"")
        _safe_out(f"{header} {msg}")
    else:
        _safe_out(f"{header} {msg}")


def is_codex_model(model_name: str) -> bool:
    m = (model_name or "").strip().lower()
    return m.startswith("codex") or m.startswith("gpt-5")


def is_claude_model(model_name: str) -> bool:
    m = (model_name or "").strip().lower()
    return m.startswith("claude")


# =============================================================================
# [Local] 프로젝트 정책/설정
# =============================================================================
def read_project_policies() -> dict:
    data = read_yaml(POLICIES_PATH)
    return data if isinstance(data, dict) else {}


def read_skill_lock() -> dict:
    data = read_yaml(SKILL_LOCK_PATH)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("skills", {})
    return data


def lock_skill_state(skill_id: str, patch: dict | None = None):
    sid = safe_id(skill_id)
    if not sid:
        return
    lock = read_skill_lock()
    skills = lock.get("skills", {})
    if not isinstance(skills, dict):
        skills = {}
    prev = skills.get(sid, {})
    if not isinstance(prev, dict):
        prev = {}
    merged = dict(prev)
    if isinstance(patch, dict):
        for k, v in patch.items():
            merged[k] = v
    merged["updated_at"] = now_iso()
    if not str(merged.get("version", "")).strip():
        merged["version"] = "1.0.0"
    if not str(merged.get("status", "")).strip():
        merged["status"] = "active"
    skills[sid] = merged
    lock["skills"] = skills
    write_yaml(SKILL_LOCK_PATH, lock)


def read_project_settings() -> dict:
    data = read_yaml(PROJECT_SETTINGS_PATH)
    return data if isinstance(data, dict) else {}


# =============================================================================
# [Local] 경로 해석 + 에이전트 오버라이드
# =============================================================================
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
    return not os.path.isabs(p) and "\\" not in p


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


# =============================================================================
__all__ = [name for name in dir() if not name.startswith('_')]
