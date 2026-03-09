def test_agent_manager_cli_bootstrap_creates_fallback_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_CHAT_PROVIDER", "gemini_cli")

    from agent_launcher import AgentManager, ModelRouter

    workspace = tmp_path / "proj"
    workspace.mkdir(parents=True, exist_ok=True)

    agent = AgentManager(ModelRouter()).get_or_create("General Assistant", workspace=str(workspace))

    assert agent["role"] == "General Assistant"
    assert agent["system_ko"]
    assert agent["signature_lines"]
    assert (workspace / "agents" / "general_assistant.yaml").exists()
