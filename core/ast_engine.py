"""
core/ast_engine.py
==================
AST 기반 구조적 코드 검색 + 변환 엔진 — Agent Factory 코어 내장 모듈.

ast-grep-py 라이브러리를 래핑하여 에이전트가 코드를 *구조적*으로 이해하고 수정합니다:
  - search(pattern, code, lang)          → 매칭 결과 리스트
  - replace(pattern, replacement, code)  → 치환된 코드 문자열
  - search_file(pattern, file_path)      → 파일에서 구조 검색
  - search_dir(pattern, directory, ext)  → 디렉토리 전체 검색
  - replace_file(pattern, replacement, file_path) → 파일 구조 치환

의존성: pip install ast-grep-py
"""

import os
from typing import Any

try:
    from ast_grep_py import SgRoot
    _AST_GREP_AVAILABLE = True
except ImportError:
    _AST_GREP_AVAILABLE = False


# =============================================================================
# Language Detection
# =============================================================================
_EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin",
    ".lua": "lua",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def detect_lang(file_path: str) -> str:
    """파일 확장자로 언어를 감지합니다."""
    _, ext = os.path.splitext(file_path.lower())
    return _EXT_TO_LANG.get(ext, "python")


def _ensure_available():
    if not _AST_GREP_AVAILABLE:
        raise ImportError(
            "ast-grep-py is not installed. Install with: pip install ast-grep-py"
        )


# =============================================================================
# Core Search / Replace
# =============================================================================
def search(pattern: str, code: str, lang: str = "python") -> list[dict]:
    """
    AST 구조 패턴으로 코드를 검색합니다.

    Args:
        pattern: 구조 패턴 (예: "print($A)")
        code: 소스 코드 문자열
        lang: 언어 이름 (python, javascript, typescript, ...)

    Returns:
        매칭 결과 리스트:
        [{"text": str, "line": int, "col": int, "end_line": int, "end_col": int}, ...]
    """
    _ensure_available()
    root = SgRoot(code, lang)
    node = root.root()
    matches = node.find_all(pattern=pattern)
    results = []
    for m in matches:
        rng = m.range()
        results.append({
            "text": m.text(),
            "line": rng.start.line,
            "col": rng.start.column,
            "end_line": rng.end.line,
            "end_col": rng.end.column,
        })
    return results


def replace(pattern: str, replacement: str, code: str, lang: str = "python") -> str:
    """
    AST 구조 패턴으로 코드를 일괄 치환합니다.

    Args:
        pattern: 검색 패턴 (예: "print($A)")
        replacement: 치환 패턴 (예: "logger.info($A)")
        code: 소스 코드 문자열
        lang: 언어 이름

    Returns:
        치환된 코드 문자열
    """
    _ensure_available()
    import re as _re
    root = SgRoot(code, lang)
    node = root.root()
    matches = list(node.find_all(pattern=pattern))

    if not matches:
        return code

    # Extract metavar names from the replacement pattern (e.g., $A, $FUNC)
    metavar_names = _re.findall(r'\$([A-Z_][A-Z0-9_]*)', replacement)

    # Build edits with manual metavar substitution
    edits = []
    for m in matches:
        replaced = replacement
        for var_name in metavar_names:
            matched_node = m.get_match(var_name)
            if matched_node:
                replaced = replaced.replace(f"${var_name}", matched_node.text())
        # Use the Edit API: create edit with the resolved replacement text
        edit = m.replace(replaced)
        edits.append(edit)

    return node.commit_edits(edits)


# =============================================================================
# File-Level Operations
# =============================================================================
def search_file(pattern: str, file_path: str, lang: str | None = None) -> list[dict]:
    """
    파일에서 AST 구조 패턴을 검색합니다.

    Returns:
        매칭 결과 리스트 (file 키 추가)
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return []
    if lang is None:
        lang = detect_lang(abs_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            code = f.read()
    except Exception:
        return []
    results = search(pattern, code, lang)
    for r in results:
        r["file"] = abs_path
    return results


def replace_file(pattern: str, replacement: str, file_path: str, lang: str | None = None, dry_run: bool = False) -> dict:
    """
    파일에서 AST 구조 패턴을 일괄 치환합니다.

    Args:
        dry_run: True이면 파일 수정 없이 결과만 반환

    Returns:
        {"file": str, "matches": int, "preview": str}
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return {"file": abs_path, "matches": 0, "preview": ""}
    if lang is None:
        lang = detect_lang(abs_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            code = f.read()
    except Exception:
        return {"file": abs_path, "matches": 0, "preview": ""}

    matches = search(pattern, code, lang)
    if not matches:
        return {"file": abs_path, "matches": 0, "preview": ""}

    new_code = replace(pattern, replacement, code, lang)

    if not dry_run:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_code)

    return {
        "file": abs_path,
        "matches": len(matches),
        "preview": new_code[:500] + "..." if len(new_code) > 500 else new_code,
    }


def search_dir(pattern: str, directory: str, extensions: list[str] | None = None, lang: str | None = None) -> list[dict]:
    """
    디렉토리 전체에서 AST 구조 패턴을 검색합니다.

    Args:
        pattern: 구조 패턴
        directory: 검색할 디렉토리
        extensions: 파일 확장자 필터 (예: [".py", ".js"]), None이면 모든 지원 언어
        lang: 언어 강제 지정 (None이면 확장자로 자동 감지)

    Returns:
        전체 매칭 결과 리스트
    """
    abs_dir = os.path.abspath(directory)
    if not os.path.isdir(abs_dir):
        return []

    if extensions is None:
        extensions = list(_EXT_TO_LANG.keys())
    extensions_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}

    all_results = []
    for dirpath, _dirnames, filenames in os.walk(abs_dir):
        # Skip hidden and common ignore directories
        base = os.path.basename(dirpath)
        if base.startswith(".") or base in ("node_modules", "__pycache__", ".git", "venv"):
            continue
        for fname in filenames:
            _, ext = os.path.splitext(fname.lower())
            if ext not in extensions_set:
                continue
            fpath = os.path.join(dirpath, fname)
            file_results = search_file(pattern, fpath, lang=lang)
            all_results.extend(file_results)
    return all_results


# =============================================================================
# Convenience: Bulk Replace in Directory
# =============================================================================
def replace_dir(pattern: str, replacement: str, directory: str, extensions: list[str] | None = None, dry_run: bool = True) -> list[dict]:
    """
    디렉토리 전체에서 AST 구조 패턴을 일괄 치환합니다.

    Args:
        dry_run: True(기본)이면 수정 없이 결과만 반환. False면 실제 수정.

    Returns:
        수정된 파일 목록 [{"file": str, "matches": int}, ...]
    """
    abs_dir = os.path.abspath(directory)
    if not os.path.isdir(abs_dir):
        return []

    if extensions is None:
        extensions = list(_EXT_TO_LANG.keys())
    extensions_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}

    results = []
    for dirpath, _dirnames, filenames in os.walk(abs_dir):
        base = os.path.basename(dirpath)
        if base.startswith(".") or base in ("node_modules", "__pycache__", ".git", "venv"):
            continue
        for fname in filenames:
            _, ext = os.path.splitext(fname.lower())
            if ext not in extensions_set:
                continue
            fpath = os.path.join(dirpath, fname)
            result = replace_file(pattern, replacement, fpath, dry_run=dry_run)
            if result["matches"] > 0:
                results.append(result)
    return results
