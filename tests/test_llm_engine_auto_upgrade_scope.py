import core.llm_engine as llm_engine


def test_auto_upgrade_is_enabled_by_env_flag(monkeypatch):
    monkeypatch.setenv("AGENT_FLASH_AUTO_UPGRADE", "1")
    monkeypatch.setattr(llm_engine, "get_latest_flash_model", lambda: "models/gemini-3-flash-preview")
    monkeypatch.setattr(llm_engine.LLMEngine, "init_model_with_current_key", lambda self: None)

    engine = llm_engine.LLMEngine(model_name="gemini-2.0-flash")
    assert engine.model_name == "models/gemini-3-flash-preview"


def test_auto_upgrade_is_disabled_without_env_flag(monkeypatch):
    monkeypatch.delenv("AGENT_FLASH_AUTO_UPGRADE", raising=False)
    monkeypatch.setattr(llm_engine, "get_latest_flash_model", lambda: "models/gemini-3-flash-preview")
    monkeypatch.setattr(llm_engine.LLMEngine, "init_model_with_current_key", lambda self: None)

    engine = llm_engine.LLMEngine(model_name="gemini-2.0-flash")
    assert engine.model_name == "models/gemini-2.0-flash"
