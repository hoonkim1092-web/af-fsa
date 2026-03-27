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
    selected_provider: str     # 최선 결과의 provider_id
    merged_output: str         # Opus가 병합한 최종 출력
    feedback: str              # 전체 피드백
    failure_patterns: list[str] = field(default_factory=list)  # 자가진화용 패턴
    confidence: float = 0.0    # 0.0~1.0
    round_num: int = 0


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
                reviewer_idx, target_idx, reviewer_pid = futures[future]
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
        # JSON 블록 추출 시도
        match = re.search(r"\{[^{}]*\"score\"[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        # 점수만 추출 시도
        score_match = re.search(r"score[\"']?\s*:\s*(\d+)", text)
        score = int(score_match.group(1)) if score_match else 50
        return {"score": score, "issues": [], "feedback": text[:500]}

    # ── 내부 메서드: Opus 판정 ──

    def _opus_judge(
        self, verified: list[VerificationResult], original_task: str, round_num: int
    ) -> JudgmentResult:
        """claude-opus-4-6으로 최종 판정한다."""

        # 결과 요약 구성
        summaries = []
        for r in verified:
            summaries.append(
                f"[{r.provider_id}] score={r.review_score} ok={r.ok}\n"
                f"리뷰어: {r.review_by or '없음'}\n"
                f"이슈: {', '.join(r.issues) or '없음'}\n"
                f"출력(앞 1500자):\n{r.output[:1500]}"
            )

        judge_prompt = (
            f"[원래 태스크]\n{original_task}\n\n"
            f"[{len(verified)}개 구현 결과 + 리뷰]\n\n"
            + "\n\n---\n\n".join(summaries)
            + "\n\n"
            f"위 결과를 종합하여 다음 JSON 형식으로 판정하세요:\n"
            f'{{\n'
            f'  "verdict": "pass" | "fail" | "partial" | "abort",\n'
            f'  "selected_provider": "provider_id",\n'
            f'  "merged_output": "최선의 결과 또는 병합된 코드",\n'
            f'  "feedback": "전체 피드백",\n'
            f'  "failure_patterns": ["pattern1", "pattern2"],\n'
            f'  "confidence": 0.0~1.0\n'
            f'}}\n\n'
            f'판정 기준:\n'
            f'- pass: 심각한 문제 없음, 태스크 충족\n'
            f'- fail: 수정 가능한 문제 있음\n'
            f'- partial: 부분 달성, 추가 작업 필요\n'
            f'- abort: 근본적으로 해결 불가 (요구사항 모순, API 미존재 등)\n'
            f'병합 불가 시 가장 높은 score의 결과를 selected_provider로 선택하세요.'
        )

        # claude_cli 설치 여부 확인 (Opus 판정은 claude_cli 필수)
        judge_provider = self._pick_judge_provider()

        try:
            from core.providers.cli import CliChatRequest, execute_cli_chat

            req = CliChatRequest(
                provider_id=judge_provider,
                model=_OPUS_MODEL if judge_provider == "claude_cli" else "",
                system_prompt="당신은 코드 품질 판정 전문가입니다. 반드시 JSON으로만 답변하세요.",
                task_input=judge_prompt,
                workspace=self.workspace,
                run_id=f"opus_judge_r{round_num}",
                auto_approve=True,
            )
            raw = execute_cli_chat(req) or {}
            text = str(raw.get("text", ""))
            parsed = self._parse_judgment_json(text)
            judge_label = f"Opus({judge_provider})" if judge_provider == "claude_cli" else judge_provider
            self._print(f"{judge_label} 판정: {parsed.get('verdict', 'unknown')} "
                        f"(confidence={parsed.get('confidence', 0):.2f})", "36")

            # merged_output이 비어있으면 가장 높은 score 결과로 채움
            merged = parsed.get("merged_output", "")
            if not merged and verified:
                best = max(verified, key=lambda r: r.review_score)
                merged = best.output

            return JudgmentResult(
                verdict=parsed.get("verdict", "fail"),
                selected_provider=parsed.get("selected_provider", "") or (verified[0].provider_id if verified else ""),
                merged_output=merged,
                feedback=parsed.get("feedback", ""),
                failure_patterns=parsed.get("failure_patterns", []),
                confidence=float(parsed.get("confidence", 0.0)),
                round_num=round_num,
            )
        except Exception as exc:
            self._print(f"판정 실패, 점수 기반 폴백: {exc}", "33")
            # 폴백: 가장 높은 score의 결과 선택
            best = max(verified, key=lambda r: r.review_score, default=None)
            fallback_output = best.output if best and best.output else "[판정 실패 - 결과 없음]"
            return JudgmentResult(
                verdict="partial",
                selected_provider=best.provider_id if best else "",
                merged_output=fallback_output,
                feedback=f"판정 실패({exc}). 점수 기반 폴백: {best.provider_id if best else '없음'}",
                confidence=0.3,
                round_num=round_num,
            )

    def _pick_judge_provider(self) -> str:
        """Opus 판정용 프로바이더를 선택한다.

        claude_cli가 설치되어 있으면 Opus 사용.
        없으면 설치된 다른 프로바이더로 폴백 (최선의 단일 판정).
        """
        if "claude_cli" in self.providers:
            return "claude_cli"
        # claude_cli 없으면 설치된 다른 프로바이더 중 첫 번째
        if self.providers:
            provider = self.providers[0]
            self._print(
                f"claude_cli 없음 → {provider}으로 판정 (Opus 미사용). "
                f"Opus 판정을 원하면 claude_cli 설치 권장.", "33"
            )
            return provider
        # 아무것도 없으면 claude_cli 시도 (설치 프롬프트 유도)
        return "claude_cli"

    def _parse_judgment_json(self, text: str) -> dict:
        """판정 응답에서 JSON을 추출한다."""
        match = re.search(r"\{[\s\S]*\"verdict\"[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        # 부분 추출
        verdict_match = re.search(r'"verdict"\s*:\s*"(\w+)"', text)
        verdict = verdict_match.group(1) if verdict_match else "fail"
        conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        confidence = float(conf_match.group(1)) if conf_match else 0.3
        return {
            "verdict": verdict,
            "selected_provider": "",
            "merged_output": text[:2000],
            "feedback": text[:500],
            "failure_patterns": [],
            "confidence": confidence,
        }

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
