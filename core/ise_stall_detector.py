"""
core/ise_stall_detector.py
==========================
ISE 정체 감지기 -- 하드 리밋 대신 지능형 정체 감지를 수행한다.

감지 신호 (4종):
  1. 에러 엔트로피 (weight 0.4): 최근 N 사이클의 에러 다양성
  2. 전략 유사도 (weight 0.3): 새 전략과 과거 전략의 다양성
  3. 에스컬레이션 포화 (weight 0.2): 모든 레벨 순회 여부
  4. 시간 예산 (weight 0.1): 누적 실행 시간

대응:
  "continue"             : 정상 진행
  "creativity_injection" : 랜덤 전략 변이 주입
  "human_escalation"     : 사용자에게 도움 요청
"""
from __future__ import annotations

import math
from collections import Counter

from core.ise_strategy_ledger import StrategyLedger, StrategyEntry


class StallDetector:
    """무한 루프의 지능형 정체 감지기."""

    def __init__(
        self,
        entropy_window: int = 5,
        entropy_threshold: float = 0.3,
        max_same_level_streak: int = 4,
        max_total_time_sec: int = 3600,
        creativity_trigger: int = 3,
        stall_threshold: float = 0.8,
        creativity_threshold: float = 0.5,
    ):
        self.entropy_window = entropy_window
        self.entropy_threshold = entropy_threshold
        self.max_same_level_streak = max_same_level_streak
        self.max_total_time_sec = max_total_time_sec
        self.creativity_trigger = creativity_trigger
        self.stall_threshold = stall_threshold
        self.creativity_threshold = creativity_threshold

    def check(self, ledger: StrategyLedger) -> str:
        """
        정체 상태를 판단한다.

        Returns: "continue" | "creativity_injection" | "human_escalation"
        """
        if len(ledger.entries) < 2:
            return "continue"

        # 사용자 힌트 직후에는 항상 계속
        if ledger._human_hints:
            recent_hint_cycle = ledger._human_hints[-1].get("cycle", 0)
            if len(ledger.entries) - recent_hint_cycle <= 1:
                return "continue"

        score = self._compute_stall_score(ledger)

        if score >= self.stall_threshold:
            return "human_escalation"
        elif score >= self.creativity_threshold:
            return "creativity_injection"
        return "continue"

    def compute_backoff(self, ledger: StrategyLedger, current_level: int) -> float:
        """
        같은 레벨 반복 시 지수 백오프 시간(초) 계산.
        base=1초, factor=2^(같은레벨연속횟수-1), max=30초
        """
        streak = self._same_level_streak(ledger, current_level)
        if streak <= 1:
            return 0.0
        backoff = min(1.0 * (2 ** (streak - 1)), 30.0)
        return backoff

    def _compute_stall_score(self, ledger: StrategyLedger) -> float:
        """
        0.0~1.0 정체 점수 계산.
        가중합: entropy(0.4) + strategy_diversity(0.3) + escalation_saturation(0.2) + time_budget(0.1)
        """
        recent = ledger.recent_entries(self.entropy_window)

        # Signal 1: 에러 엔트로피 (낮으면 같은 에러 반복 → 정체)
        entropy = self._error_entropy(recent)
        max_entropy = math.log2(max(len(recent), 1)) or 1.0
        normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0
        entropy_signal = 1.0 - normalized_entropy  # 엔트로피 낮을수록 정체

        # Signal 2: 전략 다양성 (낮으면 같은 전략 반복 → 정체)
        strategy_diversity = self._strategy_diversity(recent)
        diversity_signal = 1.0 - strategy_diversity

        # Signal 3: 에스컬레이션 포화 (모든 레벨 사용 → 정체)
        saturation_signal = 1.0 if self._escalation_saturation(ledger) else 0.0

        # Signal 4: 시간 예산 소진
        elapsed = ledger.total_elapsed_sec()
        time_signal = min(elapsed / self.max_total_time_sec, 1.0)

        score = (
            0.4 * entropy_signal
            + 0.3 * diversity_signal
            + 0.2 * saturation_signal
            + 0.1 * time_signal
        )
        return min(score, 1.0)

    def _error_entropy(self, entries: list[StrategyEntry]) -> float:
        """Shannon 엔트로피 계산."""
        if not entries:
            return 0.0
        sigs = [e.error_signature for e in entries if e.error_signature]
        if not sigs:
            return 0.0
        counts = Counter(sigs)
        total = len(sigs)
        entropy = 0.0
        for count in counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        return entropy

    def _strategy_diversity(self, entries: list[StrategyEntry]) -> float:
        """전략 해시의 고유 비율 (0.0~1.0)."""
        if not entries:
            return 1.0
        hashes = [e.strategy_hash for e in entries if e.strategy_hash]
        if not hashes:
            return 1.0
        return len(set(hashes)) / len(hashes)

    def _escalation_saturation(self, ledger: StrategyLedger) -> bool:
        """모든 에스컬레이션 레벨(1~5)을 최소 1번 이상 시도했는지."""
        counts = ledger.level_counts()
        return all(counts.get(lvl, 0) > 0 for lvl in range(1, 6))

    def _same_level_streak(self, ledger: StrategyLedger, current_level: int) -> int:
        """현재 레벨과 동일한 연속 시도 횟수."""
        streak = 0
        for e in reversed(ledger.entries):
            if e.escalation_level == current_level:
                streak += 1
            else:
                break
        return streak
