import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple

from core.skill_metadata import SkillCategory, SkillMetadata
from core.skill_registry import ensure_skills_loaded, get_global_registry


logger = logging.getLogger(__name__)

MAX_SKILLS_IN_CONTEXT = 12


class SkillDependencyGraph:
    """Dependency graph helper for skill ordering and dependency injection."""

    def __init__(self, all_skills: Dict[str, SkillMetadata]):
        self._all_skills = all_skills
        self._adj: Dict[str, Set[str]] = defaultdict(set)
        self._reverse: Dict[str, Set[str]] = defaultdict(set)
        self._build_graph()

    def _build_graph(self) -> None:
        for skill_id, meta in self._all_skills.items():
            for dep in meta.dependencies:
                if dep in self._all_skills:
                    self._adj[skill_id].add(dep)
                    self._reverse[dep].add(skill_id)

    def detect_cycles(self) -> List[List[str]]:
        white, gray, black = 0, 1, 2
        color: Dict[str, int] = {sid: white for sid in self._all_skills}
        path: List[str] = []
        cycles: List[List[str]] = []

        def dfs(node: str) -> None:
            color[node] = gray
            path.append(node)
            for dep in self._adj.get(node, set()):
                if dep not in color:
                    continue
                if color[dep] == gray:
                    cycle_start = path.index(dep)
                    cycles.append(path[cycle_start:] + [dep])
                elif color[dep] == white:
                    dfs(dep)
            path.pop()
            color[node] = black

        for skill_id in self._all_skills:
            if color[skill_id] == white:
                dfs(skill_id)
        return cycles

    def get_required_deps(self, skill_ids: List[str]) -> List[str]:
        selected = set(skill_ids)
        missing: List[str] = []
        for skill_id in skill_ids:
            for dep in self._adj.get(skill_id, set()):
                if dep not in selected and dep not in missing:
                    missing.append(dep)
        return missing

    def topological_sort(self, skill_ids: List[str]) -> Tuple[List[str], List[str]]:
        selected = set(skill_ids)
        in_degree: Dict[str, int] = {sid: 0 for sid in selected}
        for skill_id in selected:
            for dep in self._adj.get(skill_id, set()):
                if dep in selected:
                    in_degree[skill_id] += 1

        queue = deque([sid for sid, degree in in_degree.items() if degree == 0])
        ordered: List[str] = []

        while queue:
            node = queue.popleft()
            ordered.append(node)
            for dependent in self._reverse.get(node, set()):
                if dependent not in in_degree:
                    continue
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        excluded = [sid for sid in skill_ids if sid not in ordered]
        if excluded:
            logger.warning("Cycle detected in skill dependencies; excluding %s", excluded)
        return ordered, excluded


class SkillRelevance:
    """Fallback scorer for automatic skill selection."""

    _CATEGORY_KEYWORDS = {
        SkillCategory.CODING: ["code", "coding", "implement", "refactor", "fix"],
        SkillCategory.RESEARCH: ["search", "research", "analyze", "find", "investigate"],
        SkillCategory.IO: ["file", "read", "write", "save", "io"],
        SkillCategory.TESTING: ["test", "verify", "assert", "coverage"],
        SkillCategory.EVAL: ["eval", "evaluate", "benchmark", "feedback", "error"],
        SkillCategory.PLAN: ["plan", "design", "architecture", "roadmap"],
        SkillCategory.REVIEW: ["review", "audit", "inspect", "critique"],
        SkillCategory.DEBUG: ["debug", "bug", "trace", "fix"],
    }

    def __init__(self):
        self._embedder = None
        self._embedder_initialized = False

    @property
    def embedder(self):
        if not self._embedder_initialized:
            self._embedder_initialized = True
            try:
                from core.semantic_embedder import SemanticEmbedder

                self._embedder = SemanticEmbedder()
            except Exception:
                self._embedder = None
        return self._embedder

    def _keyword_match(self, task_input: str, skill: SkillMetadata) -> float:
        if not skill.when_to_use_keywords:
            return 0.0
        task_lower = task_input.lower()
        matches = sum(1 for keyword in skill.when_to_use_keywords if keyword.lower() in task_lower)
        return min(matches / max(len(skill.when_to_use_keywords), 1), 1.0)

    def _category_match(self, task_input: str, skill: SkillMetadata) -> float:
        keywords = self._CATEGORY_KEYWORDS.get(skill.category, [])
        if not keywords:
            return 0.0
        task_lower = task_input.lower()
        matches = sum(1 for keyword in keywords if keyword in task_lower)
        return min(matches / max(len(keywords), 1), 1.0)

    def compute_score(self, task_input: str, skill: SkillMetadata) -> float:
        keyword_score = self._keyword_match(task_input, skill)
        category_score = self._category_match(task_input, skill)

        if self.embedder and self.embedder.is_available:
            semantic_score = self.embedder.compute_similarity(task_input, skill)
            return keyword_score * 0.35 + semantic_score * 0.40 + category_score * 0.25

        return keyword_score * 0.60 + category_score * 0.40


class DynamicSkillLoader:
    """Selects a compact skill set for a task using metadata, relevance, and dependency rules."""

    def __init__(self):
        try:
            from core.skill_cache import OptimizedSkillRelevance

            self.relevance = OptimizedSkillRelevance()
        except Exception:
            self.relevance = SkillRelevance()
        ensure_skills_loaded()
        self._precompute_embeddings()
        self._dep_graph: SkillDependencyGraph | None = None
        self._dep_graph_skill_count: int = 0  # 크기 변경 감지용

    def _precompute_embeddings(self) -> None:
        if self.relevance.embedder and self.relevance.embedder.is_available:
            try:
                all_skills = get_global_registry().get_all()
                if all_skills:
                    self.relevance.embedder.precompute_skill_embeddings(all_skills)
            except Exception:
                pass

    def _get_dep_graph(self, all_skills: Dict[str, SkillMetadata]) -> SkillDependencyGraph:
        # 스킬 수가 바뀌면 그래프 재생성 (BUG-3 수정)
        if self._dep_graph is None or len(all_skills) != self._dep_graph_skill_count:
            self._dep_graph = SkillDependencyGraph(all_skills)
            self._dep_graph_skill_count = len(all_skills)
        return self._dep_graph

    def invalidate_dep_graph(self) -> None:
        self._dep_graph = None
        self._dep_graph_skill_count = 0

    def _resolve_conflicts(self, skill_ids: List[str], all_skills: Dict[str, SkillMetadata]) -> List[str]:
        resolved: List[str] = []
        for skill_id in skill_ids:
            skill = all_skills.get(skill_id)
            if not skill:
                continue
            conflicts = False
            for existing_id in resolved:
                existing = all_skills.get(existing_id)
                if not existing:
                    continue
                if skill_id in existing.incompatible_with or existing_id in skill.incompatible_with:
                    conflicts = True
                    break
            if not conflicts:
                resolved.append(skill_id)
        return resolved

    def _get_all_required_deps_recursive(self, skill_ids: List[str], dep_graph: SkillDependencyGraph) -> set[str]:
        visited: set[str] = set()

        def collect_recursive(items: List[str]) -> None:
            for skill_id in items:
                if skill_id in visited:
                    continue
                visited.add(skill_id)
                missing = dep_graph.get_required_deps([skill_id])
                if missing:
                    collect_recursive(missing)

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
        result = list(skill_ids)

        for _ in range(5):
            missing = dep_graph.get_required_deps(result)
            if not missing:
                break

            added = False
            for dep_id in missing:
                if dep_id in all_skills and dep_id not in result:
                    result.append(dep_id)
                    scores.setdefault(dep_id, 0.01)
                    added = True
            if not added:
                break

        if len(result) > max_skills:
            dep_set = self._get_all_required_deps_recursive(skill_ids, dep_graph)
            required = [skill_id for skill_id in result if skill_id in dep_set]
            optional = [skill_id for skill_id in result if skill_id not in dep_set]

            if len(required) >= max_skills:
                result = required[:max_skills]
            else:
                optional_sorted = sorted(optional, key=lambda skill_id: scores.get(skill_id, 0.0), reverse=True)
                result = required + optional_sorted[: max_skills - len(required)]

        return result

    def load_skills_for_task(
        self,
        task_input: str,
        exclude_skills=None,
        min_score: float = 0.0,
        verbose: bool = False,
        max_skills: int | None = None,
    ):
        effective_max = max_skills if max_skills is not None else MAX_SKILLS_IN_CONTEXT
        all_skills = get_global_registry().get_all()
        if not all_skills:
            return [], {}

        scores: Dict[str, float] = {}
        for skill_id, metadata in all_skills.items():
            if exclude_skills and skill_id in exclude_skills:
                continue
            if not metadata.is_auto_selectable:
                continue
            score = self.relevance.compute_score(task_input, metadata)
            if score >= min_score:
                scores[skill_id] = score

        sorted_skills = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:effective_max]
        selected_ids = [skill_id for skill_id, _score in sorted_skills]

        resolved_ids = self._resolve_conflicts(selected_ids, all_skills)
        dep_graph = self._get_dep_graph(all_skills)
        resolved_ids = self._inject_missing_deps(resolved_ids, dep_graph, all_skills, scores, max_skills=effective_max)
        resolved_ids, excluded = dep_graph.topological_sort(resolved_ids)

        selected_skills = [all_skills[skill_id] for skill_id in resolved_ids if skill_id in all_skills]
        selected_scores = {skill_id: scores.get(skill_id, 0.0) for skill_id in resolved_ids}

        if verbose:
            print(f"[OK] selected {len(selected_skills)} skills")
            if excluded:
                print(f"[WARN] excluded cyclic skills: {excluded}")

        return selected_skills, selected_scores

    def build_routing_prompt(self, selected_skills, scores: Optional[Dict[str, float]] = None):
        if not selected_skills:
            return "No auto-selectable skills are available."

        by_category: dict[str, list[SkillMetadata]] = {}
        for skill in selected_skills:
            by_category.setdefault(skill.category.value, []).append(skill)

        lines: list[str] = [f"[Available Skills] total={len(selected_skills)}", ""]
        for category, skills in sorted(by_category.items()):
            lines.append(f"### {category.upper()} ({len(skills)})")
            for index, skill in enumerate(skills, start=1):
                score = scores.get(skill.skill_id, 0.0) if scores else 0.0
                lines.append(f"{index}. {skill.name} ({skill.skill_id})")
                if score > 0:
                    lines.append(f"   score: {score:.2f}")
                if skill.description:
                    lines.append(f"   description: {skill.description}")
                if skill.when_to_use:
                    lines.append(f"   use when: {skill.when_to_use}")
                if skill.when_NOT_to_use:
                    lines.append(f"   avoid when: {skill.when_NOT_to_use}")
                if skill.use_case_examples:
                    lines.append(f"   examples: {', '.join(skill.use_case_examples[:2])}")
                lines.append("")
        return "\n".join(lines)


class AdaptiveSkillLoader(DynamicSkillLoader):
    """Dynamic loader variant that derives max skill count from the model context budget."""

    def __init__(self, config=None):
        super().__init__()
        if config is None:
            from core.skill_context_config import SkillLoaderConfig

            config = SkillLoaderConfig()
        self.config = config

    def load_skills_for_task(self, task_input: str, **kwargs):
        if "max_skills" not in kwargs:
            kwargs["max_skills"] = self.config.max_skills
        return super().load_skills_for_task(task_input, **kwargs)

    @classmethod
    def for_model(cls, model_name: str) -> "AdaptiveSkillLoader":
        from core.skill_context_config import SkillLoaderConfig

        return cls(config=SkillLoaderConfig(model_name=model_name))
