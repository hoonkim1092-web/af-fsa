from core.skill_forge import SkillForge



def test_skill_forge_runs_repair_pass_when_critic_requests_it():
    calls = []

    def fake_generate_code(*, prompt, workspace, run_id):
        calls.append(("code", run_id, prompt))
        if "[Forge Pass] Repair" in prompt:
            return (
                "def propose(ctx):\n"
                "    return {'ok': True}\n\n"
                "def apply(ctx):\n"
                "    return {'ok': True}\n\n"
                "def test(ctx):\n"
                "    return {'ok': True}\n",
                {"reason": "repair"},
            )
        return (
            "def propose(ctx):\n"
            "    return {'ok': True}\n\n"
            "def apply(ctx):\n"
            "    return {'ok': True}\n",
            {"reason": "implementer"},
        )

    def fake_generate_text(*, prompt, system_prompt, workspace, run_id):
        calls.append(("text", run_id, prompt))
        return (
            '{"issues": ["Missing required function: test()"], "should_repair": true, "summary": "repair needed"}',
            {"reason": "critic"},
        )

    forge = SkillForge(generate_code=fake_generate_code, generate_text=fake_generate_text, max_repair_rounds=1)
    result = forge.run(
        skill_id="demo_skill",
        base_prompt="Base prompt",
        workspace=".",
        run_id="forge_demo",
        reference_candidate={"candidate_skill_id": "existing_skill", "code_excerpt": "def apply(ctx): pass"},
    )

    assert result.repaired is True
    assert result.reference_used is True
    assert "def test(" in result.code
    assert any(call[0] == "text" for call in calls)
    assert len(calls) >= 3



def test_skill_forge_falls_back_to_local_critic_when_text_output_is_invalid():
    def fake_generate_code(*, prompt, workspace, run_id):
        del prompt, workspace, run_id
        return "def apply(ctx):\n    return {'ok': True}\n", {"reason": "implementer"}

    def fake_generate_text(*, prompt, system_prompt, workspace, run_id):
        del prompt, system_prompt, workspace, run_id
        return "not json", {"reason": "critic"}

    forge = SkillForge(generate_code=fake_generate_code, generate_text=fake_generate_text, max_repair_rounds=0)
    result = forge.run(skill_id="demo_skill", base_prompt="Base", workspace=".", run_id="forge_local")

    assert result.critique.summary == "local_critic"
    assert result.critique.should_repair is True
    assert "Missing required function: propose()" in result.critique.issues
