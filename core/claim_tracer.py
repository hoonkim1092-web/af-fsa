"""
core/claim_tracer.py
====================
Claim-to-source traceability.

산출물의 핵심 주장(claim)을 추출하고, 각 주장이 어떤 근거(source)와
연결되는지 grounding_score와 함께 매핑한다.

사용법:
    tracer = ClaimTracer()
    claim_map = tracer.trace(artifact, evidence_bundle)
    artifact["_claim_map"] = claim_map
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Claim:
    claim_id: str
    text: str
    supporting_sources: list[str] = field(default_factory=list)
    grounding_score: float = 0.0
    status: str = "ungrounded"  # "grounded" | "partial" | "ungrounded"


@dataclass
class ClaimMap:
    claims: list[Claim] = field(default_factory=list)
    grounded_count: int = 0
    total_count: int = 0
    grounded_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {
            "claims": [
                {
                    "claim_id": c.claim_id,
                    "text": c.text,
                    "supporting_sources": c.supporting_sources,
                    "grounding_score": c.grounding_score,
                    "status": c.status,
                }
                for c in self.claims
            ],
            "grounded_count": self.grounded_count,
            "total_count": self.total_count,
            "grounded_ratio": self.grounded_ratio,
        }


class ClaimTracer:
    """artifact와 evidence_bundle에서 claim-source 연결을 추적한다."""

    # 주장으로 간주할 필드 목록 (artifact 스키마 기준)
    # goal은 _extract_claims에서 별도 처리, 나머지는 list 필드로 순회
    _CLAIM_FIELDS = [
        "goal",
        "constraints",
        "risks",
        "implementation_notes",
        "evidence_summary",
        "open_questions",
    ]
    _LIST_CLAIM_FIELDS = [f for f in _CLAIM_FIELDS if f != "goal"]

    def trace(self, artifact: dict, evidence_bundle: dict | None = None) -> dict:
        """artifact에서 핵심 주장을 추출하고 evidence와 연결해 ClaimMap dict를 반환."""
        claims = self._extract_claims(artifact)
        sources = self._index_sources(evidence_bundle or {})

        for claim in claims:
            matched_ids, score = self._match_sources(claim.text, sources)
            claim.supporting_sources = matched_ids
            claim.grounding_score = score
            if score >= 0.6:
                claim.status = "grounded"
            elif score >= 0.3:
                claim.status = "partial"
            else:
                claim.status = "ungrounded"

        grounded = sum(1 for c in claims if c.status == "grounded")
        total = len(claims)
        ratio = round(grounded / total, 3) if total > 0 else 0.0

        claim_map = ClaimMap(
            claims=claims,
            grounded_count=grounded,
            total_count=total,
            grounded_ratio=ratio,
        )
        return claim_map.to_dict()

    def get_ungrounded(self, claim_map: dict) -> list[str]:
        """claim_map에서 ungrounded 주장 텍스트 목록을 반환."""
        return [
            c["text"]
            for c in (claim_map.get("claims") or [])
            if c.get("status") == "ungrounded"
        ]

    # ── 내부 메서드 ──

    def _extract_claims(self, artifact: dict) -> list[Claim]:
        """artifact에서 주장 후보를 추출한다."""
        claims: list[Claim] = []
        cid = 0

        # goal 필드
        if artifact.get("goal"):
            cid += 1
            claims.append(Claim(
                claim_id=f"C{cid:03d}",
                text=str(artifact["goal"])[:300],
            ))

        # list 필드들: constraints, risks, implementation_notes, open_questions 등
        for field_name in self._LIST_CLAIM_FIELDS:
            items = artifact.get(field_name) or []
            if isinstance(items, list):
                for item in items:
                    text = ""
                    if isinstance(item, str):
                        text = item.strip()
                    elif isinstance(item, dict):
                        # risks의 경우 description 필드 등
                        text = str(
                            item.get("description") or item.get("text") or
                            item.get("name") or item.get("statement") or ""
                        ).strip()
                    if text and len(text) > 20:  # 너무 짧은 항목 제외
                        cid += 1
                        claims.append(Claim(
                            claim_id=f"C{cid:03d}",
                            text=text[:300],
                        ))

        return claims

    def _index_sources(self, evidence_bundle: dict) -> list[dict]:
        """evidence_bundle에서 검색 가능한 소스 목록을 만든다."""
        sources: list[dict] = []

        for ref_key in ["local_references", "web_references", "llm_prior_references"]:
            for ref in (evidence_bundle.get(ref_key) or []):
                if not isinstance(ref, dict):
                    continue
                source_id = str(ref.get("source_id") or ref.get("id") or ref.get("path") or "")
                content = str(
                    ref.get("content") or ref.get("snippet") or
                    ref.get("text") or ref.get("summary") or ""
                ).lower()
                if source_id or content:
                    sources.append({
                        "id": source_id,
                        "content": content,
                        "title": str(ref.get("title") or ref.get("path") or source_id),
                    })

        # evidence_summary 텍스트도 소스로 취급
        for i, summary in enumerate(evidence_bundle.get("evidence_summary") or []):
            text = str(summary).strip().lower()
            if text:
                sources.append({
                    "id": f"ev_summary_{i}",
                    "content": text,
                    "title": f"evidence_summary[{i}]",
                })

        return sources

    def _match_sources(self, claim_text: str, sources: list[dict]) -> tuple[list[str], float]:
        """claim_text와 sources 사이의 매칭 점수를 계산한다.

        간단한 키워드 오버랩 방식 사용 (임베딩 없이도 동작).
        SemanticEmbedder가 있으면 코사인 유사도로 보완한다.
        """
        if not sources:
            return [], 0.0

        claim_tokens = set(self._tokenize(claim_text))
        if not claim_tokens:
            return [], 0.0

        matched_ids: list[str] = []
        best_score = 0.0

        for src in sources:
            content_tokens = set(self._tokenize(src["content"]))
            if not content_tokens:
                continue

            overlap = len(claim_tokens & content_tokens)
            union = len(claim_tokens | content_tokens)
            jaccard = overlap / union if union > 0 else 0.0

            # 임계값 이상이면 매칭 소스로 간주
            if jaccard >= 0.12:
                matched_ids.append(src["id"])
                best_score = max(best_score, min(1.0, jaccard * 3))  # 정규화

        return matched_ids[:5], round(best_score, 3)  # 최대 5개 소스

    def _tokenize(self, text: str) -> list[str]:
        """텍스트를 토큰화한다 (소문자, 2자 이상)."""
        return [t for t in re.findall(r"[a-z가-힣]{2,}", text.lower()) if len(t) >= 2]


__all__ = ["ClaimTracer", "ClaimMap", "Claim"]
