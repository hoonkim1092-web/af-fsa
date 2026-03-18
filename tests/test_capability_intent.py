from core.capability_intent import CapabilityIntentAnalyzer



def test_capability_intent_analyzer_derives_capabilities_and_evidence():
    intent = CapabilityIntentAnalyzer().analyze(
        agent={"role": "Backend Architect"},
        skill_name="Pytest Regression Guard",
        reqs={
            "goal": "Add pytest regression coverage and safe file updates",
            "constraints": ["offline_only", "safe_file_edit", "offline_only"],
            "risk_level": "elevated",
            "capabilities": [{"id": "pytest_regression", "required": True}],
            "missing_skills": ["pytest_regression_guard", "random_other_skill"],
        },
        target_evidence={"top_candidate": "existing_skill", "verified": True, "top_score": 70},
    )

    capability_ids = [item["id"] for item in intent["capabilities"]]

    assert intent["skill_id"] == "pytest_regression_guard"
    assert intent["role"] == "Backend Architect"
    assert intent["constraints"] == ["offline_only", "safe_file_edit"]
    assert intent["risk_level"] == "elevated"
    assert "pytest_regression" in capability_ids
    assert "pytest_regression_guard" in capability_ids
    assert intent["evidence"]["top_candidate"] == "existing_skill"
    assert intent["evidence"]["reuse_confidence"] == 70.0
    assert intent["evidence"]["verified"] is True
