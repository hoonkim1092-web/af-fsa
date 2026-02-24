
import os
import json
import time
import sys
import urllib.request
import urllib.error
import urllib.parse
import google.generativeai as genai
from config.schema import factory_config

sys.stdout.reconfigure(encoding='utf-8')

api_key = os.getenv("GOOGLE_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

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
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
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
            "gemini-3.1-pro-preview",
            "gemini-3-flash-preview",
            "codex-5.3",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-1.0-pro"
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

def resolve_dynamic_model(engine_id: str) -> str:
    """
    [Autobahn Engine] API 키 유무를 기반으로 최적의 모델명(String)을 동적으로 맵핑합니다.
    - 우선순위 1: 배정된 엔진의 주력 벤더 API 키가 있을 경우 해당 최고 모델 즉시 배정
    - 우선순위 2: 주력 벤더 키가 없으나 다른 벤더 키가 있을 경우 그쪽 최고 모델로 우회 (Fallback)
    - 우선순위 3: 모든 API 키가 누락되었을 경우 분야별 최신 무료 티어 모델로 강등 (All-Empty)
    """
    keys = {
        "google": bool(os.getenv("GOOGLE_API_KEY")),
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY"))
    }
    
    # [조건 3] API 키가 한 개도 없을 경우 (최신 무료 티어로 통일)
    if not any(keys.values()):
        if engine_id == "gemini_flash": return "gemini-3.0-flash" 
        elif engine_id == "codex": return "claude-4.5-haiku-latest"
        elif engine_id == "research_pro": return "gpt-4.1-mini"
        return "gemini-3.0-flash"

    # [조건 1 & 2] 엔진별 최적 벤더 탐색 및 우회 로직
    if engine_id == "gemini_flash":
        if keys["google"]:
            models = get_available_models()
            for m in models:
                if "gemini" in m and "flash" in m and "exp" not in m:
                    return m.replace("models/", "")
            return "gemini-3.0-flash"
        elif keys["anthropic"]:
            models = fetch_anthropic_models()
            for m in models:
                if "haiku" in m: return m
            return "claude-4.5-haiku-latest"
        elif keys["openai"]:
            models = fetch_openai_models()
            for m in models:
                if "mini" in m: return m
            return "gpt-4.1-mini"
        
    elif engine_id == "codex":
        if keys["anthropic"]:
            models = fetch_anthropic_models()
            for m in models:
                if "sonnet" in m: return m
            return "claude-4.6-sonnet-latest"
        elif keys["openai"]:
            models = fetch_openai_models()
            for m in models:
                if "codex" in m or "pro" in m: return m
            return "gpt-5.3-codex"
        elif keys["google"]:
            models = get_available_models()
            for m in models:
                if "pro" in m and "exp" not in m: return m.replace("models/", "")
            return "gemini-3.1-pro"
        
    elif engine_id == "research_pro":
        if keys["openai"]:
            models = fetch_openai_models()
            for m in models:
                if m.startswith("o") or "pro" in m: return m
            return "o3"
        elif keys["anthropic"]:
            models = fetch_anthropic_models()
            for m in models:
                if "opus" in m or "sonnet" in m: return m
            return "claude-4.6-opus-latest"
        elif keys["google"]:
            models = get_available_models()
            for m in models:
                if "pro" in m and "exp" not in m: return m.replace("models/", "")
            return "gemini-3.1-pro"

    # 알 수 없는 엔진이거나 매칭 실패 시 fallback
    return "gemini-2.0-flash"
