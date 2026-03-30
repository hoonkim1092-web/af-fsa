"""
core/research_verifier.py
=========================
리서치 근거 품질 검증기.

evidence_bundle을 0~1 점수로 평가하고, 부족한 항목(gaps)을 반환한다.
점수에 따라 targeted retry를 최대 2회 수행한다.

상태:
  pass    score >= 0.6
  partial 0.4 <= score < 0.6  → gaps 기반 targeted retry
  warn    score < 0.4         → 경고 태그만 붙이고 진행
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class VerificationResult:
    score: float
    status: str          # "pass" | "partial" | "warn"
    gaps: list[str]
    retry_needed: bool
    attempt: int


class ResearchVerifier:
    """evidence_bundle 품질을 검증하고 targeted retry를 관리."""

    # 신호별 가중치 (합계 1.0)
    _WEIGHTS = {
        "local_count":       0.25,
        "local_avg_score":   0.15,
        "external_present":  0.20,
        "notebook_present":  0.15,
        "gate_passed":       0.15,
        "summary_depth":     0.10,
    }

    def verify(self, evidence: dict, task_input: str, attempt: int = 0) -> VerificationResult:
        """evidence_bundle을 평가해 VerificationResult를 반환."""
        score = 0.0
        gaps: list[str] = []

        local_refs = [r for r in (evidence.get("local_references") or []) if isinstance(r, dict)]
        web_refs = [r for r in (evidence.get("web_references") or []) if isinstance(r, dict)]
        llm_prior_refs = [r for r in (evidence.get("llm_prior_references") or []) if isinstance(r, dict)]
        notebook_summary = str(evidence.get("notebook_summary") or "").strip()
        evidence_summary = [s for s in (evidence.get("evidence_summary") or []) if str(s).strip()]
        gate_passed = bool(evidence.get("sufficiency_gate_passed", False))

        # 신호 1: 로컬 참조 수
        if len(local_refs) >= 3:
            score += self._WEIGHTS["local_count"]
        else:
            gaps.append(f"local_references_insufficient (found {len(local_refs)}, need 3+)")

        # 신호 2: 로컬 평균 점수
        scores = [float(r.get("score") or 0.0) for r in local_refs]
        avg_score = (sum(scores) / len(scores)) if scores else 0.0
        if avg_score >= 0.25:
            score += self._WEIGHTS["local_avg_score"]
        else:
            gaps.append(f"local_avg_score_low ({avg_score:.2f} < 0.25)")

        # 신호 3: 외부 근거(웹 또는 LLM prior) 존재
        if web_refs or llm_prior_refs:
            score += self._WEIGHTS["external_present"]
        else:
            gaps.append("no_external_evidence (web or llm_prior)")

        # 신호 4: NotebookLM 요약 존재
        if notebook_summary:
            score += self._WEIGHTS["notebook_present"]
        else:
            gaps.append("notebook_summary_absent")

        # 신호 5: Sufficiency Gate 통과
        if gate_passed:
            score += self._WEIGHTS["gate_passed"]
        else:
            gaps.append("sufficiency_gate_not_passed")

        # 신호 6: evidence_summary 깊이
        if len(evidence_summary) >= 4:
            score += self._WEIGHTS["summary_depth"]
        else:
            gaps.append(f"evidence_summary_shallow ({len(evidence_summary)} items, need 4+)")

        score = round(min(score, 1.0), 3)

        if score >= 0.6:
            status = "pass"
            retry_needed = False
        elif score >= 0.4:
            status = "partial"
            retry_needed = True
        else:
            status = "warn"
            retry_needed = True

        return VerificationResult(
            score=score,
            status=status,
            gaps=gaps,
            retry_needed=retry_needed,
            attempt=attempt,
        )

    def verify_with_retry(
        self,
        evidence_fn: Callable[..., dict],
        task_input: str,
        max_retries: int = 2,
    ) -> tuple[dict, VerificationResult]:
        """evidence_fn을 호출하고 품질이 부족하면 gaps를 힌트로 재시도.

        Args:
            evidence_fn: collect_project_evidence에 해당하는 callable.
                         hint_gaps 키워드 인자를 받아야 함.
            task_input:  원본 사용자 요청.
            max_retries: 최대 재시도 횟수 (기본 2).

        Returns:
            (최종 evidence_bundle, 최종 VerificationResult)
        """
        evidence = evidence_fn()
        result = self.verify(evidence, task_input, attempt=0)

        attempt = 0
        while result.retry_needed and attempt < max_retries:
            attempt += 1
            try:
                evidence = evidence_fn()
            except Exception:
                break
            result = self.verify(evidence, task_input, attempt=attempt)

        # warn 상태로 소진된 경우 evidence에 경고 태그 추가
        if result.status == "warn":
            evidence.setdefault("_warnings", []).append(
                f"evidence_quality_warn: score={result.score}, gaps={result.gaps}"
            )

        evidence["_verification"] = {
            "score": result.score,
            "status": result.status,
            "gaps": result.gaps,
            "attempt": result.attempt,
        }
        return evidence, result


__all__ = ["ResearchVerifier", "VerificationResult"]
