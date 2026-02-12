
import os
import json
import time
import sys
import google.generativeai as genai
from dotenv import load_dotenv

# Reconfigure stdout to utf-8 for Windows console
sys.stdout.reconfigure(encoding='utf-8')

# Ensure .env is loaded
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models_cache.json")
CACHE_EXPIRY = 24 * 60 * 60  # 24 hours in seconds

def log(msg):
    # Retrieve the caller module name for better logging
    caller = sys._getframe(1).f_globals.get('__name__')
    print(f"[{caller}] 🤖 {msg}")

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Check expiry
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
    Uses file-based caching to avoid redundant API calls.
    """
    if not force_refresh:
        cached = load_cache()
        if cached:
            return cached

    log("Fetching available models from Google API...")
    try:
        models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                models.append(m.name)
        
        if models:
            save_cache(models)
        return models
    except Exception as e:
        log(f"Failed to list models: {e}. Returning fallback list.")
        # Fallback if API fails (and no cache)
        return [
            "models/gemini-2.0-flash",
            "models/gemini-1.5-flash",
            "models/gemini-1.5-pro"
        ]

def get_best_model(priority_list=None):
    """
    Selects the best available model based on the provided priority list.
    If priority_list is None, uses a default high-performance dynamic list.
    """
    if priority_list is None:
        priority_list = [
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-1.0-pro"
        ]
        
    available = get_available_models()
    
    for p in priority_list:
        for m in available:
            if p in m:
                # log(f"Selected model: {m} (matched priority '{p}')")
                return m
                
    # Fallback: return the first available model or a safe default
    if available:
        log(f"No priority match found. Using first available: {available[0]}")
        return available[0]
        
    return "models/gemini-2.0-flash"
