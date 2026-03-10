"""
core/llm_engine.py
==================
[Release Engine]
- 역할: 다중 Gemini API Key Load Balancer + 실행 엔진(LLMEngine 클래스)
- 모델 선택 두뇌: model_utils.py 에 완전 위임 (중복 제거)
- 대상: Release/Output Agents (자동 최신 Flash 배정)
"""

import os
import json
import time
from google import genai
from core.providers.registry import engine_api_keys_disabled, get_engine_api_key
from model_utils import normalize_model_name, generate_content_with_self_heal

# =============================================================================
# [Load Balancer] 다중 API Key 관리
# GOOGLE_API_KEY, GOOGLE_API_KEY_2, GOOGLE_API_KEY_3 ... 모두 수집
# =============================================================================
_gemini_keys = []
_current_key_idx = 0

def _load_gemini_keys():
    global _gemini_keys
    if not _gemini_keys:
        if engine_api_keys_disabled():
            return _gemini_keys
        for k, v in os.environ.items():
            if k.startswith("GOOGLE_API_KEY") and v.strip():
                _gemini_keys.append(v.strip())
        fallback = get_engine_api_key("google")
        if fallback and fallback not in _gemini_keys:
            _gemini_keys.append(fallback)
    return _gemini_keys

def get_next_gemini_key() -> str | None:
    global _current_key_idx
    keys = _load_gemini_keys()
    if not keys:
        return None
    _current_key_idx = (_current_key_idx + 1) % len(keys)
    return keys[_current_key_idx]

def get_current_gemini_key() -> str | None:
    keys = _load_gemini_keys()
    if not keys:
        return None
    return keys[_current_key_idx]


# =============================================================================
# [Release Model] 최신 Flash 소환 — model_utils에 위임
# =============================================================================
def get_latest_flash_model() -> str:
    """
    [Release/Output Agents] 최신 Gemini Flash 모델을 반환합니다.
    모델 탐색 두뇌는 model_utils.get_dynamic_default_model에 위임하여
    버전 번호를 코드 상에 일절 하드코딩하지 않습니다.
    """
    try:
        from model_utils import get_dynamic_default_model
        return normalize_model_name(get_dynamic_default_model("flash"))
    except Exception:
        # 극단적 실패 시에도 버전 번호 없이 API 기본 별칭 사용
        return "models/gemini-2.0-flash"


def get_best_model(fallback_list=None) -> str:
    """
    [Skeleton vs Release Split]
    - Skeleton 호출 (fallback_list 전달 시): model_utils의 Triad 우선순위 로직 사용
    - Release 호출 (fallback_list=None): 최신 Flash로 자동 업그레이드
    """
    if fallback_list is not None:
        try:
            from model_utils import get_best_model as _skeleton_best
            return _skeleton_best(fallback_list)
        except ImportError:
            pass
    return get_latest_flash_model()


def _flash_auto_upgrade_enabled() -> bool:
    """환경변수 AGENT_FLASH_AUTO_UPGRADE=1로 제어."""
    env_val = str(os.getenv("AGENT_FLASH_AUTO_UPGRADE", "") or "").strip().lower()
    return env_val in ("1", "true", "yes", "on")

# =============================================================================
# [LLMEngine] Release 에이전트용 실행 엔진
# 다중 키 Load Balancer + Auto-Upgrade + 재시도 로직 포함
# =============================================================================
class LLMEngine:
    """
    Release/Output 에이전트용 실행 엔진.
    - 구버전 flash 호출 시 자동 최신 버전 업그레이드
    - 다중 API Key 간 Load Balancer 적용
    - generate(text) / generate_json(text) 제공
    """

    def __init__(self, model_name: str = None):
        explicit_model_requested = model_name is not None
        # model_name이 없으면 동적으로 최신 Flash를 선택 (버전 하드코딩 배제)
        if model_name is None:
            model_name = get_latest_flash_model()
        model_name = normalize_model_name(model_name)

        # [Stability FIX] Auto-Upgrade: Flash 계열은 항상 최신판으로 강제 상향하되,
        # 사용자가 명시적으로 과거/특정 버전을 요청한 경우(Gemini 3 Flash 등)나 
        # get_forced_model_override()에 해당하는 경우는 업그레이드를 생략합니다.
        from model_utils import get_forced_model_override
        forced = get_forced_model_override()
        
        should_upgrade = False
        if explicit_model_requested and _flash_auto_upgrade_enabled() and "gemini" in model_name and "flash" in model_name:
            if forced and normalize_model_name(forced) == model_name:
                should_upgrade = False
            elif "gemini-3" in model_name: # Gemini 3은 현재 최신 실험 버전이므로 유지
                should_upgrade = False
            else:
                should_upgrade = True

        if should_upgrade:
            latest = get_latest_flash_model()
            if latest and latest != model_name:
                # Avoid UnicodeEncodeError on cp949 consoles.
                print(f"[Auto-Upgrade] '{model_name}' -> '{latest}'")
                self.model_name = latest
            else:
                self.model_name = model_name
        else:
            self.model_name = model_name

        self.init_model_with_current_key()

    def init_model_with_current_key(self):
        key = get_current_gemini_key()
        if not key:
            print("[WARNING] No GOOGLE_API_KEY found.")
        self._client = genai.Client(api_key=key) if key else None

    def _execute_with_retry(self, prompt: str) -> str | None:
        max_retries = (len(_gemini_keys) if _gemini_keys else 1) * 3
        for attempt in range(max_retries):
            try:
                if self._client is None:
                    raise RuntimeError("No GOOGLE_API_KEY configured")
                response = generate_content_with_self_heal(
                    self._client,
                    self.model_name,
                    prompt,
                )
                return response.text
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "quota" in err_str or "exhausted" in err_str:
                    if len(_gemini_keys) > 1:
                        print(f"🔄 [Load Balancer] Rate limit. Switching key {attempt+1}/{max_retries}...")
                        get_next_gemini_key()
                        self.init_model_with_current_key()
                        time.sleep(2)
                        continue
                    else:
                        print(f"🔄 [API] Rate limit hit. Retrying in 5s... ({attempt+1}/{max_retries})")
                        time.sleep(5)
                        continue
                elif "503" in err_str or "unavailable" in err_str or "500" in err_str:
                    print(f"🔄 [API] 503/500 Server Error. Retrying in 5s... ({attempt+1}/{max_retries})")
                    time.sleep(5)
                    continue
                else:
                    print(f"[LLMEngine Error] {e}")
                    return None
        return None

    def generate(self, prompt: str) -> str:
        """단순 텍스트 생성."""
        text = self._execute_with_retry(prompt)
        return text.strip() if text else ""

    def generate_json(self, prompt: str) -> dict:
        """JSON 응답 강제 파싱."""
        text = self._execute_with_retry(prompt)
        if not text:
            return {}
        try:
            if "```json" in text:
                json_block = text.split("```json")[1].split("```")[0].strip()
            else:
                json_block = text.strip()
                import re as _re
                _m = _re.search(r'\{[\s\S]*\}', json_block)
                if _m:
                    json_block = _m.group(0)
            return json.loads(json_block)
        except json.JSONDecodeError as e:
            print(f"[LLMEngine Error] JSON parse failed: {e}")
            return {}
        except Exception as e:
            print(f"[LLMEngine Error] {type(e).__name__}: {e}")
            return {}
