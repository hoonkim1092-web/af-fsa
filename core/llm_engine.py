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

# =============================================================================
# [Load Balancer] 다중 API Key 관리
# GOOGLE_API_KEY, GOOGLE_API_KEY_2, GOOGLE_API_KEY_3 ... 모두 수집
# =============================================================================
_gemini_keys = []
for k, v in os.environ.items():
    if k.startswith("GOOGLE_API_KEY") and v.strip():
        _gemini_keys.append(v.strip())

_current_key_idx = 0


def get_next_gemini_key() -> str | None:
    global _current_key_idx
    if not _gemini_keys:
        return None
    _current_key_idx = (_current_key_idx + 1) % len(_gemini_keys)
    return _gemini_keys[_current_key_idx]


def get_current_gemini_key() -> str | None:
    if not _gemini_keys:
        return None
    return _gemini_keys[_current_key_idx]


# =============================================================================
# [Release Model] 최신 Flash 소환 — model_utils에 위임
# =============================================================================
def get_latest_flash_model() -> str:
    """
    [Release/Output Agents] 최신 Gemini Flash 모델을 반환합니다.
    모델 탐색 두뇌는 model_utils.get_best_model에 위임합니다.
    """
    try:
        from model_utils import get_best_model
        return get_best_model(["gemini-3.1-flash", "gemini-3-flash", "gemini-2.5-flash", "gemini-2.0-flash"])
    except Exception:
        return "gemini-2.0-flash"


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

    def __init__(self, model_name: str = "gemini-2.0-flash"):
        # Auto-Upgrade: Flash 계열은 항상 최신판으로 강제 상향
        if "gemini" in model_name and "flash" in model_name:
            latest = get_latest_flash_model()
            if latest and latest != model_name:
                print(f"🔄 [Auto-Upgrade] '{model_name}' → '{latest}' 자동 업그레이드")
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
        max_retries = len(_gemini_keys) if _gemini_keys else 1
        for attempt in range(max_retries):
            try:
                if self._client is None:
                    raise RuntimeError("No GOOGLE_API_KEY configured")
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=prompt
                )
                return response.text
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "quota" in err_str or "exhausted" in err_str:
                    if len(_gemini_keys) > 1:
                        print(f"🔄 [Load Balancer] Rate limit. Switching key {attempt+1}/{max_retries}...")
                        get_next_gemini_key()
                        self.init_model_with_current_key()
                        time.sleep(1)
                        continue
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
            elif "```" in text:
                json_block = text.split("```")[1].split("```")[0].strip()
            else:
                json_block = text.strip()
            return json.loads(json_block)
        except json.JSONDecodeError as e:
            print(f"[LLMEngine Error] JSON parse failed: {e}")
            return {}
        except Exception as e:
            print(f"[LLMEngine Error] {type(e).__name__}: {e}")
            return {}
