"""
core/skill_registry.py
======================
스킬 메타데이터 중앙 레지스트리.

동적 스킬 로더(Phase 2)가 이를 참조하여 12-Cap 필터링을 수행합니다.

기능:
  1. 스킬 메타데이터 등록 및 조회
  2. 메타데이터 캐싱 (성능)
  3. 기존 YAML/MD 스킬 자동 로드 (호환성)
"""

import logging
import os
import threading
import yaml
from typing import Dict, List, Optional
from core.skill_metadata import SkillMetadata, get_skill_metadata
from core.skill_metadata_adapter import (
    auto_detect_and_convert,
    convert_yaml_config_to_metadata,
)
from core.config_paths import SKILLS_DIR, PROJECT_SKILLS_DIR
from core.file_io import read_yaml, write_yaml
from core.utils import get_codex_skill_roots

logger = logging.getLogger(__name__)

# 기존 REGISTRY_FILE, DOCS_FILE 상수 (호환성)
REGISTRY_FILE = os.path.join(PROJECT_SKILLS_DIR, "registry.yaml") if PROJECT_SKILLS_DIR else "registry.yaml"
DOCS_FILE = os.path.join(PROJECT_SKILLS_DIR, "skill_docs.md") if PROJECT_SKILLS_DIR else "skill_docs.md"


# =============================================================================
# 스킬 레지스트리
# =============================================================================
class SkillRegistry:
    """
    스킬 메타데이터 중앙 저장소.

    Thread-safe 싱글톤 패턴으로 구현.
    """

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True

        self._registry: Dict[str, SkillMetadata] = {}
        self._lock = threading.RLock()
        self._auto_loaded = False

    def register(self, metadata: SkillMetadata) -> None:
        """스킬 메타데이터를 등록합니다."""
        with self._lock:
            skill_id = metadata.skill_id
            if skill_id in self._registry:
                print(f"[WARN] 스킬 '{skill_id}' 메타데이터가 이미 등록되어 있습니다. 업데이트합니다.")

            self._registry[skill_id] = metadata

    def register_from_decorator(self, func_or_class) -> SkillMetadata:
        """@skill_metadata 데코레이터가 부착된 함수/클래스를 등록합니다."""
        metadata = get_skill_metadata(func_or_class)
        if metadata is None:
            raise ValueError(
                f"{func_or_class} 에 @skill_metadata 데코레이터가 없습니다."
            )

        self.register(metadata)
        return metadata

    def get(self, skill_id: str) -> Optional[SkillMetadata]:
        """스킬 메타데이터를 조회합니다."""
        with self._lock:
            return self._registry.get(skill_id)

    def get_all(self) -> Dict[str, SkillMetadata]:
        """모든 등록된 스킬 메타데이터를 반환합니다."""
        with self._lock:
            return dict(self._registry)

    def list_skills(self, category: str = None) -> List[str]:
        """등록된 스킬 ID 목록을 반환합니다."""
        with self._lock:
            if category:
                return [
                    sid for sid, meta in self._registry.items()
                    if meta.category.value == category
                ]
            return list(self._registry.keys())

    def count(self) -> int:
        """등록된 스킬 개수"""
        with self._lock:
            return len(self._registry)

    def clear(self) -> None:
        """레지스트리 초기화 (테스트용)"""
        with self._lock:
            self._registry.clear()
            self._auto_loaded = False

    def auto_load_from_directories(self, force: bool = False) -> int:
        """기존 스킬 디렉토리에서 메타데이터를 자동으로 로드합니다.

        스캔 순서:
          1. PROJECT_SKILLS_DIR  — 프로젝트별 스킬 (최우선)
          2. SKILLS_DIR          — agent-factory 글로벌 스킬
          3. Codex Skill Roots   — ~/.agents/skills/, ~/.codex/skills/ 등 사용자 전역 스킬
        """
        with self._lock:
            if self._auto_loaded and not force:
                return 0

            loaded_count = 0

            # 1) Project Skills
            if os.path.isdir(PROJECT_SKILLS_DIR):
                loaded_count += self._load_from_directory(PROJECT_SKILLS_DIR)

            # 2) Global Skills
            if os.path.isdir(SKILLS_DIR):
                loaded_count += self._load_from_directory(SKILLS_DIR)

            # 3) User-wide Codex Skill Roots (~/.agents/skills/, ~/.codex/skills/, etc.)
            already_scanned = {
                os.path.normpath(os.path.abspath(PROJECT_SKILLS_DIR)).lower(),
                os.path.normpath(os.path.abspath(SKILLS_DIR)).lower(),
            }
            for codex_root in get_codex_skill_roots():
                norm = os.path.normpath(os.path.abspath(codex_root)).lower()
                if norm in already_scanned:
                    continue
                already_scanned.add(norm)
                if os.path.isdir(codex_root):
                    loaded_count += self._load_from_directory(codex_root)

            self._auto_loaded = True
            return loaded_count

    _SKIP_DIRS = {"forge", "_external_cache", "__pycache__", "warehouse"}

    def _load_from_directory(self, base_dir: str, _depth: int = 0) -> int:
        """디렉토리 하위의 모든 스킬을 로드합니다 (최대 3단계 재귀)."""
        if _depth > 3:
            return 0

        count = 0
        try:
            for item in os.listdir(base_dir):
                skill_dir = os.path.join(base_dir, item)
                if not os.path.isdir(skill_dir):
                    continue

                if item.startswith(".") or item in self._SKIP_DIRS:
                    continue

                metadata = auto_detect_and_convert(skill_dir, item)
                if metadata:
                    self.register(metadata)
                    count += 1

                    # 번들 meta.yaml: sub_skills 선언이 있으면 개별 스킬도 등록
                    count += self._load_sub_skills(skill_dir)

                    # 하위 디렉토리도 재귀 탐색 (번들 내 서브 스킬)
                    count += self._load_from_directory(skill_dir, _depth + 1)
                else:
                    # 메타데이터 없으면 하위 디렉토리 재귀 탐색
                    count += self._load_from_directory(skill_dir, _depth + 1)

        except Exception as e:
            logger.warning("스킬 디렉토리 로드 실패 (%s): %s", base_dir, e)

        return count

    def _load_sub_skills(self, skill_dir: str) -> int:
        """meta.yaml의 sub_skills 선언에서 개별 스킬을 등록합니다.

        번들 meta.yaml에 sub_skills 배열이 있으면, 각 항목을 독립적인
        SkillMetadata로 변환하여 레지스트리에 등록합니다. 이를 통해
        Python 파일의 @skill_metadata 데코레이터 스킬을 동적 import 없이
        선언적으로 등록할 수 있습니다.
        """
        meta_path = os.path.join(skill_dir, "meta.yaml")
        if not os.path.exists(meta_path):
            return 0

        try:
            meta = read_yaml(meta_path)
            if not isinstance(meta, dict):
                return 0

            sub_skills = meta.get("sub_skills", [])
            if not isinstance(sub_skills, list):
                return 0

            count = 0
            for skill_config in sub_skills:
                if not isinstance(skill_config, dict):
                    continue

                sub_metadata = convert_yaml_config_to_metadata(skill_config)
                if sub_metadata and sub_metadata.skill_id not in self._registry:
                    self._registry[sub_metadata.skill_id] = sub_metadata
                    count += 1

            return count
        except Exception as e:
            logger.debug("sub_skills 로드 실패 (%s): %s", skill_dir, e)
            return 0

    def register_from_agent_config(self, agent_skills: List[dict]) -> int:
        """Agent YAML의 skills 배열을 로드합니다."""
        count = 0
        with self._lock:
            for skill_config in agent_skills:
                if not isinstance(skill_config, dict):
                    continue

                metadata = convert_yaml_config_to_metadata(skill_config)
                if metadata is None:
                    continue
                skill_id = metadata.skill_id
                if skill_id not in self._registry:
                    self.register(metadata)
                    count += 1

        return count


# =============================================================================
# 글로벌 싱글톤 인스턴스
# =============================================================================
_global_registry = None


def get_global_registry() -> SkillRegistry:
    """글로벌 스킬 레지스트리 인스턴스를 반환합니다."""
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()
    return _global_registry


def register_skill_metadata(metadata: SkillMetadata) -> None:
    """글로벌 레지스트리에 SkillMetadata를 등록합니다."""
    get_global_registry().register(metadata)


def get_skill_metadata_global(skill_id: str) -> Optional[SkillMetadata]:
    """글로벌 레지스트리에서 스킬 메타데이터를 조회합니다."""
    return get_global_registry().get(skill_id)


def list_all_skills(category: str = None) -> List[str]:
    """글로벌 레지스트리의 모든 스킬 ID를 반환합니다."""
    return get_global_registry().list_skills(category)


def ensure_skills_loaded() -> None:
    """스킬이 로드되지 않았으면 자동 로드합니다."""
    registry = get_global_registry()
    if registry.count() == 0:
        print("[INFO] 스킬 메타데이터 자동 로드 시작...")
        count = registry.auto_load_from_directories()
        print(f"[OK] {count}개 스킬 로드 완료")


# =============================================================================
# 호환성: 기존 코드 지원
# =============================================================================
def _load_registry() -> dict:
    """
    YAML 레지스트리 파일 로드 (기존 API 호환성).

    리스트 형식의 install_candidates를 딕셔너리로 정규화.

    Returns:
        {"skills": {...}, "install_candidates": {...}}
    """
    if not os.path.exists(REGISTRY_FILE):
        return {"skills": {}, "install_candidates": {}}

    try:
        data = read_yaml(REGISTRY_FILE)
        if not isinstance(data, dict):
            return {"skills": {}, "install_candidates": {}}

        # 기본값 설정
        data.setdefault("skills", {})
        data.setdefault("install_candidates", {})

        # install_candidates 정규화: 리스트 → 딕셔너리
        install_candidates = data["install_candidates"]
        if isinstance(install_candidates, list):
            normalized = {}
            for item in install_candidates:
                if not isinstance(item, dict):
                    continue

                # source로부터 source_id 생성
                source = item.get("source", "")
                source_id = _source_to_id(source)

                # 경로에서 repo/filename 추출
                path = item.get("path", "")
                repo_name = ""
                filename = ""

                if path and "/" in path:
                    parts = path.split("/")
                    # 일반적 패턴: skills/_external_cache/{source}/{repo}/{filename}.py
                    # parts: ["skills", "_external_cache", "claude", "repo_alpha", "issue_tracker.py"]
                    if len(parts) >= 5 and "external_cache" in parts[1]:
                        # parts[2] = source (예: "claude")
                        # parts[3] = repo (예: "repo_alpha")
                        # parts[4] = filename
                        repo_name = parts[3] if len(parts) > 3 else ""
                        filename = os.path.splitext(parts[4])[0] if len(parts) > 4 else ""

                    if not filename:
                        # 대체: 마지막 요소로부터 filename 추출
                        filename = os.path.splitext(os.path.basename(path))[0]

                # source_id + repo_name 조합
                # repo_name이 "repo_alpha"이면 "repo" 부분만 추출
                if repo_name:
                    repo_prefix = repo_name.split("_")[0]
                    full_source_id = f"{source_id}_{repo_prefix}".strip("_")
                else:
                    full_source_id = source_id

                # 키 생성
                if full_source_id and filename:
                    key = f"{full_source_id}_{filename}"
                elif full_source_id:
                    key = full_source_id
                elif filename:
                    key = filename
                else:
                    key = "candidate"

                # 정규화된 항목
                normalized_item = dict(item)
                if source:
                    # source_id는 source + repo_name의 조합
                    normalized_item["source_id"] = full_source_id
                    # "source" 필드 제거 (source_id로 대체)
                    normalized_item.pop("source", None)

                normalized[key] = normalized_item

            data["install_candidates"] = normalized
        elif isinstance(install_candidates, dict):
            # 이미 딕셔너리인 경우 source_id/키 재구성
            new_candidates = {}
            for old_key, item in install_candidates.items():
                if not isinstance(item, dict):
                    new_candidates[old_key] = item
                    continue

                source = item.get("source", "")
                source_repo = item.get("source_repo", "")

                # source_id 생성
                if source_repo:
                    # source + source_repo 조합 (source_repo에서 _ 앞의 부분만)
                    repo_prefix = source_repo.split("_")[0]
                    source_id = f"{_source_to_id(source)}_{repo_prefix}".strip("_")
                elif source:
                    source_id = _source_to_id(source)
                else:
                    source_id = ""

                item["source_id"] = source_id

                # 키 재구성 (source_repo가 있으면 포함)
                path = item.get("path", "")
                filename = os.path.splitext(os.path.basename(path))[0] if path else ""

                if source_id and filename:
                    new_key = f"{source_id}_{filename}"
                elif source_id:
                    new_key = source_id
                elif filename:
                    new_key = filename
                else:
                    new_key = old_key

                # "source" 필드 제거
                item.pop("source", None)

                new_candidates[new_key] = item

            data["install_candidates"] = new_candidates

        return data

    except Exception:
        return {"skills": {}, "install_candidates": {}}


def _source_to_id(source: str) -> str:
    """
    source 문자열을 source_id로 변환.

    예:
      "Claude" → "claude"
      "Claude repo_alpha" → "claude_repo"
    """
    source = str(source or "").strip().lower()
    # 공백을 언더스코어로 변환
    source_id = source.replace(" ", "_")
    # 특수 문자 제거
    source_id = "".join(c for c in source_id if c.isalnum() or c == "_")
    # 연속된 언더스코어 제거
    while "__" in source_id:
        source_id = source_id.replace("__", "_")
    return source_id.strip("_")


def _save_registry(data: dict) -> None:
    """
    YAML 레지스트리 파일 저장 (기존 API 호환성).

    Args:
        data: {"skills": {...}, "install_candidates": {...}}
    """
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    write_yaml(REGISTRY_FILE, data)


def check_skill_exists(skill_id: str) -> bool:
    """스킬 존재 여부 확인 (기존 API 호환성)"""
    ensure_skills_loaded()
    metadata = get_skill_metadata_global(skill_id)
    return metadata is not None


def rebuild_registry_from_disk() -> None:
    """
    디스크의 스킬 디렉토리에서 레지스트리 재구축 (기존 API 호환성).
    """
    get_global_registry().auto_load_from_directories(force=True)


def _normalize_skill_entry(skill_name: str, entry: dict) -> tuple[str, dict]:
    """
    스킬 엔트리를 정규화 (기존 API 호환성).

    Args:
        skill_name: 스킬 이름
        entry: 스킬 엔트리 딕셔너리

    Returns:
        (skill_id, normalized_entry)
    """
    # skill_id 생성
    skill_id = entry.get("skill_id", skill_name.lower().replace(" ", "_"))

    # 정규화된 엔트리
    normalized = {
        "skill_id": skill_id,
        "skill_name": entry.get("name", skill_name),
        "purpose": entry.get("purpose", ""),
        "path": entry.get("path", ""),
        "dependencies": entry.get("dependencies", []),
        "type": entry.get("type", "action"),  # 기본값: action
    }
    # 나머지 필드도 포함
    for key, value in entry.items():
        if key not in normalized:
            normalized[key] = value

    return skill_id, normalized


def register_skill(
    skill_name: str,
    purpose: str = "",
    path: str = "",
    dependencies: list = None,
    **kwargs
) -> None:
    """
    스킬을 레지스트리에 등록 (기존 API 호환성).

    기존 API: register_skill(skill_name, purpose, path, dependencies, ...)
    내부적으로는 YAML 레지스트리 파일에 저장.

    Args:
        skill_name: 스킬 이름
        purpose: 스킬 목적
        path: 스킬 파일 경로
        dependencies: 의존성 목록
        **kwargs: 기타 필드
    """
    data = _load_registry()
    skills = data.get("skills", {})

    # skill_id 생성 (기존 구조에서는 정수 또는 문자열)
    skill_id = str(len(skills) + 1) if skills else "1"

    # 새 스킬 엔트리
    skill_entry = {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "purpose": purpose,
        "path": path,
        "dependencies": dependencies or [],
        "type": "action",  # 기본값
    }
    skill_entry.update(kwargs)

    skills[skill_id] = skill_entry
    data["skills"] = skills
    _save_registry(data)
