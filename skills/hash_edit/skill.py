import os
from core.hashline_editor import HashlineEditor

def get_file_with_hashes(path: str) -> str:
    """
    파일의 내용을 라인별 해시와 함께 읽어옵니다. 
    형식: [라인번호]#[해시] | 내용
    수정 시 이 해시값을 target_hash로 사용해야 합니다.
    """
    return HashlineEditor.format_file_with_hashes(path)

def apply_edit(path: str, target_hash: str, new_content: str, operation: str = "replace") -> dict:
    """
    특정 해시값을 가진 라인을 대상으로 편집을 수행합니다.
    operation: 'replace' (교체), 'insert_before' (앞에 삽입), 'insert_after' (뒤에 삽입), 'delete' (삭제)
    """
    return HashlineEditor.apply_hashline_edit(path, target_hash, new_content, operation)
