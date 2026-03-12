
import time
import threading
from collections import OrderedDict
from typing import Dict, Tuple, Optional

from core.skill_metadata import SkillMetadata


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
    
    def compute_score(
        self,
        task_input: str,
        skill: SkillMetadata,
        use_cache: bool = True
    ) -> float:
        keyword_score = self._keyword_match_cached(task_input, skill, use_cache)
        semantic_score = 0.0
        category_score = self._category_match_cached(task_input, skill, use_cache)
        
        return keyword_score * 0.40 + semantic_score * 0.35 + category_score * 0.25
    
    def _keyword_match_cached(self, task_input: str, skill: SkillMetadata, use_cache: bool) -> float:
        if not skill.when_to_use_keywords:
            return 0.0
        
        cache_key = f"kw:{task_input}:{skill.skill_id}"
        
        if use_cache:
            cached = self._keyword_cache.get(cache_key)
            if cached is not None:
                return cached
        
        task_lower = task_input.lower()
        matches = sum(1 for kw in skill.when_to_use_keywords if kw.lower() in task_lower)
        score = min(matches / len(skill.when_to_use_keywords), 1.0)
        
        self._keyword_cache.put(cache_key, score)
        return score
    
    def _category_match_cached(self, task_input: str, skill: SkillMetadata, use_cache: bool) -> float:
        cache_key = f"cat:{task_input}:{skill.category.value}"
        
        if use_cache:
            cached = self._category_cache.get(cache_key)
            if cached is not None:
                return cached
        
        keywords = ["코드", "검색", "테스트", "평가", "설계"]
        task_lower = task_input.lower()
        matches = sum(1 for kw in keywords if kw in task_lower)
        score = min(matches / max(len(keywords), 1), 1.0)
        
        self._category_cache.put(cache_key, score)
        return score
    
    def get_cache_stats(self) -> Dict[str, int]:
        return {
            "keyword_cache": self._keyword_cache.size(),
            "semantic_cache": self._semantic_cache.size(),
            "category_cache": self._category_cache.size(),
            "embeddings_cache": len(self._embeddings_cache),
        }
