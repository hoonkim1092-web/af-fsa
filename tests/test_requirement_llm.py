import json


def test_model_router_requirement_compares_multiple_engine_candidates(monkeypatch):
    monkeypatch.delenv("AGENT_CHAT_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-secret")
    monkeypatch.delenv("AGENT_DISABLE_ENGINE_API_KEYS", raising=False)

    import core.requirement_llm as requirement_llm
    from core.model_router import ModelRouter

    monkeypatch.setattr(requirement_llm, "_pick_anthropic_model", lambda tier: "claude-4.6-sonnet")
    monkeypatch.setattr(requirement_llm, "_pick_openai_model", lambda prefer_reasoning=False, prefer_mini=False: "gpt-5")
    monkeypatch.setattr(requirement_llm, "get_best_model", lambda priority_list=None: "models/gemini-2.5-pro")

    candidates = requirement_llm.list_requirement_candidates()

    assert [candidate.provider_id for candidate in candidates[:3]] == [
        "anthropic_api",
        "openai_api",
        "google_api",
    ]
    assert ModelRouter().pick("requirement") == "claude-4.6-sonnet"


def test_requirement_analyzer_uses_cli_provider_when_engine_api_keys_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CHAT_PROVIDER", "codex_cli")
    monkeypatch.setenv("AGENT_DISABLE_ENGINE_API_KEYS", "1")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import core.requirement_llm as requirement_llm
    from core.manager import RequirementAnalyzer
    from core.model_router import ModelRouter

    calls = []

    def fake_execute_cli_chat(request, run_command=None, install_command_runner=None):
        calls.append({"provider_id": request.provider_id, "model": request.model, "workspace": request.workspace})
        return {
            "ok": True,
            "reason": request.provider_id,
            "text": json.dumps(
                {
                    "goal": "cli analyzed",
                    "missing_skills": ["task_planning"],
                    "constraints": ["network_allowed"],
                    "risk_level": "elevated",
                }
            ),
            "stdout": "",
            "stderr": "",
            "returncode": 0,
        }

    monkeypatch.setattr(requirement_llm, "execute_cli_chat", fake_execute_cli_chat)

    analyzer = RequirementAnalyzer(ModelRouter())
    result = analyzer.analyze({"role": "General Assistant"}, "plan the task", workspace=str(tmp_path))

    assert result["goal"] == "cli analyzed"
    assert result["missing_skills"] == ["task_planning"]
    assert result["risk_level"] == "elevated"
    assert calls == [{"provider_id": "codex_cli", "model": "gpt-5", "workspace": str(tmp_path)}]
