"""
core/document_chunker.py
========================
프로젝트 문서를 검색 가능한 청크로 분할.

Markdown 구조를 인식하여 헤딩 기준으로 분할하고,
긴 섹션은 오버랩 있는 고정 크기로 재분할한다.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DocumentChunk:
    """검색 가능한 문서 청크."""
    chunk_id: str
    source_path: str
    content: str
    heading: str = ""
    start_line: int = 0
    end_line: int = 0
    content_hash: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = hashlib.md5(self.content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Markdown 헤딩 패턴
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# 지원 확장자
SUPPORTED_EXTENSIONS = frozenset({".md", ".txt", ".yaml", ".yml", ".json", ".py", ".log"})

# 기본 설정
DEFAULT_CHUNK_SIZE = 800       # 문자 수
DEFAULT_CHUNK_OVERLAP = 100    # 오버랩 문자 수
MIN_CHUNK_SIZE = 50            # 이보다 짧은 청크는 버림


class DocumentChunker:
    """문서를 검색 가능한 청크로 분할."""

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_file(self, file_path: str) -> List[DocumentChunk]:
        """파일 하나를 청크로 분할."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return []

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (OSError, IOError):
            return []

        if not content.strip():
            return []

        if ext == ".md":
            return self._chunk_markdown(file_path, content)
        else:
            return self._chunk_plain(file_path, content)

    def chunk_directory(self, directory: str, extensions: Optional[frozenset] = None) -> List[DocumentChunk]:
        """디렉토리 내 모든 지원 파일을 청크로 분할."""
        exts = extensions or SUPPORTED_EXTENSIONS
        chunks: List[DocumentChunk] = []

        for root, _dirs, files in os.walk(directory):
            # 숨김 디렉토리, __pycache__, node_modules 스킵 (하위 탐색 차단)
            _dirs[:] = [
                d for d in _dirs
                if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")
            ]

            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in exts:
                    fpath = os.path.join(root, fname)
                    chunks.extend(self.chunk_file(fpath))

        return chunks

    # ----- Markdown 인식 청킹 -----

    def _chunk_markdown(self, file_path: str, content: str) -> List[DocumentChunk]:
        """Markdown 파일을 헤딩 기준으로 분할."""
        lines = content.split("\n")
        sections = self._split_by_headings(lines)
        chunks: List[DocumentChunk] = []

        for heading, section_lines, start_line in sections:
            section_text = "\n".join(section_lines)

            if len(section_text) <= self.chunk_size:
                if len(section_text.strip()) >= MIN_CHUNK_SIZE:
                    chunk = DocumentChunk(
                        chunk_id=self._make_chunk_id(file_path, start_line),
                        source_path=file_path,
                        content=section_text,
                        heading=heading,
                        start_line=start_line,
                        end_line=start_line + len(section_lines) - 1,
                        metadata={"type": "markdown", "heading_level": heading.count("#") if heading.startswith("#") else 0, "source_type": "local"},
                    )
                    chunks.append(chunk)
            else:
                # 긴 섹션은 오버랩 청킹
                sub_chunks = self._split_with_overlap(section_text, file_path, heading, start_line)
                chunks.extend(sub_chunks)

        return chunks

    def _split_by_headings(self, lines: list) -> list:
        """라인 리스트를 헤딩 기준으로 섹션 분리. [(heading, lines, start_line), ...]"""
        sections = []
        current_heading = ""
        current_lines: list = []
        current_start = 0

        for i, line in enumerate(lines):
            match = _HEADING_RE.match(line)
            if match:
                # 이전 섹션 저장
                if current_lines:
                    sections.append((current_heading, current_lines, current_start))
                current_heading = line.strip()
                current_lines = [line]
                current_start = i
            else:
                current_lines.append(line)

        # 마지막 섹션
        if current_lines:
            sections.append((current_heading, current_lines, current_start))

        return sections

    # ----- 일반 텍스트 청킹 -----

    def _chunk_plain(self, file_path: str, content: str) -> List[DocumentChunk]:
        """일반 텍스트를 고정 크기 오버랩으로 분할."""
        return self._split_with_overlap(content, file_path, heading="", start_line=0)

    # ----- 공통 오버랩 분할 -----

    def _split_with_overlap(
        self,
        text: str,
        file_path: str,
        heading: str,
        start_line: int,
    ) -> List[DocumentChunk]:
        """텍스트를 chunk_size + overlap으로 분할."""
        chunks: List[DocumentChunk] = []
        step = max(self.chunk_size - self.chunk_overlap, 1)
        pos = 0
        idx = 0

        while pos < len(text):
            end = pos + self.chunk_size
            segment = text[pos:end]

            if len(segment.strip()) >= MIN_CHUNK_SIZE:
                # 대략적인 라인 번호 계산
                line_offset = text[:pos].count("\n")
                chunk = DocumentChunk(
                    chunk_id=self._make_chunk_id(file_path, start_line + line_offset, idx),
                    source_path=file_path,
                    content=segment,
                    heading=heading,
                    start_line=start_line + line_offset,
                    end_line=start_line + line_offset + segment.count("\n"),
                    metadata={"type": os.path.splitext(file_path)[1].lstrip("."), "chunk_index": idx, "source_type": "local"},
                )
                chunks.append(chunk)

            pos += step
            idx += 1

        return chunks

    @staticmethod
    def _make_chunk_id(file_path: str, start_line: int, sub_idx: int = 0) -> str:
        """고유 청크 ID 생성."""
        raw = f"{file_path}:{start_line}:{sub_idx}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]


def make_virtual_chunk(
    content: str,
    title: str,
    source_type: str,
    source_url: str = "",
    weight: float = 1.0,
    verified: bool = True,
) -> DocumentChunk:
    """웹/LLM prior 근거를 DocumentChunk로 변환하는 팩토리.

    source_type:
      "web"       — Tavily로 수집한 웹 페이지 (weight=0.9)
      "llm_prior" — LLM 학습 지식 (weight=0.4, verified=False)
    """
    raw_id = f"{source_type}:{title[:80]}:{content[:80]}"
    chunk_id = hashlib.md5(raw_id.encode()).hexdigest()[:12]
    virtual_path = f"__virtual__/{source_type}/{chunk_id}"
    return DocumentChunk(
        chunk_id=chunk_id,
        source_path=virtual_path,
        content=content,
        heading=title,
        metadata={
            "source_type": source_type,
            "weight": weight,
            "verified": verified,
            "url": source_url,
        },
    )
