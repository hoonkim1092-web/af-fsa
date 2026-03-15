"""
core/semantic_embedder.py
=========================
Gemini Embedding API를 활용한 시맨틱 유사도 엔진.

스킬 선택 시 keyword/category 점수에 semantic 유사도를 추가하여
의미 기반 매칭 정확도를 높임. API 미사용 시 graceful fallback.
"""

import hashlib
import json
import math
import os
import threading
from collections import OrderedDict
from typing import Dict, List, Optional

from core.skill_metadata import SkillMetadata

EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIM = 768
CACHE_DIR = ".system_generated/cache"
CACHE_FILE = "skill_embeddings.json"
QUERY_CACHE_MAX = 100


class SemanticEmbedder:
    """Gemini 임베딩 기반 시맨틱 유사도 계산기."""

    def __init__(self):
        self._client = None
        self._is_available = False
        self._skill_embeddings: Dict[str, dict] = {}  # {skill_id: {"hash": str, "embedding": list}}
        self._query_cache: OrderedDict[str, List[float]] = OrderedDict()
        self._lock = threading.Lock()

        self._try_init_api()
        self._load_disk_cache()

    def _try_init_api(self):
        """Gemini API 초기화 시도. 실패 시 is_available=False."""
        try:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                return
            from google import genai
            self._client = genai.Client(api_key=api_key)
            self._is_available = True
        except Exception:
            self._is_available = False

    @property
    def is_available(self) -> bool:
        return self._is_available

    def precompute_skill_embeddings(self, skills: Dict[str, SkillMetadata]):
        """변경된 스킬만 임베딩 재계산. API 불가 시 스킵."""
        if not self._is_available:
            return

        texts_to_embed = {}
        for skill_id, skill in skills.items():
            text = self._build_skill_text(skill)
            text_hash = hashlib.md5(text.encode()).hexdigest()[:16]

            cached = self._skill_embeddings.get(skill_id)
            if cached and cached.get("hash") == text_hash:
                continue
            texts_to_embed[skill_id] = (text, text_hash)

        if not texts_to_embed:
            return

        try:
            texts = [t for t, _ in texts_to_embed.values()]
            embeddings = self._embed_documents(texts)
            if embeddings and len(embeddings) == len(texts):
                for (skill_id, (_, text_hash)), emb in zip(texts_to_embed.items(), embeddings):
                    self._skill_embeddings[skill_id] = {"hash": text_hash, "embedding": emb}
                self._save_disk_cache()
        except Exception:
            pass

    def compute_similarity(self, task_input: str, skill: SkillMetadata) -> float:
        """task_input과 skill 간 코사인 유사도 (0.0~1.0). 실패 시 0.0."""
        if not self._is_available:
            return 0.0

        skill_data = self._skill_embeddings.get(skill.skill_id)
        if not skill_data or not skill_data.get("embedding"):
            return 0.0

        try:
            query_emb = self._get_query_embedding(task_input)
            if not query_emb:
                return 0.0
            sim = self.cosine_similarity(query_emb, skill_data["embedding"])
            return max(0.0, sim)
        except Exception:
            return 0.0

    @staticmethod
    def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """순수 Python 코사인 유사도. numpy 불필요."""
        if len(vec_a) != len(vec_b) or not vec_a:
            return 0.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot / (norm_a * norm_b)

    @staticmethod
    def _build_skill_text(skill: SkillMetadata) -> str:
        """시맨틱 임베딩용 텍스트 결합."""
        parts = []
        if skill.semantic_tags:
            parts.append(" ".join(str(t) for t in skill.semantic_tags if t))
        if skill.description:
            parts.append(str(skill.description))
        if skill.when_to_use:
            parts.append(str(skill.when_to_use))
        return " ".join(parts)

    def _get_query_embedding(self, text: str) -> Optional[List[float]]:
        """쿼리 임베딩 (LRU 캐시 적용)."""
        cache_key = hashlib.md5(text.encode()).hexdigest()[:16]

        with self._lock:
            if cache_key in self._query_cache:
                self._query_cache.move_to_end(cache_key)
                return self._query_cache[cache_key]

        embedding = self._embed_query(text)
        if embedding:
            with self._lock:
                self._query_cache[cache_key] = embedding
                self._query_cache.move_to_end(cache_key)
                while len(self._query_cache) > QUERY_CACHE_MAX:
                    self._query_cache.popitem(last=False)

        return embedding

    def _embed_documents(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Gemini API로 문서 임베딩 생성."""
        if not self._client:
            return None
        try:
            results = []
            for text in texts:
                response = self._client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=text,
                    config={"task_type": "RETRIEVAL_DOCUMENT", "output_dimensionality": EMBEDDING_DIM},
                )
                results.append(response.embeddings[0].values)
            return results
        except Exception:
            return None

    def _embed_query(self, text: str) -> Optional[List[float]]:
        """Gemini API로 쿼리 임베딩 생성."""
        if not self._client:
            return None
        try:
            response = self._client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config={"task_type": "RETRIEVAL_QUERY", "output_dimensionality": EMBEDDING_DIM},
            )
            return response.embeddings[0].values
        except Exception:
            return None

    def _load_disk_cache(self):
        """디스크 캐시에서 스킬 임베딩 로드."""
        cache_path = os.path.join(CACHE_DIR, CACHE_FILE)
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    self._skill_embeddings = json.load(f)
        except Exception:
            self._skill_embeddings = {}

    def _save_disk_cache(self):
        """스킬 임베딩을 디스크에 저장."""
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            cache_path = os.path.join(CACHE_DIR, CACHE_FILE)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(self._skill_embeddings, f, ensure_ascii=False)
        except Exception:
            pass
