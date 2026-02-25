import os
import sys

# Remove real keys for test
orig_g = os.environ.get("GOOGLE_API_KEY")
orig_o = os.environ.get("OPENAI_API_KEY")

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import model_utils

def test_routes():
    print("[1] Both Keys Present")
    os.environ["GOOGLE_API_KEY"] = "mock_google"
    os.environ["OPENAI_API_KEY"] = "mock_openai"
    print("  - codex:", model_utils.resolve_dynamic_model("codex"))
    print("  - gemini_flash:", model_utils.resolve_dynamic_model("gemini_flash"))
    print("  - research_pro:", model_utils.resolve_dynamic_model("research_pro"))

    print("\n[2] Only Google Key")
    os.environ["GOOGLE_API_KEY"] = "mock_google"
    os.environ["OPENAI_API_KEY"] = ""
    print("  - codex:", model_utils.resolve_dynamic_model("codex"))
    print("  - gemini_flash:", model_utils.resolve_dynamic_model("gemini_flash"))
    print("  - research_pro:", model_utils.resolve_dynamic_model("research_pro"))

    print("\n[3] Only OpenAI Key")
    os.environ["GOOGLE_API_KEY"] = ""
    os.environ["OPENAI_API_KEY"] = "mock_openai"
    print("  - codex:", model_utils.resolve_dynamic_model("codex"))
    print("  - gemini_flash:", model_utils.resolve_dynamic_model("gemini_flash"))
    print("  - research_pro:", model_utils.resolve_dynamic_model("research_pro"))

    print("\n[4] No Keys")
    os.environ["GOOGLE_API_KEY"] = ""
    os.environ["OPENAI_API_KEY"] = ""
    print("  - codex:", model_utils.resolve_dynamic_model("codex"))
    print("  - gemini_flash:", model_utils.resolve_dynamic_model("gemini_flash"))
    print("  - research_pro:", model_utils.resolve_dynamic_model("research_pro"))

if __name__ == "__main__":
    test_routes()
