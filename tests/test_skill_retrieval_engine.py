from core.skill_feedback import SkillFeedbackLoop, SkillFeedbackSummary
from core.skill_retrieval_engine import SkillRetrievalEngine



def test_retrieval_engine_reuses_high_confidence_verified_candidate():
    engine = SkillRetrievalEngine()
    decision = engine.decide_reuse(
        "pytest_regression_guard",
        {
            "top_candidate": "existing_skill",
            "top_score": 92,
            "verified": True,
            "matching_rationale": "strong match",
        },
    )

    assert decision.mode == "ranked_reuse"
    assert decision.candidate_skill_id == "existing_skill"
    assert decision.confidence == 0.92
    assert decision.used_historical_signal is False



def test_retrieval_engine_uses_shadow_reuse_for_medium_confidence_candidate():
    engine = SkillRetrievalEngine()
    decision = engine.decide_reuse(
        "pytest_regression_guard",
        {
            "top_candidate": "existing_skill",
            "top_score": 70,
            "verified": True,
        },
    )

    assert decision.mode == "shadow_reuse"
    assert decision.candidate_skill_id == "existing_skill"
    assert decision.score == 70



def test_retrieval_engine_forges_when_candidate_is_too_weak():
    engine = SkillRetrievalEngine()
    decision = engine.decide_reuse(
        "pytest_regression_guard",
        {
            "top_candidate": "existing_skill",
            "top_score": 25,
            "verified": False,
        },
    )

    assert decision.mode == "forge"
    assert decision.candidate_skill_id == "existing_skill"



def test_retrieval_engine_reranks_candidates_with_feedback_history(tmp_path):
    feedback_loop = SkillFeedbackLoop(str(tmp_path / "skill-usage.jsonl"), project_id="proj")
    for _ in range(3):
        feedback_loop.record_runtime_result(skill_id="stable_skill", ok=True, run_id="run_good")
        feedback_loop.record_runtime_result(skill_id="noisy_skill", ok=False, run_id="run_bad")
    feedback_loop.record_promotion(skill_id="stable_skill", status="changed", from_stage="candidate", to_stage="active")
    feedback_loop.record_promotion(skill_id="noisy_skill", status="changed", from_stage="active", to_stage="archived")

    evidence = {
        "top_candidate": "noisy_skill",
        "top_score": 86,
        "verified": True,
        "candidates": [
            {
                "candidate_skill_id": "noisy_skill",
                "score": 86,
                "verification": {"exists_skill_py": True},
                "matching_rationale": "semantic best match",
            },
            {
                "candidate_skill_id": "stable_skill",
                "score": 82,
                "verification": {"exists_skill_py": True},
                "matching_rationale": "semantic second match",
            },
        ],
    }

    decision = SkillRetrievalEngine().decide_reuse(
        "pytest_regression_guard",
        evidence,
        feedback_loop=feedback_loop,
    )

    assert decision.candidate_skill_id == "stable_skill"
    assert decision.mode == "shadow_reuse"
    assert decision.used_historical_signal is True
    assert decision.ranked_candidates[0]["candidate_skill_id"] == "stable_skill"
    assert evidence["top_candidate"] == "stable_skill"
    assert decision.base_score == 82
    assert decision.score >= 80


def test_retrieval_engine_uses_batched_feedback_summaries():
    class BatchOnlyFeedbackLoop:
        def summarize_skills(self, skill_ids=None):
            assert list(skill_ids or []) == ["noisy_skill", "stable_skill"]
            return {
                "noisy_skill": SkillFeedbackSummary(
                    skill_id="noisy_skill",
                    total_events=3,
                    runtime_failed=3,
                    current_stage="archived",
                    historical_score=18,
                ),
                "stable_skill": SkillFeedbackSummary(
                    skill_id="stable_skill",
                    total_events=4,
                    runtime_succeeded=3,
                    current_stage="active",
                    historical_score=94,
                ),
            }

    evidence = {
        "top_candidate": "noisy_skill",
        "top_score": 86,
        "verified": True,
        "candidates": [
            {
                "candidate_skill_id": "noisy_skill",
                "score": 86,
                "verification": {"exists_skill_py": True},
                "matching_rationale": "semantic best match",
            },
            {
                "candidate_skill_id": "stable_skill",
                "score": 82,
                "verification": {"exists_skill_py": True},
                "matching_rationale": "semantic second match",
            },
        ],
    }

    decision = SkillRetrievalEngine().decide_reuse(
        "pytest_regression_guard",
        evidence,
        feedback_loop=BatchOnlyFeedbackLoop(),
    )

    assert decision.candidate_skill_id == "stable_skill"
    assert decision.used_historical_signal is True
    assert decision.ranked_candidates[0]["candidate_skill_id"] == "stable_skill"
