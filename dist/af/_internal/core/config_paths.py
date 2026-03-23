import os
import re
from config.schema import factory_config
from core.engine_auth import engine_api_keys_disabled, get_engine_api_key, supports_cli_bootstrap

def _boot_safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "default"

global_project_root = os.getenv("AGENT_PROJECT_ROOT", None)


def _config_value(name: str, env_key: str) -> str:
    if isinstance(factory_config, dict):
        value = factory_config.get(name)
    else:
        value = getattr(factory_config, name, None)
    if value:
        return str(value)
    return str(os.getenv(env_key, "") or "")

GOOGLE_API_KEY = get_engine_api_key("google")
OPENAI_API_KEY = get_engine_api_key("openai")
# ── API 키 필수 체크 (비활성화 중) ──────────────────────────────────────────
# 원복하려면 아래 주석을 해제하세요.
# if not GOOGLE_API_KEY and not OPENAI_API_KEY and not supports_cli_bootstrap() and not engine_api_keys_disabled():
#     raise RuntimeError("Neither GOOGLE_API_KEY nor OPENAI_API_KEY found in env/.env")
# ────────────────────────────────────────────────────────────────────────────
# [New SDK] genai.Client은 각 모듈에서 개별 생성 (config_paths는 경로만 담당)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# [Project Specific Path Resolution]
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
GLOBAL_AGENTS_DIR = os.path.join(BASE_DIR, "agents")
SKILLS_DIR = os.path.join(BASE_DIR, "skills")
GLOBAL_RUNS_DIR = os.path.join(BASE_DIR, "runs")


_proj_id_env = _boot_safe_id(os.environ.get("AGENT_PROJECT_ID", "").strip()) if os.environ.get("AGENT_PROJECT_ID") else ""
_proj_root_env = os.environ.get("AGENT_PROJECT_ROOT", "").strip()
if _proj_root_env:
    PROJECT_ROOT = os.path.abspath(_proj_root_env)
    PROJECT_ID = _proj_id_env or _boot_safe_id(os.path.basename(PROJECT_ROOT))
else:
    PROJECT_ID = _proj_id_env or "default"
    PROJECT_ROOT = os.path.abspath(os.path.join(PROJECTS_DIR, PROJECT_ID))

_global_user_key_raw = os.environ.get("AGENT_GLOBAL_USER_KEY", "").strip()
GLOBAL_USER_KEY = _boot_safe_id(_global_user_key_raw) if _global_user_key_raw else ""
GLOBAL_STORAGE_ID = f"global_{GLOBAL_USER_KEY}" if GLOBAL_USER_KEY else ""
_global_root_env = os.environ.get("AGENT_GLOBAL_PROJECT_ROOT", "").strip()
if _global_root_env:
    GLOBAL_PROJECT_ROOT = os.path.abspath(_global_root_env)
elif GLOBAL_STORAGE_ID:
    GLOBAL_PROJECT_ROOT = os.path.abspath(os.path.join(PROJECTS_DIR, GLOBAL_STORAGE_ID))
else:
    GLOBAL_PROJECT_ROOT = PROJECT_ROOT

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

GLOBAL_AGENTS_PATH = os.path.join(GLOBAL_PROJECT_ROOT, "agents")
GLOBAL_RUNS_PATH = os.path.join(GLOBAL_PROJECT_ROOT, "runs")
GLOBAL_DATA_DIR = os.path.join(GLOBAL_PROJECT_ROOT, "data")
GLOBAL_ARTIFACTS_DIR = os.path.join(GLOBAL_PROJECT_ROOT, "artifacts")
GLOBAL_MEMORY_DIR = os.path.join(GLOBAL_DATA_DIR, "memory")

REGISTRY_PATH = os.path.join(SKILLS_DIR, "registry.yaml")
WORKFLOW_PATH = os.path.join(SKILLS_DIR, "workflow_registry.yaml")

for d in [
    PROJECTS_DIR,
    GLOBAL_AGENTS_DIR,
    GLOBAL_RUNS_DIR,
    AGENTS_DIR,
    SKILLS_DIR,
    RUNS_DIR,
    DATA_DIR,
    ARTIFACTS_DIR,
    PROJECT_SKILLS_DIR,
    EXTERNAL_CACHE_DIR,
    GLOBAL_PROJECT_ROOT,
    GLOBAL_AGENTS_PATH,
    GLOBAL_RUNS_PATH,
    GLOBAL_DATA_DIR,
    GLOBAL_ARTIFACTS_DIR,
    GLOBAL_MEMORY_DIR,
]:
    os.makedirs(d, exist_ok=True)
