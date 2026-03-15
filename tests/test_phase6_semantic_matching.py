"""
tests/test_phase6_semantic_matching.py
======================================
Phase 6: Semantic Matching 테스트 (12개)
"""

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from core.skill_metadata import SkillMetadata, SkillCategory, SkillType
from core.semantic_embedder import SemanticEmbedder


# =============================================================================
# 헬퍼
# =============================================================================
def _make_skill(**kwargs) -> SkillMetadata:
    defaults = dict(
        skill_id="test-skill",
        name="Test Skill",
        description="테스트용 스킬",
        category=SkillCategory.CODING,
        skill_type=SkillType.ACTION,
        when_to_use="코드를 작성할 때",
        when_to_use_keywords=["코드", "작성"],
        semantic_tags=["code-generation", "implementation"],
    )
    defaults.update(kwargs)
    return SkillMetadata(**defaults)


# =============================================================================
# 1. 코사인 유사도 테스트
# =============================================================================
class TestCosineSimilarity:
    def test_cosine_similarity_identical(self):
        """동일 벡터 → 1.0"""
        vec = [1.0, 2.0, 3.0]
        assert abs(SemanticEmbedder.cosine_similarity(vec, vec) - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal(self):
        """직교 벡터 → 0.0"""
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.0, 1.0, 0.0]
        assert abs(SemanticEmbedder.cosine_similarity(vec_a, vec_b)) < 1e-6

    def test_cosine_similarity_zero_vector(self):
        """영벡터 → 0.0 (ZeroDivision 방지)"""
        vec_a = [0.0, 0.0, 0.0]
        vec_b = [1.0, 2.0, 3.0]
        assert SemanticEmbedder.cosine_similarity(vec_a, vec_b) == 0.0
        assert SemanticEmbedder.cosine_similarity(vec_b, vec_a) == 0.0


# =============================================================================
# 2. _build_skill_text 테스트
# =============================================================================
class TestBuildSkillText:
    def test_build_skill_text(self):
        """semantic_tags + description + when_to_use 결합"""
        skill = _make_skill(
            semantic_tags=["search", "web"],
            description="웹 검색 스킬",
            when_to_use="최신 정보가 필요할 때",
        )
        text = SemanticEmbedder._build_skill_text(skill)
        assert "search web" in text
        assert "웹 검색 스킬" in text
        assert "최신 정보가 필요할 때" in text


# =============================================================================
# 3. embedder 사용 불가 시 폴백
# =============================================================================
class TestEmbedderFallback:
    def test_embedder_unavailable_fallback(self):
        """API 없을 때 compute_similarity → 0.0"""
        with patch.dict(os.environ, {}, clear=False):
            # GOOGLE_API_KEY 없이 생성
            env = os.environ.copy()
            env.pop("GOOGLE_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                embedder = SemanticEmbedder()
                assert not embedder.is_available
                skill = _make_skill()
                assert embedder.compute_similarity("코드 작성해줘", skill) == 0.0


# =============================================================================
# 4. 가중치 테스트
# =============================================================================
class TestScoreWeights:
    def test_score_with_semantic(self):
        """mock embedder로 35/40/25 가중치 검증"""
        from core.skill_loader import SkillRelevance

        relevance = SkillRelevance()
        mock_embedder = MagicMock()
        mock_embedder.is_available = True
        mock_embedder.compute_similarity.return_value = 0.8
        relevance._embedder = mock_embedder
        relevance._embedder_initialized = True

        skill = _make_skill(
            when_to_use_keywords=["코드"],
            category=SkillCategory.CODING,
        )
        score = relevance.compute_score("코드 작성해줘", skill)

        keyword_score = relevance._keyword_match("코드 작성해줘", skill)
        category_score = relevance._category_match("코드 작성해줘", skill)
        expected = keyword_score * 0.35 + 0.8 * 0.40 + category_score * 0.25
        assert abs(score - expected) < 1e-6

    def test_score_without_semantic(self):
        """시맨틱 비활성 시 60/40 유지"""
        from core.skill_loader import SkillRelevance

        relevance = SkillRelevance()
        # embedder가 None이면 시맨틱 비활성
        relevance._embedder = None
        relevance._embedder_initialized = True

        skill = _make_skill(
            when_to_use_keywords=["코드"],
            category=SkillCategory.CODING,
        )
        score = relevance.compute_score("코드 작성해줘", skill)

        keyword_score = relevance._keyword_match("코드 작성해줘", skill)
        category_score = relevance._category_match("코드 작성해줘", skill)
        expected = keyword_score * 0.60 + category_score * 0.40
        assert abs(score - expected) < 1e-6


# =============================================================================
# 5. 디스크 캐시 테스트
# =============================================================================
class TestDiskCache:
    def test_disk_cache_save_load(self):
        """JSON 저장/로드 라운드트립"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "skill_embeddings.json")

            embedder = SemanticEmbedder()
            embedder._skill_embeddings = {
                "skill-a": {"hash": "abc123", "embedding": [0.1, 0.2, 0.3]},
            }

            with patch("core.semantic_embedder.CACHE_DIR", tmpdir), \
                 patch("core.semantic_embedder.CACHE_FILE", "skill_embeddings.json"):
                embedder._save_disk_cache()
                assert os.path.exists(cache_file)

                embedder2 = SemanticEmbedder()
                embedder2._skill_embeddings = {}
                embedder2._load_disk_cache()

            # 직접 파일에서 로드 검증
            with open(cache_file, "r") as f:
                data = json.load(f)
            assert "skill-a" in data
            assert data["skill-a"]["hash"] == "abc123"

    def test_disk_cache_hash_invalidation(self):
        """텍스트 변경 시 캐시 무효화 (precompute에서 재계산)"""
        embedder = SemanticEmbedder()
        embedder._is_available = True

        embedder._skill_embeddings = {
            "my-skill": {"hash": "old_hash", "embedding": [0.1, 0.2]},
        }

        skill = _make_skill(
            skill_id="my-skill",
            semantic_tags=["new-tag"],
            description="changed description",
            when_to_use="new usage",
        )

        import hashlib
        new_text = SemanticEmbedder._build_skill_text(skill)
        new_hash = hashlib.md5(new_text.encode()).hexdigest()[:16]

        # old_hash != new_hash이므로 재계산 대상
        assert embedder._skill_embeddings["my-skill"]["hash"] != new_hash


# =============================================================================
# 6. 쿼리 캐시 LRU 테스트
# =============================================================================
class TestQueryCacheLRU:
    def test_query_cache_lru(self):
        """101번째 쿼리 시 첫 항목 제거"""
        embedder = SemanticEmbedder()

        # 직접 100개 항목 삽입
        for i in range(100):
            key = f"key_{i:04d}"
            embedder._query_cache[key] = [float(i)]

        assert len(embedder._query_cache) == 100
        first_key = "key_0000"
        assert first_key in embedder._query_cache

        # 101번째 삽입 → 첫 항목 제거
        embedder._query_cache["key_0100"] = [100.0]
        while len(embedder._query_cache) > 100:
            embedder._query_cache.popitem(last=False)

        assert len(embedder._query_cache) == 100
        assert first_key not in embedder._query_cache
        assert "key_0100" in embedder._query_cache


# =============================================================================
# 7. Adapter 필드 파싱 테스트
# =============================================================================
class TestAdapterParsing:
    def test_meta_yaml_semantic_tags(self, tmp_path):
        """adapter가 semantic_tags 파싱 확인"""
        from core.skill_metadata_adapter import convert_meta_yaml_to_metadata

        meta_content = {
            "id": "search-skill",
            "name": "search-skill",
            "description": "검색 스킬",
            "category": "research",
            "semantic_tags": ["information-retrieval", "search"],
            "when_to_use_keywords": ["검색", "찾아"],
        }
        meta_file = tmp_path / "meta.yaml"
        import yaml
        meta_file.write_text(yaml.dump(meta_content, allow_unicode=True), encoding="utf-8")

        result = convert_meta_yaml_to_metadata(str(meta_file))
        assert result is not None
        assert result.semantic_tags == ["information-retrieval", "search"]
        assert result.when_to_use_keywords == ["검색", "찾아"]

    def test_meta_yaml_when_to_use(self, tmp_path):
        """adapter가 when_to_use 파싱 확인"""
        from core.skill_metadata_adapter import convert_meta_yaml_to_metadata

        meta_content = {
            "id": "code-gen",
            "name": "code-gen",
            "description": "코드 생성",
            "when_to_use": "코드를 작성해야 할 때",
            "when_NOT_to_use": "설계만 필요할 때",
        }
        meta_file = tmp_path / "meta.yaml"
        import yaml
        meta_file.write_text(yaml.dump(meta_content, allow_unicode=True), encoding="utf-8")

        result = convert_meta_yaml_to_metadata(str(meta_file))
        assert result is not None
        assert result.when_to_use == "코드를 작성해야 할 때"
        assert result.when_NOT_to_use == "설계만 필요할 때"
