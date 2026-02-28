from core.hashline_editor import HashlineEditor


class HashlineEditSkill:
    """
    (V2) OMOC-Grade Hashline Edit Skill
    줄 단위 해시 검증 기반 정밀 편집 스킬.
    블록 편집, 해시 충돌 방지, dry_run, diff 미리보기, 변경 이력 지원.
    """
    __skill_id__ = "hashline_edit"

    def format_file(self, ctx: dict, file_path: str) -> str:
        """파일을 해시 포맷으로 읽기. 해시 충돌 시 ⚠DUP 마커 표시."""
        return HashlineEditor.format_file_with_hashes(file_path)

    def find_hash(self, ctx: dict, file_path: str, target_hash: str) -> list:
        """특정 해시를 가진 모든 라인 검색 (충돌 해소용)."""
        return HashlineEditor.find_hash(file_path, target_hash)

    def apply_edit(self, ctx: dict, file_path: str, target_hash: str,
                   new_content: str, operation: str = "replace",
                   target_line: int = None, dry_run: bool = False) -> dict:
        """단일 라인 해시 편집. dry_run, target_line 지원."""
        return HashlineEditor.apply_hashline_edit(
            file_path, target_hash, new_content, operation,
            target_line=target_line, dry_run=dry_run,
        )

    def apply_block_edit(self, ctx: dict, file_path: str,
                         start_hash: str, end_hash: str, new_block: str,
                         start_line: int = None, end_line: int = None,
                         dry_run: bool = False) -> dict:
        """멀티라인 블록 편집. 함수/클래스 단위 리팩토링용."""
        return HashlineEditor.apply_block_edit(
            file_path, start_hash, end_hash, new_block,
            start_line=start_line, end_line=end_line, dry_run=dry_run,
        )

    def get_history(self, ctx: dict, file_path: str = None, last_n: int = 20) -> list:
        """편집 이력 조회."""
        return HashlineEditor.get_edit_history(file_path=file_path, last_n=last_n)
