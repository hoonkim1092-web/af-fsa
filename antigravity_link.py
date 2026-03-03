import os
import sys
import subprocess
import json
import re
from datetime import datetime

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


def safe_id(text: str, fallback: str = "") -> str:
    t = str(text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or fallback


def safe_key(text: str, fallback: str = "entry") -> str:
    cleaned = "".join([c for c in str(text or "") if c.isalnum() or c in (" ", "_", "-")]).strip()
    if not cleaned:
        return fallback
    return cleaned[:120]


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)].rstrip() + "..."


def resolve_global_memory_dir() -> tuple[str | None, str | None]:
    raw_user = os.getenv("AGENT_GLOBAL_USER_KEY", "").strip()
    user_key = safe_id(raw_user, fallback="")
    if not user_key:
        return None, None

    override_root = os.getenv("AGENT_GLOBAL_PROJECT_ROOT", "").strip()
    if override_root:
        global_root = os.path.abspath(os.path.expanduser(override_root))
    else:
        global_root = os.path.abspath(os.path.join(current_dir, "projects", f"global_{user_key}"))
    memory_dir = os.path.join(global_root, "data", "memory", "antigravity", "routing")
    os.makedirs(memory_dir, exist_ok=True)
    return user_key, memory_dir


def mirror_global_memory(user_input: str, plan: dict, exit_code: int) -> None:
    user_key, memory_dir = resolve_global_memory_dir()
    if not user_key or not memory_dir:
        return

    now = datetime.utcnow().isoformat() + "Z"
    role_name = str((plan or {}).get("role_name") or "General Assistant")
    model_name = str((plan or {}).get("actual_model") or "")
    reason = str((plan or {}).get("reason") or "")

    summary = (
        f"user={_truncate(user_input, 240)} | "
        f"role={_truncate(role_name, 80)} | "
        f"model={_truncate(model_name, 80)} | "
        f"reason={_truncate(reason, 140)} | "
        f"exit={int(exit_code)}"
    )

    key_base = safe_key(f"antigravity {role_name} {user_input}", fallback="antigravity_entry")
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    file_path = os.path.join(memory_dir, f"{safe_id(key_base, fallback='entry')}_{stamp}.json")
    record = {
        "key": key_base,
        "value": summary,
        "category": "routing",
        "agent_id": "antigravity",
        "memory_scope": "global",
        "source": "antigravity_link",
        "global_user_key": user_key,
        "details": {
            "user_input": _truncate(user_input, 1200),
            "role_name": role_name,
            "actual_model": model_name,
            "reason": reason,
            "exit_code": int(exit_code),
        },
        "created_at": now,
        "updated_at": now,
    }
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except Exception:
        # Best-effort mirror only.
        return


class SmartLinker:
    def __init__(self):
        self.gatekeeper = genai.GenerativeModel(get_best_model(["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]))

    def analyze_and_route(self, user_input: str) -> dict:
        print("[Antigravity] analyzing request and selecting model...")

        prompt = f"""
User Request: \"{user_input}\"

Analyze the request and return JSON only:
{{
  \"role_name\": \"English role name (e.g., Logistics Manager)\",
  \"model_choice\": \"GEMINI_3_PRO|GEMINI_1_5_PRO|GEMINI_2_FLASH\",
  \"reason\": \"short reason\"
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
                priority = ["gemini-1.5-pro", "gemini-2.5-flash"]
            else:
                priority = ["gemini-2.5-flash", "gemini-1.5-flash"]

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
    proc = subprocess.run([resolve_python_exec(), factory_path, role_name, selected_model], check=False)
    mirror_global_memory(user_input=user_input, plan=plan, exit_code=int(proc.returncode))
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
