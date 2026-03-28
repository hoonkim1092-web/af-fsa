"""
core/ise_analyzer.py
====================
ISE 실패 분석 엔진 -- 기존 StrategyEvaluator를 확장하여
에러 분류 + 근본 원인 추론 + 전략 추천을 수행한다.

에러 카테고리 (6종):
  transient       : 일시적 오류 (타임아웃, rate limit, 네트워크)
  syntax          : 구문/타입 오류 (파싱, import, typo)
  logic           : 로직 오류 (잘못된 결과, assertion 실패)
  architecture    : 구조적 결함 (접근법 자체가 잘못됨)
  skill_deficiency: 스킬 결함 (스킬 코드 버그, 누락된 스킬)
  resource        : 리소스 부족 (메모리, 디스크, 권한)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.llm_engine import LLMEngine
from core.utils import safe_json_load, print_agent_msg


ERROR_CATEGORIES = ("transient", "syntax", "logic", "architecture", "skill_deficiency", "resource")


@dataclass
class ISEAnalysis:
    """분석 결과."""
    error_category: str = "unknown"
    error_signature: str = ""
    root_cause: str = ""
    suggested_strategy: str = ""
    failed_skills: list[str] = field(default_factory=list)
    confidence: float = 0.5
    is_fundamental: bool = False
    evaluator_action: str = "retry"
    evaluator_reasoning: str = ""
    new_instruction: str = ""

    def to_dict(self) -> dict:
        return {
            "error_category": self.error_category,
            "error_signature": self.error_signature,
            "root_cause": self.root_cause,
            "suggested_strategy": self.suggested_strategy,
            "failed_skills": self.failed_skills,
            "confidence": self.confidence,
            "is_fundamental": self.is_fundamental,
            "evaluator_action": self.evaluator_action,
            "evaluator_reasoning": self.evaluator_reasoning,
            "new_instruction": self.new_instruction,
        }


class ISEAnalyzer:
    """
    실패를 분석하여 에스컬레이션 결정에 필요한 구조화된 정보를 생성한다.

    기존 StrategyEvaluator의 retry/pivot/abort 결정을 확장하여:
    - 에러 분류 체계 (6개 카테고리)
    - 근본 원인 추론 (LLM 기반)
    - 과거 시도와의 유사도 분석 (StrategyLedger 참조)
    - 스킬 결함 식별
    """

    def __init__(self, model_name: str = "gemini-1.5-pro-latest"):
        self.llm = LLMEngine(model_name=model_name)

    def analyze_failure(
        self,
        task: str,
        result: dict,
        ledger=None,
    ) -> ISEAnalysis:
        """실패를 분석하여 ISEAnalysis를 반환한다."""
        error_log = result.get("reason", "") or ""
        analysis = ISEAnalysis()

        # 1단계: 규칙 기반 에러 분류
        analysis.error_category = self._classify_error(error_log)
        analysis.error_signature = self._compute_error_signature(error_log)
        analysis.failed_skills = self._identify_failed_skills(error_log)
        analysis.is_fundamental = analysis.error_category in ("architecture", "resource")

        if analysis.failed_skills:
            analysis.error_category = "skill_deficiency"

        # 2단계: LLM 기반 근본 원인 + 전략 추천
        try:
            llm_analysis = self._llm_analyze(task, error_log, ledger)
            if llm_analysis:
                analysis.root_cause = llm_analysis.get("root_cause", "")
                analysis.suggested_strategy = llm_analysis.get("suggested_strategy", "")
                analysis.confidence = float(llm_analysis.get("confidence", 0.5))
                analysis.evaluator_action = llm_analysis.get("action", "retry")
                analysis.evaluator_reasoning = llm_analysis.get("reasoning", "")
                analysis.new_instruction = llm_analysis.get("new_instruction", "")

                # LLM이 architecture로 판단하면 is_fundamental 오버라이드
                llm_category = llm_analysis.get("error_category", "")
                if llm_category in ERROR_CATEGORIES:
                    analysis.error_category = llm_category
                if llm_category == "architecture":
                    analysis.is_fundamental = True
        except Exception as e:
            print_agent_msg("ISEAnalyzer", f"LLM 분석 실패, 규칙 기반 결과 사용: {e}", "⚠️")

        return analysis

    def _classify_error(self, error_log: str) -> str:
        """규칙 기반 에러 분류."""
        lower = error_log.lower()

        # transient
        transient_patterns = [
            "timeout", "timed out", "rate limit", "429", "503", "502",
            "connection refused", "network", "econnreset", "enotfound",
        ]
        if any(p in lower for p in transient_patterns):
            return "transient"

        # syntax
        syntax_patterns = [
            "syntaxerror", "indentationerror", "importerror", "modulenotfounderror",
            "nameerror", "typeerror: ", "unexpected token", "parsing error",
        ]
        if any(p in lower for p in syntax_patterns):
            return "syntax"

        # resource
        resource_patterns = [
            "out of memory", "oom", "disk full", "no space left",
            "permission denied", "eacces", "eperm",
        ]
        if any(p in lower for p in resource_patterns):
            return "resource"

        # logic (assertion, test failure)
        logic_patterns = [
            "assertionerror", "assert ", "test failed", "expected ",
            "valueerror", "keyerror", "indexerror",
        ]
        if any(p in lower for p in logic_patterns):
            return "logic"

        # architecture
        arch_patterns = [
            "not supported", "deprecated", "incompatible", "version conflict",
            "no such", "does not exist", "cannot find module",
        ]
        if any(p in lower for p in arch_patterns):
            return "architecture"

        return "unknown"

    def _compute_error_signature(self, error_log: str) -> str:
        """에러 로그에서 핵심 패턴을 추출하여 정규화한 서명."""
        if not error_log:
            return ""
        # 파일 경로 제거
        normalized = re.sub(r'[A-Za-z]:\\[^\s"\']+', '<PATH>', error_log)
        normalized = re.sub(r'/[^\s"\']+', '<PATH>', normalized)
        # 숫자 일반화
        normalized = re.sub(r'\b\d{2,}\b', '<N>', normalized)
        # 타임스탬프 제거
        normalized = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*', '<TS>', normalized)
        return normalized.strip()[:200]

    def _identify_failed_skills(self, error_log: str) -> list[str]:
        """에러 로그에서 실패한 스킬 이름을 추출한다."""
        skills = []
        # skill_*.py 또는 skills/skill_name 패턴 매칭
        matches = re.findall(r'skills[/\\]([a-zA-Z_][a-zA-Z0-9_-]*)', error_log)
        skills.extend(matches)
        # "skill '...' failed" 패턴
        matches2 = re.findall(r"skill\s+['\"]([^'\"]+)['\"]", error_log, re.IGNORECASE)
        skills.extend(matches2)
        return list(set(skills))

    def _llm_analyze(self, task: str, error_log: str, ledger=None) -> dict | None:
        """LLM을 사용하여 근본 원인과 전략을 분석한다."""
        failed_history = ""
        if ledger is not None:
            failed_history = f"\n[이전 실패 전략 이력]\n{ledger.failed_strategies_summary(5)}\n"

        prompt = f"""당신은 소프트웨어 디버깅 전문가입니다.
에이전트가 태스크 실행 중 실패했습니다. 실패를 분석하고 다음 전략을 제안하세요.

[태스크]
{task[:1000]}

[에러 로그]
{error_log[:2000]}
{failed_history}
반드시 JSON으로만 답변하세요:
{{
    "error_category": "transient|syntax|logic|architecture|skill_deficiency|resource",
    "root_cause": "근본 원인 (1-2 문장)",
    "suggested_strategy": "다음에 시도할 구체적 전략 (1-3 문장)",
    "confidence": 0.0~1.0,
    "action": "retry|pivot|abort",
    "reasoning": "판단 근거",
    "new_instruction": "수정된 태스크 지시 (retry/pivot 시)"
}}"""

        try:
            raw = self.llm.generate(prompt)
            if not raw:
                return None
            data = safe_json_load(raw)
            if data and "action" in data:
                return data
            # JSON 추출 재시도
            match = re.search(r'\{[\s\S]*"action"[\s\S]*\}', raw)
            if match:
                return safe_json_load(match.group())
        except Exception:
            pass
        return None
