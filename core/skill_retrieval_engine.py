from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.skill_feedback import SkillFeedbackLoop, SkillFeedbackSummary
from core.utils import safe_id


@dataclass
class ReuseDecision:
    need_skill_id: str
    candidate_skill_id: str = ""
    mode: str = "forge"
    confidence: float = 0.0
    score: int = 0
    threshold_high: float = 0.85
    threshold_medium: float = 0.60
    verified: bool = False
    rationale: str = ""
    base_score: int = 0
    historical_score: int = 0
    combined_score: int = 0
    historical_weight: float = 0.0
    used_historical_signal: bool = False
    ranked_candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillRetrievalEngine:
    """Resolve next-generation reuse-vs-forge decisions from ranked evidence."""

    def __init__(
        self,
        *,
        high_confidence: float = 0.85,
        medium_confidence: float = 0.60,
        historical_weight: float = 0.25,
    ):
        self.high_confidence = float(high_confidence)
        self.medium_confidence = float(medium_confidence)
        self.historical_weight = max(0.0, min(0.5, float(historical_weight)))

    def decide_reuse(
        self,
        need_skill_id: str,
        evidence: dict[str, Any] | None,
        *,
        feedback_loop: SkillFeedbackLoop | None = None,
        feedback_summaries: dict[str, SkillFeedbackSummary] | None = None,
    ) -> ReuseDecision:
        payload = evidence if isinstance(evidence, dict) else {}
        ranked_candidates = self._rank_candidates(
            payload,
            feedback_loop=feedback_loop,
            feedback_summaries=feedback_summaries,
        )
        best = ranked_candidates[0] if ranked_candidates else {}

        candidate_skill_id = safe_id(str(best.get("candidate_skill_id") or payload.get("top_candidate") or ""))
        verified = bool(best.get("verified", payload.get("verified", False)))
        base_score = int(best.get("base_score") or self._normalize_score(payload.get("top_score")))
        historical_score = int(best.get("historical_score") or base_score)
        combined_score = int(best.get("combined_score") or base_score)
        used_historical_signal = bool(best.get("used_historical_signal", False))
        historical_weight = float(best.get("historical_weight") or 0.0)
        confidence = round(combined_score / 100.0, 3)
        rationale = self._build_rationale(payload, best, combined_score)

        if payload is evidence:
            payload["top_candidate"] = candidate_skill_id
            payload["top_score"] = combined_score
            payload["top_score_base"] = base_score
            payload["top_score_historical"] = historical_score
            payload["verified"] = verified
            payload["matching_rationale"] = rationale
            if ranked_candidates:
                payload["candidates"] = ranked_candidates
                payload["feedback_history"] = [best.get("feedback_summary") or {}] if best.get("feedback_summary") else []

        if candidate_skill_id and verified and confidence >= self.high_confidence:
            mode = "ranked_reuse"
            reason = rationale or f"confidence={confidence:.2f} verified candidate is safe to reuse"
        elif candidate_skill_id and confidence >= self.medium_confidence:
            mode = "shadow_reuse"
            reason = rationale or f"confidence={confidence:.2f} candidate should be adapted via forge"
        else:
            mode = "forge"
            reason = rationale or "no candidate cleared the reuse confidence gate"

        return ReuseDecision(
            need_skill_id=safe_id(need_skill_id),
            candidate_skill_id=candidate_skill_id,
            mode=mode,
            confidence=confidence,
            score=combined_score,
            threshold_high=self.high_confidence,
            threshold_medium=self.medium_confidence,
            verified=verified,
            rationale=reason,
            base_score=base_score,
            historical_score=historical_score,
            combined_score=combined_score,
            historical_weight=historical_weight,
            used_historical_signal=used_historical_signal,
            ranked_candidates=ranked_candidates,
        )

    def _rank_candidates(
        self,
        payload: dict[str, Any],
        *,
        feedback_loop: SkillFeedbackLoop | None,
        feedback_summaries: dict[str, SkillFeedbackSummary] | None = None,
    ) -> list[dict[str, Any]]:
        candidates = self._iter_candidates(payload)
        summaries = self._resolve_feedback_summaries(
            candidates,
            feedback_loop=feedback_loop,
            feedback_summaries=feedback_summaries,
        )
        ranked: list[dict[str, Any]] = []
        for item in candidates:
            candidate_skill_id = safe_id(str(item.get("candidate_skill_id") or item.get("skill_id") or item.get("id") or ""))
            if not candidate_skill_id:
                continue
            base_score = self._normalize_score(item.get("score"))
            summary = summaries.get(candidate_skill_id, SkillFeedbackSummary(skill_id=candidate_skill_id))
            used_historical_signal = bool(summary.total_events > 0)
            historical_score = summary.historical_score if used_historical_signal else base_score
            effective_weight = self._effective_historical_weight(summary) if used_historical_signal else 0.0
            combined_score = self._combine_scores(base_score, historical_score, effective_weight)
            verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
            verified = bool(
                item.get("verified")
                or verification.get("exists_skill_py")
                or (
                    candidate_skill_id == safe_id(str(payload.get("top_candidate") or ""))
                    and bool(payload.get("verified", False))
                )
            )
            row = dict(item)
            row.update(
                {
                    "candidate_skill_id": candidate_skill_id,
                    "verified": verified,
                    "base_score": base_score,
                    "historical_score": historical_score,
                    "combined_score": combined_score,
                    "score": combined_score,
                    "historical_weight": round(effective_weight, 3),
                    "used_historical_signal": used_historical_signal,
                    "current_stage": summary.current_stage,
                    "feedback_summary": summary.to_dict() if used_historical_signal else {},
                }
            )
            ranked.append(row)
        ranked.sort(key=lambda item: (int(item.get("combined_score") or 0), int(item.get("base_score") or 0)), reverse=True)
        return ranked

    def _resolve_feedback_summaries(
        self,
        candidates: list[dict[str, Any]],
        *,
        feedback_loop: SkillFeedbackLoop | None,
        feedback_summaries: dict[str, SkillFeedbackSummary] | None,
    ) -> dict[str, SkillFeedbackSummary]:
        candidate_ids: list[str] = []
        for item in candidates:
            candidate_skill_id = safe_id(str(item.get("candidate_skill_id") or item.get("skill_id") or item.get("id") or ""))
            if candidate_skill_id and candidate_skill_id not in candidate_ids:
                candidate_ids.append(candidate_skill_id)
        if not candidate_ids:
            return {}

        raw_summaries = feedback_summaries or {}
        if not raw_summaries and feedback_loop:
            if hasattr(feedback_loop, "summarize_skills"):
                raw_summaries = feedback_loop.summarize_skills(candidate_ids)
            else:
                raw_summaries = {
                    candidate_skill_id: feedback_loop.summarize_skill(candidate_skill_id)
                    for candidate_skill_id in candidate_ids
                }

        summaries: dict[str, SkillFeedbackSummary] = {}
        for candidate_skill_id in candidate_ids:
            summary = raw_summaries.get(candidate_skill_id) if isinstance(raw_summaries, dict) else None
            summaries[candidate_skill_id] = self._coerce_summary(candidate_skill_id, summary)
        return summaries

    @staticmethod
    def _coerce_summary(candidate_skill_id: str, summary: Any) -> SkillFeedbackSummary:
        if isinstance(summary, SkillFeedbackSummary):
            return summary
        if isinstance(summary, dict):
            payload = dict(summary)
            payload["skill_id"] = safe_id(str(payload.get("skill_id") or candidate_skill_id))
            allowed_keys = {
                "skill_id",
                "total_events",
                "selection_count",
                "build_passed",
                "build_failed",
                "runtime_succeeded",
                "runtime_failed",
                "current_stage",
                "last_event_at",
                "runtime_success_rate",
                "build_success_rate",
                "stage_weight",
                "usage_signal",
                "historical_score",
            }
            normalized = {key: payload[key] for key in allowed_keys if key in payload}
            return SkillFeedbackSummary(**normalized)
        return SkillFeedbackSummary(skill_id=candidate_skill_id)

    def _iter_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        if raw_candidates:
            return [item for item in raw_candidates if isinstance(item, dict)]
        candidate_skill_id = safe_id(str(payload.get("top_candidate") or ""))
        if not candidate_skill_id:
            return []
        return [
            {
                "candidate_skill_id": candidate_skill_id,
                "score": self._normalize_score(payload.get("top_score")),
                "verified": bool(payload.get("verified", False)),
                "matching_rationale": str(payload.get("matching_rationale") or "").strip(),
            }
        ]

    def _build_rationale(self, payload: dict[str, Any], best: dict[str, Any], combined_score: int) -> str:
        base_reason = str(best.get("matching_rationale") or payload.get("matching_rationale") or "").strip()
        if not best:
            return base_reason
        if not best.get("used_historical_signal"):
            return base_reason or f"score={combined_score}"
        summary = best.get("feedback_summary") if isinstance(best.get("feedback_summary"), dict) else {}
        runtime_total = int(summary.get("runtime_succeeded") or 0) + int(summary.get("runtime_failed") or 0)
        runtime_label = f"{summary.get('runtime_succeeded', 0)}/{runtime_total}" if runtime_total else "0/0"
        parts = []
        if base_reason:
            parts.append(base_reason)
        parts.append(
            (
                f"historical_rerank base={best.get('base_score', 0)} "
                f"hist={best.get('historical_score', 0)} "
                f"combined={combined_score} "
                f"stage={summary.get('current_stage', 'unknown') or 'unknown'} "
                f"runtime={runtime_label}"
            )
        )
        return "; ".join(parts)

    def _effective_historical_weight(self, summary: SkillFeedbackSummary) -> float:
        if summary.total_events <= 0:
            return 0.0
        return min(self.historical_weight, 0.10 + min(summary.total_events, 5) * 0.03)

    @staticmethod
    def _combine_scores(base_score: int, historical_score: int, historical_weight: float) -> int:
        if historical_weight <= 0:
            return int(base_score)
        base_weight = max(0.0, 1.0 - historical_weight)
        combined = (base_score * base_weight) + (historical_score * historical_weight)
        return int(round(max(0.0, min(100.0, combined))))

    @staticmethod
    def _normalize_score(value: Any) -> int:
        try:
            score = float(value or 0)
        except (TypeError, ValueError):
            score = 0.0
        if score <= 1.0:
            score *= 100.0
        score = max(0.0, min(100.0, score))
        return int(round(score))
