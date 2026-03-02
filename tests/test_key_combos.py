# -*- coding: utf-8 -*-
"""
[Test] Key Combination Engine Selection Verification (v2)
Handles ModelSelection namedtuple returns.
"""
import os, sys, importlib, types
from unittest.mock import patch, MagicMock

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock config.schema
fake_schema = types.ModuleType("config.schema")
fake_schema.factory_config = {}
sys.modules["config"] = types.ModuleType("config")
sys.modules["config.schema"] = fake_schema

ENGINE_IDS = [
    "researcher_gemini", "gemini_pro", "gemini_flash",
    "architect_claude", "coder_claude",
    "manager_gpt", "codex", "reasoner_o",
]

FAKE_GEMINI = ["models/gemini-3.1-pro-latest", "models/gemini-3.1-flash-latest"]
FAKE_ANTHROPIC = ["claude-opus-4-20260201", "claude-sonnet-4.5-20260115"]
FAKE_OPENAI = ["gpt-4.1", "o3-2026-01", "codex-5.3"]

SCENARIOS = {
    "S1: Anthropic ONLY": {"GOOGLE_API_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant"},
    "S2: OpenAI ONLY":    {"GOOGLE_API_KEY": "", "OPENAI_API_KEY": "sk-oai", "ANTHROPIC_API_KEY": ""},
    "S3: Google ONLY":    {"GOOGLE_API_KEY": "AIza",     "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""},
    "S4: NO KEYS":        {"GOOGLE_API_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""},
}

def run():
    print("=" * 80)
    print("[TEST] Engine selection per key combination")
    print("=" * 80)
    issues = []

    for sname, env in SCENARIOS.items():
        print(f"\n--- {sname} ---")

        with patch.dict(os.environ, env, clear=False):
            import model_utils
            importlib.reload(model_utils)

            with patch.object(model_utils, 'get_available_models', return_value=FAKE_GEMINI), \
                 patch.object(model_utils, 'fetch_anthropic_models', return_value=FAKE_ANTHROPIC if env["ANTHROPIC_API_KEY"] else []), \
                 patch.object(model_utils, 'fetch_openai_models', return_value=FAKE_OPENAI if env["OPENAI_API_KEY"] else []):

                for eid in ENGINE_IDS:
                    try:
                        res = model_utils.resolve_dynamic_model(eid)
                        model = res.model
                        tier = res.tier
                        tag = "OK"
                        
                        # [검증 1] UNCALLABLE 상태여야 하는 경우 (S4)
                        if sname == "S4: NO KEYS" and tier != "uncallable":
                            tag = "FAIL"
                            issues.append((sname, eid, f"Should be UNCALLABLE but got {tier}"))
                        
                        # [검증 2] 교차 폴백 확인
                        if sname == "S1: Anthropic ONLY" and eid == "researcher_gemini":
                            if tier != "uncallable": # 현재 로직상 Google 키 없으므로 uncallable이어야 함
                                tag = "FAIL"
                                issues.append((sname, eid, f"Expected UNCALLABLE (no Google key), got {tier}"))

                        print(f"  [{tag:4s}] {eid:20s} -> {model:30s} ({tier})")
                    except Exception as e:
                        print(f"  [ERR ] {eid:20s} -> {e}")
                        issues.append((sname, eid, f"Exception: {e}"))

    print(f"\n{'=' * 80}\n[SUMMARY] {len(issues)} issues found\n{'=' * 80}")
    for s, e, i in issues: print(f"  [{s}] {e}: {i}")
    return issues

if __name__ == "__main__":
    if not run(): sys.exit(0)
    else: sys.exit(1)
