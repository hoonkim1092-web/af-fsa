"""
core/hooks/skill_self_evolution.py
===================================
스킬 자가 진화 훅.

동작:
  - post_execute() 에서 N회마다 전체 스킬 메타데이터 품질 자가 점검
  - 미비 스킬을 LLM으로 자동 보강 (bulk_enrich_all_skills)
  - on_skill_evolved() 에서 진화 이력을 로그 및 메모리에 기록
  - on_skill_quality_checked() 에서 품질 감사 결과 기록

PRIORITY = 80 (CheckpointHook=90, MemoryConsolidationHook=95 이전 실행)
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class SkillSelfEvolutionHook:
    """
    스킬 자가 진화 훅.

    post_execute 마다 카운터를 증가시키고 check_interval 주기로
    전체 스킬 메타데이터 품질 감사를 비동기 실행합니다.
    """

    PRIORITY = 80

    def __init__(
        self,
        check_interval: int = 10,
        max_enrich_per_cycle: int = 5,
        quality_threshold: float = 0.5,
        coding_engine: Optional[str] = None,
    ) -> None:
        self._check_interval = check_interval
        self._max_enrich = max_enrich_per_cycle
        self._quality_threshold = quality_threshold
        self._coding_engine = coding_engine

        self._execution_count = 0
        self._audit_lock = threading.Lock()
        self._audit_in_progress = False

    # ------------------------------------------------------------------
    # Hook 인터페이스
    # ------------------------------------------------------------------

    def post_execute(self, agent_state: dict, result: dict) -> dict:
        """실행 완료 후 주기적 품질 감사 트리거."""
        self._execution_count += 1

        if self._execution_count % self._check_interval == 0:
            self._trigger_audit_async()

        return result

    def on_skill_evolved(
        self,
        skill_id: str,
        old_version: str,
        new_version: str,
        trigger: str,
    ) -> None:
        """스킬 진화 이벤트 수신 → 로그 + 메모리 기록."""
        logger.info(
            "[SelfEvolution] 스킬 진화 완료: %s (%s → %s) trigger=%s",
            skill_id, old_version or "?", new_version or "?", trigger,
        )
        self._record_evolution_to_memory(skill_id, old_version, new_version, trigger)

    def on_skill_quality_checked(self, skill_id: str, quality_report: dict) -> None:
        """품질 감사 결과 수신 → 로그."""
        score = quality_report.get("score", 0.0)
        enriched = quality_report.get("enriched", False)
        logger.debug(
            "[SelfEvolution] 품질 감사: %s score=%.2f enriched=%s",
            skill_id, score, enriched,
        )

    # ------------------------------------------------------------------
    # 내부 구현
    # ------------------------------------------------------------------

    def _trigger_audit_async(self) -> None:
        """품질 감사를 백그라운드 스레드에서 실행 (메인 파이프라인 차단 없음)."""
        with self._audit_lock:
            if self._audit_in_progress:
                logger.debug("[SelfEvolution] 감사 이미 실행 중, 스킵")
                return
            self._audit_in_progress = True

        thread = threading.Thread(
            target=self._run_quality_audit,
            name="SkillSelfEvolutionAudit",
            daemon=True,
        )
        thread.start()

    def _run_quality_audit(self) -> None:
        """
        전체 스킬 품질 감사 + 자동 보강.

        흐름:
          1. 전체 스킬 품질 점수 계산
          2. 미비 스킬 상위 N개 선정
          3. SkillMetaEnricher로 자동 보강
          4. 보강된 스킬을 SkillEvolutionBus로 캐시 무효화
        """
        try:
            from core.skill_enricher import bulk_enrich_all_skills
            from core.skill_evolution_bus import SkillEvolutionBus

            logger.info(
                "[SelfEvolution] 품질 감사 시작 (threshold=%.2f, max=%d)",
                self._quality_threshold, self._max_enrich,
            )

            enriched_ids = bulk_enrich_all_skills(
                coding_engine=self._coding_engine,
                max_skills=self._max_enrich,
                quality_threshold=self._quality_threshold,
            )

            if enriched_ids:
                evo_bus = SkillEvolutionBus.get_instance()
                evo_bus.on_bulk_enriched(enriched_ids)
                logger.info(
                    "[SelfEvolution] 자동 보강 완료: %d개 → %s",
                    len(enriched_ids), enriched_ids,
                )
            else:
                logger.debug("[SelfEvolution] 보강 대상 없음 (모든 스킬 품질 충분)")

        except Exception as e:
            logger.error("[SelfEvolution] 품질 감사 오류: %s", e)
        finally:
            with self._audit_lock:
                self._audit_in_progress = False

    def _record_evolution_to_memory(
        self,
        skill_id: str,
        old_version: str,
        new_version: str,
        trigger: str,
    ) -> None:
        """진화 이벤트를 UnifiedMemoryFacade에 비동기 기록."""
        try:
            import asyncio
            import json
            from core.memory_system.facade import UnifiedMemoryFacade
            from core.memory_system.models import MemoryType
            from core.utils import now_iso

            facade = UnifiedMemoryFacade.get_instance()
            if not facade._initialised:
                return  # 초기화되지 않은 facade는 skip (no-op)

            content = json.dumps({
                "event_type": "skill_evolution",
                "skill_id": skill_id,
                "old_version": old_version,
                "new_version": new_version,
                "trigger": trigger,
                "timestamp": now_iso(),
            })

            async def _write() -> None:
                await facade.write(
                    content,
                    memory_type=MemoryType.SEMANTIC,
                    metadata={"event_type": "skill_evolution", "skill_id": skill_id},
                    target_backend="trace_log",
                )

            thread = threading.Thread(
                target=lambda: asyncio.run(_write()),
                daemon=True,
            )
            thread.start()
        except Exception as e:
            logger.debug("[SelfEvolution] 메모리 기록 실패 (무시): %s", e)

    # ------------------------------------------------------------------
    # 상태 조회
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            "execution_count": self._execution_count,
            "check_interval": self._check_interval,
            "audit_in_progress": self._audit_in_progress,
            "next_audit_in": self._check_interval - (self._execution_count % self._check_interval),
        }
