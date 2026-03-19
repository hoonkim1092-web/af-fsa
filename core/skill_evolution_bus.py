"""
core/skill_evolution_bus.py
===========================
스킬 변경 시 모든 캐시/그래프를 연쇄 무효화하는 중앙 허브.

스킬이 진화(evolve)되거나 메타데이터가 보강(enrich)되면
반드시 이 버스를 통해 전체 캐시 체인을 무효화해야 합니다.

무효화 순서:
  1. SkillRegistry 강제 갱신
  2. DependencyGraph 무효화
  3. SemanticEmbedder 재계산 (변경된 스킬만)
  4. OptimizedSkillRelevance 3종 캐시 클리어
  5. AgentRunner 모듈 캐시 제거
  6. AdaptiveSkillLoader 인스턴스 캐시 제거
  7. HookEventBus 이벤트 브로드캐스트
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SkillEvolutionBus:
    """
    스킬 진화/보강 후 전체 캐시 연쇄 무효화 및 이벤트 브로드캐스트.

    사용 예:
        bus = SkillEvolutionBus.get_instance()
        bus.on_skill_evolved(skill_id="git-master", skill_dir="/path/to/git_master")
    """

    _instance: "SkillEvolutionBus | None" = None

    def __init__(self) -> None:
        # 약한 참조로 외부 컴포넌트를 보관 (순환 import 방지)
        self._runner_ref = None          # AgentRunner 인스턴스 (선택)
        self._event_bus_ref = None       # HookEventBus 인스턴스 (선택)
        self._skill_loader_ref = None    # DynamicSkillLoader 인스턴스 (선택)

    @classmethod
    def get_instance(cls) -> "SkillEvolutionBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def bind_runner(self, runner) -> None:
        """AgentRunner 인스턴스 바인딩 (모듈 캐시 무효화용)."""
        self._runner_ref = runner

    def bind_event_bus(self, event_bus) -> None:
        """HookEventBus 바인딩 (이벤트 브로드캐스트용)."""
        self._event_bus_ref = event_bus

    def bind_skill_loader(self, loader) -> None:
        """DynamicSkillLoader 바인딩 (dep graph 무효화용)."""
        self._skill_loader_ref = loader

    # ------------------------------------------------------------------
    # 메인 진입점
    # ------------------------------------------------------------------

    def on_skill_evolved(
        self,
        skill_id: str,
        skill_dir: str = "",  # noqa: ARG002 (reserved for future per-skill reload)
        old_version: str = "",
        new_version: str = "",
        trigger: str = "manual",
    ) -> None:
        """
        스킬 코드 또는 메타데이터 변경 후 호출.

        Args:
            skill_id:    변경된 스킬 ID
            skill_dir:   스킬 디렉토리 경로 (없으면 레지스트리에서 조회)
            old_version: 이전 버전 (로그용)
            new_version: 새 버전 (로그용)
            trigger:     트리거 출처 ("fsa_failure"|"manual"|"schedule"|"quality_check")
        """
        logger.info(
            "[EvolutionBus] on_skill_evolved: %s (%s → %s) trigger=%s",
            skill_id, old_version or "?", new_version or "?", trigger,
        )

        # 1. SkillRegistry 강제 갱신
        self._step1_reload_registry()

        # 2. DependencyGraph 무효화
        self._step2_invalidate_dep_graph()

        # 3. SemanticEmbedder 재계산
        self._step3_recompute_embeddings()

        # 4. 관련성 점수 캐시 클리어
        self._step4_clear_relevance_caches()

        # 5. AgentRunner 모듈 캐시 제거
        self._step5_evict_module_cache(skill_id)

        # 6. AdaptiveSkillLoader 인스턴스 캐시 제거
        self._step6_evict_loader_cache()

        # 7. 이벤트 브로드캐스트
        self._step7_broadcast(skill_id, old_version, new_version, trigger)

        logger.info("[EvolutionBus] 캐시 체인 무효화 완료: %s", skill_id)

    def on_bulk_enriched(self, skill_ids: list[str]) -> None:
        """여러 스킬이 한꺼번에 보강되었을 때 호출 (배치 최적화)."""
        if not skill_ids:
            return

        logger.info("[EvolutionBus] bulk enrich: %d개 스킬", len(skill_ids))

        self._step1_reload_registry()
        self._step2_invalidate_dep_graph()
        self._step3_recompute_embeddings()
        self._step4_clear_relevance_caches()

        for skill_id in skill_ids:
            self._step5_evict_module_cache(skill_id)

        self._step6_evict_loader_cache()
        logger.info("[EvolutionBus] bulk 캐시 체인 무효화 완료")

    # ------------------------------------------------------------------
    # 단계별 구현
    # ------------------------------------------------------------------

    def _step1_reload_registry(self) -> None:
        try:
            from core.skill_registry import get_global_registry
            registry = get_global_registry()
            count = registry.auto_load_from_directories(force=True)
            logger.debug("[EvolutionBus] step1 registry reload: %d 스킬", count)
        except Exception as e:
            logger.error("[EvolutionBus] step1 실패: %s", e)

    def _step2_invalidate_dep_graph(self) -> None:
        try:
            if self._skill_loader_ref is not None:
                self._skill_loader_ref.invalidate_dep_graph()
                logger.debug("[EvolutionBus] step2 dep_graph invalidated (bound loader)")
                return

            # 바인딩 없으면 AgentRunner의 loader 캐시를 통해 접근
            if self._runner_ref is not None:
                for loader in getattr(self._runner_ref, "_skill_loader_cache", {}).values():
                    loader.invalidate_dep_graph()
                logger.debug("[EvolutionBus] step2 dep_graph invalidated (runner loaders)")
        except Exception as e:
            logger.error("[EvolutionBus] step2 실패: %s", e)

    def _step3_recompute_embeddings(self) -> None:
        try:
            from core.skill_registry import get_global_registry

            # SemanticEmbedder 싱글톤은 없으므로 OptimizedSkillRelevance 통해 접근
            embedder = self._get_embedder()
            if embedder and embedder.is_available:
                all_skills = get_global_registry().get_all()
                embedder.precompute_skill_embeddings(all_skills)
                logger.debug("[EvolutionBus] step3 embeddings recomputed")
        except Exception as e:
            logger.error("[EvolutionBus] step3 실패: %s", e)

    def _step4_clear_relevance_caches(self) -> None:
        try:
            relevance = self._get_relevance()
            if relevance is not None:
                relevance.clear_all_caches()
                logger.debug("[EvolutionBus] step4 relevance caches cleared")
        except Exception as e:
            logger.error("[EvolutionBus] step4 실패: %s", e)

    def _step5_evict_module_cache(self, skill_id: str) -> None:
        try:
            if self._runner_ref is None:
                return
            module_cache = getattr(self._runner_ref, "_skill_module_cache", {})
            if skill_id in module_cache:
                del module_cache[skill_id]
                logger.debug("[EvolutionBus] step5 module cache evicted: %s", skill_id)
        except Exception as e:
            logger.error("[EvolutionBus] step5 실패: %s", e)

    def _step6_evict_loader_cache(self) -> None:
        try:
            if self._runner_ref is None:
                return
            loader_cache = getattr(self._runner_ref, "_skill_loader_cache", {})
            loader_cache.clear()
            logger.debug("[EvolutionBus] step6 loader cache cleared")
        except Exception as e:
            logger.error("[EvolutionBus] step6 실패: %s", e)

    def _step7_broadcast(
        self,
        skill_id: str,
        old_version: str,
        new_version: str,
        trigger: str,
    ) -> None:
        try:
            if self._event_bus_ref is None:
                return
            self._event_bus_ref.run_skill_evolved(
                skill_id=skill_id,
                old_version=old_version,
                new_version=new_version,
                trigger=trigger,
            )
            logger.debug("[EvolutionBus] step7 event broadcast: %s", skill_id)
        except Exception as e:
            logger.error("[EvolutionBus] step7 실패: %s", e)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _get_relevance(self):
        """AgentRunner의 DynamicSkillLoader에서 relevance 객체 추출."""
        if self._runner_ref is None:
            return None
        for loader in getattr(self._runner_ref, "_skill_loader_cache", {}).values():
            return getattr(loader, "relevance", None)
        return None

    def _get_embedder(self):
        """relevance 객체에서 embedder 추출."""
        relevance = self._get_relevance()
        if relevance is not None:
            return getattr(relevance, "_embedder", None) or getattr(relevance, "embedder", None)
        return None
