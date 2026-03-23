import os
from core.hashline_editor import HashlineEditor


def get_file_with_hashes(path: str) -> str:
    """
    파일의 내용을 라인별 해시와 함께 읽어옵니다.
    형식: [라인번호]#[해시] | 내용
    해시 충돌 시 ⚠DUP 마커가 표시됩니다. 이 경우 target_line을 함께 지정하세요.
    """
    return HashlineEditor.format_file_with_hashes(path)


def find_hash(path: str, target_hash: str) -> list:
    """
    파일에서 특정 해시를 가진 모든 라인을 검색합니다.
    해시 충돌 시 정확한 라인을 선택하기 위해 사용합니다.
    Returns: [{"line_num": int, "hash": str, "content": str}, ...]
    """
    return HashlineEditor.find_hash(path, target_hash)


def apply_edit(
    path: str,
    target_hash: str,
    new_content: str,
    operation: str = "replace",
    target_line: int = None,
    dry_run: bool = False,
) -> dict:
    """
    특정 해시값을 가진 라인을 대상으로 편집을 수행합니다.
    operation: 'replace' (교체), 'insert_before' (앞에 삽입), 'insert_after' (뒤에 삽입), 'delete' (삭제)
    target_line: 해시 충돌(⚠DUP) 시 정확한 라인 번호 지정 (1-based)
    dry_run: True이면 실제 파일을 변경하지 않고 diff만 반환
    """
    return HashlineEditor.apply_hashline_edit(
        path, target_hash, new_content, operation,
        target_line=target_line, dry_run=dry_run,
    )


def apply_block_edit(
    path: str,
    start_hash: str,
    end_hash: str,
    new_block: str,
    start_line: int = None,
    end_line: int = None,
    dry_run: bool = False,
) -> dict:
    """
    start_hash ~ end_hash 범위의 라인 블록을 new_block으로 교체합니다.
    함수/클래스 단위 리팩토링에 최적화된 블록 편집 기능.
    dry_run: True이면 실제 파일을 변경하지 않고 diff만 반환
    """
    return HashlineEditor.apply_block_edit(
        path, start_hash, end_hash, new_block,
        start_line=start_line, end_line=end_line, dry_run=dry_run,
    )


def get_edit_history(path: str = None, last_n: int = 20) -> list:
    """
    편집 이력을 조회합니다.
    path 지정 시 해당 파일만 필터링됩니다.
    """
    return HashlineEditor.get_edit_history(file_path=path, last_n=last_n)
