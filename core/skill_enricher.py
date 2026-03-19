"""
core/skill_enricher.py
======================
스킬 메타데이터 자동 보강 엔진.

역할:
  - skill.py / SKILL.md 내용을 LLM으로 분석
  - description, when_to_use_keywords, semantic_tags, category 자동 추출
  - meta.yaml에 병합 저장 (기존 값 덮어쓰기가 아닌 보강)
  - SkillMetadata 품질 점수 계산
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import yaml

from core.skill_metadata import SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)

# 메타데이터 품질 점수 임계값
QUALITY_THRESHOLD = 0.5

# LLM 요청 타임아웃
LLM_TIMEOUT_SEC = 30


def meta_quality_score(meta: SkillMetadata) -> float:
    """
    메타데이터 품질 점수 계산 (0.0 ~ 1.0).

    채점 기준:
      description         → +0.20
      when_to_use         → +0.20
      when_to_use_keywords→ +0.20
      semantic_tags       → +0.20
      category (비기본값) → +0.10
      when_NOT_to_use     → +0.10
    """
    score = 0.0
    if meta.description and len(meta.description.strip()) > 10:
        score += 0.20
    if meta.when_to_use and len(meta.when_to_use.strip()) > 10:
        score += 0.20
    if meta.when_to_use_keywords:
        score += 0.20
    if meta.semantic_tags:
        score += 0.20
    if meta.category != SkillCategory.CODING:
        score += 0.10
    if meta.when_NOT_to_use and len(meta.when_NOT_to_use.strip()) > 5:
        score += 0.10
    return round(score, 2)


def _read_skill_content(skill_dir: str) -> tuple[str, str]:
    """스킬 주요 파일 내용 읽기. (content, skill_type)"""
    skill_py = os.path.join(skill_dir, "skill.py")
    if os.path.exists(skill_py):
        try:
            with open(skill_py, "r", encoding="utf-8") as f:
                return f.read(), "action"
        except Exception:
            pass

    for md_name in ("SKILL.md", "skill.md"):
        md_path = os.path.join(skill_dir, md_name)
        if os.path.exists(md_path):
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    return f.read(), "knowledge"
            except Exception:
                pass

    return "", "unknown"


def _read_meta(skill_dir: str) -> dict:
    meta_path = os.path.join(skill_dir, "meta.yaml")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


def _write_meta(skill_dir: str, meta: dict) -> None:
    meta_path = os.path.join(skill_dir, "meta.yaml")
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)


def _extract_enrichment_via_llm(
    skill_name: str,
    content: str,
    skill_type: str,
    coding_engine: Optional[str] = None,
) -> Optional[dict]:
    """
    LLM을 사용하여 스킬 메타데이터 추출.

    Returns:
        {
          "description": str,
          "when_to_use": str,
          "when_NOT_to_use": str,
          "when_to_use_keywords": [str, ...],
          "semantic_tags": [str, ...],
          "category": str,
        }
        또는 None (실패 시)
    """
    try:
        from core.llm_engine import LLMEngine
        from model_utils import get_best_model
    except ImportError:
        logger.warning("[SkillEnricher] LLM 의존성 없음, 메타 보강 스킵")
        return None

    engine = coding_engine or "gemini-2.0-flash"
    try:
        llm = LLMEngine(model_name=get_best_model([engine]))
    except Exception as e:
        logger.warning("[SkillEnricher] LLM 초기화 실패: %s", e)
        return None

    # 콘텐츠가 너무 길면 앞부분만 사용
    content_excerpt = content[:3000] if len(content) > 3000 else content

    prompt = f"""\
다음 {skill_type} 스킬 '{skill_name}'의 코드/내용을 분석하여 JSON으로만 응답하세요.

스킬 내용:
{content_excerpt}

아래 JSON 형식으로만 응답 (코드 블록 없이):
{{
  "description": "이 스킬이 무엇을 하는지 1-2문장",
  "when_to_use": "어떤 상황에서 사용해야 하는지 1문장",
  "when_NOT_to_use": "어떤 상황에서 사용하지 말아야 하는지 1문장",
  "when_to_use_keywords": ["한글키워드1", "영어keyword2", "키워드3", "keyword4", "키워드5"],
  "semantic_tags": ["tag1", "tag2", "tag3"],
  "category": "coding 또는 research 또는 io 또는 testing 또는 eval 또는 plan 또는 review 또는 debug 중 하나"
}}

규칙:
- when_to_use_keywords: 5~10개, 한글+영어 혼합
- semantic_tags: 3~5개, 소문자 hyphen-case
- category: 반드시 8개 중 하나만 선택
- JSON 외 다른 텍스트 없이 JSON만 응답
"""

    try:
        raw = llm.generate(prompt)
        # 코드 블록 제거
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
        raw = raw.strip("`").strip()

        data = json.loads(raw)
        if not isinstance(data, dict):
            return None

        # 필수 필드 검증
        if not data.get("description"):
            return None

        # category 검증
        valid_cats = {"coding", "research", "io", "testing", "eval", "plan", "review", "debug"}
        if data.get("category", "").lower() not in valid_cats:
            data["category"] = "coding"

        return data
    except Exception as e:
        logger.warning("[SkillEnricher] LLM 응답 파싱 실패 (%s): %s", skill_name, e)
        return None


def enrich_skill_metadata(
    skill_dir: str,
    coding_engine: Optional[str] = None,
    force: bool = False,
) -> bool:
    """
    스킬 메타데이터를 자동으로 보강합니다.

    Args:
        skill_dir: 스킬 디렉토리 경로
        coding_engine: LLM 엔진 이름 (None=자동 선택)
        force: True면 품질 점수 무관하게 강제 보강

    Returns:
        보강 성공 여부
    """
    if not os.path.isdir(skill_dir):
        return False

    skill_name = os.path.basename(skill_dir)

    # 현재 메타데이터 읽기
    meta = _read_meta(skill_dir)

    # 스킬 콘텐츠 읽기
    content, skill_type = _read_skill_content(skill_dir)
    if not content:
        logger.debug("[SkillEnricher] 콘텐츠 없음: %s", skill_name)
        return False

    # 품질 점수 확인 (force가 아니면 이미 충분한 경우 스킵)
    if not force:
        # 임시 SkillMetadata로 품질 점수 계산
        from core.skill_metadata_adapter import auto_detect_and_convert
        existing_meta = auto_detect_and_convert(skill_dir, skill_name)
        if existing_meta:
            score = meta_quality_score(existing_meta)
            if score >= QUALITY_THRESHOLD:
                logger.debug(
                    "[SkillEnricher] 품질 충분 (%.2f >= %.2f), 스킵: %s",
                    score, QUALITY_THRESHOLD, skill_name,
                )
                return True  # 이미 충분함 → 성공으로 처리

    logger.info("[SkillEnricher] 메타 보강 시작: %s (type=%s)", skill_name, skill_type)

    # LLM으로 메타데이터 추출
    enriched = _extract_enrichment_via_llm(skill_name, content, skill_type, coding_engine)
    if not enriched:
        logger.warning("[SkillEnricher] LLM 추출 실패: %s", skill_name)
        return False

    # meta.yaml에 병합 (기존 값 보존, 누락된 필드만 채움)
    changed = False

    def _set_if_missing(key: str, value) -> None:
        nonlocal changed
        if value and not meta.get(key):
            meta[key] = value
            changed = True

    _set_if_missing("description", enriched.get("description"))
    _set_if_missing("when_to_use", enriched.get("when_to_use"))
    _set_if_missing("when_NOT_to_use", enriched.get("when_NOT_to_use"))
    _set_if_missing("when_to_use_keywords", enriched.get("when_to_use_keywords"))
    _set_if_missing("semantic_tags", enriched.get("semantic_tags"))
    _set_if_missing("category", enriched.get("category"))

    if not changed:
        logger.debug("[SkillEnricher] 변경 없음: %s", skill_name)
        return True

    # meta.yaml 저장
    try:
        _write_meta(skill_dir, meta)
        logger.info("[SkillEnricher] 메타 보강 완료: %s", skill_name)
        return True
    except Exception as e:
        logger.error("[SkillEnricher] meta.yaml 저장 실패 (%s): %s", skill_name, e)
        return False


def bulk_enrich_all_skills(
    coding_engine: Optional[str] = None,
    max_skills: int = 5,
    quality_threshold: float = QUALITY_THRESHOLD,
) -> list[str]:
    """
    전체 스킬 중 메타데이터가 미비한 상위 max_skills개를 자동 보강.

    Returns:
        보강된 skill_id 목록
    """
    from core.skill_registry import get_global_registry, ensure_skills_loaded

    ensure_skills_loaded()
    registry = get_global_registry()
    all_skills = registry.get_all()

    # 품질 점수 계산 및 정렬 (낮은 순)
    scored = []
    for skill_id, meta in all_skills.items():
        if not meta.source_path:
            continue
        score = meta_quality_score(meta)
        if score < quality_threshold:
            scored.append((score, skill_id, meta))

    scored.sort(key=lambda x: x[0])  # 낮은 품질 먼저
    targets = scored[:max_skills]

    enriched_ids = []
    for score, skill_id, meta in targets:
        skill_dir = os.path.dirname(meta.source_path) if meta.source_path else ""
        if not skill_dir or not os.path.isdir(skill_dir):
            continue

        logger.info(
            "[BulkEnrich] %s (현재 품질: %.2f)", skill_id, score
        )
        ok = enrich_skill_metadata(skill_dir, coding_engine=coding_engine, force=True)
        if ok:
            enriched_ids.append(skill_id)

    return enriched_ids
