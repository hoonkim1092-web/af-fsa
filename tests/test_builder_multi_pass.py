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
    monkeypatch.setenv("AGENT_PROJECT_ID", "proj_builder_multi")
    if provider:
        monkeypatch.setenv("AGENT_CHAT_PROVIDER", provider)
    else:
        monkeypatch.delenv("AGENT_CHAT_PROVIDER", raising=False)

    import core.config_paths
    import core.builder

    importlib.reload(core.config_paths)
    return importlib.reload(core.builder)



def test_builder_runs_multi_pass_forge_with_reference_candidate(monkeypatch, tmp_path):
    builder_mod = _load_builder(monkeypatch, tmp_path, provider="gemini_cli")
    monkeypatch.setattr(builder_mod, "SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(builder_mod, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(builder_mod, "quick_guard", lambda code: (True, []))
    monkeypatch.setattr(builder_mod, "run_isolated", lambda path, timeout_sec: (True, {"ok": True}, ""))

    reference_dir = tmp_path / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    reference_path = reference_dir / "skill.py"
    reference_path.write_text("def apply(ctx):\n    return {'ok': True}\n", encoding="utf-8")
    monkeypatch.setattr(builder_mod, "resolve_skill_paths", lambda sid: (str(reference_path), None) if sid == "candidate_skill" else (None, None))

    requests = []

    def fake_execute_cli_chat(request):
        requests.append(request)
        if "critic pass" in request.system_prompt:
            return {
                "ok": True,
                "provider_id": request.provider_id,
                "reason": request.provider_id,
                "text": '{"issues": ["Missing required function: test()"], "should_repair": true, "summary": "repair"}',
            }
        if "[Forge Pass] Repair" in request.task_input:
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
        return {
            "ok": True,
            "provider_id": request.provider_id,
            "reason": request.provider_id,
            "text": (
                "def propose(ctx):\n"
                "    return {'ok': True}\n\n"
                "def apply(ctx):\n"
                "    return {'ok': True}\n"
            ),
        }

    monkeypatch.setattr(builder_mod, "execute_cli_chat", fake_execute_cli_chat)

    builder = builder_mod.SandboxedBuilder(types.SimpleNamespace(pick=lambda _stage: "models/gemini-2.5-flash"))
    ok, code_path, meta = builder.build_skill(
        agent={"role": "Backend Architect"},
        skill_name="demo_skill",
        reqs={"goal": "adapt candidate skill", "constraints": ["offline_only"]},
        run_id="run_builder_multi",
        evidence_pack={
            "targets": {
                "demo_skill": {
                    "verified": True,
                    "top_candidate": "candidate_skill",
                    "top_score": 70,
                    "reuse_decision": {
                        "mode": "shadow_reuse",
                        "candidate_skill_id": "candidate_skill",
                        "confidence": 0.7,
                        "score": 70,
                    },
                }
            }
        },
    )

    assert ok is True
    assert code_path
    assert Path(code_path).exists()
    assert meta["forge_repaired"] is True
    assert meta["reference_candidate_id"] == "candidate_skill"
    assert len(requests) >= 3
    assert any("ReferenceSkill(JSON)" in request.task_input for request in requests)
