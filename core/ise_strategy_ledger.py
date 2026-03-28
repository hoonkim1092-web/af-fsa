"""
core/ise_strategy_ledger.py
============================
ISE 전략 원장 -- 시도한 모든 전략과 결과를 추적하여
동일 전략 반복 방지 + 학습 데이터 축적.

영속화: {workspace}/.af/ise_ledger_{run_id}.json
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from core.utils import now_iso


@dataclass
class StrategyEntry:
    """단일 시도 기록."""
    meta_cycle: int
    timestamp: str
    escalation_level: int
    task_input_hash: str
    strategy_description: str
    strategy_hash: str
    error_category: str
    error_signature: str
    result_ok: bool
    result_reason: str
    analysis: dict = field(default_factory=dict)
    evolved_skills: list[str] = field(default_factory=list)
    duration_ms: int = 0


class StrategyLedger:
    """
    전략 원장: 모든 시도를 기록하고 패턴을 분석한다.

    핵심 기능:
    - 시도 기록 및 조회
    - 동일 전략/에러 반복 감지
    - 에스컬레이션 레벨별 카운터
    - 과거 실패 전략 필터링 (LLM 프롬프트 주입용)
    - JSON 영속화
    """

    def __init__(self, run_id: str, original_task: str):
        self.run_id = run_id
        self.original_task = original_task
        self.entries: list[StrategyEntry] = []
        self._level_counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        self._human_hints: list[dict] = []
        self._start_time = time.time()

    # ── 기록 ──

    def record_attempt(
        self,
        meta_cycle: int,
        task_input: str,
        result: dict,
        analysis: dict,
        escalation_level: int = 1,
        strategy_description: str = "",
        evolved_skills: list[str] | None = None,
    ) -> StrategyEntry:
        """시도 결과를 기록한다."""
        error_reason = result.get("reason", "")
        entry = StrategyEntry(
            meta_cycle=meta_cycle,
            timestamp=now_iso(),
            escalation_level=escalation_level,
            task_input_hash=self._hash(task_input),
            strategy_description=strategy_description or analysis.get("suggested_strategy", ""),
            strategy_hash=self._hash(strategy_description or analysis.get("suggested_strategy", "")),
            error_category=analysis.get("error_category", "unknown"),
            error_signature=analysis.get("error_signature", self._normalize_error(error_reason)),
            result_ok=result.get("ok", False),
            result_reason=error_reason[:500],
            analysis=analysis,
            evolved_skills=evolved_skills or [],
            duration_ms=int(result.get("latency_ms", 0) or 0),
        )
        self.entries.append(entry)
        self._level_counts[escalation_level] = self._level_counts.get(escalation_level, 0) + 1
        return entry

    def record_human_hint(self, hint: str) -> None:
        """사용자 힌트를 기록한다."""
        self._human_hints.append({"hint": hint, "timestamp": now_iso(), "cycle": len(self.entries)})

    # ── 조회 ──

    def recent_entries(self, window: int = 5) -> list[StrategyEntry]:
        return self.entries[-window:]

    def consecutive_same_error_count(self) -> int:
        """연속 동일 에러 서명 횟수."""
        if not self.entries:
            return 0
        last_sig = self.entries[-1].error_signature
        count = 0
        for e in reversed(self.entries):
            if e.error_signature == last_sig:
                count += 1
            else:
                break
        return count

    def pivot_count(self) -> int:
        return self._level_counts.get(2, 0)

    def redesign_count(self) -> int:
        return self._level_counts.get(3, 0)

    def decompose_count(self) -> int:
        return self._level_counts.get(5, 0)

    def has_abort_verdict(self) -> bool:
        return any(
            e.analysis.get("action") == "abort" or e.error_category == "abort"
            for e in self.entries
        )

    def has_skill_failure(self) -> bool:
        return any(
            e.error_category == "skill_deficiency" or e.analysis.get("failed_skills")
            for e in self.entries[-3:]  # 최근 3개만 확인
        )

    def total_elapsed_sec(self) -> float:
        return time.time() - self._start_time

    def level_counts(self) -> dict[int, int]:
        return dict(self._level_counts)

    # ── LLM 프롬프트 주입용 ──

    def failed_strategies_summary(self, max_items: int = 10) -> str:
        """실패한 전략을 요약 문자열로 반환한다."""
        failed = [e for e in self.entries if not e.result_ok]
        if not failed:
            return "(없음)"
        lines = []
        for e in failed[-max_items:]:
            lines.append(
                f"  - [Cycle {e.meta_cycle}, Level {e.escalation_level}] "
                f"{e.strategy_description[:120]} → 에러: {e.error_category} ({e.error_signature[:80]})"
            )
        return "\n".join(lines)

    def failed_strategy_descriptions(self, max_items: int = 5) -> list[str]:
        failed = [e for e in self.entries if not e.result_ok]
        return [e.strategy_description[:150] for e in failed[-max_items:]]

    def top_error_signatures(self, top_n: int = 3) -> list[tuple[str, int]]:
        """가장 빈번한 에러 서명 상위 N개."""
        sigs = [e.error_signature for e in self.entries if not e.result_ok and e.error_signature]
        return Counter(sigs).most_common(top_n)

    def tried_strategy_hashes(self) -> set[str]:
        return {e.strategy_hash for e in self.entries}

    def human_hints(self) -> list[str]:
        return [h["hint"] for h in self._human_hints]

    # ── 에스컬레이션 리셋 ──

    def reset_escalation_counters(self) -> None:
        """Level 5 분해 실패 후 카운터를 리셋하여 재시도 가능하게 한다."""
        self._level_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    # ── 영속화 ──

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "original_task": self.original_task[:500],
            "total_attempts": len(self.entries),
            "level_counts": self._level_counts,
            "human_hints": self._human_hints,
            "elapsed_sec": round(self.total_elapsed_sec(), 1),
            "entries": [asdict(e) for e in self.entries],
        }

    def save(self, workspace: str) -> str:
        """워크스페이스에 원장을 저장한다."""
        af_dir = os.path.join(workspace, ".af")
        os.makedirs(af_dir, exist_ok=True)
        path = os.path.join(af_dir, f"ise_ledger_{self.run_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, workspace: str, run_id: str) -> StrategyLedger | None:
        """저장된 원장을 로딩한다."""
        path = os.path.join(workspace, ".af", f"ise_ledger_{run_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ledger = cls(run_id=data["run_id"], original_task=data.get("original_task", ""))
            ledger._level_counts = {int(k): v for k, v in data.get("level_counts", {}).items()}
            ledger._human_hints = data.get("human_hints", [])
            for ed in data.get("entries", []):
                ledger.entries.append(StrategyEntry(**{
                    k: v for k, v in ed.items()
                    if k in StrategyEntry.__dataclass_fields__
                }))
            return ledger
        except Exception:
            return None

    # ── 내부 유틸 ──

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _normalize_error(error_log: str) -> str:
        """에러 로그에서 핵심 패턴을 추출하여 정규화한다."""
        if not error_log:
            return ""
        import re
        # 파일 경로의 동적 부분 제거
        normalized = re.sub(r'[A-Za-z]:\\[^\s"\']+', '<PATH>', error_log)
        normalized = re.sub(r'/[^\s"\']+', '<PATH>', normalized)
        # 숫자 일반화 (줄 번호 등)
        normalized = re.sub(r'\b\d{2,}\b', '<N>', normalized)
        # 타임스탬프 제거
        normalized = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*', '<TS>', normalized)
        # 핵심만 추출 (첫 200자)
        return normalized.strip()[:200]
