import importlib

import model_utils


def test_normalize_model_name_for_gemini_prefix():
    assert model_utils.normalize_model_name("gemini-2.0-flash") == "models/gemini-2.0-flash"
    assert model_utils.normalize_model_name("models/gemini-2.0-flash") == "models/gemini-2.0-flash"


def test_normalize_model_name_non_gemini_passthrough():
    assert model_utils.normalize_model_name("claude-sonnet-4.5") == "claude-sonnet-4.5"
    assert model_utils.normalize_model_name("codex-5.3") == "codex-5.3"


def test_forced_model_override(monkeypatch):
    monkeypatch.setenv("AGENT_FORCE_MODEL", "gemini-2.0-flash")
    importlib.reload(model_utils)
    assert model_utils.get_forced_model_override() == "models/gemini-2.0-flash"


class _FakeModels:
    def __init__(self):
        self.called = []

    def generate_content(self, model, contents, **kwargs):
        self.called.append(model)
        if model != "models/gemini-2.0-flash":
            raise RuntimeError("404 NOT_FOUND")
        return type("Resp", (), {"text": "ok"})()


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def test_generate_content_with_self_heal_retries_on_404():
    client = _FakeClient()
    res = model_utils.generate_content_with_self_heal(client, "gemini-1.5-flash", "hello")
    assert res.text == "ok"
    assert client.models.called[0] == "models/gemini-1.5-flash"
    assert "models/gemini-2.0-flash" in client.models.called
