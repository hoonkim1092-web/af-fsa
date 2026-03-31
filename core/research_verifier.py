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

Per-source quality metadata (v2):
  evidence_bundle의 각 reference에 아래 필드를 선택적으로 태깅:
    - relevance_score: float (0.0-1.0)
    - recency: str (ISO date)
    - authority_level: "primary" | "secondary" | "tertiary"
    - content_hash: str (sha256 prefix)
  태깅된 경우 source_diversity, relevance_mean, authority_check 신호 추가 평가.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class VerificationResult:
    score: float
    status: str          # "pass" | "partial" | "warn"
    gaps: list[str]
    retry_needed: bool
    attempt: int
    per_source_scores: list[dict] = field(default_factory=list)
    filtered_evidence_ids: list[str] = field(default_factory=list)


class ResearchVerifier:
    """evidence_bundle 품질을 검증하고 targeted retry를 관리."""

    # 신호별 가중치 (합계 1.0)
    # 기본 6-signal: 0.85, per-source 3-signal: 0.15 (metadata 있을 때만)
    _WEIGHTS = {
        "local_count":       0.20,
        "local_avg_score":   0.15,
        "external_present":  0.15,
        "notebook_present":  0.15,
        "gate_passed":       0.10,
        "summary_depth":     0.10,
        # per-source 추가 신호 (metadata가 있는 경우에만 적용)
        "source_diversity":  0.05,
        "relevance_mean":    0.05,
        "authority_check":   0.05,
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

        # ── Per-source quality 신호 (metadata가 있는 경우에만 평가) ──
        all_refs = local_refs + web_refs + llm_prior_refs
        per_source_scores, filtered_ids = self._evaluate_per_source(all_refs)
        has_metadata = any(r.get("relevance_score") is not None for r in all_refs)

        if has_metadata:
            # 신호 7: 소스 유형 다양성 (2가지 이상)
            source_types = set(
                r.get("source_type") or "" for r in all_refs if r.get("source_type")
            )
            if len(source_types) >= 2:
                score += self._WEIGHTS["source_diversity"]
            else:
                gaps.append(f"source_diversity_low ({len(source_types)} types, need 2+)")

            # 신호 8: 전체 소스 평균 관련도
            rel_scores = [
                float(r.get("relevance_score") or 0.0)
                for r in all_refs
                if r.get("relevance_score") is not None
            ]
            rel_mean = (sum(rel_scores) / len(rel_scores)) if rel_scores else 0.0
            if rel_mean >= 0.6:
                score += self._WEIGHTS["relevance_mean"]
            else:
                gaps.append(f"relevance_mean_low ({rel_mean:.2f} < 0.6)")

            # 신호 9: primary 권위 소스 존재
            has_primary = any(r.get("authority_level") == "primary" for r in all_refs)
            if has_primary:
                score += self._WEIGHTS["authority_check"]
            else:
                gaps.append("no_primary_authority_source")

        # metadata가 없으면 base signal 합계가 0.85가 상한.
        # 0~0.85 범위를 0~1.0으로 정규화해 임계값(0.6/0.4) 일관성 유지.
        if not has_metadata:
            score = round(min(score / 0.85, 1.0), 3)
        else:
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
            per_source_scores=per_source_scores,
            filtered_evidence_ids=filtered_ids,
        )

    def _evaluate_per_source(self, refs: list[dict]) -> tuple[list[dict], list[str]]:
        """각 소스의 quality 점수를 계산하고 filtered_ids(유지할 소스)를 반환."""
        per_source: list[dict] = []
        filtered_ids: list[str] = []

        for i, ref in enumerate(refs):
            source_id = str(ref.get("source_id") or ref.get("id") or f"src_{i}")
            relevance = float(ref.get("relevance_score") or 0.5)
            authority = ref.get("authority_level") or "secondary"

            authority_bonus = {"primary": 0.2, "secondary": 0.0, "tertiary": -0.1}.get(authority, 0.0)
            quality = min(1.0, max(0.0, relevance + authority_bonus))

            keep = quality >= 0.3
            entry = {
                "source_id": source_id,
                "source_type": ref.get("source_type") or "",
                "relevance_score": relevance,
                "authority_level": authority,
                "quality": round(quality, 3),
                "keep": keep,
            }
            per_source.append(entry)
            if keep:
                filtered_ids.append(source_id)

        return per_source, filtered_ids

    @staticmethod
    def tag_source_metadata(ref: dict, content: str = "", source_type: str = "") -> dict:
        """reference dict에 per-source quality metadata를 태깅해 반환.

        연구 단계에서 근거를 수집할 때 이 메서드로 메타데이터를 추가한다.

        Args:
            ref:         기존 reference dict
            content:     content_hash 생성을 위한 원문 텍스트
            source_type: "web" | "local" | "llm_prior" | "notebook"
        """
        tagged = dict(ref)
        if source_type:
            tagged.setdefault("source_type", source_type)
        if content:
            tagged["content_hash"] = "sha256:" + hashlib.sha256(
                content.encode("utf-8", errors="replace")
            ).hexdigest()[:16]
        tagged.setdefault("relevance_score", float(ref.get("score") or 0.5))
        tagged.setdefault("authority_level", "secondary")
        tagged.setdefault("recency", "")
        return tagged

    def verify_with_retry(
        self,
        evidence_fn: Callable[..., dict],
        task_input: str,
        max_retries: int = 2,
    ) -> tuple[dict, VerificationResult]:
        """evidence_fn을 호출하고 품질이 부족하면 재시도.

        Args:
            evidence_fn: collect_project_evidence에 해당하는 callable.
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
                # hint_gaps를 전달해 targeted retry 유도.
                # evidence_fn이 hint_gaps를 지원하지 않으면 kwargs 없이 재시도.
                try:
                    evidence = evidence_fn(hint_gaps=result.gaps)
                except TypeError:
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
