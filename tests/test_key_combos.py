import importlib
from unittest.mock import patch

import pytest


ENGINE_IDS = [
    "researcher_gemini",
    "gemini_pro",
    "gemini_flash",
    "architect_claude",
    "coder_claude",
    "manager_gpt",
    "codex",
    "reasoner_o",
]

FAKE_GEMINI = ["models/gemini-3.1-pro-latest", "models/gemini-3.1-flash-latest"]
FAKE_ANTHROPIC = ["claude-opus-4-20260201", "claude-sonnet-4.5-20260115"]
FAKE_OPENAI = ["gpt-4.1", "o3-2026-01", "codex-5.3"]


@pytest.mark.parametrize(
    ("env", "expected_tiers"),
    [
        (
            {"GOOGLE_API_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""},
            {engine_id: "uncallable" for engine_id in ENGINE_IDS},
        ),
        (
            {"GOOGLE_API_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant"},
            {"architect_claude": "primary", "coder_claude": "primary", "researcher_gemini": "uncallable"},
        ),
        (
            {"GOOGLE_API_KEY": "", "OPENAI_API_KEY": "sk-oai", "ANTHROPIC_API_KEY": ""},
            {"codex": "primary", "manager_gpt": "primary", "reasoner_o": "primary"},
        ),
        (
            {"GOOGLE_API_KEY": "AIza", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""},
            {"researcher_gemini": "primary", "gemini_flash": "primary", "codex": "uncallable"},
        ),
    ],
)
def test_engine_selection_per_key_combination(monkeypatch, env, expected_tiers):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AGENT_FORCE_MODEL", raising=False)

    import model_utils

    importlib.reload(model_utils)

    with (
        patch.object(model_utils, "get_available_models", return_value=FAKE_GEMINI),
        patch.object(
            model_utils,
            "fetch_anthropic_models",
            return_value=FAKE_ANTHROPIC if env["ANTHROPIC_API_KEY"] else [],
        ),
        patch.object(
            model_utils,
            "fetch_openai_models",
            return_value=FAKE_OPENAI if env["OPENAI_API_KEY"] else [],
        ),
    ):
        for engine_id in ENGINE_IDS:
            selection = model_utils.resolve_dynamic_model(engine_id)
            assert selection.model
            if engine_id in expected_tiers:
                assert selection.tier == expected_tiers[engine_id]
