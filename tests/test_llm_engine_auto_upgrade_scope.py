import core.llm_engine as llm_engine


def test_auto_upgrade_applies_only_for_minesweeper(monkeypatch):
    monkeypatch.setenv("AGENT_PROJECT_ID", "minesweeper")
    monkeypatch.setattr(llm_engine, "get_latest_flash_model", lambda: "models/gemini-3-flash-preview")
    monkeypatch.setattr(llm_engine.LLMEngine, "init_model_with_current_key", lambda self: None)

    engine = llm_engine.LLMEngine(model_name="gemini-2.0-flash")
    assert engine.model_name == "models/gemini-3-flash-preview"


def test_auto_upgrade_is_disabled_outside_minesweeper(monkeypatch):
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_general")
    monkeypatch.setattr(llm_engine, "get_latest_flash_model", lambda: "models/gemini-3-flash-preview")
    monkeypatch.setattr(llm_engine.LLMEngine, "init_model_with_current_key", lambda self: None)

    engine = llm_engine.LLMEngine(model_name="gemini-2.0-flash")
    assert engine.model_name == "models/gemini-2.0-flash"
