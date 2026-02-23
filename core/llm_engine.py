import os
import re
import json
import google.generativeai as genai

class LLMEngine:
    def __init__(self, model_name="gemini-2.0-flash"):
        self.model_name = model_name
        
        # Configure Gemini API if key is available
        api_key = os.getenv("GOOGLE_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        else:
            print("[WARNING] GOOGLE_API_KEY is not set.")
            
        self.model = genai.GenerativeModel(model_name=self.model_name)

    def generate(self, prompt: str) -> str:
        """단순 텍스트 생성을 수행합니다."""
        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            print(f"[LLMEngine Error] Text generation failed: {e}")
            return ""

    def generate_json(self, prompt: str) -> dict:
        """JSON 형식의 응답을 강제하고 파싱하여 반환합니다."""
        try:
            response = self.model.generate_content(prompt)
            text = response.text
            
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
    """(기존 model_utils 대체/통합 가능성 고려용)"""
    if fallback_list:
        return fallback_list[0]
    return "gemini-2.0-flash" # Fallback
