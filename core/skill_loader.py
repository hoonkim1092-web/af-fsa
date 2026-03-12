from typing import List, Dict, Tuple
from core.skill_metadata import SkillMetadata, SkillCategory
from core.skill_registry import get_global_registry, ensure_skills_loaded

MAX_SKILLS_IN_CONTEXT = 12

class SkillRelevance:
    def __init__(self):
        self.encoder = None
    
    def compute_score(self, task_input: str, skill: SkillMetadata) -> float:
        keyword_score = self._keyword_match(task_input, skill)
        semantic_score = 0.0
        category_score = self._category_match(task_input, skill)
        return keyword_score * 0.40 + semantic_score * 0.35 + category_score * 0.25
    
    def _keyword_match(self, task_input: str, skill: SkillMetadata) -> float:
        if not skill.when_to_use_keywords:
            return 0.0
        task_lower = task_input.lower()
        matches = sum(1 for kw in skill.when_to_use_keywords if kw.lower() in task_lower)
        return min(matches / len(skill.when_to_use_keywords), 1.0)
    
    def _category_match(self, task_input: str, skill: SkillMetadata) -> float:
        keywords = ["코드", "검색", "테스트", "평가", "설계", "리뷰", "디버그"]
        task_lower = task_input.lower()
        matches = sum(1 for kw in keywords if kw in task_lower)
        return min(matches / max(len(keywords), 1), 1.0)

class DynamicSkillLoader:
    def __init__(self):
        self.relevance = SkillRelevance()
        ensure_skills_loaded()
    
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
        resolved_ids = self._resolve_conflicts(selected_ids, all_skills)
        
        selected_skills = [all_skills[sid] for sid in resolved_ids if sid in all_skills]
        selected_scores = {sid: scores[sid] for sid in resolved_ids}
        
        if verbose:
            print(f"[OK] {len(selected_skills)}개 스킬 선택 완료")
        
        return selected_skills, selected_scores
    
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
