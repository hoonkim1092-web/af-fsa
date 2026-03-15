"""
Phase 9: Internal Hybrid Retrieval 테스트.

DocumentChunker, DocumentIndex, IngestionPipeline 검증.
"""
import os
import tempfile
import pytest

from core.document_chunker import DocumentChunker, DocumentChunk, SUPPORTED_EXTENSIONS
from core.document_index import DocumentIndex, SearchResult, _SparseIndex, _tokenize
from core.ingestion_pipeline import IngestionPipeline


# ============================================================================
# DocumentChunker 테스트
# ============================================================================
class TestDocumentChunker:
    """문서 청킹 기능 테스트."""

    def setup_method(self):
        self.chunker = DocumentChunker(chunk_size=200, chunk_overlap=50)

    def test_chunk_markdown_by_headings(self):
        """Markdown 파일을 헤딩 기준으로 분할."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Introduction\n\nThis is the intro section with enough content to pass the minimum chunk size threshold for valid chunking.\n\n")
            f.write("## Setup\n\nSetup instructions here with enough content to be a valid chunk for testing purposes and verification.\n\n")
            f.write("## Usage\n\nUsage guide with detailed explanation of features and capabilities for end users of the system.\n")
            fpath = f.name

        try:
            chunks = self.chunker.chunk_file(fpath)
            assert len(chunks) >= 2
            headings = [c.heading for c in chunks]
            assert any("Introduction" in h for h in headings)
            assert any("Setup" in h for h in headings)
        finally:
            os.unlink(fpath)

    def test_chunk_plain_text(self):
        """일반 텍스트 파일을 고정 크기로 분할."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("word " * 200)  # 1000 chars
            fpath = f.name

        try:
            chunks = self.chunker.chunk_file(fpath)
            assert len(chunks) >= 2  # 1000 / (200-50) > 1
        finally:
            os.unlink(fpath)

    def test_empty_file_returns_no_chunks(self):
        """빈 파일은 청크 없음."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("")
            fpath = f.name

        try:
            chunks = self.chunker.chunk_file(fpath)
            assert len(chunks) == 0
        finally:
            os.unlink(fpath)

    def test_unsupported_extension_ignored(self):
        """지원하지 않는 확장자는 무시."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".png", delete=False) as f:
            f.write("not an image")
            fpath = f.name

        try:
            chunks = self.chunker.chunk_file(fpath)
            assert len(chunks) == 0
        finally:
            os.unlink(fpath)

    def test_chunk_has_required_fields(self):
        """청크에 필수 필드가 있는지 확인."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Title\n\nSome content that is long enough to be a valid chunk for testing purposes.\n")
            fpath = f.name

        try:
            chunks = self.chunker.chunk_file(fpath)
            assert len(chunks) >= 1
            chunk = chunks[0]
            assert chunk.chunk_id
            assert chunk.source_path == fpath
            assert chunk.content
            assert chunk.content_hash
        finally:
            os.unlink(fpath)

    def test_long_section_splits_with_overlap(self):
        """긴 섹션은 오버랩 분할."""
        chunker = DocumentChunker(chunk_size=100, chunk_overlap=20)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Long Section\n\n")
            f.write("This is a very long paragraph. " * 20)
            fpath = f.name

        try:
            chunks = chunker.chunk_file(fpath)
            assert len(chunks) >= 3  # 600+ chars / (100-20) = 7+
        finally:
            os.unlink(fpath)

    def test_chunk_directory(self):
        """디렉토리 내 파일을 재귀적으로 청킹."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 파일 생성
            with open(os.path.join(tmpdir, "readme.md"), "w", encoding="utf-8") as f:
                f.write("# README\n\nProject documentation with enough content for chunking.\n")
            sub = os.path.join(tmpdir, "sub")
            os.makedirs(sub)
            with open(os.path.join(sub, "guide.txt"), "w", encoding="utf-8") as f:
                f.write("Guide content that is long enough to be a valid chunk.\n" * 3)

            chunks = self.chunker.chunk_directory(tmpdir)
            assert len(chunks) >= 2
            sources = {c.source_path for c in chunks}
            assert any("readme.md" in s for s in sources)
            assert any("guide.txt" in s for s in sources)


# ============================================================================
# SparseIndex (TF-IDF) 테스트
# ============================================================================
class TestSparseIndex:
    """TF-IDF 기반 sparse 인덱스 테스트."""

    def test_basic_search(self):
        """기본 키워드 검색."""
        idx = _SparseIndex()
        idx.add("doc1", "python machine learning tutorial")
        idx.add("doc2", "javascript web development guide")
        idx.add("doc3", "python data science analysis")

        results = idx.search("python tutorial")
        assert len(results) > 0
        # doc1이 가장 높은 점수 (python + tutorial 둘 다 매칭)
        assert results[0][0] == "doc1"

    def test_korean_search(self):
        """한글 검색."""
        idx = _SparseIndex()
        idx.add("doc1", "시스템 아키텍처 설계 문서")
        idx.add("doc2", "사용자 가이드 매뉴얼")
        idx.add("doc3", "시스템 관리자 설정 가이드")

        results = idx.search("시스템 설계")
        assert len(results) > 0
        assert results[0][0] == "doc1"

    def test_empty_query_returns_nothing(self):
        """빈 쿼리는 빈 결과."""
        idx = _SparseIndex()
        idx.add("doc1", "some content")
        assert idx.search("") == []

    def test_no_match_returns_empty(self):
        """매칭 없으면 빈 결과."""
        idx = _SparseIndex()
        idx.add("doc1", "python tutorial")
        assert idx.search("quantum physics") == []


# ============================================================================
# DocumentIndex (Hybrid) 테스트
# ============================================================================
class TestDocumentIndex:
    """하이브리드 인덱스 테스트."""

    def _make_chunks(self) -> list:
        return [
            DocumentChunk(
                chunk_id="c1", source_path="/docs/readme.md",
                content="Agent Factory는 AI 에이전트 오케스트레이션 플랫폼입니다. 스킬 시스템과 파이프라인을 제공합니다.",
                heading="# Overview",
            ),
            DocumentChunk(
                chunk_id="c2", source_path="/docs/setup.md",
                content="설치 방법: pip install agent-factory 명령어로 설치할 수 있습니다. Python 3.10 이상이 필요합니다.",
                heading="## Installation",
            ),
            DocumentChunk(
                chunk_id="c3", source_path="/docs/api.md",
                content="REST API 엔드포인트 목록. GET /agents, POST /skills, DELETE /runs 등을 지원합니다.",
                heading="## API Reference",
            ),
            DocumentChunk(
                chunk_id="c4", source_path="/skills/web_search/SKILL.md",
                content="웹 검색 스킬은 인터넷에서 최신 정보를 검색합니다. query 파라미터를 받아 결과를 반환합니다.",
                heading="# Web Search Skill",
            ),
        ]

    def test_index_and_search(self):
        """인덱싱 후 검색."""
        idx = DocumentIndex()
        chunks = self._make_chunks()
        idx.index_chunks(chunks)

        assert idx.chunk_count == 4

        results = idx.search("설치 방법")
        assert len(results) > 0
        assert results[0].chunk.chunk_id == "c2"

    def test_sparse_only_fallback(self):
        """Dense 불가 시 sparse-only 폴백."""
        idx = DocumentIndex()
        assert idx.chunk_count == 0

        chunks = self._make_chunks()
        idx.index_chunks(chunks)

        results = idx.search("API 엔드포인트")
        assert len(results) > 0
        # sparse만으로도 c3가 매칭되어야 함
        result_ids = [r.chunk.chunk_id for r in results]
        assert "c3" in result_ids

    def test_incremental_indexing(self):
        """증분 인덱싱 — 변경 없는 청크는 스킵."""
        idx = DocumentIndex()
        chunks = self._make_chunks()

        idx.index_chunks(chunks, incremental=True)
        assert idx.chunk_count == 4

        # 같은 청크 다시 인덱싱 — 카운트 동일
        idx.index_chunks(chunks, incremental=True)
        assert idx.chunk_count == 4

    def test_full_rebuild(self):
        """전체 리빌드."""
        idx = DocumentIndex()
        idx.index_chunks(self._make_chunks(), incremental=False)
        assert idx.chunk_count == 4

        # 2개만으로 리빌드
        idx.index_chunks(self._make_chunks()[:2], incremental=False)
        assert idx.chunk_count == 2

    def test_metadata_filter(self):
        """메타데이터 필터 적용."""
        idx = DocumentIndex()
        chunks = [
            DocumentChunk(
                chunk_id="f1", source_path="/a.md",
                content="developer guide for building applications",
                metadata={"role": "developer"},
            ),
            DocumentChunk(
                chunk_id="f2", source_path="/b.md",
                content="admin guide for system administration",
                metadata={"role": "admin"},
            ),
        ]
        idx.index_chunks(chunks)

        results = idx.search("guide", filters={"role": "admin"})
        assert len(results) == 1
        assert results[0].chunk.chunk_id == "f2"

    def test_remove_stale(self):
        """존재하지 않는 파일의 청크 제거."""
        idx = DocumentIndex()
        idx.index_chunks(self._make_chunks())
        assert idx.chunk_count == 4

        idx.remove_stale({"/docs/readme.md", "/docs/setup.md"})
        # c3, c4는 제거됨
        assert idx.chunk_count == 2

    def test_search_result_fields(self):
        """SearchResult에 점수 필드가 있는지 확인."""
        idx = DocumentIndex()
        idx.index_chunks(self._make_chunks())

        results = idx.search("오케스트레이션")
        assert len(results) > 0
        r = results[0]
        assert isinstance(r.score, float)
        assert isinstance(r.sparse_score, float)
        assert isinstance(r.dense_score, float)
        assert r.score >= 0.0


# ============================================================================
# IngestionPipeline 테스트
# ============================================================================
class TestIngestionPipeline:
    """인덱싱 파이프라인 테스트."""

    def test_pipeline_indexes_directory(self):
        """디렉토리 파일을 자동 인덱싱."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # docs 디렉토리 생성
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "guide.md"), "w", encoding="utf-8") as f:
                f.write("# User Guide\n\nThis is the user guide with enough content for indexing.\n")
            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as f:
                f.write("# Project\n\nProject README with description and details.\n")

            pipeline = IngestionPipeline(project_root=tmpdir)
            result = pipeline.run(force=True)

            assert result["ok"] is True
            assert result["chunks_indexed"] >= 2
            assert result["files_scanned"] >= 2

    def test_pipeline_search(self):
        """파이프라인 검색."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "arch.md"), "w", encoding="utf-8") as f:
                f.write("# Architecture\n\nThe system uses microservices architecture pattern.\n")
            with open(os.path.join(docs_dir, "install.md"), "w", encoding="utf-8") as f:
                f.write("# Installation\n\nRun pip install to set up the environment correctly.\n")

            pipeline = IngestionPipeline(project_root=tmpdir)
            pipeline.run(force=True)

            results = pipeline.search("architecture")
            assert len(results) >= 1
            assert any("arch" in r.chunk.source_path for r in results)

    def test_pipeline_skips_when_recent(self):
        """최근 실행 시 스킵."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = IngestionPipeline(project_root=tmpdir)
            pipeline.run(force=True)

            # 바로 다시 실행 — 스킵
            result = pipeline.run(force=False)
            assert result.get("skipped") is True

    def test_pipeline_incremental(self):
        """증분 인덱싱 — 새 파일 추가 시 반영."""
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "a.md"), "w", encoding="utf-8") as f:
                f.write("# Doc A\n\nFirst document content for testing purposes with enough text to exceed minimum chunk size threshold.\n")

            pipeline = IngestionPipeline(project_root=tmpdir)
            r1 = pipeline.run(force=True)
            count1 = r1["chunks_indexed"]
            assert count1 >= 1

            # 새 파일 추가
            with open(os.path.join(docs_dir, "b.md"), "w", encoding="utf-8") as f:
                f.write("# Doc B\n\nSecond document with completely different content that also exceeds the minimum chunk size threshold.\n")

            # reindex interval 리셋
            pipeline._last_run = 0.0
            r2 = pipeline.run(force=True)
            assert r2["chunks_indexed"] >= count1 + 1


# ============================================================================
# RetrievalRouter + DocumentIndex 통합 테스트
# ============================================================================
class TestRouterDocumentIntegration:
    """RetrievalRouter에서 DOCUMENT_RAG 검색 통합."""

    def test_router_retrieve_documents(self):
        """retrieve_documents로 문서 검색."""
        from core.retrieval_router import RetrievalRouter

        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = os.path.join(tmpdir, "docs")
            os.makedirs(docs_dir)
            with open(os.path.join(docs_dir, "test.md"), "w", encoding="utf-8") as f:
                f.write("# Test Doc\n\nArchitecture design document with detailed information.\n")

            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                router = RetrievalRouter()
                results = router.retrieve_documents("architecture design")
                assert isinstance(results, list)
                # 문서가 인덱싱되어 검색 가능해야 함
                if results:
                    assert hasattr(results[0], "chunk")
                    assert hasattr(results[0], "score")
            finally:
                os.chdir(original_cwd)


# ============================================================================
# 토크나이저 테스트
# ============================================================================
class TestTokenizer:
    """토크나이저 테스트."""

    def test_english_tokens(self):
        tokens = _tokenize("Hello World python_module test123")
        assert "hello" in tokens
        assert "world" in tokens
        assert "python_module" in tokens

    def test_korean_tokens(self):
        tokens = _tokenize("시스템 아키텍처 설계")
        assert "시스템" in tokens
        assert "아키텍처" in tokens
        assert "설계" in tokens

    def test_mixed_tokens(self):
        tokens = _tokenize("Agent Factory 에이전트 플랫폼")
        assert "agent" in tokens
        assert "factory" in tokens
        assert "에이전트" in tokens

    def test_short_tokens_filtered(self):
        """1글자 토큰은 제거."""
        tokens = _tokenize("a b c dd ee")
        assert "a" not in tokens
        assert "dd" in tokens


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
