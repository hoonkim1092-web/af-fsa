import importlib


def test_inject_implementation_language_contract_is_idempotent(monkeypatch):
    monkeypatch.setenv("AGENT_DOC_LANGUAGE_CODE", "ko-KR")
    monkeypatch.setenv("AGENT_ENFORCE_OS_LANGUAGE_FOR_HUMAN_TEXT", "1")

    from core.implementation_language_policy import inject_implementation_language_contract

    base = "system prompt"
    combined = inject_implementation_language_contract(base)

    assert "system prompt" in combined
    assert "[Implementation Language Contract]" in combined
    assert "ko-KR" in combined
    assert "사람이 읽는 새 주석" in combined
    assert "외부 API 계약 문자열" in combined
    assert inject_implementation_language_contract(combined) == combined


def test_inject_implementation_language_contract_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_DOC_LANGUAGE_CODE", "ko-KR")
    monkeypatch.setenv("AGENT_ENFORCE_OS_LANGUAGE_FOR_HUMAN_TEXT", "0")

    from core.implementation_language_policy import inject_implementation_language_contract

    assert inject_implementation_language_contract("system prompt") == "system prompt"


def test_agent_runner_runtime_prompt_includes_implementation_language_contract(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_DOC_LANGUAGE_CODE", "ko-KR")
    monkeypatch.setenv("AGENT_ENFORCE_OS_LANGUAGE_FOR_HUMAN_TEXT", "1")

    import core.config_paths
    import core.utils
    import core.agent_runner as ar

    importlib.reload(core.config_paths)
    importlib.reload(core.utils)
    ar = importlib.reload(ar)

    runner = ar.AgentRunner(ar.ModelRouter())
    runtime_prompt = runner._build_runtime_system_prompt({"prompt": {"system_ko": "nested-prompt"}})

    assert "nested-prompt" in runtime_prompt
    assert "[Implementation Language Contract]" in runtime_prompt
    assert "사람이 읽는 새 주석" in runtime_prompt
    assert "ko-KR" in runtime_prompt


def test_codex_cli_prompt_includes_implementation_language_contract(monkeypatch):
    monkeypatch.setenv("AGENT_DOC_LANGUAGE_CODE", "ko-KR")
    monkeypatch.setenv("AGENT_ENFORCE_OS_LANGUAGE_FOR_HUMAN_TEXT", "1")

    from core.providers.cli import CliChatRequest, compose_cli_prompt

    prompt = compose_cli_prompt(
        CliChatRequest(
            provider_id="codex_cli",
            model="gpt-5",
            system_prompt="system prompt",
            task_input="execute task",
            workspace="D:/workspace",
        )
    )

    assert prompt.startswith("[Task]\nexecute task")
    assert "[Implementation Language Contract]" in prompt
    assert "사람이 읽는 새 주석" in prompt
    assert "ko-KR" in prompt