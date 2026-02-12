import google.generativeai as genai
import os

# API Key 설정 (환경변수 우선)
api_key = os.getenv("GOOGLE_API_KEY") or "AIzaSyAZMMA66FaeMWWY580Taby7mft8DVXapMs"
genai.configure(api_key=api_key)

print("[Check] Listing available models...")

try:
    available_models = []
    # 페이지네이션 처리 없이 전체 목록 조회
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"- Found: {m.name}")
            available_models.append(m.name)

    if not available_models:
        print("[Error] No available models found.")
    else:
        print(f"\n[Success] Found {len(available_models)} models.")
        
except Exception as e:
    print(f"[Error] {e}")