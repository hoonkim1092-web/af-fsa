import logging
from typing import Dict, List, Set, Tuple
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

    def load_skills_for_task(self, task_input: str, exclude_skills=None, min_score=0.0, verbose=False):
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

        sorted_skills = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:MAX_SKILLS_IN_CONTEXT]
        selected_ids = [sid for sid, _ in sorted_skills]

        # 충돌 해결
        resolved_ids = self._resolve_conflicts(selected_ids, all_skills)

        # 의존성 자동 포함 + 위상 정렬
        dep_graph = self._get_dep_graph(all_skills)
        resolved_ids = self._inject_missing_deps(resolved_ids, dep_graph, all_skills, scores)
        resolved_ids, excluded = dep_graph.topological_sort(resolved_ids)

        # 12-Cap 재적용 (의존성 추가로 초과할 수 있음)
        if len(resolved_ids) > MAX_SKILLS_IN_CONTEXT:
            resolved_ids = resolved_ids[:MAX_SKILLS_IN_CONTEXT]

        selected_skills = [all_skills[sid] for sid in resolved_ids if sid in all_skills]
        selected_scores = {sid: scores.get(sid, 0.0) for sid in resolved_ids}

        if verbose:
            print(f"[OK] {len(selected_skills)}개 스킬 선택 완료")
            if excluded:
                print(f"[WARN] 순환 의존성으로 {len(excluded)}개 제외: {excluded}")

        return selected_skills, selected_scores

    def _inject_missing_deps(
        self,
        skill_ids: List[str],
        dep_graph: SkillDependencyGraph,
        all_skills: Dict[str, SkillMetadata],
        scores: Dict[str, float],
    ) -> List[str]:
        """선택된 스킬의 미포함 의존 스킬을 자동 주입."""
        missing = dep_graph.get_required_deps(skill_ids)
        if not missing:
            return skill_ids

        result = list(skill_ids)
        for dep_id in missing:
            if dep_id in all_skills and dep_id not in result:
                result.append(dep_id)
                # 의존성으로 추가된 스킬은 최소 점수 부여
                if dep_id not in scores:
                    scores[dep_id] = 0.01
                if len(result) >= MAX_SKILLS_IN_CONTEXT:
                    break

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

    def build_routing_prompt(self, selected_skills, scores: Dict[str, float] = None):
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
