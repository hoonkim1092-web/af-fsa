"""
core/document_index.py
======================
Dense + Sparse 하이브리드 문서 인덱스.

- Dense: Gemini Embedding (SemanticEmbedder 인프라 재활용)
- Sparse: TF-IDF 기반 키워드 매칭
- 결합: dense_weight * dense_score + sparse_weight * sparse_score

API 불가 시 sparse-only 폴백으로 동작.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.document_chunker import DocumentChunk

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
INDEX_CACHE_DIR = ".system_generated/cache"
INDEX_CACHE_FILE = "document_index.json"
DENSE_WEIGHT = 0.6
SPARSE_WEIGHT = 0.4
DEFAULT_TOP_K = 10


@dataclass
class SearchResult:
    """검색 결과."""
    chunk: DocumentChunk
    score: float
    dense_score: float = 0.0
    sparse_score: float = 0.0


# ---------------------------------------------------------------------------
# TF-IDF Sparse Index
# ---------------------------------------------------------------------------
class _SparseIndex:
    """메모리 기반 TF-IDF 인덱스."""

    def __init__(self):
        # {term: {chunk_id: tf}} 역인덱스
        self._inverted: Dict[str, Dict[str, float]] = defaultdict(dict)
        # {chunk_id: doc_length}
        self._doc_lengths: Dict[str, int] = {}
        self._total_docs: int = 0
        self._avg_doc_length: float = 0.0

    def add(self, chunk_id: str, text: str):
        """청크를 인덱스에 추가. 이미 존재하면 이전 엔트리를 제거 후 재추가."""
        # Bug fix: 재인덱싱 시 옛 term→chunk_id 매핑 제거
        self.remove(chunk_id)

        tokens = _tokenize(text)
        if not tokens:
            return

        self._doc_lengths[chunk_id] = len(tokens)
        self._total_docs = len(self._doc_lengths)
        self._avg_doc_length = sum(self._doc_lengths.values()) / max(self._total_docs, 1)

        tf_counts = Counter(tokens)
        for term, count in tf_counts.items():
            self._inverted[term][chunk_id] = count / len(tokens)  # normalized TF

    def remove(self, chunk_id: str):
        """청크를 역인덱스에서 제거."""
        if chunk_id not in self._doc_lengths:
            return
        self._doc_lengths.pop(chunk_id, None)
        # 역인덱스에서 해당 chunk_id 제거
        empty_terms = []
        for term, postings in self._inverted.items():
            postings.pop(chunk_id, None)
            if not postings:
                empty_terms.append(term)
        for term in empty_terms:
            del self._inverted[term]
        self._total_docs = len(self._doc_lengths)
        self._avg_doc_length = sum(self._doc_lengths.values()) / max(self._total_docs, 1) if self._doc_lengths else 0.0

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> List[Tuple[str, float]]:
        """BM25-lite 스코어링으로 검색. [(chunk_id, score), ...]"""
        tokens = _tokenize(query)
        if not tokens:
            return []

        scores: Dict[str, float] = defaultdict(float)
        k1, b = 1.2, 0.75

        for term in tokens:
            if term not in self._inverted:
                continue
            postings = self._inverted[term]
            df = len(postings)
            idf = math.log((self._total_docs - df + 0.5) / (df + 0.5) + 1.0)

            for chunk_id, tf in postings.items():
                doc_len = self._doc_lengths.get(chunk_id, 1)
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / max(self._avg_doc_length, 1)))
                scores[chunk_id] += idf * tf_norm

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]

    def clear(self):
        self._inverted.clear()
        self._doc_lengths.clear()
        self._total_docs = 0
        self._avg_doc_length = 0.0


# ---------------------------------------------------------------------------
# Dense Index (Gemini Embedding)
# ---------------------------------------------------------------------------
class _DenseIndex:
    """Gemini Embedding 기반 Dense 인덱스."""

    def __init__(self):
        self._embeddings: Dict[str, List[float]] = {}  # {chunk_id: embedding}
        self._client = None
        self._is_available = False
        self._model = "models/gemini-embedding-001"
        self._dim = 768
        self._try_init()

    def _try_init(self):
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

    def add_batch(self, items: List[Tuple[str, str]], force_update: bool = True):
        """[(chunk_id, text), ...] 배치 임베딩 추가.

        force_update=True: 이미 있는 chunk_id도 재계산 (콘텐츠 변경 대응)
        """
        if not self._is_available or not items:
            return

        try:
            for chunk_id, text in items:
                if not force_update and chunk_id in self._embeddings:
                    continue
                truncated = text[:2000]  # API 입력 제한
                response = self._client.models.embed_content(
                    model=self._model,
                    contents=truncated,
                    config={"task_type": "RETRIEVAL_DOCUMENT", "output_dimensionality": self._dim},
                )
                self._embeddings[chunk_id] = response.embeddings[0].values
        except Exception:
            pass

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> List[Tuple[str, float]]:
        """쿼리와 가장 유사한 청크 반환."""
        if not self._is_available or not self._embeddings:
            return []

        try:
            response = self._client.models.embed_content(
                model=self._model,
                contents=query[:1000],
                config={"task_type": "RETRIEVAL_QUERY", "output_dimensionality": self._dim},
            )
            query_emb = response.embeddings[0].values
        except Exception:
            return []

        scored = []
        for chunk_id, emb in self._embeddings.items():
            sim = _cosine_similarity(query_emb, emb)
            scored.append((chunk_id, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def remove(self, chunk_id: str):
        """청크 임베딩 제거."""
        self._embeddings.pop(chunk_id, None)

    def clear(self):
        self._embeddings.clear()

    def get_embeddings_snapshot(self) -> Dict[str, List[float]]:
        return dict(self._embeddings)

    def load_embeddings(self, data: Dict[str, List[float]]):
        self._embeddings.update(data)


# ---------------------------------------------------------------------------
# Hybrid Document Index
# ---------------------------------------------------------------------------
class DocumentIndex:
    """Dense + Sparse 하이브리드 문서 인덱스."""

    def __init__(self, dense_weight: float = DENSE_WEIGHT, sparse_weight: float = SPARSE_WEIGHT):
        self._sparse = _SparseIndex()
        self._dense = _DenseIndex()
        self._chunks: Dict[str, DocumentChunk] = {}
        self._chunk_hashes: Dict[str, str] = {}  # {chunk_id: content_hash} for freshness
        self._lock = threading.Lock()
        self._dense_weight = dense_weight
        self._sparse_weight = sparse_weight
        self._last_indexed_at: float = 0.0

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def is_dense_available(self) -> bool:
        return self._dense.is_available

    def index_chunks(self, chunks: List[DocumentChunk], incremental: bool = True):
        """청크 리스트를 인덱스에 추가.

        incremental=True: 변경된 청크만 업데이트 (content_hash 비교)
        incremental=False: 전체 리빌드
        """
        with self._lock:
            if not incremental:
                self._sparse.clear()
                self._dense.clear()
                self._chunks.clear()
                self._chunk_hashes.clear()

            new_items: List[Tuple[str, str]] = []

            for chunk in chunks:
                cid = chunk.chunk_id
                # 증분: 해시 변경 없으면 스킵
                if incremental and cid in self._chunk_hashes:
                    if self._chunk_hashes[cid] == chunk.content_hash:
                        continue

                self._chunks[cid] = chunk
                self._chunk_hashes[cid] = chunk.content_hash
                self._sparse.add(cid, chunk.content)
                new_items.append((cid, chunk.content))

            # Dense 배치 인덱싱
            if new_items:
                self._dense.add_batch(new_items)

            self._last_indexed_at = time.time()

    def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        filters: Optional[Dict[str, str]] = None,
    ) -> List[SearchResult]:
        """하이브리드 검색. dense + sparse 점수 결합."""
        with self._lock:
            # Sparse 검색
            sparse_results = self._sparse.search(query, top_k=top_k * 3)
            sparse_map = dict(sparse_results)

            # Dense 검색
            dense_results = self._dense.search(query, top_k=top_k * 3)
            dense_map = dict(dense_results)

            # 후보 집합 통합
            all_ids = set(sparse_map.keys()) | set(dense_map.keys())

            # 점수 정규화 + 결합
            sparse_max = max(sparse_map.values()) if sparse_map else 1.0
            dense_max = max(dense_map.values()) if dense_map else 1.0

            results: List[SearchResult] = []
            for cid in all_ids:
                chunk = self._chunks.get(cid)
                if not chunk:
                    continue

                # 메타데이터 필터
                if filters and not self._match_filters(chunk, filters):
                    continue

                s_score = sparse_map.get(cid, 0.0) / max(sparse_max, 1e-9)
                d_score = dense_map.get(cid, 0.0) / max(dense_max, 1e-9)

                if self._dense.is_available:
                    combined = self._dense_weight * d_score + self._sparse_weight * s_score
                else:
                    combined = s_score  # sparse-only 폴백

                results.append(SearchResult(
                    chunk=chunk,
                    score=combined,
                    dense_score=d_score,
                    sparse_score=s_score,
                ))

            results.sort(key=lambda r: r.score, reverse=True)
            return results[:top_k]

    def remove_stale(self, valid_paths: set):
        """존재하지 않는 파일의 청크를 모든 인덱스에서 제거."""
        with self._lock:
            to_remove = [
                cid for cid, chunk in self._chunks.items()
                if chunk.source_path not in valid_paths
            ]
            for cid in to_remove:
                self._chunks.pop(cid, None)
                self._chunk_hashes.pop(cid, None)
                # Bug fix: sparse/dense 인덱스에서도 제거
                self._sparse.remove(cid)
                self._dense.remove(cid)

    def save_cache(self):
        """인덱스 메타데이터를 디스크에 저장 (임베딩 포함)."""
        try:
            os.makedirs(INDEX_CACHE_DIR, exist_ok=True)
            cache_path = os.path.join(INDEX_CACHE_DIR, INDEX_CACHE_FILE)
            data = {
                "chunk_hashes": self._chunk_hashes,
                "last_indexed_at": self._last_indexed_at,
            }
            # Dense 임베딩도 캐싱
            if self._dense.is_available:
                data["dense_embeddings"] = self._dense.get_embeddings_snapshot()

            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def load_cache(self):
        """디스크 캐시에서 인덱스 메타데이터 복원."""
        cache_path = os.path.join(INDEX_CACHE_DIR, INDEX_CACHE_FILE)
        try:
            if not os.path.exists(cache_path):
                return
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._chunk_hashes = data.get("chunk_hashes", {})
            self._last_indexed_at = data.get("last_indexed_at", 0.0)
            dense_embs = data.get("dense_embeddings", {})
            if dense_embs:
                self._dense.load_embeddings(dense_embs)
        except Exception:
            pass

    @staticmethod
    def _match_filters(chunk: DocumentChunk, filters: Dict[str, str]) -> bool:
        """청크 메타데이터가 필터 조건을 만족하는지 확인."""
        for key, value in filters.items():
            chunk_val = chunk.metadata.get(key, "")
            if str(chunk_val) != value:
                return False
        return True


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------
_TOKEN_RE = None


def _tokenize(text: str) -> List[str]:
    """간단한 토크나이저. 영문 소문자 + 한글 (완성형+자모)."""
    global _TOKEN_RE
    if _TOKEN_RE is None:
        import re
        # 영문+숫자+언더스코어 | 한글 완성형+자모
        _TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\uac00-\ud7af\u3131-\u3163\u314f-\u3163]+")
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= 2]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
