"""
core/cross_verification.py
==========================
멀티 CLI 교차검증 + Opus 최종판정 + 자가진화 루프.

흐름:
  ① EXECUTE   — 설치된 CLI 엔진들이 병렬로 태스크를 실행
  ② CROSS-VERIFY — 각 엔진이 다른 엔진의 결과를 순환 리뷰
  ③ OPUS JUDGE — claude-opus-4-6 이 3개 결과+리뷰를 종합해 최종 판정
  ④ EVOLVE    — FAIL 시 failure_patterns → 관련 스킬 자동 진화
  ⑤ PASS      — 성공 패턴 기록 후 결과 반환

탈출 조건 (5가지):
  1. PASS    — Opus verdict == "pass"
  2. ABORT   — Opus verdict == "abort"  (근본적으로 해결 불가)
  3. STALL   — confidence 개선 없음 (이전 라운드 대비 향상 없음)
  4. MAX     — max_rounds 소진
  5. BUDGET  — 레벨별 라운드 제한 (Starter: 비활성, Dynamic: 1, Enterprise: 3)
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────────────────────

@dataclass
class VerificationResult:
    provider_id: str       # "gemini_cli" | "claude_cli" | "codex_cli"
    model: str             # 사용된 모델명
    output: str            # 실행 출력
    ok: bool               # 실행 성공 여부
    review_by: str = ""    # 리뷰한 엔진 ID
    review_score: int = 0  # 0~100
    review_feedback: str = ""
    issues: list[str] = field(default_factory=list)


@dataclass
class JudgmentResult:
    verdict: str               # "pass" | "fail" | "partial" | "abort"
    selected_provider: str     # 베이스로 선택된 provider_id
    merged_output: str         # 통합된 최종 출력 (keep 부분 합성 + discard 제거)
    feedback: str              # 전체 피드백
    failure_patterns: list[str] = field(default_factory=list)  # 자가진화용 패턴
    confidence: float = 0.0    # 0.0~1.0
    round_num: int = 0
    keep_parts: list[str] = field(default_factory=list)    # 통합에 채택된 부분 설명
    discard_parts: list[str] = field(default_factory=list) # 폐기된 부분 및 이유


# ──────────────────────────────────────────────────────────────
# 레벨별 라운드 제한
# ──────────────────────────────────────────────────────────────

_LEVEL_MAX_ROUNDS: dict[str, int] = {
    "starter":    0,   # 비활성 (단일 실행으로 직접 진행)
    "dynamic":    1,   # 1라운드
    "enterprise": 3,   # 풀 루프
}

# Opus 판정에 사용할 모델
_OPUS_MODEL = "claude-opus-4-6"

# 각 프로바이더의 코딩 전문 모델 (빈 문자열 = CLI 기본 모델)
_CODING_MODELS: dict[str, str] = {
    "claude_cli": "",   # claude CLI 기본 (sonnet)
    "gemini_cli": "",   # gemini CLI 기본
    "codex_cli":  "",   # codex 기본
}


# ──────────────────────────────────────────────────────────────
# CrossVerificationLoop
# ──────────────────────────────────────────────────────────────

class CrossVerificationLoop:
    """
    멀티 CLI 교차검증 + Opus 최종판정 반복 루프.

    사용 예:
        loop = CrossVerificationLoop(workspace="/path/to/project", level="enterprise")
        judgment = loop.run(task="API 서버 구현", system_prompt="시니어 개발자로 행동하세요")
        if judgment.verdict == "pass":
            print(judgment.merged_output)
    """

    def __init__(
        self,
        workspace: str,
        level: str = "dynamic",
        max_rounds: int | None = None,
        providers: list[str] | None = None,
    ):
        self.workspace = workspace
        self.level = level.lower()

        # 레벨별 기본 라운드 수 (명시적 override 가능)
        budget = _LEVEL_MAX_ROUNDS.get(self.level, 1)
        self.max_rounds = max_rounds if max_rounds is not None else budget

        # 설치된 프로바이더 탐지 (override 가능)
        self.providers: list[str] = providers or self._detect_providers()
        self.history: list[JudgmentResult] = []

    # ── 공개 진입점 ──

    def run(self, task: str, system_prompt: str = "") -> JudgmentResult:
        """교차검증 루프 실행. 탈출 조건 충족 시 JudgmentResult 반환."""

        # Starter 레벨 또는 max_rounds == 0 → 교차검증 비활성
        if self.max_rounds == 0 or not self.providers:
            return self._skip_result(task)

        # 프로바이더가 1개 → 단일 실행 + Opus 단독 검증
        if len(self.providers) == 1:
            return self._single_provider_run(task, system_prompt)

        prev_confidence = -1.0
        current_task = task

        for round_num in range(1, self.max_rounds + 1):
            self._print(f"[교차검증 라운드 {round_num}/{self.max_rounds}] 시작", "36")

            # ① 병렬 실행
            results = self._execute_parallel(current_task, system_prompt)

            # ② 교차 검증
            verified = self._cross_verify(results, current_task)

            # ③ Opus 최종 판정
            judgment = self._opus_judge(verified, task, round_num)
            self.history.append(judgment)

            # 탈출 조건 1: PASS
            if judgment.verdict == "pass":
                self._print(f"[PASS] 라운드 {round_num}에서 통과", "32")
                self._record_success(judgment)
                return judgment

            # 탈출 조건 2: ABORT (근본적 실패)
            if judgment.verdict == "abort":
                self._print(f"[ABORT] Opus 판정: 근본적 해결 불가", "31")
                return judgment

            # 탈출 조건 3: STALL (confidence 개선 없음)
            if round_num > 1 and judgment.confidence <= prev_confidence:
                self._print(
                    f"[STALL] confidence {judgment.confidence:.2f} ≤ {prev_confidence:.2f} "
                    f"— 개선 없음, 조기 중단", "33"
                )
                return judgment

            prev_confidence = judgment.confidence

            # ④ 자가진화 트리거
            if judgment.failure_patterns:
                self._trigger_evolution(judgment)

            # 다음 라운드를 위한 태스크 리파인
            current_task = self._refine_task(task, judgment)

        # 탈출 조건 4: MAX 소진
        self._print(f"[MAX] 최대 라운드({self.max_rounds}) 소진 — 마지막 결과 반환", "33")
        return self.history[-1]

    # ── 내부 메서드: 탐지 ──

    def _detect_providers(self) -> list[str]:
        """설치된 CLI 프로바이더를 탐지한다."""
        try:
            from core.providers.registry import detect_installed_cli_providers
            return detect_installed_cli_providers()
        except Exception:
            return []

    # ── 내부 메서드: 실행 ──

    def _execute_parallel(self, task: str, system_prompt: str) -> list[VerificationResult]:
        """ThreadPoolExecutor로 N개 CLI를 병렬 실행한다."""
        try:
            from core.providers.cli import CliChatRequest, execute_cli_chat
        except ImportError:
            return []

        results: list[VerificationResult] = []
        with ThreadPoolExecutor(max_workers=len(self.providers)) as pool:
            futures: dict[Any, str] = {}
            for pid in self.providers:
                model = _CODING_MODELS.get(pid, "")
                req = CliChatRequest(
                    provider_id=pid,
                    model=model,
                    system_prompt=system_prompt or "당신은 시니어 소프트웨어 엔지니어입니다.",
                    task_input=task,
                    workspace=self.workspace,
                    run_id=f"cv_{pid}",
                    auto_approve=True,
                )
                futures[pool.submit(execute_cli_chat, req)] = pid

            for future in as_completed(futures):
                pid = futures[future]
                try:
                    raw = future.result() or {}
                    results.append(VerificationResult(
                        provider_id=pid,
                        model=_CODING_MODELS.get(pid, ""),
                        output=str(raw.get("text", "")),
                        ok=bool(raw.get("ok", False)),
                    ))
                except Exception as exc:
                    results.append(VerificationResult(
                        provider_id=pid,
                        model="",
                        output=f"[실행 오류] {exc}",
                        ok=False,
                    ))

        self._print(f"병렬 실행 완료: {len(results)}개 결과", "90")
        return results

    # ── 내부 메서드: 교차 검증 ──

    def _cross_verify(
        self, results: list[VerificationResult], original_task: str
    ) -> list[VerificationResult]:
        """순환 방식(A→B, B→C, C→A)으로 교차 리뷰한다."""
        if len(results) < 2:
            return results

        try:
            from core.providers.cli import CliChatRequest, execute_cli_chat
        except ImportError:
            return results

        n = len(results)
        verified = list(results)

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {}
            for i, reviewer in enumerate(results):
                target_idx = (i + 1) % n
                target = results[target_idx]

                review_prompt = (
                    f"다음 코드/결과를 검토하세요.\n\n"
                    f"[원래 태스크]\n{original_task}\n\n"
                    f"[검토 대상 ({target.provider_id} 결과)]\n{target.output[:3000]}\n\n"
                    f"다음 항목을 분석하고 JSON으로 답변하세요:\n"
                    f'{{"score": 0~100, "issues": ["문제1", "문제2"], '
                    f'"feedback": "전체 코멘트"}}'
                )
                req = CliChatRequest(
                    provider_id=reviewer.provider_id,
                    model=_CODING_MODELS.get(reviewer.provider_id, ""),
                    system_prompt="당신은 시니어 코드 리뷰어입니다. JSON으로만 답변하세요.",
                    task_input=review_prompt,
                    workspace=self.workspace,
                    run_id=f"cv_review_{i}",
                    auto_approve=True,
                )
                futures[pool.submit(execute_cli_chat, req)] = (i, target_idx, reviewer.provider_id)

            for future in as_completed(futures):
                _, target_idx, reviewer_pid = futures[future]
                try:
                    raw = future.result() or {}
                    text = str(raw.get("text", ""))
                    parsed = self._parse_review_json(text)
                    verified[target_idx].review_by = reviewer_pid
                    verified[target_idx].review_score = parsed.get("score", 50)
                    verified[target_idx].review_feedback = parsed.get("feedback", "")
                    verified[target_idx].issues = parsed.get("issues", [])
                except Exception:
                    pass

        self._print("교차 검증 완료", "90")
        return verified

    def _parse_review_json(self, text: str) -> dict:
        """리뷰 응답에서 JSON을 추출한다."""
        # 깊이 추적 방식으로 JSON 추출 (feedback에 {}가 있어도 안전)
        parsed = self._extract_json_by_depth(text)
        if parsed is not None and "score" in parsed:
            return parsed
        # 코드블록 안에서 재시도
        block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if block_match:
            parsed = self._extract_json_by_depth(block_match.group(1))
            if parsed is not None and "score" in parsed:
                return parsed
        # 점수만 추출 시도
        score_match = re.search(r'"?score"?\s*:\s*(\d+)', text)
        score = int(score_match.group(1)) if score_match else 50
        return {"score": score, "issues": [], "feedback": text[:500]}

    # ── 내부 메서드: Opus 판정 (2단계) ──

    def _opus_judge(
        self, verified: list[VerificationResult], original_task: str, round_num: int
    ) -> JudgmentResult:
        """2단계 판정: Phase1(판정만) → Phase2(통합 합성, 필요 시만)."""
        judge_provider = self._pick_judge_provider(verified)

        # Phase 1: 판정 — 코드 없이 메타데이터만 JSON으로
        meta = self._judge_phase(verified, original_task, round_num, judge_provider)

        verdict = meta.get("verdict", "fail")
        keep_parts = meta.get("keep_parts", [])
        discard_parts = meta.get("discard_parts", [])
        selected_base = meta.get("selected_provider", "")
        feedback = meta.get("feedback", "")
        confidence = float(meta.get("confidence", 0.0))
        failure_patterns = meta.get("failure_patterns", [])

        judge_label = f"Opus({judge_provider})" if judge_provider == "claude_cli" else judge_provider
        self._print(
            f"{judge_label} 판정: {verdict} (confidence={confidence:.2f}) "
            f"채택 {len(keep_parts)}건 / 폐기 {len(discard_parts)}건", "36"
        )

        # 압도적 승자 판단 (score 차 20점 이상) 또는 abort/fail → Phase 2 스킵
        base_result = next((r for r in verified if r.provider_id == selected_base), None)
        if base_result is None and verified:
            base_result = max(verified, key=lambda r: r.review_score)

        skip_synthesis = (
            verdict in ("abort", "fail")
            or not keep_parts  # 채택할 부분이 없으면 합성 의미 없음
            or self._has_dominant_winner(verified)
        )

        if skip_synthesis:
            merged = base_result.output if base_result else ""
            if verdict not in ("abort",) and not merged:
                merged = "[결과 없음]"
        else:
            # Phase 2: 합성 — JSON 없이 순수 코드/텍스트 생성
            self._print("Phase 2: 통합 합성 시작", "36")
            merged = self._synthesize_phase(
                verified=verified,
                keep_parts=keep_parts,
                discard_parts=discard_parts,
                base_result=base_result,
                original_task=original_task,
                judge_provider=judge_provider,
                round_num=round_num,
            )

        return JudgmentResult(
            verdict=verdict,
            selected_provider=selected_base or (base_result.provider_id if base_result else ""),
            merged_output=merged,
            feedback=feedback,
            failure_patterns=failure_patterns,
            confidence=confidence,
            round_num=round_num,
            keep_parts=keep_parts,
            discard_parts=discard_parts,
        )

    def _judge_phase(
        self,
        verified: list[VerificationResult],
        original_task: str,
        round_num: int,
        judge_provider: str,
    ) -> dict:
        """Phase 1: 판정만 수행. JSON에 코드를 포함하지 않아 파싱이 안정적."""
        summaries = []
        for r in verified:
            summaries.append(
                f"[{r.provider_id}] score={r.review_score} ok={r.ok}\n"
                f"리뷰어: {r.review_by or '없음'}\n"
                f"이슈: {', '.join(r.issues) or '없음'}\n"
                f"리뷰 코멘트: {r.review_feedback[:500] or '없음'}\n"
                f"출력 앞 800자:\n{r.output[:800]}"
            )

        judge_prompt = (
            f"[원래 태스크]\n{original_task}\n\n"
            f"[{len(verified)}개 구현 결과 + 교차 리뷰 요약]\n\n"
            + "\n\n---\n\n".join(summaries)
            + "\n\n"
            f"## 판정 지침\n\n"
            f"각 구현의 장단점을 분석하여 판정하세요.\n"
            f"merged_output(실제 코드)은 여기서 작성하지 마세요 — 판정 메타데이터만 JSON으로 답변하세요.\n\n"
            f"다음 JSON만 출력하세요 (코드블록 없이):\n"
            f'{{\n'
            f'  "verdict": "pass" | "fail" | "partial" | "abort",\n'
            f'  "selected_provider": "베이스로 사용할 provider_id",\n'
            f'  "keep_parts": [\n'
            f'    "provider_id: 채택할 부분과 이유 (예: gemini_cli: 에러 핸들링 패턴이 견고함)"\n'
            f'  ],\n'
            f'  "discard_parts": [\n'
            f'    "provider_id: 폐기할 부분과 이유 (예: codex_cli: SQL 직접 포매팅 — injection 위험)"\n'
            f'  ],\n'
            f'  "feedback": "판정 근거 및 남은 문제점 (코드 제외, 설명만)",\n'
            f'  "failure_patterns": ["수정 필요 패턴명 (fail/partial 시만, 없으면 [])"],\n'
            f'  "confidence": <0.0~1.0 사이 실수>\n'
            f'}}\n\n'
            f'verdict 기준:\n'
            f'- pass: 태스크 완전 충족 (심각한 문제 없음)\n'
            f'- partial: 부분 달성, 추가 작업 필요\n'
            f'- fail: 수정 필요한 심각한 문제 존재\n'
            f'- abort: 근본적으로 불가능한 요구사항\n'
        )

        try:
            from core.providers.cli import CliChatRequest, execute_cli_chat
            req = CliChatRequest(
                provider_id=judge_provider,
                model=_OPUS_MODEL if judge_provider == "claude_cli" else "",
                system_prompt="당신은 코드 품질 판정 전문가입니다. 반드시 JSON으로만 답변하세요. 코드는 절대 출력하지 마세요.",
                task_input=judge_prompt,
                workspace=self.workspace,
                run_id=f"judge_phase1_r{round_num}",
                auto_approve=True,
            )
            raw = execute_cli_chat(req) or {}
            text = str(raw.get("text", ""))
            return self._parse_judgment_json(text)
        except Exception as exc:
            self._print(f"Phase 1 판정 실패, 점수 기반 폴백: {exc}", "33")
            best = max(verified, key=lambda r: r.review_score, default=None)
            return {
                "verdict": "partial",
                "selected_provider": best.provider_id if best else "",
                "keep_parts": [],
                "discard_parts": [],
                "feedback": f"판정 실패({exc}). 점수 기반 폴백.",
                "failure_patterns": [],
                "confidence": 0.3,
            }

    def _synthesize_phase(
        self,
        verified: list[VerificationResult],
        keep_parts: list[str],
        discard_parts: list[str],
        base_result: VerificationResult | None,
        original_task: str,
        judge_provider: str,
        round_num: int,
    ) -> str:
        """Phase 2: 판정 결과를 바탕으로 실제 코드 합성. JSON 없이 순수 텍스트 반환."""
        base_output = base_result.output if base_result else ""
        base_provider = base_result.provider_id if base_result else "unknown"

        # 채택할 다른 provider 결과 수집 (base 제외)
        supplements = []
        for r in verified:
            if r.provider_id != base_provider and r.output:
                supplements.append(
                    f"[{r.provider_id} 결과 — 일부 채택 예정]\n{r.output[:1200]}"
                )

        keep_section = (
            "[채택 지시]\n" + "\n".join(f"- {k}" for k in keep_parts) + "\n\n"
            if keep_parts else ""
        )
        discard_section = (
            "[폐기 지시]\n" + "\n".join(f"- {d}" for d in discard_parts) + "\n\n"
            if discard_parts else ""
        )
        synth_prompt = (
            f"[원래 태스크]\n{original_task}\n\n"
            f"[베이스 구현 ({base_provider})]\n{base_output[:2000]}\n\n"
            + (("\n\n".join(supplements) + "\n\n") if supplements else "")
            + keep_section
            + discard_section
            + "## 합성 지시\n\n"
            + "위 채택/폐기 지시에 따라 베이스 구현을 수정하여 최종 결과물을 만드세요.\n"
            + "- 채택 항목의 좋은 부분을 베이스에 통합하세요\n"
            + "- 폐기 항목에 해당하는 코드를 제거하거나 수정하세요\n"
            + "- 일관된 스타일로 완성된 결과물만 출력하세요 (설명 없이 결과물만)\n"
        )

        try:
            from core.providers.cli import CliChatRequest, execute_cli_chat
            req = CliChatRequest(
                provider_id=judge_provider,
                model=_OPUS_MODEL if judge_provider == "claude_cli" else "",
                system_prompt="당신은 시니어 소프트웨어 엔지니어입니다. 설명 없이 완성된 결과물만 출력하세요.",
                task_input=synth_prompt,
                workspace=self.workspace,
                run_id=f"judge_phase2_r{round_num}",
                auto_approve=True,
            )
            raw = execute_cli_chat(req) or {}
            result = str(raw.get("text", "")).strip()
            if result:
                self._print("Phase 2: 합성 완료", "32")
                return result
        except Exception as exc:
            self._print(f"Phase 2 합성 실패, 베이스 결과 사용: {exc}", "33")

        # 합성 실패 시 베이스 결과 그대로 반환
        return base_output

    def _has_dominant_winner(self, verified: list[VerificationResult]) -> bool:
        """score 차이가 20점 이상이면 압도적 승자로 판단 → 합성 스킵."""
        if len(verified) < 2:
            return True
        scores = sorted([r.review_score for r in verified], reverse=True)
        return (scores[0] - scores[1]) >= 20

    def _pick_judge_provider(self, verified: list | None = None) -> str:
        """Opus 판정용 프로바이더를 선택한다.

        claude_cli가 설치되어 있으면 항상 claude_cli(Opus) 사용.
        없으면 교차검증에서 상대방에게 더 높은 점수를 받은 provider를 판정자로 선택.
        동점이면 providers 리스트에서 마지막 항목 사용 (0번이 기본 판정자가 되는 것 방지).
        """
        if "claude_cli" in self.providers:
            return "claude_cli"

        # claude_cli 없음 → 교차검증 점수 기반 선택 (이해충돌 최소화)
        if verified and len(verified) >= 2:
            # review_score: 상대방이 나에게 준 점수 → 높을수록 더 좋은 코드
            best = max(verified, key=lambda r: r.review_score)
            provider = best.provider_id
            self._print(
                f"claude_cli 없음 → 교차검증 최고점({best.review_score}점) "
                f"{provider}가 판정 (Opus 미사용). "
                f"Opus 판정을 원하면 claude_cli 설치 권장.", "33"
            )
            return provider

        # verified 없거나 단일 결과 → 리스트 마지막 항목 (0번 편향 방지)
        if self.providers:
            provider = self.providers[-1]
            self._print(
                f"claude_cli 없음 → {provider}으로 판정 (Opus 미사용). "
                f"Opus 판정을 원하면 claude_cli 설치 권장.", "33"
            )
            return provider

        # 아무것도 없으면 claude_cli 시도 — execute_cli_chat이 실패하면 try/except 폴백 처리
        self._print("설치된 CLI 없음 → claude_cli 시도 (설치 안내 유도)", "33")
        return "claude_cli"

    def _parse_judgment_json(self, text: str) -> dict:
        """Phase 1 판정 응답에서 JSON을 추출한다.

        코드블록(```json ... ```) 안의 JSON도 처리한다.
        중괄호 깊이 추적 방식으로 정확한 범위를 찾는다.
        """
        # 코드블록이 있으면 그 안에서 JSON 범위를 추출
        block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        candidate = block_match.group(1) if block_match else text

        parsed = self._extract_json_by_depth(candidate)
        if parsed is not None:
            return parsed

        # 코드블록 밖에서 한 번 더 시도
        if block_match:
            parsed = self._extract_json_by_depth(text)
            if parsed is not None:
                return parsed

        # 폴백: 필드별 개별 추출
        verdict_match = re.search(r'"verdict"\s*:\s*"(\w+)"', text)
        verdict = verdict_match.group(1) if verdict_match else "fail"
        conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        confidence = float(conf_match.group(1)) if conf_match else 0.3
        provider_match = re.search(r'"selected_provider"\s*:\s*"([^"]+)"', text)
        selected = provider_match.group(1) if provider_match else ""
        feedback_match = re.search(r'"feedback"\s*:\s*"([^"]*)"', text)
        feedback = feedback_match.group(1) if feedback_match else text[:300]
        return {
            "verdict": verdict,
            "selected_provider": selected,
            "keep_parts": [],
            "discard_parts": [],
            "feedback": feedback,
            "failure_patterns": [],
            "confidence": confidence,
        }

    def _extract_json_by_depth(self, text: str) -> dict | None:
        """중괄호 깊이를 추적해 첫 번째 완전한 JSON 객체를 추출한다."""
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        return None
        return None

    # ── 내부 메서드: 자가진화 ──

    def _trigger_evolution(self, judgment: JudgmentResult) -> None:
        """실패 패턴 기반으로 관련 스킬을 자가진화시킨다."""
        try:
            from core.skill_evolution_bus import SkillEvolutionBus
            from core.skill_creator import evolve_skill
            from core.config_paths import SKILLS_DIR

            bus = SkillEvolutionBus.get_instance()
            patterns = judgment.failure_patterns

            self._print(f"자가진화 트리거: 패턴 {patterns}", "35")

            # 패턴에서 스킬 이름 추출 (예: "security:sql_injection" → "sql", "security")
            keywords = set()
            for p in patterns:
                parts = re.split(r"[:\-_]", p.lower())
                keywords.update(parts)

            # 관련 스킬 탐색
            evolved = []
            if os.path.isdir(SKILLS_DIR):
                for skill_name in os.listdir(SKILLS_DIR):
                    skill_dir = os.path.join(SKILLS_DIR, skill_name)
                    if not os.path.isdir(skill_dir):
                        continue
                    name_lower = skill_name.lower().replace("-", "_")
                    if any(kw in name_lower for kw in keywords if len(kw) > 3):
                        self._print(f"스킬 진화 시도: {skill_name}", "35")
                        ok = evolve_skill(
                            skill_dir=skill_dir,
                            feedback=judgment.feedback,
                            error_log=f"실패 패턴: {patterns}",
                        )
                        if ok:
                            evolved.append(skill_name)
                            old_v = "unknown"
                            new_v = "evolved"
                            bus.on_skill_evolved(
                                skill_id=skill_name,
                                skill_dir=skill_dir,
                                old_version=old_v,
                                new_version=new_v,
                                trigger="cross_verification",
                            )

            if evolved:
                self._print(f"진화 완료: {evolved}", "32")
        except Exception as exc:
            self._print(f"자가진화 실패 (무시): {exc}", "33")

    # ── 내부 메서드: 태스크 리파인 ──

    def _refine_task(self, original_task: str, judgment: JudgmentResult) -> str:
        """판정 피드백을 반영해 다음 라운드용 태스크를 보강한다."""
        if not judgment.feedback:
            return original_task
        return (
            f"[이전 시도 피드백]\n{judgment.feedback}\n\n"
            f"[수정 방향]\n{', '.join(judgment.failure_patterns) or '위 피드백 참고'}\n\n"
            f"[원래 태스크]\n{original_task}"
        )

    # ── 내부 메서드: 성공 기록 ──

    def _record_success(self, judgment: JudgmentResult) -> None:
        """성공 패턴을 메모리에 기록한다."""
        try:
            from core.utils import now_iso
            record = {
                "event": "cross_verification_pass",
                "provider": judgment.selected_provider,
                "confidence": judgment.confidence,
                "round": judgment.round_num,
                "providers_used": self.providers,
                "timestamp": now_iso(),
            }
            log_path = os.path.join(self.workspace, ".af", "cv_success_log.jsonl")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 단일 프로바이더 / 스킵 모드 ──

    def _single_provider_run(self, task: str, system_prompt: str) -> JudgmentResult:
        """CLI가 1개일 때: 단일 실행 후 Opus 단독 검증."""
        self._print("단일 프로바이더 모드: 교차검증 없이 Opus 단독 판정", "90")
        results = self._execute_parallel(task, system_prompt)
        if not results:
            return self._skip_result(task)
        # 교차검증 없이 바로 Opus 판정
        for r in results:
            r.review_score = 70
            r.review_feedback = "단일 실행 (교차검증 없음)"
        return self._opus_judge(results, task, round_num=1)

    def _skip_result(self, task: str) -> JudgmentResult:
        """교차검증 비활성 시 반환하는 더미 결과."""
        return JudgmentResult(
            verdict="pass",
            selected_provider="",
            merged_output="",
            feedback="교차검증 비활성 (Starter 레벨 또는 CLI 없음)",
            confidence=1.0,
            round_num=0,
        )

    # ── 유틸 ──

    def _print(self, msg: str, color: str = "0") -> None:
        import sys
        if sys.stdout.isatty():
            print(f"\033[{color}m  [CrossVerify] {msg}\033[0m")
        else:
            print(f"  [CrossVerify] {msg}")


# ──────────────────────────────────────────────────────────────
# 편의 함수
# ──────────────────────────────────────────────────────────────

def run_cross_verification(
    task: str,
    workspace: str,
    level: str = "dynamic",
    system_prompt: str = "",
    max_rounds: int | None = None,
) -> JudgmentResult:
    """CrossVerificationLoop 편의 래퍼."""
    loop = CrossVerificationLoop(
        workspace=workspace,
        level=level,
        max_rounds=max_rounds,
    )
    return loop.run(task=task, system_prompt=system_prompt)
