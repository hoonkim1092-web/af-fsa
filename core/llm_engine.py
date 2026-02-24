import os
import re
import json
import time
import google.generativeai as genai

# --- API Key Load Balancer ---
_gemini_keys = []
for k, v in os.environ.items():
    if k.startswith("GOOGLE_API_KEY") and v.strip():
        _gemini_keys.append(v.strip())

_current_key_idx = 0

def get_next_gemini_key():
    global _current_key_idx
    if not _gemini_keys:
        return None
    _current_key_idx = (_current_key_idx + 1) % len(_gemini_keys)
    return _gemini_keys[_current_key_idx]

def get_current_gemini_key():
    if not _gemini_keys:
        return None
    return _gemini_keys[_current_key_idx]

_LATEST_FLASH_MODEL = None

def get_latest_flash_model() -> str:
    """API를 스캔하여 가용한 가장 최신의 gemini-flash 모델을 찾아냅니다."""
    global _LATEST_FLASH_MODEL
    if _LATEST_FLASH_MODEL:
        return _LATEST_FLASH_MODEL
        
    try:
        models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'gemini' in m.name and 'flash' in m.name and 'exp' not in m.name and 'vision' not in m.name:
                    models.append(m.name)
        
        def extract_version(name):
            match = re.search(r'gemini-(\d+\.\d+)-flash', name)
            if match:
                return float(match.group(1))
            return 0.0
            
        if models:
            models.sort(key=extract_version, reverse=True)
            _LATEST_FLASH_MODEL = models[0].replace('models/', '')
            print(f"🚀 [Auto-Upgrade] 최신 프론티어 플래시 모델 탐지됨: {_LATEST_FLASH_MODEL}")
            return _LATEST_FLASH_MODEL
    except Exception as e:
        pass
        
    _LATEST_FLASH_MODEL = "gemini-3.0-flash" 
    return _LATEST_FLASH_MODEL

class LLMEngine:
    def __init__(self, model_name="gemini-3.0-flash"):
        # Auto-Upgrade Logic: 사용자가 구버전 flash를 호출했어도 자동으로 최신판으로 상향
        if "gemini" in model_name and "flash" in model_name:
            latest = get_latest_flash_model()
            
            def extract_v(name):
                m = re.search(r'gemini-(\d+\.\d+)-flash', name)
                return float(m.group(1)) if m else 0.0
                
            if latest and extract_v(latest) > extract_v(model_name):
                print(f"🔄 [Auto-Upgrade Enforcer] '{model_name}' -> '{latest}' 로 자동 업그레이드 배정되었습니다.")
                self.model_name = latest
            else:
                self.model_name = model_name
        else:
            self.model_name = model_name
        
        self.init_model_with_current_key()

    def init_model_with_current_key(self):
        key = get_current_gemini_key()
        if key:
            genai.configure(api_key=key)
        else:
            print("[WARNING] No GOOGLE_API_KEY found.")
        self.model = genai.GenerativeModel(model_name=self.model_name)

    def _execute_with_retry(self, prompt: str) -> str:
        max_retries = len(_gemini_keys) if _gemini_keys else 1
        for attempt in range(max_retries):
            try:
                response = self.model.generate_content(prompt)
                return response.text
            except Exception as e:
                err_str = str(e).lower()
                # 429 Too Many Requests 또는 Quota 초과 시 다음 키로 스위칭
                if "429" in err_str or "quota" in err_str or "exhausted" in err_str:
                    if len(_gemini_keys) > 1:
                        print(f"🔄 [Load Balancer] Rate limit hit. Switching API Key {attempt+1}/{max_retries}...")
                        get_next_gemini_key()
                        self.init_model_with_current_key()
                        time.sleep(1)
                        continue
                print(f"[LLMEngine Error] Execution failed: {e}")
                return None
        return None

    def generate(self, prompt: str) -> str:
        """단순 텍스트 생성을 수행합니다."""
        text = self._execute_with_retry(prompt)
        return text.strip() if text else ""

    def generate_json(self, prompt: str) -> dict:
        """JSON 형식의 응답을 강제하고 파싱하여 반환합니다."""
        text = self._execute_with_retry(prompt)
        if not text:
            return {}
        try:
            
            # Extract JSON block if surrounded by markdown
            if "```json" in text:
                json_block = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                 json_block = text.split("```")[1].split("```")[0].strip()
            else:
                json_block = text.strip()
                
            return json.loads(json_block)
        except json.JSONDecodeError as e:
            print(f"[LLMEngine Error] JSON parsing failed: {e}\nRaw Text: {text}")
            return {}
        except Exception as e:
            print(f"[LLMEngine Error] JSON generation failed: {type(e).__name__}: {str(e)}")
            return {}

def get_best_model(fallback_list=None):
    """Fallback List를 무시하고 무조건 최신 발견 모델로 Auto-Upgrade 강제"""
    return get_latest_flash_model()
