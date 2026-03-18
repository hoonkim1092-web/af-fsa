import json
from unittest.mock import MagicMock, patch

from core.researcher import HimariResearchAgent
from core.skill_feedback import SkillFeedbackLoop



def test_researcher_applies_feedback_aware_ranking_to_evidence(tmp_path):
    feedback_loop = SkillFeedbackLoop(str(tmp_path / "skill-usage.jsonl"), project_id="demo_project")
    for _ in range(3):
        feedback_loop.record_runtime_result(skill_id="stable_skill", ok=True, run_id="run_good")
        feedback_loop.record_runtime_result(skill_id="noisy_skill", ok=False, run_id="run_bad")
    feedback_loop.record_promotion(skill_id="stable_skill", status="changed", from_stage="candidate", to_stage="active")
    feedback_loop.record_promotion(skill_id="noisy_skill", status="changed", from_stage="active", to_stage="archived")

    agent = HimariResearchAgent(mr=MagicMock())
    mock_reqs = {"goal": "shared capability test", "missing_skills": ["shared_capability"]}
    mock_agent = {"role": "developer", "name": "test"}
    registry = {
        "noisy_skill": {
            "id": "noisy_skill",
            "name": "Noisy Skill",
            "capabilities": ["shared", "capability"],
            "meta": {"last_test_ok": True},
        },
        "stable_skill": {
            "id": "stable_skill",
            "name": "Stable Skill",
            "capabilities": ["shared", "capability"],
            "meta": {"last_test_ok": True},
        },
    }
    suggestion_payload = {"ok": True, "text": json.dumps({"suggestions": {"shared_capability": ["noisy_skill", "stable_skill"]}})}

    with patch.object(agent, "_registry_skill_index", return_value=registry):
        with patch("core.researcher.execute_requirement_prompt", return_value=suggestion_payload):
            with patch("core.researcher.query_notebooklm", return_value=""):
                with patch("core.researcher.SkillFeedbackLoop.for_workspace", return_value=feedback_loop):
                    result = agent.research(mock_agent, mock_reqs)

    target = result["evidence_pack"]["targets"]["shared_capability"]

    assert target["top_candidate"] == "stable_skill"
    assert target["candidates"][0]["candidate_skill_id"] == "stable_skill"
    assert target["feedback_history"]
    assert target["feedback_history"][0]["skill_id"] == "stable_skill"
    assert target["top_score_historical"] >= target["top_score_base"]
    assert "historical_rerank" in target["matching_rationale"]
