
import os
import sys
import google.generativeai as genai
from dotenv import load_dotenv
import json

# Reconfigure stdout to utf-8 for Windows console
sys.stdout.reconfigure(encoding='utf-8')

# Load env
load_dotenv(".env")
api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

from model_utils import get_best_model

model_name = get_best_model()
print(f"DEBUG: Selected model: {model_name}")
model = genai.GenerativeModel(model_name)

def load_agent_config(agent_name):
    try:
        import yaml
        with open(f"agents/{agent_name}.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return None

def test_research(role):
    print(f"Testing research for role: {role}")
    himari_config = load_agent_config("himari")
    
    system_instruction = himari_config.get("prompt", {}).get("system_ko", "")
    print(f"Loaded Himari prompt length: {len(system_instruction)}")

    prompt = f"""
    {system_instruction}
    
    [사용자 요청]
    Role: {role}
    
    위 역할을 완벽하게 수행하기 위해 필요한 **Python CLI 도구(Skill) 2~3개**를 추천해줘.
    
    [출력 형식]
    너의 분석 결과(JSON)에서 `recommended_tools` 리스트만 추출해서 사용할 거야.
    하지만 너의 그 "초천재적인 분석"을 듣고 싶으니까, **JSON 블록**으로 결과를 줘.
    
    ```json
    {{
        "thought_process": "히마리의 분석 내용 (한국어, 반말, 도도하게)",
        "recommended_tools": ["tool_name_a", "tool_name_b"]
    }}
    ```
    도구 이름은 반드시 **영어, snake_case**여야 해.
    """
    
    try:
        response = model.generate_content(prompt)
        print("Response received.")
        print("-" * 20)
        print(response.text)
        print("-" * 20)
    except Exception as e:
        print(f"Error generating content: {e}")

if __name__ == "__main__":
    test_research("Stock Analyst")
