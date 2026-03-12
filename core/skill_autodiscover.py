
import os
import threading
import time
from typing import Dict, List, Tuple
from collections import OrderedDict

from core.skill_metadata import SkillMetadata
from core.skill_registry import get_global_registry
from core.skill_metadata_adapter import auto_detect_and_convert
from core.config_paths import SKILLS_DIR, PROJECT_SKILLS_DIR


class SkillAutoDiscovery:
    def __init__(self, max_cache_size: int = 1000):
        self.max_cache_size = max_cache_size
        self._cache: OrderedDict[str, Tuple[float, SkillMetadata]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._scan_in_progress = False
        self._last_scan_time = 0
        self._last_scan_count = 0
    
    def discover_all_skills(self, force_refresh: bool = False, verbose: bool = False) -> int:
        if self._scan_in_progress:
            return 0
        
        self._scan_in_progress = True
        try:
            now = time.time()
            if not force_refresh and (now - self._last_scan_time) < 60:
                if verbose:
                    print(f"[CACHE] 마지막 스캔으로부터 60초 미만. 캐시 사용")
                return self._last_scan_count
            
            registry = get_global_registry()
            count = 0
            
            for base_dir in [PROJECT_SKILLS_DIR, SKILLS_DIR]:
                if not os.path.isdir(base_dir):
                    continue
                count += self._scan_directory(base_dir, registry, verbose)
            
            self._last_scan_time = now
            self._last_scan_count = count
            
            if verbose:
                print(f"[OK] {count}개 스킬 발견 완료 (캐시: {len(self._cache)}개)")
            
            return count
        finally:
            self._scan_in_progress = False
    
    def _scan_directory(self, base_dir: str, registry, verbose: bool) -> int:
        count = 0
        try:
            for item in os.listdir(base_dir):
                skill_dir = os.path.join(base_dir, item)
                if not os.path.isdir(skill_dir):
                    continue
                
                if item.startswith(".") or item in ("forge", "_external_cache"):
                    continue
                
                metadata = auto_detect_and_convert(skill_dir, item)
                if metadata:
                    registry.register(metadata)
                    self._cache_skill(item, metadata)
                    count += 1
        except Exception as e:
            if verbose:
                print(f"[WARN] 디렉토리 스캔 실패 ({base_dir}): {e}")
        
        return count
    
    def _cache_skill(self, skill_id: str, metadata: SkillMetadata):
        with self._cache_lock:
            self._cache[skill_id] = (time.time(), metadata)
            if len(self._cache) > self.max_cache_size:
                self._cache.popitem(last=False)
    
    def get_cached_skill(self, skill_id: str) -> SkillMetadata:
        with self._cache_lock:
            if skill_id in self._cache:
                _, metadata = self._cache[skill_id]
                return metadata
        return None
    
    def cache_hit_rate(self) -> float:
        with self._cache_lock:
            registry = get_global_registry()
            all_count = registry.count()
            cache_count = len(self._cache)
        
        return cache_count / max(all_count, 1)


_global_discoverer = None


def get_discoverer() -> SkillAutoDiscovery:
    global _global_discoverer
    if _global_discoverer is None:
        _global_discoverer = SkillAutoDiscovery()
    return _global_discoverer


def auto_discover_skills(force_refresh: bool = False, verbose: bool = False) -> int:
    return get_discoverer().discover_all_skills(force_refresh, verbose)
