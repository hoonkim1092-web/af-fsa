
import hashlib
import time
import threading
from collections import OrderedDict
from typing import Dict, Tuple, Optional

from core.skill_metadata import SkillMetadata, SkillCategory


class SkillRelevanceCache:
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, cache_key: str) -> Optional[float]:
        with self._lock:
            if cache_key not in self._cache:
                return None

            timestamp, score = self._cache[cache_key]

            if time.time() - timestamp > self.ttl_seconds:
                del self._cache[cache_key]
                return None

            self._cache.move_to_end(cache_key)
            return score

    def put(self, cache_key: str, score: float):
        with self._lock:
            self._cache[cache_key] = (time.time(), score)
            self._cache.move_to_end(cache_key)

            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def hit_rate(self, total_requests: int) -> float:
        return len(self._cache) / max(total_requests, 1)


class OptimizedSkillRelevance:
    def __init__(self):
        self.encoder = None
        self._keyword_cache = SkillRelevanceCache(max_size=500)
        self._semantic_cache = SkillRelevanceCache(max_size=500)
        self._category_cache = SkillRelevanceCache(max_size=500)
        self._embeddings_cache: Dict[str, list] = {}
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

    def compute_score(
        self,
        task_input: str,
        skill: SkillMetadata,
        use_cache: bool = True
    ) -> float:
        keyword_score = self._keyword_match_cached(task_input, skill, use_cache)
        category_score = self._category_match_cached(task_input, skill, use_cache)

        if self.embedder and self.embedder.is_available:
            semantic_score = self._semantic_match_cached(task_input, skill, use_cache)
            return keyword_score * 0.35 + semantic_score * 0.40 + category_score * 0.25

        # 시맨틱 불가 → 기존 키워드(60%) + 카테고리(40%) 폴백
        return keyword_score * 0.60 + category_score * 0.40

    def _semantic_match_cached(self, task_input: str, skill: SkillMetadata, use_cache: bool) -> float:
        """시맨틱 유사도 (캐시 적용)."""
        task_hash = hashlib.md5(task_input.encode()).hexdigest()[:12]
        cache_key = f"sem:{task_hash}:{skill.skill_id}"

        if use_cache:
            cached = self._semantic_cache.get(cache_key)
            if cached is not None:
                return cached

        score = 0.0
        if self.embedder:
            score = self.embedder.compute_similarity(task_input, skill)

        self._semantic_cache.put(cache_key, score)
        return score

    def _keyword_match_cached(self, task_input: str, skill: SkillMetadata, use_cache: bool) -> float:
        if not skill.when_to_use_keywords:
            return 0.0

        task_hash = hashlib.md5(task_input.encode()).hexdigest()[:12]
        cache_key = f"kw:{task_hash}:{skill.skill_id}"

        if use_cache:
            cached = self._keyword_cache.get(cache_key)
            if cached is not None:
                return cached

        task_lower = task_input.lower()
        matches = sum(1 for kw in skill.when_to_use_keywords if kw.lower() in task_lower)
        score = min(matches / len(skill.when_to_use_keywords), 1.0)

        self._keyword_cache.put(cache_key, score)
        return score

    # 카테고리별 매칭 키워드 (SkillRelevance와 동일)
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

    def _category_match_cached(self, task_input: str, skill: SkillMetadata, use_cache: bool) -> float:
        task_hash = hashlib.md5(task_input.encode()).hexdigest()[:12]
        cache_key = f"cat:{task_hash}:{skill.category.value}"

        if use_cache:
            cached = self._category_cache.get(cache_key)
            if cached is not None:
                return cached

        keywords = self._CATEGORY_KEYWORDS.get(skill.category, [])
        if not keywords:
            self._category_cache.put(cache_key, 0.0)
            return 0.0
        task_lower = task_input.lower()
        matches = sum(1 for kw in keywords if kw in task_lower)
        score = min(matches / len(keywords), 1.0)

        self._category_cache.put(cache_key, score)
        return score

    def get_cache_stats(self) -> Dict[str, int]:
        return {
            "keyword_cache": self._keyword_cache.size(),
            "semantic_cache": self._semantic_cache.size(),
            "category_cache": self._category_cache.size(),
            "embeddings_cache": len(self._embeddings_cache),
        }
