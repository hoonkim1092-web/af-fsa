"""
core/parallel_critique.py
=========================
이종 모델 병렬 비평 (Heterogeneous Parallel Critique).

A-HMAD 패턴 적용: 서로 다른 CLI 프로바이더가 독립적으로 비평을 수행하고
Critique Aggregator가 confirmed/suspected gap을 분류해 병합한다.

흐름:
  artifact + task_profile
    ├─> [Critic A: Claude CLI]  정합성 + 누락 검토
    ├─> [Critic B: Gemini CLI]  실행 가능성 + 기술 선택 검토
    └─> (선택) [Critic C: Codex] 근거 충분성 + 대안 검토
        │
        v
  CritiqueAggregator
    → confirmed_gaps (2+ critics 동의)
    → suspected_gaps (1 critic만 지적)
    → weighted score
    → revision_instructions
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────────────────────

@dataclass
class CritiqueResult:
    provider_id: str        # "claude_cli" | "gemini_cli" | "codex_cli"
    focus: str              # "alignment" | "feasibility" | "grounding"
    score: float            # 0.0 - 1.0
    gaps: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    revision_instructions: list[str] = field(default_factory=list)
    raw_output: str = ""
    ok: bool = True


@dataclass
class MergedCritique:
    score: float                        # 가중 평균 (0.0-1.0)
    status: str                         # "pass" | "partial" | "fail"
    confirmed_gaps: list[str] = field(default_factory=list)   # 2+ critics 동의
    suspected_gaps: list[str] = field(default_factory=list)   # 1 critic만 지적
    strengths: list[str] = field(default_factory=list)
    revision_instructions: list[str] = field(default_factory=list)
    critic_count: int = 0
    individual_scores: dict[str, float] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────
# Critique 프롬프트 정의
# ──────────────────────────────────────────────────────────────

_CRITIQUE_PROMPTS: dict[str, str] = {
    "alignment": """You are a rigorous technical reviewer. Evaluate the following artifact for:
1. Goal alignment: Does it match the original request?
2. Completeness: Are all deliverables present with owners?
3. Risk coverage: Does each risk have a mitigation strategy?
4. Acceptance criteria: Are they observable and testable?

Respond in JSON:
{
  "score": 0.0-1.0,
  "gaps": ["..."],
  "strengths": ["..."],
  "revision_instructions": ["..."]
}
""",
    "feasibility": """You are a senior implementation architect. Evaluate the following artifact for:
1. Technical feasibility: Is the approach implementable?
2. Technology choices: Are rationale and alternatives provided?
3. Implementation specificity: Are steps concrete enough to execute?
4. Role/module consistency: Are there contradictions or circular dependencies?

Respond in JSON:
{
  "score": 0.0-1.0,
  "gaps": ["..."],
  "strengths": ["..."],
  "unsupported_claims": ["..."],
  "revision_instructions": ["..."]
}
""",
    "grounding": """You are a critical fact-checker. Evaluate the following artifact for:
1. Evidence grounding: What fraction of key claims have cited sources?
2. Unsupported assertions: List claims that lack evidence.
3. Alternative perspectives: Are important alternatives considered?
4. Recency: Is the information current?

Respond in JSON:
{
  "score": 0.0-1.0,
  "gaps": ["..."],
  "unsupported_claims": ["..."],
  "strengths": ["..."],
  "revision_instructions": ["..."]
}
""",
}

# 프로바이더 → 비평 포커스 매핑 (frontier 우선)
_PROVIDER_FOCUS: dict[str, str] = {
    "claude_cli": "alignment",
    "gemini_cli": "feasibility",
    "codex_cli":  "grounding",
}

# 프로바이더별 가중치 (frontier 모델에 높은 가중치)
_PROVIDER_WEIGHTS: dict[str, float] = {
    "claude_cli": 0.40,
    "gemini_cli": 0.35,
    "codex_cli":  0.25,
}


# ──────────────────────────────────────────────────────────────
# ParallelCritiqueEngine
# ──────────────────────────────────────────────────────────────

class ParallelCritiqueEngine:
    """이종 CLI 프로바이더를 사용한 병렬 비평 실행기."""

    def __init__(self, workspace: str = "", providers: list[str] | None = None):
        self.workspace = workspace
        self._providers = providers  # None이면 자동 탐지

    def critique(self, artifact: dict, task_profile: dict | None = None) -> MergedCritique:
        """artifact에 대해 이종 병렬 비평을 실행하고 MergedCritique를 반환."""
        providers = self._get_providers()
        if not providers:
            # 프로바이더 없으면 기본 비평 반환
            return self._fallback_critique(artifact)

        artifact_text = json.dumps(artifact, ensure_ascii=False, indent=2)
        # CLI 토큰 한도 초과 방지: 약 40K chars(~10K tokens)로 트런케이트
        _MAX_ARTIFACT_CHARS = 40_000
        if len(artifact_text) > _MAX_ARTIFACT_CHARS:
            artifact_text = artifact_text[:_MAX_ARTIFACT_CHARS] + "\n...[truncated]"

        # 병렬로 각 프로바이더에서 비평 실행
        results: list[CritiqueResult] = []
        with ThreadPoolExecutor(max_workers=len(providers)) as executor:
            futures = {
                executor.submit(self._run_single_critique, pid, artifact_text, task_profile): pid
                for pid in providers
            }
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    result = future.result(timeout=120)
                    results.append(result)
                except Exception as exc:
                    print(f"[ParallelCritique] {pid} 비평 실패: {exc}")
                    results.append(CritiqueResult(
                        provider_id=pid,
                        focus=_PROVIDER_FOCUS.get(pid, "alignment"),
                        score=0.5,
                        ok=False,
                    ))

        return self._aggregate(results)

    # ── 내부 메서드 ──

    def _get_providers(self) -> list[str]:
        if self._providers is not None:
            return self._providers
        try:
            from core.providers.registry import detect_installed_cli_providers
            return detect_installed_cli_providers()
        except Exception:
            return []

    def _run_single_critique(
        self, provider_id: str, artifact_text: str, task_profile: dict | None
    ) -> CritiqueResult:
        """단일 프로바이더로 비평을 실행한다."""
        focus = _PROVIDER_FOCUS.get(provider_id, "alignment")
        system_prompt = _CRITIQUE_PROMPTS.get(focus, _CRITIQUE_PROMPTS["alignment"])

        user_msg = f"Artifact to critique:\n\n{artifact_text}"
        if task_profile:
            user_msg = (
                f"Task profile: {json.dumps(task_profile, ensure_ascii=False)}\n\n"
                + user_msg
            )

        raw_output = self._call_cli(provider_id, system_prompt, user_msg)
        return self._parse_critique(provider_id, focus, raw_output)

    def _call_cli(self, provider_id: str, system_prompt: str, user_msg: str) -> str:
        """CLI 프로바이더를 호출해 응답 텍스트를 반환한다.

        cross_verification.py와 동일한 CliChatRequest + execute_cli_chat 패턴 사용.
        """
        try:
            from core.providers.cli import CliChatRequest, execute_cli_chat
            req = CliChatRequest(
                provider_id=provider_id,
                model="",
                system_prompt=system_prompt,
                task_input=user_msg,
                workspace=self.workspace,
                run_id=f"critique_{provider_id}",
                auto_approve=True,
            )
            result = execute_cli_chat(req) or {}
            return str(result.get("text") or result.get("stdout") or "")
        except Exception as exc:
            print(f"[ParallelCritique] CliChatRequest failed for {provider_id}: {exc}")

        # 폴백: requirement_llm 경유 (텍스트만 추출)
        try:
            from core.requirement_llm import execute_requirement_prompt
            combined = f"{system_prompt}\n\n{user_msg}"
            result = execute_requirement_prompt(combined)
            if isinstance(result, dict):
                return str(result.get("text") or result.get("stdout") or "")
            return str(result or "")
        except Exception as exc:
            print(f"[ParallelCritique] fallback also failed for {provider_id}: {exc}")
            return ""

    def _parse_critique(self, provider_id: str, focus: str, raw_output: str) -> CritiqueResult:
        """CLI 출력에서 JSON을 파싱해 CritiqueResult를 반환한다."""
        # JSON 블록 추출
        text = raw_output.strip()
        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            try:
                data = json.loads(text[json_start:json_end])
                return CritiqueResult(
                    provider_id=provider_id,
                    focus=focus,
                    score=float(data.get("score") or 0.5),
                    gaps=list(data.get("gaps") or []),
                    strengths=list(data.get("strengths") or []),
                    unsupported_claims=list(data.get("unsupported_claims") or []),
                    revision_instructions=list(data.get("revision_instructions") or []),
                    raw_output=raw_output,
                    ok=True,
                )
            except (json.JSONDecodeError, ValueError):
                pass

        # 파싱 실패: 텍스트 기반 기본값
        return CritiqueResult(
            provider_id=provider_id,
            focus=focus,
            score=0.5,
            gaps=["critique_parse_failed"],
            raw_output=raw_output,
            ok=bool(raw_output),
        )

    def _aggregate(self, results: list[CritiqueResult]) -> MergedCritique:
        """여러 CritiqueResult를 병합해 MergedCritique를 반환한다.

        핵심 로직:
        - 2+ critics가 동일/유사 gap을 지적하면 confirmed_gap
        - 1 critic만 지적하면 suspected_gap
        - 점수는 프로바이더 가중치 기반 평균
        """
        if not results:
            return MergedCritique(score=0.5, status="partial")

        ok_results = [r for r in results if r.ok]
        if not ok_results:
            ok_results = results  # 모두 실패해도 진행

        # 가중 점수 계산
        total_weight = 0.0
        weighted_score = 0.0
        individual_scores: dict[str, float] = {}
        for r in ok_results:
            w = _PROVIDER_WEIGHTS.get(r.provider_id, 0.33)
            weighted_score += r.score * w
            total_weight += w
            individual_scores[r.provider_id] = r.score

        score = round(weighted_score / total_weight, 3) if total_weight > 0 else 0.5

        # Gap 집계: 텍스트 정규화 후 중복 감지
        gap_counts: dict[str, int] = {}
        gap_sources: dict[str, list[str]] = {}
        for r in ok_results:
            seen_in_this = set()
            for gap in r.gaps:
                key = self._normalize_gap(gap)
                if key not in seen_in_this:
                    gap_counts[key] = gap_counts.get(key, 0) + 1
                    gap_sources.setdefault(key, []).append(gap)
                    seen_in_this.add(key)

        confirmed_gaps = [gap_sources[k][0] for k, cnt in gap_counts.items() if cnt >= 2]
        suspected_gaps = [gap_sources[k][0] for k, cnt in gap_counts.items() if cnt == 1]

        # Strengths 합집합
        strengths: list[str] = []
        seen_strengths: set[str] = set()
        for r in ok_results:
            for s in r.strengths:
                key = self._normalize_gap(s)
                if key not in seen_strengths:
                    strengths.append(s)
                    seen_strengths.add(key)

        # Revision instructions: confirmed gap 우선 정렬
        all_instructions: list[str] = []
        seen_instr: set[str] = set()
        for r in ok_results:
            for inst in r.revision_instructions:
                key = self._normalize_gap(inst)
                if key not in seen_instr:
                    all_instructions.append(inst)
                    seen_instr.add(key)

        # 상태 판정
        if score >= 0.8 and not confirmed_gaps:
            status = "pass"
        elif score >= 0.6 or not confirmed_gaps:
            status = "partial"
        else:
            status = "fail"

        return MergedCritique(
            score=score,
            status=status,
            confirmed_gaps=confirmed_gaps,
            suspected_gaps=suspected_gaps,
            strengths=strengths,
            revision_instructions=all_instructions,
            critic_count=len(ok_results),
            individual_scores=individual_scores,
        )

    def _normalize_gap(self, text: str) -> str:
        """Gap 텍스트를 정규화해 중복 감지에 사용한다."""
        return re.sub(r"\s+", " ", str(text).lower().strip())[:80]

    def _fallback_critique(self, artifact: dict) -> MergedCritique:
        """프로바이더가 없을 때 기본 비평 결과를 반환한다."""
        gaps: list[str] = []
        if not artifact.get("goal"):
            gaps.append("goal is missing or empty")
        if not artifact.get("deliverables"):
            gaps.append("deliverables are missing")
        if not artifact.get("acceptance_criteria"):
            gaps.append("acceptance_criteria are missing")

        score = max(0.4, 1.0 - len(gaps) * 0.15)
        return MergedCritique(
            score=score,
            status="partial" if gaps else "pass",
            suspected_gaps=gaps,
            critic_count=0,
        )


__all__ = ["ParallelCritiqueEngine", "MergedCritique", "CritiqueResult"]
