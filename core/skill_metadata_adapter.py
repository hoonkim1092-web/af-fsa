"""
core/skill_metadata_adapter.py
==============================
기존 YAML/MD 기반 스킬을 SkillMetadata로 변환하는 어댑터.

마이그레이션 전략:
  - Phase 1: 신규 스킬은 @skill_metadata 데코레이터 사용
  - Phase 2: 기존 스킬을 이 어댑터로 자동 변환
  - Phase 3: 호환성 레이어 제거 (6개월 후)
"""

import os
import yaml
from typing import Optional
from core.skill_metadata import SkillMetadata, SkillCategory, SkillType
from core.file_io import read_yaml


def convert_meta_yaml_to_metadata(meta_path: str) -> Optional[SkillMetadata]:
    """
    meta.yaml (action 스킬) → SkillMetadata 변환.

    Args:
        meta_path: meta.yaml 파일 경로

    Returns:
        SkillMetadata 객체 또는 None (변환 실패 시)
    """
    if not os.path.exists(meta_path):
        return None

    try:
        meta = read_yaml(meta_path)
        if not isinstance(meta, dict):
            return None

        skill_id = meta.get("id") or meta.get("name", "unknown")

        return SkillMetadata(
            skill_id=skill_id,
            name=meta.get("name", skill_id).replace("-", " ").title(),
            version=meta.get("version", "0.1.0"),
            description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use", ""),
            when_NOT_to_use=meta.get("when_NOT_to_use", ""),
            category=_parse_category(meta.get("category", "coding")),
            skill_type=SkillType.ACTION,
            when_to_use_keywords=meta.get("when_to_use_keywords", []),
            semantic_tags=meta.get("semantic_tags", []),
            max_tokens=meta.get("max_tokens", 4000),
            timeout_sec=meta.get("timeout_sec", 30),
            incompatible_with=meta.get("incompatible_with", []),
            tags=meta.get("capabilities", []),
            requires_auth=meta.get("requires_auth", False),
            network_required=meta.get("network_required", False),
            stateful=meta.get("stateful", False),
            experimental=meta.get("experimental", False),
        )
    except Exception:
        return None


def convert_skill_md_to_metadata(md_path: str, skill_id: str) -> Optional[SkillMetadata]:
    """
    SKILL.md / skill.md (knowledge 스킬) → SkillMetadata 변환.

    Args:
        md_path: SKILL.md 또는 skill.md 파일 경로
        skill_id: 스킬 식별자 (디렉토리 이름 권장)

    Returns:
        SkillMetadata 객체 또는 None (변환 실패 시)
    """
    if not os.path.exists(md_path):
        return None

    try:
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()

        # frontmatter 파싱
        import re
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        frontmatter = {}
        if match:
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError:
                pass

        name = frontmatter.get("name", skill_id)
        description = frontmatter.get("description", "")

        return SkillMetadata(
            skill_id=name,
            name=name.replace("-", " ").title(),
            version="0.1.0",
            description=description,
            category=SkillCategory.PLAN,  # knowledge 스킬은 기본 PLAN 카테고리
            skill_type=SkillType.KNOWLEDGE,
            max_tokens=2000,
            timeout_sec=10,
            tags=frontmatter.get("metadata", {}).get("tags", []),
        )
    except Exception:
        return None


def convert_yaml_config_to_metadata(skill_config: dict) -> Optional[SkillMetadata]:
    """
    agent YAML 파일의 스킬 설정 → SkillMetadata 변환.

    예) agents/developer.yaml의 skills 배열 항목:
        - skill_id: web-search
          description: "구글 검색"
          category: research

    Args:
        skill_config: 스킬 설정 딕셔너리

    Returns:
        SkillMetadata 객체
    """
    skill_id = skill_config.get("skill_id", skill_config.get("id", ""))
    if not skill_id or skill_id == "unknown":
        name = skill_config.get("name", "")
        if name:
            skill_id = name.lower().replace(" ", "-")
        else:
            return None

    return SkillMetadata(
        skill_id=skill_id,
        name=skill_config.get("name", skill_id.replace("-", " ").title()),
        version=skill_config.get("version", "0.1.0"),
        description=skill_config.get("description", ""),
        when_to_use=skill_config.get("when_to_use", ""),
        when_NOT_to_use=skill_config.get("when_not_to_use", ""),
        use_case_examples=skill_config.get("examples", []),
        category=_parse_category(skill_config.get("category", "coding")),
        skill_type=_parse_skill_type(skill_config.get("type", "action")),
        when_to_use_keywords=skill_config.get("keywords", []),
        semantic_tags=skill_config.get("semantic_tags", []),
        max_tokens=skill_config.get("max_tokens", 4000),
        timeout_sec=skill_config.get("timeout_sec", 30),
        dependencies=skill_config.get("dependencies", []),
        incompatible_with=skill_config.get("incompatible_with", []),
        requires_auth=skill_config.get("requires_auth", False),
        network_required=skill_config.get("network_required", False),
        stateful=skill_config.get("stateful", False),
        tags=skill_config.get("tags", []),
        experimental=skill_config.get("experimental", False),
    )


# =============================================================================
# 헬퍼 함수
# =============================================================================
def _parse_category(category_str: str) -> SkillCategory:
    """문자열을 SkillCategory enum으로 변환"""
    if not category_str:
        return SkillCategory.CODING

    cat_lower = str(category_str).lower().strip()
    try:
        return SkillCategory(cat_lower)
    except ValueError:
        return SkillCategory.CODING


def _parse_skill_type(type_str: str) -> SkillType:
    """문자열을 SkillType enum으로 변환"""
    if not type_str:
        return SkillType.ACTION

    type_lower = str(type_str).lower().strip()
    try:
        return SkillType(type_lower)
    except ValueError:
        return SkillType.ACTION


def auto_detect_and_convert(skill_dir: str, skill_id: str) -> Optional[SkillMetadata]:
    """
    스킬 디렉토리에서 메타데이터를 자동 감지하여 변환.

    우선순위:
      1. meta.yaml (action 스킬)
      2. SKILL.md / skill.md (knowledge 스킬)

    Args:
        skill_dir: 스킬 디렉토리 경로
        skill_id: 스킬 식별자

    Returns:
        SkillMetadata 또는 None
    """
    # 1. meta.yaml 확인
    meta_path = os.path.join(skill_dir, "meta.yaml")
    metadata = convert_meta_yaml_to_metadata(meta_path)
    if metadata:
        return metadata

    # 2. SKILL.md / skill.md 확인
    for md_name in ["SKILL.md", "skill.md"]:
        md_path = os.path.join(skill_dir, md_name)
        metadata = convert_skill_md_to_metadata(md_path, skill_id)
        if metadata:
            return metadata

    return None
