"""
core/pipeline_quality.py
========================
파이프라인 품질 집계 및 장애 복구 유틸리티.

1. AggregatedVerdict   — 5차원 가중 공식 기반 최종 판정
2. PipelineStageGuard  — 단계별 graceful degradation wrapper
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ──────────────────────────────────────────────────────────────
# AggregatedVerdict
# ──────────────────────────────────────────────────────────────

@dataclass
class VerdictResult:
    decision: str                   # "accepted" | "accepted_with_warnings" | "rejected"
    quality_score: float            # 0.0 - 1.0
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dimension_scores: dict[str, float] = field(default_factory=dict)
    evidence_status: str = ""
    semantic_status: str = ""
    structural_status: str = ""
    execution_status: str = ""


class AggregatedVerdict:
    """
    5차원 가중 공식으로 최종 판정을 내린다.

    차원 가중치:
      evidence_quality  0.15
      semantic_quality  0.25
      structural_quality 0.20
      execution_quality  0.25
      grounding_ratio    0.15

    판정 규칙:
      accepted             total >= 0.80 AND min_dimension >= 0.10
      accepted_with_warnings total >= 0.65 AND no_blockers
      rejected             그 외
    """

    _WEIGHTS = {
        "evidence_quality":   0.15,
        "semantic_quality":   0.25,
        "structural_quality": 0.20,
        "execution_quality":  0.25,
        "grounding_ratio":    0.15,
    }

    def evaluate(
        self,
        evidence_v: dict,
        critique_v: dict,
        gate_v: dict,
        qa_v: dict | None = None,
        rewrite_v: dict | None = None,
    ) -> VerdictResult:
        """각 단계 결과를 받아 최종 판정을 반환한다.

        Args:
            evidence_v:  ResearchVerifier 결과 {"score": float, "status": str, ...}
            critique_v:  MergedCritique 결과 {"score": float, "confirmed_gaps": [...], ...}
            gate_v:      run_structural_gate 결과 {"pass": bool, "rubric_score": float, ...}
            qa_v:        Agent QA 결과 (없으면 None → 건너뜀)
            rewrite_v:   Final rewrite 결과 (없으면 None → 건너뜀)
        """
        blocking_issues: list[str] = []
        warnings: list[str] = []

        # ── 차단 조건 ──
        if not gate_v.get("pass", True):
            blocking_issues.append(f"structural_gate_fail: {gate_v.get('errors', [])}")

        if qa_v and qa_v.get("evaluator_action") == "abort":
            blocking_issues.append("agent_qa_abort")

        # ── 차원 점수 계산 ──
        ev_score = float(evidence_v.get("score") or 0.0)
        sem_score = float(critique_v.get("score") or 0.0)
        struct_score = float(gate_v.get("rubric_score") or 0.5)

        # QA 차원 점수
        if qa_v:
            qa_dims = qa_v.get("dimension_scores") or {}
            if qa_dims:
                exec_score = sum(qa_dims.values()) / len(qa_dims)
            else:
                exec_score = 1.0 if qa_v.get("execution_pass") else 0.4
        else:
            exec_score = struct_score  # QA 없으면 structural 점수 재사용

        # Grounding ratio
        if rewrite_v:
            grounding = float(rewrite_v.get("grounded_claims_ratio") or 0.5)
        elif critique_v.get("_claim_map"):
            grounding = float((critique_v["_claim_map"] or {}).get("grounded_ratio") or 0.5)
        else:
            grounding = 0.5  # 정보 없으면 중립

        dimensions = {
            "evidence_quality":   ev_score,
            "semantic_quality":   sem_score,
            "structural_quality": struct_score,
            "execution_quality":  exec_score,
            "grounding_ratio":    grounding,
        }

        # ── 가중 합산 ──
        total = sum(
            dimensions[k] * self._WEIGHTS[k]
            for k in self._WEIGHTS
        )
        total = round(min(1.0, max(0.0, total)), 3)

        # ── 경고 수집 ──
        for w in (gate_v.get("warnings") or []):
            warnings.append(f"gate: {w}")
        if critique_v.get("suspected_gaps"):
            warnings.append(f"suspected_gaps: {len(critique_v['suspected_gaps'])} items")

        # ── 판정 ──
        if blocking_issues:
            decision = "rejected"
        elif total >= 0.80 and min(dimensions.values()) >= 0.10:
            decision = "accepted"
        elif total >= 0.65 and not blocking_issues:
            decision = "accepted_with_warnings"
        else:
            decision = "rejected"
            if not blocking_issues:
                blocking_issues.append(f"quality_score_too_low: {total:.3f} < 0.65")

        return VerdictResult(
            decision=decision,
            quality_score=total,
            blocking_issues=blocking_issues,
            warnings=warnings,
            dimension_scores=dimensions,
            evidence_status=str(evidence_v.get("status") or ""),
            semantic_status="pass" if sem_score >= 0.75 else "partial",
            structural_status=str(gate_v.get("status") or ""),
            execution_status="pass" if exec_score >= 0.7 else "partial",
        )


# ──────────────────────────────────────────────────────────────
# PipelineStageGuard  — 단계별 graceful degradation
# ──────────────────────────────────────────────────────────────

class PipelineStageGuard:
    """파이프라인 각 단계를 graceful degradation으로 감싸는 guard.

    사용법:
        guard = PipelineStageGuard()
        evidence = guard.run(
            stage="evidence_acquisition",
            fn=lambda: collect_evidence(task_input),
            fallback=lambda exc: {"_warnings": [str(exc)]}
        )
    """

    def run(
        self,
        stage: str,
        fn: Callable[[], Any],
        fallback: Callable[[Exception], Any] | None = None,
        timeout_sec: float | None = None,
    ) -> Any:
        """fn을 실행하고 실패 시 fallback을 반환한다.

        Args:
            stage:       단계 이름 (로깅용)
            fn:          실행할 함수
            fallback:    실패 시 호출할 함수 (예외를 인자로 받음)
            timeout_sec: 타임아웃 (None이면 제한 없음)
        """
        try:
            if timeout_sec:
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(fn)
                    try:
                        return future.result(timeout=timeout_sec)
                    except _cf.TimeoutError:
                        # TimeoutError를 표준 예외로 변환해 아래 fallback 핸들러가 처리하게 함
                        raise TimeoutError(f"{stage} timed out after {timeout_sec}s")
            else:
                return fn()
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                print(f"[PipelineStageGuard] {stage} TIMEOUT: {exc}")
            else:
                print(f"[PipelineStageGuard] {stage} failed: {type(exc).__name__}: {exc}")
            if fallback is not None:
                try:
                    result = fallback(exc)
                    if isinstance(result, dict):
                        result.setdefault("_stage_degraded", stage)
                        result.setdefault("_degradation_reason", str(exc))
                    return result
                except Exception as fb_exc:
                    print(f"[PipelineStageGuard] {stage} fallback also failed: {fb_exc}")
            return self._default_fallback(stage, exc)

    def _default_fallback(self, stage: str, exc: Exception) -> dict:
        """기본 폴백: 빈 dict + 경고 태그."""
        return {
            "_stage_degraded": stage,
            "_degradation_reason": str(exc),
            "_warnings": [f"{stage} degraded: {exc}"],
        }


# 편의 인스턴스
stage_guard = PipelineStageGuard()
verdict_engine = AggregatedVerdict()


__all__ = [
    "AggregatedVerdict", "VerdictResult",
    "PipelineStageGuard", "stage_guard", "verdict_engine",
]
