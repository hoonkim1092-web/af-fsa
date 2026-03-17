import logging
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict, deque
from core.skill_metadata import SkillMetadata, SkillCategory
from core.skill_registry import get_global_registry, ensure_skills_loaded

logger = logging.getLogger(__name__)

MAX_SKILLS_IN_CONTEXT = 12


# =============================================================================
# 의존성 그래프 위상 정렬
# =============================================================================
class SkillDependencyGraph:
    """
    스킬 간 dependencies 필드를 기반으로 DAG를 구축하고
    위상 정렬(Topological Sort)로 실행 순서를 결정합니다.

    순환 의존성 감지 시 해당 스킬을 제외하고 경고합니다.
    """

    def __init__(self, all_skills: Dict[str, SkillMetadata]):
        self._all_skills = all_skills
        self._adj: Dict[str, Set[str]] = defaultdict(set)      # skill → 의존 대상
        self._reverse: Dict[str, Set[str]] = defaultdict(set)   # 의존 대상 → 이를 필요로 하는 스킬
        self._build_graph()

    def _build_graph(self):
        """dependencies 필드로 인접 리스트 구축."""
        for skill_id, meta in self._all_skills.items():
            for dep in meta.dependencies:
                if dep in self._all_skills:
                    self._adj[skill_id].add(dep)
                    self._reverse[dep].add(skill_id)

    def detect_cycles(self) -> List[List[str]]:
        """순환 의존성을 감지하여 사이클 목록 반환. DFS 기반."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {sid: WHITE for sid in self._all_skills}
        cycles: List[List[str]] = []
        path: List[str] = []

        def dfs(node: str):
            color[node] = GRAY
            path.append(node)
            for dep in self._adj.get(node, set()):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    # 사이클 발견: path에서 dep 위치부터 현재까지
                    cycle_start = path.index(dep)
                    cycles.append(path[cycle_start:] + [dep])
                elif color[dep] == WHITE:
                    dfs(dep)
            path.pop()
            color[node] = BLACK

        for sid in self._all_skills:
            if color[sid] == WHITE:
                dfs(sid)

        return cycles

    def topological_sort(self, skill_ids: List[str]) -> Tuple[List[str], List[str]]:
        """
        주어진 스킬 ID 목록을 의존성 순서로 정렬합니다 (Kahn's algorithm).

        Returns:
            (sorted_ids, excluded_cycle_ids)
            - sorted_ids: 의존성 순서로 정렬된 스킬 ID
            - excluded_cycle_ids: 순환 의존성으로 제외된 스킬 ID
        """
        # 부분 그래프 구축 (선택된 스킬만)
        selected = set(skill_ids)
        in_degree: Dict[str, int] = {sid: 0 for sid in selected}

        for sid in selected:
            for dep in self._adj.get(sid, set()):
                if dep in selected:
                    in_degree[sid] += 1

        # Kahn's algorithm
        queue = deque([sid for sid, deg in in_degree.items() if deg == 0])
        sorted_ids: List[str] = []

        while queue:
            node = queue.popleft()
            sorted_ids.append(node)
            for dependent in self._reverse.get(node, set()):
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        # 정렬되지 않은 스킬 = 순환 의존성에 포함
        excluded = [sid for sid in skill_ids if sid not in sorted_ids]
        if excluded:
            logger.warning("순환 의존성 감지, 제외됨: %s", excluded)

        return sorted_ids, excluded

    def get_required_deps(self, skill_ids: List[str]) -> List[str]:
        """선택된 스킬들이 필요로 하는 미포함 의존 스킬을 반환."""
        selected = set(skill_ids)
        missing: List[str] = []

        for sid in skill_ids:
            for dep in self._adj.get(sid, set()):
                if dep not in selected and dep not in missing:
                    missing.append(dep)

        return missing

class SkillRelevance:
    def __init__(self):
        self.encoder = None
        self._embedder = None
        self._embedder_initialized = False

    @property
    def embedder(self):
        """SemanticEmbedder 지연 초기화."""
        if not self._embedder_initialized:
            self._embedder_initialized = True
            try:
                from core.semantic_embedder import SemanticEmbedder
                self._embedder = SemanticEmbedder()
            except Exception:
                self._embedder = None
        return self._embedder

    def compute_score(self, task_input: str, skill: SkillMetadata) -> float:
        keyword_score = self._keyword_match(task_input, skill)
        category_score = self._category_match(task_input, skill)

        if self.embedder and self.embedder.is_available:
            semantic_score = self.embedder.compute_similarity(task_input, skill)
            return keyword_score * 0.35 + semantic_score * 0.40 + category_score * 0.25

        # 시맨틱 불가 → 기존 키워드(60%) + 카테고리(40%) 폴백
        return keyword_score * 0.60 + category_score * 0.40

    def _keyword_match(self, task_input: str, skill: SkillMetadata) -> float:
        if not skill.when_to_use_keywords:
            return 0.0
        task_lower = task_input.lower()
        matches = sum(1 for kw in skill.when_to_use_keywords if kw.lower() in task_lower)
        return min(matches / len(skill.when_to_use_keywords), 1.0)

    # 카테고리별 매칭 키워드
    _CATEGORY_KEYWORDS = {
        SkillCategory.CODING: ["코드", "코딩", "구현", "작성", "리팩토링", "code", "implement"],
        SkillCategory.RESEARCH: ["검색", "조사", "분석", "찾아", "search", "research"],
        SkillCategory.IO: ["파일", "저장", "읽기", "쓰기", "메모리", "file", "io"],
        SkillCategory.TESTING: ["테스트", "검증", "확인", "test", "verify"],
        SkillCategory.EVAL: ["평가", "에러", "피드백", "eval", "error"],
        SkillCategory.PLAN: ["설계", "계획", "아키텍처", "plan", "design"],
        SkillCategory.REVIEW: ["리뷰", "검토", "품질", "review"],
        SkillCategory.DEBUG: ["디버그", "디버깅", "버그", "debug", "bug"],
    }

    def _category_match(self, task_input: str, skill: SkillMetadata) -> float:
        keywords = self._CATEGORY_KEYWORDS.get(skill.category, [])
        if not keywords:
            return 0.0
        task_lower = task_input.lower()
        matches = sum(1 for kw in keywords if kw in task_lower)
        return min(matches / max(len(keywords), 1), 1.0)

class DynamicSkillLoader:
    def __init__(self):
        try:
            from core.skill_cache import OptimizedSkillRelevance
            self.relevance = OptimizedSkillRelevance()
        except Exception:
            self.relevance = SkillRelevance()
        ensure_skills_loaded()
        self._precompute_embeddings()
        self._dep_graph: SkillDependencyGraph | None = None

    def _precompute_embeddings(self):
        """로드된 스킬들의 임베딩을 사전 계산."""
        if self.relevance.embedder and self.relevance.embedder.is_available:
            try:
                registry = get_global_registry()
                all_skills = registry.get_all()
                if all_skills:
                    self.relevance.embedder.precompute_skill_embeddings(all_skills)
            except Exception:
                pass

    def _get_dep_graph(self, all_skills: Dict[str, SkillMetadata]) -> SkillDependencyGraph:
        """의존성 그래프 지연 생성 (레지스트리 변경 시 재생성)."""
        if self._dep_graph is None:
            self._dep_graph = SkillDependencyGraph(all_skills)
        return self._dep_graph

    def load_skills_for_task(
        self,
        task_input: str,
        exclude_skills=None,
        min_score=0.0,
        verbose=False,
        max_skills: int | None = None,  # ✅ MAJ-2 수정: 명확한 타입
    ):
        """
        작업 입력에 맞춰 스킬을 자동 선택.

        Args:
            task_input: 작업 설명
            exclude_skills: 제외할 스킬 ID 목록
            min_score: 최소 점수 필터
            verbose: 상세 로그 출력 여부
            max_skills: 최대 스킬 개수
                - None (기본값): MAX_SKILLS_IN_CONTEXT 사용 (레거시 호환)
                - 양수: 지정된 개수만 선택

        Returns:
            (선택된 SkillMetadata 목록, 스킬별 점수 딕셔너리)
        """
        effective_max = max_skills if max_skills is not None else MAX_SKILLS_IN_CONTEXT

        registry = get_global_registry()
        all_skills = registry.get_all()

        if not all_skills:
            return [], {}

        scores = {}
        for skill_id, metadata in all_skills.items():
            if exclude_skills and skill_id in exclude_skills:
                continue
            score = self.relevance.compute_score(task_input, metadata)
            if score >= min_score:
                scores[skill_id] = score

        sorted_skills = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:effective_max]
        selected_ids = [sid for sid, _ in sorted_skills]

        # 충돌 해결
        resolved_ids = self._resolve_conflicts(selected_ids, all_skills)

        # 의존성 자동 포함 + 위상 정렬
        dep_graph = self._get_dep_graph(all_skills)
        resolved_ids = self._inject_missing_deps(
            resolved_ids, dep_graph, all_skills, scores, max_skills=effective_max
        )
        resolved_ids, excluded = dep_graph.topological_sort(resolved_ids)

        selected_skills = [all_skills[sid] for sid in resolved_ids if sid in all_skills]
        selected_scores = {sid: scores.get(sid, 0.0) for sid in resolved_ids}

        if verbose:
            print(f"[OK] {len(selected_skills)}개 스킬 선택 완료")
            if excluded:
                print(f"[WARN] 순환 의존성으로 {len(excluded)}개 제외: {excluded}")

        return selected_skills, selected_scores

    def _get_all_required_deps_recursive(
        self,
        skill_ids: List[str],
        dep_graph: SkillDependencyGraph,
    ) -> set:
        """
        선택된 스킬들의 모든 의존성을 재귀적으로 수집.

        주어진 스킬 ID들에 대해 직접 의존성뿐 아니라,
        그 의존성이 필요로 하는 의존성까지 모두 포함하여 반환.

        Args:
            skill_ids: 초기 스킬 ID 목록
            dep_graph: 의존성 그래프

        Returns:
            모든 의존성 스킬 ID의 집합
        """
        visited = set()

        def collect_recursive(sids: List[str]):
            """재귀적으로 의존성 수집."""
            for sid in sids:
                if sid not in visited:
                    visited.add(sid)
                    missing = dep_graph.get_required_deps([sid])
                    if missing:
                        collect_recursive(missing)  # ← 재귀!

        collect_recursive(skill_ids)
        return visited

    def _inject_missing_deps(
        self,
        skill_ids: List[str],
        dep_graph: SkillDependencyGraph,
        all_skills: Dict[str, SkillMetadata],
        scores: Dict[str, float],
        max_skills: int = MAX_SKILLS_IN_CONTEXT,
    ) -> List[str]:
        """
        선택된 스킬의 미포함 의존 스킬을 재귀적으로 주입 (최대 5단계).

        의존성 추가로 max_skills 초과 시:
        - 모든 선택된 스킬의 필수 의존성은 보존 (재귀적 포함)
        - 필수 의존성만으로도 max_skills 이상이면 경고 후 의존성만 반환
        - 그 외 경우, 의존성 + 비의존성(점수 순)으로 max_skills 채움

        Args:
            skill_ids: 선택된 스킬 ID 목록
            dep_graph: 의존성 그래프
            all_skills: 전체 스킬 메타데이터
            scores: 스킬별 점수
            max_skills: 최대 스킬 개수

        Returns:
            최종 스킬 ID 목록 (의존성 일관성 유지)
        """
        result = list(skill_ids)
        logger.debug("[의존성 주입 시작] 초기: %d개", len(result))

        # 재귀적 의존성 주입 (최대 5단계)
        for iteration in range(5):
            missing = dep_graph.get_required_deps(result)
            if not missing:
                logger.debug("✓ 필수 의존성 모두 충족")
                break

            added = False
            for dep_id in missing:
                if dep_id in all_skills and dep_id not in result:
                    result.append(dep_id)
                    logger.debug("  [+] 의존성 추가: %s", dep_id)
                    if dep_id not in scores:
                        scores[dep_id] = 0.01
                    added = True

            if added:
                logger.debug("  반복 %d: %d개", iteration + 1, len(result))
            else:
                unregistered = [d for d in missing if d not in all_skills]
                if unregistered:
                    logger.warning(
                        "미등록 의존성 스킬 발견 (레지스트리에 없음): %s",
                        unregistered,
                    )
                else:
                    logger.warning("의존성이 이미 포함되어 추가 불가")
                break

        logger.debug("[의존성 주입 완료] 최종: %d개", len(result))

        # max_skills 초과 처리 (의존성 우선 보존)
        if len(result) > max_skills:
            # ✅ CR-2 수정: 재귀적으로 모든 의존성 수집
            # 원본 선택 스킬의 모든 (직접 + 간접) 의존성을 포함
            dep_set = self._get_all_required_deps_recursive(skill_ids, dep_graph)

            # 의존성인 스킬 vs 일반 스킬 분류
            core_deps = [s for s in result if s in dep_set]
            non_deps = [s for s in result if s not in dep_set]

            if len(core_deps) >= max_skills:
                logger.warning(
                    "의존성(%d개)만으로 max_skills(%d) 초과. "
                    "일부 의존성이 누락될 수 있습니다.",
                    len(core_deps),
                    max_skills,
                )
                result = core_deps[:max_skills]
            else:
                # ✅ MAJ-1 수정: non_deps를 점수 순으로 정렬
                # 의존성을 우선 유지하고, 점수 높은 스킬부터 추가
                non_deps_sorted = sorted(
                    non_deps,
                    key=lambda s: scores.get(s, 0),
                    reverse=True  # 높은 점수부터
                )
                result = core_deps + non_deps_sorted[: max_skills - len(core_deps)]

        return result

    def _resolve_conflicts(self, skill_ids, all_skills):
        resolved = []
        for skill_id in skill_ids:
            skill = all_skills.get(skill_id)
            if not skill:
                continue
            conflicts = False
            for existing_id in resolved:
                existing_skill = all_skills.get(existing_id)
                if existing_skill and (skill_id in existing_skill.incompatible_with or existing_id in skill.incompatible_with):
                    conflicts = True
                    break
            if not conflicts:
                resolved.append(skill_id)
        return resolved

    def invalidate_dep_graph(self):
        """레지스트리 변경 시 의존성 그래프 캐시 무효화."""
        self._dep_graph = None

    def build_routing_prompt(self, selected_skills, scores: Optional[Dict[str, float]] = None):
        """
        라우터용 스킬 프롬프트 생성 (Decision Tree 포함).

        Args:
            selected_skills: 선택된 SkillMetadata 목록
            scores: 각 스킬의 관련성 점수

        Returns:
            LLM용 포맷팅된 스킬 설명
        """
        if not selected_skills:
            return "사용 가능한 스킬이 없습니다."

        # 카테고리별로 그룹화
        by_category = {}
        for skill in selected_skills:
            cat = skill.category.value
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(skill)

        lines = []
        lines.append(f"[이용 가능한 스킬] 총 {len(selected_skills)}개\n")

        for category, skills in sorted(by_category.items()):
            lines.append(f"### {category.upper()} ({len(skills)}개)")

            for i, skill in enumerate(skills, 1):
                score = scores.get(skill.skill_id, 0.0) if scores else 0.0
                lines.append(f"{i}. **{skill.name}** ({skill.skill_id})")

                if score > 0:
                    lines.append(f"   관련성: {score:.2f}/1.00")

                if skill.description:
                    lines.append(f"   설명: {skill.description}")

                if skill.when_to_use:
                    lines.append(f"   사용 시기: {skill.when_to_use}")

                if skill.when_NOT_to_use:
                    lines.append(f"   사용 금지: {skill.when_NOT_to_use}")

                if skill.use_case_examples:
                    lines.append(f"   예시: {', '.join(skill.use_case_examples[:2])}")

                lines.append("")

        lines.append("[Decision Tree]")
        lines.append("- 실시간 정보 필요? → 검색 스킬 사용")
        lines.append("- 코드 작성 필요? → 코딩 스킬 사용")
        lines.append("- 검증 필요? → 테스트 스킬 사용")

        return "\n".join(lines)


# =============================================================================
# 적응형 스킬 로더 (모델별 컨텍스트 자동 계산)
# =============================================================================
class AdaptiveSkillLoader(DynamicSkillLoader):
    """
    모델 이름에 따라 컨텍스트 크기에 적합한 스킬 개수를 자동 계산하는 로더.

    Example:
        >>> loader = AdaptiveSkillLoader.for_model("claude-sonnet-4-6")
        >>> selected, scores = loader.load_skills_for_task("코드 작성")
        >>> # max_skills가 자동으로 228개로 설정됨
    """

    def __init__(self, config=None):
        """
        Args:
            config: SkillLoaderConfig 인스턴스 (None이면 기본값 사용)
        """
        super().__init__()
        if config is None:
            from core.skill_context_config import SkillLoaderConfig
            config = SkillLoaderConfig()
        self.config = config

    def load_skills_for_task(self, task_input: str, **kwargs):
        """
        작업 입력에 맞춰 스킬을 자동 선택 (모델별 max_skills 자동 적용).

        Args:
            task_input: 작업 설명
            **kwargs: load_skills_for_task()의 다른 파라미터

        Returns:
            (선택된 SkillMetadata 목록, 스킬별 점수 딕셔너리)
        """
        # max_skills를 명시하지 않으면 설정값 사용
        if "max_skills" not in kwargs:
            kwargs["max_skills"] = self.config.max_skills
        return super().load_skills_for_task(task_input, **kwargs)

    @classmethod
    def for_model(cls, model_name: str) -> "AdaptiveSkillLoader":
        """
        모델 이름으로 적응형 로더 생성.

        Args:
            model_name: 모델 이름 (예: "claude-haiku-4-5", "claude-sonnet-4-6")

        Returns:
            AdaptiveSkillLoader 인스턴스
        """
        from core.skill_context_config import SkillLoaderConfig
        config = SkillLoaderConfig(model_name=model_name)
        return cls(config=config)
