import os
import json
import time
import sys
import re
import urllib.request
import urllib.error
import urllib.parse
from typing import NamedTuple, List, Dict, Any, Optional
from config.schema import factory_config
from core.providers.registry import get_engine_api_key

# sys.stdout.reconfigure(encoding='utf-8')  # [BUG #1 제거] 모듈 레벨에서 강제 reconfigure는
# 비-UTF-8 실행 환경(윈도우 cp949 등)에서 예외를 유발할 수 있음.
# 학습 환경 전용이 필요하다면 agent_launcher / CLI 단에서만 호출할 것.

_genai_client = None

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models_cache.json")
CACHE_EXPIRY = 24 * 60 * 60  # 24 hours in seconds
FORCED_MODEL_ENV = "AGENT_FORCE_MODEL"


# =============================================================================
# [반환 타입 정의] — Plan A: 튜플 기반 티어 정보 전달
# =============================================================================
class ModelSelection(NamedTuple):
    model: str          # 선택된 모델명 (예: "claude-opus-4-20260201")
    tier: str           # "primary" | "cross_fallback" | "free_fallback"
    reason: str         # 사람이 읽을 수 있는 이유 설명


TIER_PRIMARY       = "primary"
TIER_CROSS_FALLBACK = "cross_fallback"   # 다른 벤더로 교차 전환
TIER_FREE_FALLBACK  = "free_fallback"    # 무료 모델로 전환 (키 없음)
TIER_UNCALLABLE      = "uncallable"       # 어떤 키도 없어 실행 불가한 상태


# =============================================================================
# [캐시 관리] Google Gemini 모델 캐시
# =============================================================================
def log(msg: str):
    caller = sys._getframe(1).f_globals.get('__name__')
    print(f"[{caller}] * {msg}")


def _get_genai_client():
    global _genai_client
    if _genai_client is not None:
        return _genai_client

    api_key = get_engine_api_key("google")
    if not api_key:
        return None

    from google import genai

    _genai_client = genai.Client(api_key=api_key)
    return _genai_client


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if time.time() - data.get('timestamp', 0) > CACHE_EXPIRY:
            log("Model cache expired.")
            return None
        return data.get('models', [])
    except Exception as e:
        log(f"Error loading cache: {e}")
        return None


def save_cache(models: list):
    try:
        data = {'timestamp': time.time(), 'models': models}
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(f"Model cache saved to {CACHE_FILE}")
    except Exception as e:
        log(f"Error saving cache: {e}")


def normalize_model_name(model_name: str) -> str:
    """
    Normalize model ID before provider calls.
    Gemini IDs are normalized to include 'models/' prefix.
    """
    name = str(model_name or "").strip()
    if not name:
        return name
    lower = name.lower()
    if lower.startswith("models/"):
        return name
    if lower.startswith("gemini"):
        return f"models/{name}"
    return name


def get_forced_model_override() -> str:
    raw = str(os.getenv(FORCED_MODEL_ENV, "") or "").strip()
    if not raw:
        return ""
    return normalize_model_name(raw)


def _is_not_found_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return ("404" in text) or ("not_found" in text) or ("not found" in text)


def _build_gemini_retry_candidates(model_name: str) -> list[str]:
    base = normalize_model_name(model_name)
    candidates = [base]

    if "gemini" in base.lower():
        fallback = "models/gemini-2.5-flash"
        if fallback not in candidates:
            candidates.append(fallback)
        try:
            dynamic = normalize_model_name(get_dynamic_default_model("flash"))
            if dynamic and dynamic not in candidates:
                candidates.append(dynamic)
        except Exception:
            pass
    return candidates


def generate_content_with_self_heal(client, model_name: str, contents, **kwargs):
    """
    Google SDK self-heal wrapper for model 404.
    Retries with normalized Gemini model candidates on NOT_FOUND.
    """
    last_exc = None
    for candidate in _build_gemini_retry_candidates(model_name):
        try:
            return client.models.generate_content(model=candidate, contents=contents, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_not_found_error(exc):
                raise
            log(f"[Self-Heal] model not found: {candidate} -> trying next candidate")
    if last_exc:
        raise last_exc


def create_chat_with_self_heal(client, model_name: str, **kwargs):
    """
    Google SDK chat session self-heal wrapper for model 404.
    Retries with normalized Gemini model candidates on NOT_FOUND.
    """
    last_exc = None
    for candidate in _build_gemini_retry_candidates(model_name):
        try:
            return client.chats.create(model=candidate, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_not_found_error(exc):
                raise
            log(f"[Self-Heal] chat model not found: {candidate} -> trying next candidate")
    if last_exc:
        raise last_exc


# =============================================================================
# [모델 목록 조회]
# =============================================================================
def get_available_models(force_refresh: bool = False) -> list[str]:
    """Google Gemini 가용 모델 목록 반환 (캐시 24시간 유지)."""
    if not force_refresh:
        cached = load_cache()
        if cached:
            return cached
    log("Fetching available models from Google API...")
    try:
        models = []
        client = _get_genai_client()
        if client is None:
            raise RuntimeError("No GOOGLE_API_KEY configured")
        for m in client.models.list():
            if hasattr(m, 'supported_actions') and 'generateContent' in (m.supported_actions or []):
                models.append(m.name)
            elif hasattr(m, 'supported_generation_methods') and 'generateContent' in (m.supported_generation_methods or []):
                models.append(m.name)
        if models:
            save_cache(models)
        return models
    except Exception as e:
        log(f"Failed to list models: {e}. Returning fallback list.")
        return ["models/gemini-2.5-flash", "models/gemini-2.5-pro", "models/gemini-2.0-flash", "models/gemini-1.5-flash", "models/gemini-1.5-pro"]


def fetch_openai_models() -> list[str]:
    """OpenAI API에서 gpt-* / o-시리즈 모델 동적 조회. 키 없으면 []."""
    api_key = get_engine_api_key("openai")
    if not api_key:
        return []
    try:
        req = urllib.request.Request("https://api.openai.com/v1/models")
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            models = [
                m for m in data.get("data", [])
                if "gpt" in m["id"] or re.match(r"^o\d", m["id"])
            ]
            models.sort(key=lambda x: x.get("created", 0), reverse=True)
            return [m["id"] for m in models]
    except Exception as e:
        log(f"OpenAI model fetch error: {e}")
        return []


def fetch_anthropic_models() -> list[str]:
    """Anthropic API에서 claude-* 모델 동적 조회. 키 없으면 []."""
    api_key = get_engine_api_key("anthropic")
    if not api_key:
        return []
    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/models")
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", "2023-06-01")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            models = [m for m in data.get("data", []) if "claude" in m.get("id", "")]
            models.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return [m["id"] for m in models]
    except Exception as e:
        log(f"Anthropic model fetch error: {e}")
        return []


# =============================================================================
# [Anthropic] 역할별 최신 모델 선택 (Opus / Sonnet / Haiku)
# =============================================================================
def _pick_anthropic_model(tier: str) -> str | None:
    """
    tier: 'opus' (architect), 'sonnet' (coder), 'haiku' (lightweight)
    반환: 해당 티어의 최신 모델 ID, 없으면 None
    """
    models = fetch_anthropic_models()
    if not models:
        return None
    matched = [m for m in models if tier.lower() in m.lower()]
    if matched:
        log(f"[Anthropic] Latest {tier}: {matched[0]}")
        return matched[0]
    log(f"[Anthropic] No '{tier}' tier -> using latest: {models[0]}")
    return models[0]


# =============================================================================
# [OpenAI] 역할별 최신 모델 선택
# =============================================================================
def _pick_openai_model(prefer_reasoning: bool = False, prefer_mini: bool = False) -> str | None:
    """
    prefer_reasoning=True  → o-시리즈 우선
    prefer_reasoning=False → gpt-* 우선
    prefer_mini=True      → gpt-*-mini 우선
    반환: 최신 모델 ID, 없으면 None
    """
    models = fetch_openai_models()
    if not models:
        return None
    if prefer_reasoning:
        reasoning = [m for m in models if re.match(r"^o\d", m)]
        if reasoning:
            return reasoning[0]
    
    if prefer_mini:
        mini = [m for m in models if "mini" in m.lower() and "gpt" in m.lower()]
        if mini:
            return mini[0]

    gpt_models = [m for m in models if "gpt" in m]
    return gpt_models[0] if gpt_models else (models[0] if models else None)


# =============================================================================
# [유틸] Google Gemini 태그 기반 최신 버전 검색
# =============================================================================
def find_latest_model(tag: str, available_models: list) -> str:
    """태그(예: 'gemini-*-pro')로 가용 Gemini 모델 중 최신 버전 검색."""
    pattern = tag.replace("-*", r"-[\d\.]+").replace("*", r"[\d\.]+")
    matches = []
    for m in available_models:
        # [Stability FIX] Keep the 'models/' prefix if present to avoid 404s in SDK
        m_name = m.replace("models/", "")
        
        # [Stability FIX] 'lite' 모델은 현재 API 지원이 불안정하거나 (404/503) 
        # 특정 메서드를 지원하지 않을 수 있으므로 자동 선택에서 제외함.
        if "lite" in m_name.lower():
            continue

        if re.search(pattern, m_name):
            ver_match = re.search(r"(\d+\.\d+|\d+)", m_name)
            version = float(ver_match.group(1)) if ver_match else 0.0
            
            # 사용자 요청: 안정판(Stable) 모델을 최우선으로 반영하되, 
            # 3.x 대비 2.0의 신뢰도가 높으므로 2.0 계열에 보정치를 주거나 
            # preview/exp 선호도를 낮춤 (안정성 확보)
            priority = 0
            if "exp" in m_name:
                priority = -2  # 실험용은 가장 낮게
            elif "preview" in m_name:
                priority = -1  # 프리뷰도 가급적 회피
            elif m_name.startswith("gemini-2.5") or m_name.startswith("gemini-2.0"):
                # 2.5와 2.0 모두 안정 계열로 취급 — 버전 번호(version)로 최신 선택
                priority = 5

            matches.append({"name": m, "version": version, "priority": priority})

    if not matches:
        # 하드코딩 제거: 패턴에서 동적으로 기본 별칭을 추출
        fallback = tag.replace("-*", "").replace("*", "")
        if "flash" in fallback:
            return "models/gemini-2.5-flash"
        return fallback
        
    # 우선순위(안정성) -> 버전 순으로 정렬 (안정된 모델 중 최신 버전)
    matches.sort(key=lambda x: (x["priority"], x["version"]), reverse=True)
    return matches[0]["name"]


def get_dynamic_default_model(tier: str = "flash") -> str:
    """
    [자가 진화] API를 통해 확보한 가용 모델 목록 중
    지정된 tier(flash 또는 pro)의 가장 최신 버전 모델명을 동적으로 반환합니다.
    하드코딩된 버전 번호(예: '2.0')를 일절 사용하지 않습니다.
    """
    forced = get_forced_model_override()
    if forced:
        log(f"[Forced Model] {FORCED_MODEL_ENV}={forced}")
        return forced

    available = get_available_models()
    selected = normalize_model_name(find_latest_model(f"gemini-*-{tier}", available))
    log(f"[Dynamic Default] tier={tier} → {selected}")
    return selected


def get_best_model(priority_list: list = None) -> str:
    """우선순위 리스트 기반 최적 Gemini 모델 선택."""
    forced = get_forced_model_override()
    if forced:
        log(f"[Forced Model] {FORCED_MODEL_ENV}={forced}")
        return forced

    if priority_list is None:
        # 하드코딩 없이 API 목록에서 최신 모델을 직접 추출
        return get_dynamic_default_model("pro")
    available = get_available_models()
    for p in priority_list:
        for m in available:
            if p in m:
                return normalize_model_name(m)
    if available:
        return normalize_model_name(available[0])
    # 최후의 수단: 동적 기본 모델 (버전 하드코딩 배제)
    dynamic = get_dynamic_default_model("flash")
    if not dynamic:
        return ""
    return normalize_model_name(dynamic)


# =============================================================================
# [핵심] resolve_dynamic_model — Plan A: ModelSelection(model, tier, reason) 반환
#
#  tier 정의:
#    primary        → 지정된 엔진 키가 있고, 해당 벤더 최신 모델 선택
#    cross_fallback → 지정 키 없음, 타 벤더(Google)로 교차 전환
#    free_fallback  → 모든 키 없음, Gemini Flash(무료)로 강제 전환
#
#  호출부(ModelRouter) 에서 tier != "primary" 일 경우 사용자 승인 요청.
# =============================================================================
def resolve_dynamic_model(engine_id: str) -> ModelSelection:
    """
    [Plan A] 역할 전문화 + 자가 진화 + 사용자 확인 기반 교차 폴백.

    반환: ModelSelection(model, tier, reason)
      - tier == 'primary'        -> 정상 실행
      - tier == 'cross_fallback' -> ModelRouter가 사용자에게 경고 후 동의 시 실행
      - tier == 'free_fallback'  -> ModelRouter가 사용자에게 경고 후 동의 시 실행
    """
    forced = get_forced_model_override()
    if forced:
        return ModelSelection(
            normalize_model_name(forced),
            TIER_PRIMARY,
            f"[forced] {FORCED_MODEL_ENV} override active",
        )

    available = get_available_models()
    keys = {
        "google":    bool(get_engine_api_key("google")),
        "openai":    bool(get_engine_api_key("openai")),
        "anthropic": bool(get_engine_api_key("anthropic")),
    }

    def _check_callable(sel: ModelSelection) -> ModelSelection:
        """[BUG FIX] 선택된 모델을 호출할 수 있는 API 키가 있는지 최종 확인.
        
        codex 계열 식별을 startswith('o') 대신 regex로 정확히 매칭하여
        'ollama', 'open-*' 등과 구분합니다.
        """
        m_lower = sel.model.lower()

        # codex 계열 먼저 체크 (startswith 'c' -> openai)
        if "codex" in m_lower:
            required_key = "openai"
        # gpt 계열
        elif "gpt" in m_lower:
            required_key = "openai"
        # o-시리즈: 'o' + 숫자로 시작하는 경우만 (오탐 방지: ollama, open-* 제외)
        elif re.match(r'^o\d', m_lower):
            required_key = "openai"
        # claude 계열
        elif "claude" in m_lower:
            required_key = "anthropic"
        # 그 외: Google Gemini 계열로 간주
        else:
            required_key = "google"

        if not keys[required_key]:
            return ModelSelection(
                sel.model,
                TIER_UNCALLABLE,
                f"[{engine_id}] 호출 불가: {required_key.upper()}_API_KEY가 등록되지 않았습니다."
            )
        return sel

    def _cross(model: str, reason: str) -> ModelSelection:
        return _check_callable(ModelSelection(normalize_model_name(model), TIER_CROSS_FALLBACK, reason))

    def _free(model: str, reason: str) -> ModelSelection:
        return _check_callable(ModelSelection(normalize_model_name(model), TIER_FREE_FALLBACK, reason))

    def _primary(model: str) -> ModelSelection:
        return _check_callable(ModelSelection(normalize_model_name(model), TIER_PRIMARY, ""))

    # ─────────────────────────────────────────────────────────────────────────
    # [1] Google Gemini — Super Researcher / Release Agents
    # ─────────────────────────────────────────────────────────────────────────
    if engine_id in ("researcher_gemini", "research_pro", "gemini_pro"):
        if keys["google"]:
            return _primary(find_latest_model("gemini-*-pro", available))
        # Google 키 없음 → 무료 Flash
        return _free(
            find_latest_model("gemini-*-flash", available),
            f"[{engine_id}] GOOGLE_API_KEY 없음 → 무료 Gemini Flash로 대체 (품질 저하 가능)"
        )

    if engine_id == "gemini_flash":
        if keys["google"]:
            return _primary(find_latest_model("gemini-*-flash", available))
        return _free(
            find_latest_model("gemini-*-flash", available),
            f"[gemini_flash] GOOGLE_API_KEY 없음 → 무료 Gemini Flash (호출 불가 위험)"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # [2] Claude — Architect (Opus) / Coder (Sonnet) 역할 분리
    # ─────────────────────────────────────────────────────────────────────────
    if engine_id in ("architect_claude", "claude_pro"):
        if keys["anthropic"]:
            model = _pick_anthropic_model("opus")
            if model:
                return _primary(model)
        if keys["google"]:
            model = find_latest_model("gemini-*-pro", available)
            return _cross(
                model,
                f"[{engine_id}] ANTHROPIC_API_KEY 없음 → Google Pro({model})로 교차 진행 "
                f"(설계 정밀도 저하 가능)"
            )
        return _free(
            find_latest_model("gemini-*-flash", available),
            f"[{engine_id}] 모든 API 키 없음 → 무료 Gemini Flash로 강제 대체 "
            f"(아키텍처 품질 보장 불가)"
        )

    if engine_id == "coder_claude":
        if keys["anthropic"]:
            model = _pick_anthropic_model("sonnet")
            if model:
                return _primary(model)
        if keys["google"]:
            model = find_latest_model("gemini-*-pro", available)
            return _cross(
                model,
                f"[coder_claude] ANTHROPIC_API_KEY 없음 → Google Pro({model})로 교차 진행 "
                f"(코딩 품질 저하 가능)"
            )
        return _free(
            find_latest_model("gemini-*-flash", available),
            "[coder_claude] 모든 API 키 없음 → 무료 Gemini Flash로 강제 대체 "
            "(코드 품질 보장 불가)"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # [3] GPT — Manager / Codex / Reasoner
    # ─────────────────────────────────────────────────────────────────────────
    if engine_id == "manager_gpt":
        if keys["openai"]:
            model = _pick_openai_model(prefer_reasoning=False)
            if model:
                return _primary(model)
        if keys["google"]:
            model = find_latest_model("gemini-*-flash", available)
            return _cross(
                model,
                f"[manager_gpt] OPENAI_API_KEY 없음 → Google Flash({model})로 교차 진행"
            )
        return _free(
            find_latest_model("gemini-*-flash", available),
            "[manager_gpt] 모든 API 키 없음 → 무료 Gemini Flash로 강제 대체"
        )

    if engine_id == "codex":
        _CODEX_ALLOWLIST = ["codex-5.3", "codex-5", "codex-4"]
        if keys["openai"]:
            all_openai = fetch_openai_models()
            for candidate in _CODEX_ALLOWLIST:
                if any(candidate in m for m in all_openai):
                    matched = next(m for m in all_openai if candidate in m)
                    return _primary(matched)
        # 키 없거나 allowlist 미매칭 → 하드코딩 + free_fallback
        return _free(
            "codex-5.3",
            "[codex] OPENAI_API_KEY 없음 또는 allowlist 미매칭 → codex-5.3 고정 "
            "(호출 불가 위험)"
        )

    if engine_id == "reasoner_o":
        if keys["openai"]:
            model = _pick_openai_model(prefer_reasoning=True)
            if model:
                return _primary(model)
        if keys["google"]:
            model = find_latest_model("gemini-*-flash", available)
            return _cross(
                model,
                f"[reasoner_o] OPENAI_API_KEY 없음 → Google Flash({model})로 교차 진행 "
                f"(추론 수준 저하 가능)"
            )
        return _free(
            find_latest_model("gemini-*-flash", available),
            "[reasoner_o] 모든 API 키 없음 → 무료 Gemini Flash로 강제 대체"
        )

    if engine_id == "lightweight":
        # 1. Anthropic Haiku
        if keys["anthropic"]:
            model = _pick_anthropic_model("haiku")
            if model:
                return _primary(model)
        # 2. OpenAI Mini
        if keys["openai"]:
            model = _pick_openai_model(prefer_mini=True)
            if model:
                return _primary(model)
        # 3. Google Flash (Default/Free)
        model = find_latest_model("gemini-*-flash", available)
        if keys["google"]:
            return _primary(model)
        return _free(model, "[lightweight] 모든 API 키 없음 → 무료 Gemini Flash 적용")

    # ─────────────────────────────────────────────────────────────────────────
    # [기본] 알 수 없는 engine_id
    # ─────────────────────────────────────────────────────────────────────────
    model = find_latest_model("gemini-*-flash", available)
    return _cross(model, f"[{engine_id}] 알 수 없는 engine_id → Gemini Flash 기본 적용")


# =============================================================================
# [공개 API] 에이전트 역할 기반 선호 모델 자동 결정
# =============================================================================

# 역할 키워드 → engine_id 매핑 테이블
# 에이전트의 role/tagline/name에서 키워드를 찾아 최적 엔진을 결정한다.
_ROLE_ENGINE_MAP: list[tuple[list[str], str]] = [
    (["qa", "tester", "quality", "test_eng"], "codex"),
    (["architect", "design", "blueprint", "system design"], "architect_claude"),
    (["coder", "developer", "_dev", "engineer", "programmer"], "coder_claude"),
    (["research", "researcher", "analyst", "study"], "researcher_gemini"),
    (["manager", "pm", "pd", "project", "director", "orchestrat"], "manager_gpt"),
    (["reason", "verif", "logic", "validator", "reviewer"], "reasoner_o"),
    (["codex", "automat", "pipeline"], "codex"),
]

_DEFAULT_ENGINE = "researcher_gemini"   # 키워드 매칭 실패 시 기본값

# =============================================================================
# [CLI 프로바이더 우선순위] — engine_id별 선호 CLI 프로바이더 순서
#   복수 구독 시 역할에 맞는 CLI를 자동 선택한다.
#   단일 구독이면 이 테이블을 무시하고 유일한 프로바이더를 사용한다.
# =============================================================================
_ROLE_CLI_PREFERENCE: dict[str, list[str]] = {
    "architect_claude":  ["claude_cli", "gemini_cli", "codex_cli"],
    "coder_claude":      ["claude_cli", "codex_cli", "gemini_cli"],
    "researcher_gemini": ["gemini_cli", "claude_cli", "codex_cli"],
    "manager_gpt":       ["codex_cli", "claude_cli", "gemini_cli"],
    "reasoner_o":        ["codex_cli", "gemini_cli", "claude_cli"],
    "codex":             ["codex_cli", "claude_cli", "gemini_cli"],
    "gemini_flash":      ["gemini_cli", "claude_cli", "codex_cli"],
    "lightweight":       ["gemini_cli", "claude_cli", "codex_cli"],
}

# LLM이 학습한 역할→프로바이더 캐시 (role_provider_cache.json에서 로드/저장)
_learned_role_provider_cache: dict[str, str] | None = None

_ROLE_PROVIDER_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "role_provider_cache.json"
)


def _load_role_provider_cache() -> dict[str, str]:
    """디스크에서 LLM 학습 캐시를 로드한다."""
    global _learned_role_provider_cache
    if _learned_role_provider_cache is not None:
        return _learned_role_provider_cache
    _learned_role_provider_cache = {}
    try:
        if os.path.exists(_ROLE_PROVIDER_CACHE_FILE):
            with open(_ROLE_PROVIDER_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _learned_role_provider_cache = data
    except Exception:
        pass
    return _learned_role_provider_cache


def _save_role_provider_cache(cache: dict[str, str]) -> None:
    """LLM 학습 결과를 디스크에 저장한다."""
    global _learned_role_provider_cache
    _learned_role_provider_cache = cache
    try:
        with open(_ROLE_PROVIDER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def pick_cli_provider_for_role(
    engine_id: str,
    available_providers: list[str],
    role_description: str = "",
) -> str:
    """
    하이브리드 CLI 프로바이더 선택 (3-Layer):
      Layer 1: 정적 매핑 테이블 (_ROLE_CLI_PREFERENCE)
      Layer 2: LLM 학습 캐시 (role_provider_cache.json)
      Layer 3: LLM 판단 (매칭 실패 시 1회 호출 → 캐시에 저장)

    단일 프로바이더만 가용하면 즉시 반환한다.
    """
    if not available_providers:
        return ""
    if len(available_providers) == 1:
        return available_providers[0]

    available_set = set(available_providers)

    # Layer 1: 정적 매핑 테이블에서 가용한 첫 번째 프로바이더
    preference = _ROLE_CLI_PREFERENCE.get(engine_id, [])
    for prov in preference:
        if prov in available_set:
            return prov

    # Layer 2: LLM 학습 캐시 확인
    cache = _load_role_provider_cache()
    cache_key = engine_id
    if role_description:
        cache_key = f"{engine_id}:{role_description.strip().lower()[:80]}"
    cached = cache.get(cache_key, "")
    if cached and cached in available_set:
        return cached

    # Layer 3: LLM 판단 (새로운 역할) → 결과를 캐시에 저장
    learned = _llm_decide_provider(engine_id, role_description, available_providers)
    if learned and learned in available_set:
        cache[cache_key] = learned
        _save_role_provider_cache(cache)
        log(f"[ProviderLearn] LLM decided: {engine_id} -> {learned} (cached)")
        return learned

    # 최종 폴백: 가용 목록의 첫 번째
    return available_providers[0]


# provider_id → 해당 프로바이더가 primary인 engine_id
_PROVIDER_PRIMARY_ENGINE: dict[str, str] = {
    "claude_cli": "coder_claude",
    "gemini_cli": "researcher_gemini",
    "codex_cli":  "codex",
}


def resolve_engine_for_available(engine_id: str, available_providers: list[str]) -> str:
    """preferred CLI provider가 미구독/미설치인 경우 실제 가용 provider에 맞는 engine_id로 remapping한다.

    예) researcher_gemini (gemini_cli 선호) + gemini_cli 없음 + claude_cli 있음
        → coder_claude (claude_cli 선호) 로 remapping

    단일 프로바이더 환경이거나 preferred provider가 가용하면 engine_id를 그대로 반환한다.
    """
    available_set = set(available_providers or [])
    preference = _ROLE_CLI_PREFERENCE.get(engine_id, [])

    # preferred provider 가용 → 변경 없음
    if not preference or preference[0] in available_set:
        return engine_id

    # preferred provider 없음 → fallback preference에서 실제 사용될 provider 탐색
    actual_provider = next((p for p in preference if p in available_set), "")
    if not actual_provider:
        return engine_id  # 가용 provider 없음 → 변경 불가

    return _PROVIDER_PRIMARY_ENGINE.get(actual_provider, engine_id)


def _llm_decide_provider(
    engine_id: str,
    role_description: str,
    available_providers: list[str],
) -> str:
    """
    LLM에게 역할에 가장 적합한 CLI 프로바이더를 질문한다.
    실패 시 빈 문자열 반환 (Layer 1 폴백으로 안전하게 처리).
    """
    provider_descriptions = {
        "claude_cli": "Claude Code - 코드 아키텍처 설계, 복잡한 코딩, 리팩토링에 강점",
        "gemini_cli": "Gemini CLI - 긴 컨텍스트 분석, 리서치, 문서 처리에 강점",
        "codex_cli": "Codex CLI - 자동화, 파이프라인 실행, 코드 생성에 강점",
    }
    options = "\n".join(
        f"- {pid}: {provider_descriptions.get(pid, pid)}"
        for pid in available_providers
    )
    prompt = (
        f"에이전트 역할: {role_description or engine_id}\n"
        f"엔진 ID: {engine_id}\n\n"
        f"가용 CLI 프로바이더:\n{options}\n\n"
        f"이 역할에 가장 적합한 프로바이더 ID 하나만 답하세요.\n"
        f"반드시 {available_providers} 중 하나를 선택하세요.\n"
        f"프로바이더 ID만 출력하세요. 설명 없이."
    )
    try:
        # 가용한 CLI 중 첫 번째로 판단 요청
        from core.providers.cli import CliChatRequest, execute_cli_chat
        result = execute_cli_chat(
            CliChatRequest(
                provider_id=available_providers[0],
                model="",
                system_prompt="You are a routing assistant. Answer with only the provider ID.",
                task_input=prompt,
                workspace=os.getcwd(),
                timeout_sec=30,
            )
        )
        if not result.get("ok"):
            return ""
        answer = str(result.get("text", "")).strip().lower()
        # 응답에서 유효한 프로바이더 ID 추출
        for pid in available_providers:
            if pid in answer:
                return pid
        return ""
    except Exception:
        return ""


def _infer_engine_id(role: str) -> str:
    """역할 문자열에서 엔진 ID를 자동 추론한다."""
    role_lower = (role or "").lower()
    for keywords, engine_id in _ROLE_ENGINE_MAP:
        if any(kw in role_lower for kw in keywords):
            return engine_id
    return _DEFAULT_ENGINE


def resolve_preferred_model(role: str) -> str:
    """역할별 최적 모델을 결정한다 (동적 라우팅)."""
    forced = get_forced_model_override()
    if forced:
        log(f"[Model Override] Forcing '{forced}' for role: {role}")
        return forced
    engine_id = _infer_engine_id(role)
    selection = resolve_dynamic_model(engine_id)
    model = normalize_model_name(selection.model)
    log(f"[Model Resolve] role={role} engine={engine_id} tier={selection.tier} -> {model}")
    return model


# 엔진 ID → 표시 이름 (CLI 출력용)
_ENGINE_DISPLAY: dict[str, str] = {
    "architect_claude":  "Claude Opus   (아키텍터)",
    "coder_claude":      "Claude Sonnet (코더)",
    "researcher_gemini": "Gemini Pro    (리서처)",
    "gemini_flash":      "Gemini Flash  (경량)",
    "manager_gpt":       "GPT-4o        (매니저)",
    "reasoner_o":        "GPT o-series  (추론)",
    "codex":             "Codex         (자동화)",
    "lightweight":       "Lightweight   (단순작업)",
}


def print_agent_model_summary(agent: dict, selected_model: str = "", selected_tier: str = "", selected_reason: str = "") -> None:
    """
    에이전트 실행 직후 CLI에 역할/엔진/모델 요약을 출력한다.
    selected_tier/selected_reason이 있으면 실제 라우팅 결과를 함께 표시한다.
    """
    name = str(agent.get("name", agent.get("id", "unknown")))
    role = str(agent.get("role", "") or (agent.get("identity", {}) or {}).get("role_summary", ""))
    rr = agent.get("runtime_rules", {}) if isinstance(agent.get("runtime_rules"), dict) else {}
    model = str(selected_model or "").strip()
    tier = str(selected_tier or "").strip()
    reason = str(selected_reason or "").strip()
    engine_id = _infer_engine_id(role or name)
    engine_label = _ENGINE_DISPLAY.get(engine_id, engine_id)

    if not model:
        model = str(rr.get("preferred_model", "")).strip()
    if not model:
        model = resolve_preferred_model(role or name)
    if not tier:
        tier = "primary" if model else ""

    W = 60
    sep = "-" * W
    print(f"\n+{sep}+")
    print(f"|  Agent Model Summary{' ' * (W - 21)}|")
    print(f"+{sep}+")
    print(f"|  Name   : {name:<{W - 12}}|")
    print(f"|  Role   : {(role or '(none)'):<{W - 12}}|")
    print(f"|  Engine : {engine_label:<{W - 12}}|")
    print(f"|  Model  : {model:<{W - 12}}|")
    print(f"|  Tier   : {(tier or '(n/a)'):<{W - 12}}|")
    if reason:
        reason_line = reason if len(reason) <= (W - 12) else reason[:(W - 15)] + "..."
        print(f"|  Reason : {reason_line:<{W - 12}}|")
    print(f"+{sep}+")
    print("  To override: AGENT_CHAT_MODEL=<model> python agent_launcher.py")
    print("  Or edit YAML runtime_rules.preferred_model directly.\n")
