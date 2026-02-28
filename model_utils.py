
import os
import json
import time
import sys
import urllib.request
import urllib.error
import urllib.parse
from google import genai
from config.schema import factory_config

sys.stdout.reconfigure(encoding='utf-8')

_google_api_key = os.getenv("GOOGLE_API_KEY")
_genai_client = genai.Client(api_key=_google_api_key) if _google_api_key else None

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models_cache.json")
CACHE_EXPIRY = 24 * 60 * 60  # 24 hours in seconds

def log(msg):
    caller = sys._getframe(1).f_globals.get('__name__')
    print(f"[{caller}] ?쨼 {msg}")

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

def save_cache(models):
    try:
        data = {
            'timestamp': time.time(),
            'models': models
        }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(f"Model cache saved to {CACHE_FILE}")
    except Exception as e:
        log(f"Error saving cache: {e}")

def get_available_models(force_refresh=False):
    """
    Returns a list of available Gemini model names.
    """
    if not force_refresh:
        cached = load_cache()
        if cached: return cached

    log("Fetching available models from Google API...")
    try:
        models = []
        if _genai_client is None:
            raise RuntimeError("No GOOGLE_API_KEY configured")
        for m in _genai_client.models.list():
            if hasattr(m, 'supported_actions') and 'generateContent' in (m.supported_actions or []):
                models.append(m.name)
            elif hasattr(m, 'supported_generation_methods') and 'generateContent' in (m.supported_generation_methods or []):
                models.append(m.name)
        if models: save_cache(models)
        return models
    except Exception as e:
        log(f"Failed to list models: {e}. Returning fallback list.")
        return ["models/gemini-2.0-flash", "models/gemini-1.5-flash", "models/gemini-1.5-pro"]

def fetch_openai_models():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key: return []
    try:
        req = urllib.request.Request("https://api.openai.com/v1/models")
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            # gpt 나 o 시리즈 필터링 후 최신순 정렬
            models = [m for m in data.get("data", []) if "gpt" in m["id"] or m["id"].startswith("o")]
            models.sort(key=lambda x: x.get("created", 0), reverse=True)
            return [m["id"] for m in models]
    except Exception as e:
        log(f"OpenAI fetch error: {e}")
        return []

def fetch_anthropic_models():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key: return []
    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/models")
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", "2023-06-01")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            # claude 시리즈 필터링 후 최신순 정렬
            models = [m for m in data.get("data", []) if "claude" in m.get("id", "")]
            models.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return [m["id"] for m in models]
    except Exception as e:
        log(f"Anthropic fetch error: {e}")
        return []

def get_best_model(priority_list=None):
    """
    Selects the best available model based on the provided priority list.
    If priority_list is None, uses a default high-performance dynamic list.
    """
    if priority_list is None:
        priority_list = [
            "gemini-3.1-pro",
            "gemini-3-pro",
            "gemini-3-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "codex-5.3",
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash"
        ]
        
    available = get_available_models()
    
    for p in priority_list:
        for m in available:
            if p in m:
                return m
                
    if available:
        log(f"No priority match found. Using first available: {available[0]}")
        return available[0]
        
    return "models/gemini-2.0-flash"

def find_latest_model(tag: str, available_models: list) -> str:
    """
    관련 모델 태그(예: 'gemini-*-pro')를 기반으로 가용한 최신 버전을 동적으로 검색합니다.
    """
    import re
    
    # 태그를 정규식 패턴으로 변환 (e.g. gemini-*-pro -> gemini-[\d.]+ -pro)
    pattern = tag.replace("-*", r"-[\d\.]+")
    pattern = pattern.replace("*", r"[\d\.]+")
    
    matches = []
    for m in available_models:
        m_name = m.replace("models/", "")
        if re.search(pattern, m_name):
            # 버전 숫자 추출 (e.g. 3.1, 2.0)
            ver_match = re.search(r"(\d+\.\d+|\d+)", m_name)
            version = float(ver_match.group(1)) if ver_match else 0.0
            # experimental/preview 모델은 후순위로 밀되, 버전이 높으면 우선
            priority = 0
            if "exp" in m_name: priority = -1
            elif "preview" in m_name: priority = 1 # Preview는 최신 기능을 포함하므로 우선순위 부여 가능
            
            matches.append({
                "name": m_name,
                "version": version,
                "priority": priority
            })
            
    if not matches:
        return tag.replace("*", "2.0") # Fallback
        
    # 버전 -> 우선순위 순으로 정렬
    matches.sort(key=lambda x: (x["version"], x["priority"]), reverse=True)
    return matches[0]["name"]

def resolve_dynamic_model(engine_id: str) -> str:
    """
    [Elite Synergy Triad] 모델 전문화 원칙을 지원합니다.
    [Self-Evolution] 최신 엔진 출시 시 즉시 반영합니다.
    
    2026-03 최신 모델 매핑:
    - researcher_gemini: Gemini 3.1 Pro (압도적 컨텍스트 리서치)
    - architect_claude: Claude Sonnet 4 (아키텍처 설계)
    - coder_claude: Claude Sonnet 4 (정밀 코딩)
    - manager_gpt: GPT-4.1 (도구 조율 및 논리 검증)
    """
    available = get_available_models()
    keys = {
        "google": bool(os.getenv("GOOGLE_API_KEY")),
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY"))
    }
    
    # [1] Google Gemini (Super Researcher / Fallback)
    if engine_id in ("researcher_gemini", "research_pro", "gemini_flash", "gemini_pro"):
        if keys["google"]:
            target = "gemini-*-pro" if "flash" not in engine_id else "gemini-*-flash"
            return find_latest_model(target, available)
        return find_latest_model("gemini-*-flash", available)

    # [2] Claude Sonnet 4 (Architect & Coder) — 2025-05 Latest
    elif engine_id in ("architect_claude", "coder_claude", "claude_pro"):
        if keys["anthropic"]:
            return "claude-sonnet-4-20250514"
        # Fallback: Gemini Pro (cross-engine)
        if keys["google"]:
            return find_latest_model("gemini-*-pro", available)
        return "claude-sonnet-4-20250514"

    # [3] GPT-4.1 (Manager & Action Verifier) — 2025 Latest
    elif engine_id in ("manager_gpt", "codex"):
        if keys["openai"]:
            return "gpt-4.1" if engine_id == "manager_gpt" else "o3"
        # Fallback: Gemini Flash (cross-engine)
        if keys["google"]:
            return find_latest_model("gemini-*-flash", available)
        return "gpt-4.1"

    return find_latest_model("gemini-*-flash", available)

