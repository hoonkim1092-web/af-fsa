import os
import sys
import subprocess
import json

import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import google.generativeai as genai
from dotenv import load_dotenv
from model_utils import get_best_model
from repo_shortcuts import handle_repo_shortcut

sys.stdout.reconfigure(encoding="utf-8")

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, ".env")
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    print("[ERROR] GOOGLE_API_KEY is missing in .env")
    sys.exit(1)

genai.configure(api_key=api_key)


def resolve_python_exec() -> str:
    candidates = [
        sys.executable,
        os.path.expandvars(r"%LOCALAPPDATA%\Python\bin\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return "python"


class SmartLinker:
    def __init__(self):
        self.gatekeeper = genai.GenerativeModel(get_best_model(["gemini-2.0-flash", "gemini-1.5-flash"]))

    def analyze_and_route(self, user_input: str) -> dict:
        print("[Antigravity] analyzing request and selecting model...")

        prompt = f"""
User Request: "{user_input}"

Analyze the request and return JSON only:
{{
  "role_name": "English role name (e.g., Logistics Manager)",
  "model_choice": "GEMINI_3_PRO|GEMINI_1_5_PRO|GEMINI_2_FLASH",
  "reason": "short reason"
}}
"""
        try:
            response = self.gatekeeper.generate_content(prompt)
            res_text = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(res_text)

            choice = str(data.get("model_choice", "")).upper()
            if "GEMINI_3" in choice:
                priority = ["gemini-3.0-pro", "gemini-2.0-pro", "gemini-1.5-pro"]
            elif "GEMINI_1_5" in choice:
                priority = ["gemini-1.5-pro", "gemini-2.0-flash"]
            else:
                priority = ["gemini-2.0-flash", "gemini-1.5-flash"]

            data["actual_model"] = get_best_model(priority)
            return data
        except Exception as e:
            print(f"[WARN] routing failed, fallback to default model: {e}")
            return {"role_name": "General Assistant", "actual_model": get_best_model()}


def handle_command(linker: SmartLinker, user_input: str) -> bool:
    user_input = user_input.strip()
    if not user_input:
        return True
    if user_input.lower() in ["exit", "quit"]:
        return False
    if handle_repo_shortcut(user_input):
        return True

    plan = linker.analyze_and_route(user_input)
    role_name = plan["role_name"]
    selected_model = plan["actual_model"]

    print(f"Target role: {role_name}")
    print(f"Selected model: {selected_model}")
    print("Launching factory manager...\n")

    factory_path = os.path.join(current_dir, "factory_manager.py")
    subprocess.run([resolve_python_exec(), factory_path, role_name, selected_model])
    print("\nDone.\n")
    return True


def main():
    linker = SmartLinker()

    print("\n" + "=" * 50)
    print("[Logi-Mind Intelligent Link] started")
    print("=" * 50 + "\n")

    if not sys.stdin.isatty():
        for line in sys.stdin:
            if not handle_command(linker, line):
                break
        return

    while True:
        try:
            user_input = input("Command: ")
            if not handle_command(linker, user_input):
                break
        except KeyboardInterrupt:
            break
        except EOFError:
            print()
            break


if __name__ == "__main__":
    main()
