"""
core/run_budget.py
==================
글로벌 토큰 예산 추적 모듈.

4-char ≈ 1-token 휴리스틱으로 소비량을 추정하며,
80% 경고 + 100% 자동 중단을 제공한다.

사용:
    set_run_budget(50000)          # CLI에서 --budget 50000
    get_run_budget().record(text)  # agent_runner 결과마다
    get_run_budget().is_exhausted()  # orchestrator 매 사이클 체크
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class RunBudget:
    max_tokens: int = 0       # 0 = unlimited
    consumed: int = 0
    warned_80: bool = False
    stopped: bool = False

    def record(self, text: str) -> None:
        """4-char ≈ 1-token 휴리스틱으로 소비량을 갱신한다."""
        self.consumed += max(1, len(text) // 4)
        if self.max_tokens <= 0:
            return
        if not self.warned_80 and self.consumed >= self.max_tokens * 0.8:
            self.warned_80 = True
            print(f"[RunBudget] WARNING: 80% budget consumed ({self.consumed}/{self.max_tokens})")
        if not self.stopped and self.consumed >= self.max_tokens:
            self.stopped = True
            print(f"[RunBudget] STOP: budget exhausted ({self.consumed}/{self.max_tokens})")

    def is_exhausted(self) -> bool:
        return self.stopped

    def remaining(self) -> int:
        if self.max_tokens <= 0:
            return 999_999_999
        return max(0, self.max_tokens - self.consumed)


# ── 모듈 싱글턴 ──────────────────────────────────────────────
_budget: RunBudget = RunBudget()


def set_run_budget(max_tokens: int) -> RunBudget:
    global _budget
    _budget = RunBudget(max_tokens=max_tokens)
    return _budget


def get_run_budget() -> RunBudget:
    return _budget
