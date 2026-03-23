from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from core.policy import resolve_quality_gate_policy
from core.skill_eval_harness import SkillEvalReport, _phase_from_payload, _shadow_from_payload, load_eval_report
from core.skill_feedback import SkillFeedbackLoop, SkillFeedbackSummary
from core.utils import lock_skill_state, now_iso, read_project_policies, read_skill_lock, safe_id


PROMOTION_REPORT_FILENAME = "skill-promotion.json"


@dataclass
class PromotionDecision:
    skill_id: str
    current_stage: str
    next_stage: str
    changed: bool
    installable: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    promotion_path: str = ""
    written_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillPromotionManager:
    """Apply lifecycle transitions based on eval evidence."""

    def __init__(self, *, project_policies: dict | None = None, pass_rate_threshold: float = 0.8):
        self.quality_gate = resolve_quality_gate_policy(project_policies or read_project_policies())
        self.pass_rate_threshold = float(pass_rate_threshold)
        self.active_runtime_min_events = max(0, int(self.quality_gate.get("active_runtime_min_events", 3) or 0))
        self.active_runtime_min_success_rate = float(self.quality_gate.get("active_runtime_min_success_rate", 0.8) or 0.0)
        self.demote_runtime_min_events = max(0, int(self.quality_gate.get("demote_runtime_min_events", 3) or 0))
        self.demote_runtime_below_success_rate = float(self.quality_gate.get("demote_runtime_below_success_rate", 0.5) or 0.0)

    def decide(
        self,
        report: SkillEvalReport,
        current_stage: str = "draft",
        *,
        feedback_summary: SkillFeedbackSummary | None = None,
    ) -> PromotionDecision:
        current = _normalize_stage(current_stage or "draft")
        static_ok = bool(report.static_gate.get("ok", False))
        contract_ok = report.contract_eval.total_cases == 0 or report.contract_eval.pass_rate >= self.pass_rate_threshold
        hidden_ok = report.hidden_eval.total_cases == 0 or report.hidden_eval.pass_rate >= self.pass_rate_threshold
        shadow_seen = report.shadow_eval.total_cases > 0
        shadow_nonnegative = shadow_seen and report.shadow_eval.delta >= 0
        shadow_positive = shadow_seen and report.shadow_eval.delta > 0
        shadow_negative = shadow_seen and report.shadow_eval.delta < 0

        summary = feedback_summary or SkillFeedbackSummary(skill_id=report.skill_id)
        runtime_total = summary.runtime_succeeded + summary.runtime_failed
        runtime_success_rate = summary.runtime_success_rate if runtime_total else 0.0
        runtime_ready_for_active = self._runtime_ready_for_active(runtime_total, runtime_success_rate)
        runtime_regressed = self._runtime_regressed(runtime_total, runtime_success_rate)

        next_stage = current
        reason = "retain_stage"

        if not static_ok:
            next_stage = "archived" if current in {"canary", "active"} else "draft"
            reason = "static_gate_failed"
        elif current == "draft":
            next_stage = "candidate" if contract_ok and hidden_ok else "draft"
            reason = "external_eval_passed" if next_stage == "candidate" else "evals_insufficient"
        elif current == "candidate":
            next_stage = "canary" if contract_ok and hidden_ok and shadow_nonnegative else "candidate"
            reason = "shadow_eval_ready" if next_stage == "canary" else "awaiting_shadow_evidence"
        elif current == "canary":
            if not contract_ok or not hidden_ok:
                next_stage = "candidate"
                reason = "canary_regressed_external_eval"
            elif shadow_negative:
                next_stage = "candidate"
                reason = "canary_lost_shadow_comparison"
            elif shadow_positive and runtime_ready_for_active:
                next_stage = "active"
                reason = "canary_promoted_with_runtime_evidence"
            elif shadow_positive:
                next_stage = "canary"
                reason = "awaiting_runtime_evidence"
            else:
                next_stage = "canary"
                reason = "need_stronger_shadow_evidence"
        elif current == "active":
            if not contract_ok or not hidden_ok:
                next_stage = "candidate"
                reason = "active_regressed_external_eval"
            elif shadow_negative:
                next_stage = "candidate"
                reason = "active_lost_shadow_comparison"
            elif runtime_regressed:
                next_stage = "candidate"
                reason = "active_runtime_regressed"
            else:
                next_stage = "active"
                reason = "active_retained"
        elif current == "archived":
            next_stage = "candidate" if contract_ok and hidden_ok else "archived"
            reason = "reactivated_from_archive" if next_stage == "candidate" else "archive_retained"

        installable = next_stage in set(self.quality_gate.get("installable_statuses") or ["active"])
        return PromotionDecision(
            skill_id=report.skill_id,
            current_stage=current,
            next_stage=next_stage,
            changed=next_stage != current,
            installable=installable,
            reason=reason,
            evidence={
                "static_ok": static_ok,
                "contract_pass_rate": report.contract_eval.pass_rate,
                "hidden_pass_rate": report.hidden_eval.pass_rate,
                "shadow_delta": report.shadow_eval.delta,
                "shadow_cases": report.shadow_eval.total_cases,
                "recommended_stage": report.recommended_stage,
                "feedback_total_events": summary.total_events,
                "feedback_current_stage": summary.current_stage,
                "runtime_total": runtime_total,
                "runtime_success_rate": runtime_success_rate,
                "runtime_ready_for_active": runtime_ready_for_active,
                "runtime_regressed": runtime_regressed,
                "historical_score": summary.historical_score,
            },
        )

    def apply(
        self,
        skill_id: str,
        report: SkillEvalReport | dict[str, Any] | str,
        *,
        current_stage: str | None = None,
        promotion_path: str | None = None,
        feedback_loop: SkillFeedbackLoop | None = None,
    ) -> PromotionDecision:
        eval_report = _ensure_report(report)
        requested_skill_id = safe_id(skill_id)
        report_skill_id = safe_id(eval_report.skill_id)
        if requested_skill_id and report_skill_id and requested_skill_id != report_skill_id:
            raise ValueError(f"skill_id_mismatch:{requested_skill_id}!={report_skill_id}")
        effective_skill_id = eval_report.skill_id or skill_id
        resolved_stage = current_stage or self._current_stage(effective_skill_id)
        resolved_feedback_loop = feedback_loop or self._resolve_feedback_loop(eval_report)
        feedback_summary = resolved_feedback_loop.summarize_skill(effective_skill_id)
        decision = self.decide(eval_report, current_stage=resolved_stage, feedback_summary=feedback_summary)

        decision_path = promotion_path or os.path.join(os.path.dirname(eval_report.report_path or eval_report.skill_path), PROMOTION_REPORT_FILENAME)
        decision.promotion_path = os.path.abspath(decision_path)
        decision.written_at = now_iso()
        _write_json(decision.promotion_path, decision.to_dict())

        lock_skill_state(
            effective_skill_id,
            {
                "status": decision.next_stage,
                "quality_stage": decision.next_stage,
                "installable": decision.installable,
                "last_eval_report": eval_report.report_path,
                "last_promotion_report": decision.promotion_path,
                "promotion_reason": decision.reason,
                "promotion_updated_at": decision.written_at,
            },
        )
        self._record_feedback(decision, eval_report, feedback_loop=resolved_feedback_loop)
        return decision

    def _record_feedback(
        self,
        decision: PromotionDecision,
        report: SkillEvalReport,
        *,
        feedback_loop: SkillFeedbackLoop | None = None,
    ) -> None:
        try:
            resolved_feedback_loop = feedback_loop or self._resolve_feedback_loop(report)
            resolved_feedback_loop.record_promotion(
                skill_id=decision.skill_id,
                status="changed" if decision.changed else "retained",
                from_stage=decision.current_stage,
                to_stage=decision.next_stage,
                payload={
                    "installable": decision.installable,
                    "reason": decision.reason,
                    "evidence": decision.evidence,
                    "report_path": report.report_path,
                    "promotion_path": decision.promotion_path,
                },
            )
        except Exception:
            return

    def _resolve_feedback_loop(self, report: SkillEvalReport) -> SkillFeedbackLoop:
        workspace = self._infer_workspace(report)
        if workspace:
            return SkillFeedbackLoop.for_workspace(workspace)
        return SkillFeedbackLoop()

    def _infer_workspace(self, report: SkillEvalReport) -> str:
        for raw_path in (report.report_path, report.skill_path, report.evals_path):
            candidate = str(raw_path or "").strip()
            if not candidate:
                continue
            normalized = os.path.abspath(candidate)
            for marker in (f"{os.sep}skills{os.sep}", f"{os.sep}runs{os.sep}"):
                if marker in normalized:
                    return normalized.split(marker, 1)[0]
        return ""

    def _runtime_ready_for_active(self, runtime_total: int, runtime_success_rate: float) -> bool:
        if runtime_total < self.active_runtime_min_events:
            return False
        if runtime_total == 0:
            return self.active_runtime_min_events == 0
        return runtime_success_rate >= self.active_runtime_min_success_rate

    def _runtime_regressed(self, runtime_total: int, runtime_success_rate: float) -> bool:
        if runtime_total < self.demote_runtime_min_events:
            return False
        return runtime_success_rate < self.demote_runtime_below_success_rate

    def _current_stage(self, skill_id: str) -> str:
        item = (read_skill_lock().get("skills", {}) or {}).get(safe_id(skill_id), {})
        return _normalize_stage(str(item.get("status") or "draft"))


def _ensure_report(report: SkillEvalReport | dict[str, Any] | str) -> SkillEvalReport:
    if isinstance(report, SkillEvalReport):
        return report
    if isinstance(report, str):
        return load_eval_report(report)
    if not isinstance(report, dict):
        raise TypeError("report must be SkillEvalReport, dict, or path")
    return SkillEvalReport(
        skill_id=str(report.get("skill_id") or ""),
        skill_path=str(report.get("skill_path") or ""),
        evals_path=str(report.get("evals_path") or ""),
        static_gate=report.get("static_gate") or {},
        contract_eval=_phase_from_payload("contract", report.get("contract_eval") or {}),
        hidden_eval=_phase_from_payload("hidden", report.get("hidden_eval") or {}),
        shadow_eval=_shadow_from_payload(report.get("shadow_eval") or {}),
        recommended_stage=str(report.get("recommended_stage") or "draft"),
        report_path=str(report.get("report_path") or ""),
        written_at=str(report.get("written_at") or ""),
    )


def _normalize_stage(stage: str) -> str:
    normalized = safe_id(stage) or "draft"
    if normalized == "archive":
        return "archived"
    return normalized


def _write_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply next-generation skill promotion rules")
    parser.add_argument("skill_id", help="Skill id to promote or demote")
    parser.add_argument("--report", required=True, help="Path to skill-eval-report.json")
    parser.add_argument("--current-stage", help="Override current lifecycle stage")
    parser.add_argument("--out", dest="promotion_path", help="Output path for skill-promotion.json")
    parser.add_argument("--json", action="store_true", help="Print the decision as JSON")
    args = parser.parse_args(argv)

    manager = SkillPromotionManager()
    decision = manager.apply(
        args.skill_id,
        args.report,
        current_stage=args.current_stage,
        promotion_path=args.promotion_path,
    )
    if args.json:
        print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"[SkillPromotion] {decision.skill_id}: {decision.current_stage} -> {decision.next_stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())

