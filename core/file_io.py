"""
core/file_io.py
===============
파일 I/O 전담 모듈: YAML/Text 읽기·쓰기 + LRU 캐시.
core/utils.py 에서 추출.
"""

import os
import copy
import hashlib
import threading
import yaml
from collections import OrderedDict

# =============================================================================
# YAML LRU Cache
# =============================================================================
_YAML_CACHE: "OrderedDict[str, tuple[int, int, str, dict]]" = OrderedDict()
_YAML_CACHE_LOCK = threading.Lock()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y")


def _yaml_cache_max_entries() -> int:
    raw = str(os.getenv("YAML_CACHE_MAX_ENTRIES", "256")).strip()
    try:
        return max(1, int(raw))
    except Exception:
        return 256


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _yaml_cache_put(path: str, mtime_ns: int, size: int, content_hash: str, data: dict):
    with _YAML_CACHE_LOCK:
        _YAML_CACHE[path] = (mtime_ns, size, content_hash, data)
        _YAML_CACHE.move_to_end(path, last=True)
        max_entries = _yaml_cache_max_entries()
        while len(_YAML_CACHE) > max_entries:
            _YAML_CACHE.popitem(last=False)


# =============================================================================
# YAML Read / Write
# =============================================================================
def read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        st = os.stat(path)
        mtime_ns = int(st.st_mtime_ns)
        size = int(st.st_size)
    except Exception:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    verify_hash = _env_flag("YAML_CACHE_VERIFY_HASH", default=False)
    with _YAML_CACHE_LOCK:
        cached = _YAML_CACHE.get(path)
        if cached and cached[0] == mtime_ns and cached[1] == size:
            cached_hash = str(cached[2] or "")
            if not verify_hash:
                _YAML_CACHE.move_to_end(path, last=True)
                return copy.deepcopy(cached[3])
            if cached_hash:
                cur_hash = _sha256_file(path)
                if cur_hash == cached_hash:
                    _YAML_CACHE.move_to_end(path, last=True)
                    return copy.deepcopy(cached[3])
            _YAML_CACHE.pop(path, None)

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        data = {}
    content_hash = _sha256_file(path) if verify_hash else ""
    _yaml_cache_put(path, mtime_ns, size, content_hash, data)
    return copy.deepcopy(data)


def write_yaml(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    try:
        st = os.stat(path)
        verify_hash = _env_flag("YAML_CACHE_VERIFY_HASH", default=False)
        content_hash = _sha256_file(path) if verify_hash else ""
        _yaml_cache_put(
            path,
            int(st.st_mtime_ns),
            int(st.st_size),
            content_hash,
            copy.deepcopy(data if isinstance(data, dict) else {}),
        )
    except Exception:
        with _YAML_CACHE_LOCK:
            _YAML_CACHE.pop(path, None)


# =============================================================================
# Text / Hash Helpers
# =============================================================================
def write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
