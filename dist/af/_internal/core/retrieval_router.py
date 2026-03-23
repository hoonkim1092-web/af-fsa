"""
core/retrieval_router.py
========================
검색 요청을 분류하여 최적의 검색 전략을 결정하는 라우터.

Phase 1: Retrieval Integration Architecture
- 코드/설정 → direct_local (Glob/Grep)
- 스킬 후보 → semantic_skill (SemanticEmbedder)
- 문서/가이드 → document_rag (DocumentIndex 하이브리드 검색)
- 최신 정보 → live_web (향후)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.document_index import SearchResult


class RetrievalStrategy(Enum):
    DIRECT_LOCAL = "direct_local"
    SEMANTIC_SKILL = "semantic_skill"
    DOCUMENT_RAG = "document_rag"
    LIVE_WEB = "live_web"


@dataclass
class RetrievalPlan:
    """검색 전략 계획."""
    strategies: List[RetrievalStrategy]
    primary: RetrievalStrategy
    filters: Dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# 키워드 사전
# ---------------------------------------------------------------------------
_CODE_KEYWORDS = frozenset({
    "import", "function", "class", "def ", "return", "파일", "경로",
    ".py", ".yaml", ".json", ".ts", ".js", "config", "설정",
    "코드", "구현", "함수", "모듈", "패키지", "변수",
})

_DOC_KEYWORDS = frozenset({
    "설명", "가이드", "문서", "readme", "방법", "사용법", "튜토리얼",
    "howto", "guide", "docs", "manual", "위키", "wiki",
    "아키텍처", "설계", "architecture", "design",
})

_WEB_KEYWORDS = frozenset({
    "최신", "latest", "2026", "2025", "업데이트", "update", "뉴스",
    "트렌드", "trend", "현재", "current", "릴리스", "release",
})

_SKILL_KEYWORDS = frozenset({
    "스킬", "skill", "도구", "tool", "기능", "capability",
    "설치", "install", "빌드", "build", "생성", "create",
    "추천", "recommend", "후보", "candidate",
})

# 파일 경로 패턴
_PATH_PATTERN = re.compile(
    r"[a-zA-Z_/\\][\w/\\.-]*\.(py|yaml|json|md|ts|js|tsx|jsx)"
)


class RetrievalRouter:
    """요청을 분석하여 최적의 검색 전략을 결정."""

    def __init__(self):
        self._pipeline = None  # lazy init IngestionPipeline

    def classify(self, query: str, context: Optional[dict] = None) -> RetrievalPlan:
        """
        쿼리를 분석하여 검색 전략을 결정한다.

        Returns:
            RetrievalPlan: 전략 목록 (우선순위 순) + 필터
        """
        q = query.lower()
        scores: Dict[RetrievalStrategy, float] = {
            RetrievalStrategy.DIRECT_LOCAL: 0.0,
            RetrievalStrategy.SEMANTIC_SKILL: 0.0,
            RetrievalStrategy.DOCUMENT_RAG: 0.0,
            RetrievalStrategy.LIVE_WEB: 0.0,
        }

        # 파일 경로가 포함되어 있으면 direct_local 강력 부스트
        if _PATH_PATTERN.search(query):
            scores[RetrievalStrategy.DIRECT_LOCAL] += 3.0

        # 키워드 매칭
        for kw in _CODE_KEYWORDS:
            if kw in q:
                scores[RetrievalStrategy.DIRECT_LOCAL] += 1.0

        for kw in _DOC_KEYWORDS:
            if kw in q:
                scores[RetrievalStrategy.DOCUMENT_RAG] += 1.0

        for kw in _WEB_KEYWORDS:
            if kw in q:
                scores[RetrievalStrategy.LIVE_WEB] += 1.0

        for kw in _SKILL_KEYWORDS:
            if kw in q:
                scores[RetrievalStrategy.SEMANTIC_SKILL] += 1.0

        # 컨텍스트 기반 보정
        if context:
            phase = context.get("phase", "")
            if phase in ("research", "procure"):
                scores[RetrievalStrategy.SEMANTIC_SKILL] += 2.0
            elif phase in ("build", "implement"):
                scores[RetrievalStrategy.DIRECT_LOCAL] += 1.5
                scores[RetrievalStrategy.DOCUMENT_RAG] += 1.0

        # 기본값: 스킬 검색이 아무 점수도 없으면 최소 보장
        if all(v == 0.0 for v in scores.values()):
            scores[RetrievalStrategy.SEMANTIC_SKILL] = 1.0
            scores[RetrievalStrategy.DIRECT_LOCAL] = 0.5

        # 정렬
        sorted_strategies = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        ordered = [s for s, sc in sorted_strategies if sc > 0]
        if not ordered:
            ordered = [RetrievalStrategy.SEMANTIC_SKILL]

        primary = ordered[0]
        top_score = scores[primary]
        confidence = min(top_score / max(sum(scores.values()), 1.0), 1.0)

        return RetrievalPlan(
            strategies=ordered,
            primary=primary,
            filters=self._build_filters(context),
            confidence=confidence,
        )

    def retrieve_documents(
        self,
        query: str,
        top_k: int = 5,
        context: Optional[dict] = None,
    ) -> List["SearchResult"]:
        """DOCUMENT_RAG 전략으로 문서 검색.

        IngestionPipeline을 lazy 초기화하여 프로젝트 문서를 검색한다.
        """
        if self._pipeline is None:
            try:
                from core.ingestion_pipeline import IngestionPipeline
                self._pipeline = IngestionPipeline()
                self._pipeline.run()
            except Exception:
                return []

        filters = self._build_filters(context) if context else None
        return self._pipeline.search(query, top_k=top_k, filters=filters)

    @staticmethod
    def _build_filters(context: Optional[dict]) -> Dict[str, str]:
        """컨텍스트에서 메타데이터 필터를 추출."""
        if not context:
            return {}
        filters = {}
        if context.get("role"):
            filters["role"] = str(context["role"])
        if context.get("category"):
            filters["category"] = str(context["category"])
        return filters
