"""
core/ingestion_pipeline.py
===========================
프로젝트 문서 자동 인덱싱 파이프라인.

프로젝트 시작 시 docs/, skills/, README 등을 청킹하고
DocumentIndex에 색인한다. 증분 업데이트 지원.
"""
from __future__ import annotations

import os
import time
from typing import List, Optional, Set

from core.document_chunker import DocumentChunker, DocumentChunk, SUPPORTED_EXTENSIONS
from core.document_index import DocumentIndex


# ---------------------------------------------------------------------------
# 기본 스캔 대상
# ---------------------------------------------------------------------------
DEFAULT_SCAN_DIRS = ["docs", "skills", "agents", "artifacts"]
DEFAULT_SCAN_FILES = ["README.md", "AGENTS.md", "CLAUDE.md", "SYNC_GUIDE.md"]

# 무시할 디렉토리
IGNORE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".system_generated",
    ".tmp_smoke_projects", "af_claude_smoke", "runs",
})

# 인덱싱 간격 (초) — 같은 세션 내 중복 인덱싱 방지
MIN_REINDEX_INTERVAL = 60


class IngestionPipeline:
    """프로젝트 문서 인덱싱 파이프라인."""

    def __init__(
        self,
        project_root: Optional[str] = None,
        chunker: Optional[DocumentChunker] = None,
        index: Optional[DocumentIndex] = None,
    ):
        self.project_root = project_root or os.getcwd()
        self.chunker = chunker or DocumentChunker()
        self.index = index or DocumentIndex()
        self._last_run: float = 0.0

    def run(self, force: bool = False) -> dict:
        """인덱싱 실행.

        Returns:
            {"ok": bool, "chunks_indexed": int, "files_scanned": int, "elapsed_ms": int}
        """
        now = time.time()
        if not force and (now - self._last_run) < MIN_REINDEX_INTERVAL:
            return {
                "ok": True,
                "chunks_indexed": self.index.chunk_count,
                "files_scanned": 0,
                "elapsed_ms": 0,
                "skipped": True,
            }

        start = time.time()

        # 1. 디스크 캐시 로드
        self.index.load_cache()

        # 2. 파일 수집
        files = self._collect_files()

        # 3. 청킹
        all_chunks: List[DocumentChunk] = []
        for fpath in files:
            chunks = self.chunker.chunk_file(fpath)
            all_chunks.extend(chunks)

        # 4. 증분 인덱싱
        self.index.index_chunks(all_chunks, incremental=True)

        # 5. stale 청크 제거
        valid_paths = set(files)
        self.index.remove_stale(valid_paths)

        # 6. 캐시 저장
        self.index.save_cache()

        self._last_run = time.time()
        elapsed = int((self._last_run - start) * 1000)

        return {
            "ok": True,
            "chunks_indexed": self.index.chunk_count,
            "files_scanned": len(files),
            "elapsed_ms": elapsed,
            "skipped": False,
        }

    def search(self, query: str, top_k: int = 10, filters: Optional[dict] = None):
        """인덱스 검색 (인덱싱 안 됐으면 자동 실행)."""
        if self.index.chunk_count == 0:
            self.run(force=True)
        return self.index.search(query, top_k=top_k, filters=filters)

    def _collect_files(self) -> List[str]:
        """인덱싱 대상 파일 목록 수집."""
        files: List[str] = []

        # 루트 파일
        for fname in DEFAULT_SCAN_FILES:
            fpath = os.path.join(self.project_root, fname)
            if os.path.isfile(fpath):
                files.append(fpath)

        # 스캔 디렉토리
        for dirname in DEFAULT_SCAN_DIRS:
            dirpath = os.path.join(self.project_root, dirname)
            if not os.path.isdir(dirpath):
                continue
            for root, dirs, fnames in os.walk(dirpath):
                # 무시 디렉토리 필터링
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

                for fname in sorted(fnames):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in SUPPORTED_EXTENSIONS:
                        files.append(os.path.join(root, fname))

        return files
