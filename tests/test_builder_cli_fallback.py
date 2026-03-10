import importlib
import types
from pathlib import Path


def _load_builder(monkeypatch, tmp_path, provider: str | None):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_BUILDER_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_BUILDER_MODEL", raising=False)
    monkeypatch.setenv("AGENT_DISABLE_ENGINE_API_KEYS", "1")
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(tmp_path / "proj"))
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_builder_cli")
    if provider:
        monkeypatch.setenv("AGENT_CHAT_PROVIDER", provider)
    else:
        monkeypatch.delenv("AGENT_CHAT_PROVIDER", raising=False)

    import core.config_paths
    import core.builder

    importlib.reload(core.config_paths)
    return importlib.reload(core.builder)


def test_builder_uses_cli_provider_when_google_key_missing(monkeypatch, tmp_path):
    builder_mod = _load_builder(monkeypatch, tmp_path, provider="gemini_cli")
    monkeypatch.setattr(builder_mod, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(builder_mod, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(builder_mod, "quick_guard", lambda code: (True, []))
    monkeypatch.setattr(builder_mod, "run_isolated", lambda path, timeout_sec: (True, {"ok": True}, ""))

    requests = []

    def fake_execute_cli_chat(request):
        requests.append(request)
        return {
            "ok": True,
            "provider_id": request.provider_id,
            "reason": request.provider_id,
            "text": (
                "def propose(ctx):\n"
                "    return {'ok': True}\n\n"
                "def apply(ctx):\n"
                "    return {'ok': True}\n\n"
                "def test(ctx):\n"
                "    return {'ok': True}\n"
            ),
        }

    monkeypatch.setattr(builder_mod, "execute_cli_chat", fake_execute_cli_chat)

    builder = builder_mod.SandboxedBuilder(types.SimpleNamespace(pick=lambda _stage: "models/gemini-2.5-flash"))
    ok, code_path, meta = builder.build_skill(
        agent={"role": "Game Logic Dev"},
        skill_name="demo_skill",
        reqs={"goal": "build game logic", "constraints": ["offline_only"]},
        run_id="run_builder_cli",
        evidence_pack={"targets": {"demo_skill": {"verified": False, "top_candidate": None}}},
    )

    assert ok is True
    assert code_path
    assert Path(code_path).exists()
    assert meta["last_test_ok"] is True
    assert requests
    assert requests[0].provider_id == "gemini_cli"
    assert requests[0].model == "gemini"
    assert "Return only raw Python code" in requests[0].system_prompt


def test_builder_without_cli_or_google_key_returns_no_api_key(monkeypatch, tmp_path):
    builder_mod = _load_builder(monkeypatch, tmp_path, provider=None)
    monkeypatch.setattr(builder_mod, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(builder_mod, "RUNS_DIR", str(tmp_path / "runs"))

    builder = builder_mod.SandboxedBuilder(types.SimpleNamespace(pick=lambda _stage: "models/gemini-2.5-flash"))
    ok, code_path, meta = builder.build_skill(
        agent={"role": "General Dev"},
        skill_name="missing_skill",
        reqs={"goal": "build missing skill", "constraints": []},
        run_id="run_builder_no_key",
        evidence_pack={"targets": {"missing_skill": {"verified": False, "top_candidate": None}}},
    )

    assert ok is False
    assert code_path is None
    assert meta["status"] == "disabled"
    assert meta["last_test_detail"]["reason"] == "no_api_key"
