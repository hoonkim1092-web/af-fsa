"""
core/ise_redesigner.py
======================
ISE 재설계 엔진 -- 실패 이력을 바탕으로 태스크를 재구성한다.

Level 2 (전략 피벗):
  - 기존 접근법의 문제점을 분석하고 대안 생성
  - 과거 실패 전략을 LLM에 주입하여 반복 방지

Level 3 (설계 재시작):
  - 과거 실패 전략 요약을 LLM에 주입
  - 완전히 새로운 접근법으로 태스크 재작성

Level 5 (태스크 분해):
  - 단일 태스크를 독립 실행 가능한 서브태스크로 분할
  - 서브태스크 간 의존 관계 추출
"""
from __future__ import annotations

import json
import random
import re

from core.llm_engine import LLMEngine
from core.ise_analyzer import ISEAnalysis
from core.ise_strategy_ledger import StrategyLedger
from core.utils import safe_json_load, print_agent_msg


# 정체 시 랜덤 전략 변이 주입용 제약 조건
_CREATIVITY_PERTURBATIONS = [
    "표준 라이브러리만 사용하여 구현하세요. 외부 의존성을 최소화합니다.",
    "가장 단순한 방법으로 접근하세요. 복잡성을 최소화합니다.",
    "이 문제를 반대로 생각해보세요. 출력에서 입력으로 역추적합니다.",
    "단계별로 가장 작은 단위부터 구현하세요. 각 단계를 검증합니다.",
    "테스트를 먼저 작성한 후 구현하세요 (TDD 접근).",
    "기존 코드를 최대한 재사용하세요. 새로 작성하는 코드를 최소화합니다.",
    "완전히 다른 알고리즘/패턴을 사용하세요.",
    "문제를 2~3개의 독립적인 하위 문제로 분리하세요.",
    "에러가 발생하는 부분을 우회하는 대안을 찾으세요.",
    "프로토타입을 먼저 만들고 점진적으로 개선하세요.",
]


class ISERedesigner:
    """LLM 기반 태스크 재설계 + 분해 엔진."""

    def __init__(self, model_name: str = "gemini-1.5-pro-latest"):
        self.llm = LLMEngine(model_name=model_name)

    # ── Level 1: 단순 재시도 피드백 ──

    def apply_retry_feedback(
        self,
        original_task: str,
        analysis: ISEAnalysis,
    ) -> str:
        """실패 분석을 바탕으로 태스크에 피드백을 주입한다."""
        feedback = analysis.new_instruction or analysis.evaluator_reasoning
        if not feedback:
            feedback = f"이전 실패 원인: {analysis.root_cause or analysis.error_category}"

        return (
            f"[ISE RETRY FEEDBACK]\n"
            f"이전 시도에서 다음 문제가 발생했습니다:\n"
            f"- 에러 유형: {analysis.error_category}\n"
            f"- 원인: {analysis.root_cause}\n"
            f"- 조치: {feedback}\n\n"
            f"[Original Task]\n{original_task}"
        )

    # ── Level 2: 전략 피벗 ──

    def apply_pivot(
        self,
        original_task: str,
        analysis: ISEAnalysis,
        ledger: StrategyLedger,
    ) -> str:
        """LLM을 사용하여 대안 접근법을 생성한다."""
        prompt = f"""당신은 소프트웨어 아키텍트입니다.
에이전트가 태스크를 여러 번 실패했습니다. 완전히 다른 접근법을 제안하세요.

[원래 태스크]
{original_task[:1000]}

[현재 에러]
- 카테고리: {analysis.error_category}
- 근본 원인: {analysis.root_cause}

[이전에 실패한 전략 -- 이 접근법들을 사용하지 마세요]
{ledger.failed_strategies_summary(5)}

위 실패한 전략과는 완전히 다른 새로운 접근법으로 태스크를 재작성하세요.
반드시 구체적인 구현 지시를 포함하세요.

JSON으로 답변:
{{"pivoted_task": "재작성된 태스크 지시 (구체적으로)", "strategy_description": "새 전략 요약 (1줄)"}}"""

        try:
            raw = self.llm.generate(prompt)
            data = self._extract_json(raw)
            if data and "pivoted_task" in data:
                strategy = data.get("strategy_description", "전략 피벗")
                return (
                    f"[ISE STRATEGY PIVOT — {strategy}]\n"
                    f"{data['pivoted_task']}\n\n"
                    f"[Original Task]\n{original_task}"
                )
        except Exception as e:
            print_agent_msg("ISERedesigner", f"피벗 생성 실패: {e}", "⚠️")

        # 폴백: 단순 피드백 (무한 재시도 방지를 위해 실패 마커 포함)
        return (
            f"[ISE PIVOT FAILED — fallback to retry feedback]\n"
            + self.apply_retry_feedback(original_task, analysis)
        )

    # ── Level 3: 설계 재시작 ──

    def redesign_task(
        self,
        original_task: str,
        analysis: ISEAnalysis,
        ledger: StrategyLedger,
    ) -> str:
        """실패 이력을 고려하여 태스크를 완전히 재설계한다."""
        human_hints = ledger.human_hints()
        hints_section = ""
        if human_hints:
            hints_section = f"\n[사용자 힌트]\n" + "\n".join(f"- {h}" for h in human_hints[-3:]) + "\n"

        prompt = f"""당신은 시니어 소프트웨어 아키텍트입니다.
에이전트가 반복적으로 실패하고 있어 접근법 자체를 재설계해야 합니다.

[원래 태스크]
{original_task[:1000]}

[실패 이력 (이 모든 접근법이 실패했음)]
{ledger.failed_strategies_summary(8)}

[최근 에러 분석]
- 카테고리: {analysis.error_category}
- 근본 원인: {analysis.root_cause}
- 근본적 결함 여부: {analysis.is_fundamental}
{hints_section}
위 실패 이력의 모든 접근법과 근본적으로 다른 새로운 설계로 태스크를 재작성하세요.

요구사항:
1. 이전 실패를 반복하지 않는 완전히 새로운 아키텍처/접근법
2. 단계별 구현 지시 포함
3. 예상되는 위험과 대안 포함

JSON으로 답변:
{{
    "redesigned_task": "완전히 재설계된 태스크 지시 (상세하게)",
    "architecture_rationale": "왜 이 설계가 이전과 다른지 (1-2 문장)",
    "strategy_description": "새 전략 요약 (1줄)"
}}"""

        try:
            raw = self.llm.generate(prompt)
            data = self._extract_json(raw)
            if data and "redesigned_task" in data:
                rationale = data.get("architecture_rationale", "")
                strategy = data.get("strategy_description", "설계 재시작")
                return (
                    f"[ISE DESIGN RESTART — {strategy}]\n"
                    f"설계 근거: {rationale}\n\n"
                    f"{data['redesigned_task']}\n\n"
                    f"[Original Task]\n{original_task}"
                )
        except Exception as e:
            print_agent_msg("ISERedesigner", f"재설계 생성 실패: {e}", "⚠️")

        # 폴백: 피벗 (무한 재시도 방지를 위해 실패 마커 포함)
        return (
            f"[ISE REDESIGN FAILED — fallback to pivot]\n"
            + self.apply_pivot(original_task, analysis, ledger)
        )

    # ── Level 5: 태스크 분해 ──

    def decompose_task(
        self,
        original_task: str,
        analysis: ISEAnalysis,
        ledger: StrategyLedger,
    ) -> list[dict]:
        """
        태스크를 서브태스크로 분해한다.

        Returns: [
            {"subtask": str, "role": str, "dependencies": list[int], "priority": int},
            ...
        ]
        """
        prompt = f"""당신은 프로젝트 매니저입니다.
다음 태스크가 너무 복잡하여 반복 실패하고 있습니다. 독립적으로 실행 가능한 서브태스크로 분해하세요.

[원래 태스크]
{original_task[:1500]}

[실패 이력]
{ledger.failed_strategies_summary(5)}

요구사항:
1. 각 서브태스크는 독립적으로 실행 가능해야 합니다
2. 서브태스크 간 의존 관계를 명시하세요
3. 3~7개의 서브태스크로 분해하세요
4. 각 서브태스크에 적합한 에이전트 역할을 지정하세요

JSON 배열로 답변:
[
    {{"subtask": "구체적 지시", "role": "적합한 역할", "dependencies": [], "priority": 1}},
    {{"subtask": "구체적 지시", "role": "적합한 역할", "dependencies": [0], "priority": 2}}
]"""

        try:
            raw = self.llm.generate(prompt)
            # JSON 배열 추출
            match = re.search(r'\[[\s\S]*\]', raw or "")
            if match:
                data = json.loads(match.group())
                if isinstance(data, list) and len(data) >= 2:
                    return [
                        {
                            "subtask": str(item.get("subtask", "")),
                            "role": str(item.get("role", "Developer")),
                            "dependencies": list(item.get("dependencies", [])),
                            "priority": int(item.get("priority", i + 1)),
                        }
                        for i, item in enumerate(data)
                        if item.get("subtask")
                    ]
        except Exception as e:
            print_agent_msg("ISERedesigner", f"태스크 분해 실패: {e}", "⚠️")

        # 폴백: 2분할
        mid = len(original_task) // 2
        return [
            {"subtask": f"1단계: 기반 구조 구현\n{original_task[:mid]}", "role": "Developer", "dependencies": [], "priority": 1},
            {"subtask": f"2단계: 나머지 기능 구현\n{original_task[mid:]}", "role": "Developer", "dependencies": [0], "priority": 2},
        ]

    # ── Creativity Injection ──

    def inject_creativity(self, task: str, ledger: StrategyLedger) -> str:
        """정체 상태에서 랜덤 전략 변이를 주입한다."""
        perturbation = random.choice(_CREATIVITY_PERTURBATIONS)
        print_agent_msg("ISE", f"창의성 주입: {perturbation}", "🎲")
        return (
            f"[ISE CREATIVITY INJECTION]\n"
            f"새로운 제약 조건: {perturbation}\n\n"
            f"[이전 실패 요약]\n{ledger.failed_strategies_summary(3)}\n\n"
            f"[Original Task]\n{task}"
        )

    # ── 유틸 ──

    def _extract_json(self, text: str | None) -> dict | None:
        if not text:
            return None
        data = safe_json_load(text)
        if data:
            return data
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            return safe_json_load(match.group())
        return None
