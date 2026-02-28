import hashlib
import difflib
import os
import time


class HashlineEditor:
    """
    (V2) OMOC-Grade Hashline Editor
    줄 단위 해시 검증 기반 정밀 편집기.
    
    V2 강화 사항:
      1. 멀티라인 블록 편집 (apply_block_edit)
      2. 해시 충돌 방지 (line_num:hash 복합 키)
      3. dry_run 모드
      4. diff 미리보기
      5. 변경 이력 추적
    """

    # ── 편집 이력 (인메모리, 세션 단위) ──
    _edit_history: list[dict] = []

    # =========================================================================
    # Hash Computation
    # =========================================================================
    @staticmethod
    def _compute_line_hash(line: str) -> str:
        """Computes a stable 8-char hex hash for a single line of text."""
        normalized = line.rstrip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]

    # =========================================================================
    # File Reading (with hashes)
    # =========================================================================
    @classmethod
    def format_file_with_hashes(cls, file_path: str) -> str:
        """
        파일을 읽어 라인별 해시를 붙여 반환합니다.
        형식: LINE_NUM#HASH | content
        동일 해시가 2개 이상이면 ⚠ 마커를 표시하여 에이전트에게 주의를 줍니다.
        """
        if not os.path.exists(file_path):
            return f"Error: File {file_path} not found."

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 해시 충돌 탐지
        hash_counts: dict[str, int] = {}
        for line in lines:
            h = cls._compute_line_hash(line)
            hash_counts[h] = hash_counts.get(h, 0) + 1

        formatted = []
        for i, line in enumerate(lines, start=1):
            h = cls._compute_line_hash(line)
            clean = line.rstrip("\n")
            dup_marker = " ⚠DUP" if hash_counts.get(h, 0) > 1 else ""
            formatted.append(f"{i}#{h}{dup_marker} | {clean}")

        return "\n".join(formatted)

    # =========================================================================
    # Hash Lookup (충돌 방지)
    # =========================================================================
    @classmethod
    def find_hash(cls, file_path: str, target_hash: str) -> list[dict]:
        """
        파일에서 target_hash와 일치하는 모든 라인을 반환합니다.
        해시 충돌 시 에이전트가 line_num으로 정확한 대상을 선택할 수 있게 합니다.
        
        Returns: [{"line_num": int, "hash": str, "content": str}, ...]
        """
        if not os.path.exists(file_path):
            return []

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        matches = []
        for i, line in enumerate(lines, start=1):
            if cls._compute_line_hash(line) == target_hash:
                matches.append({
                    "line_num": i,
                    "hash": target_hash,
                    "content": line.rstrip("\n"),
                })
        return matches

    # =========================================================================
    # Internal: resolve target line index
    # =========================================================================
    @classmethod
    def _resolve_target(cls, lines: list[str], target_hash: str,
                        target_line: int | None = None) -> int:
        """
        target_hash로 매칭되는 라인 인덱스(0-based)를 반환.
        해시 충돌 시 target_line (1-based)으로 보강합니다.
        
        Returns: 0-based index, or -1 if not found.
        """
        candidates = []
        for i, line in enumerate(lines):
            if cls._compute_line_hash(line) == target_hash:
                candidates.append(i)

        if not candidates:
            return -1

        # 해시 충돌 없으면 바로 반환
        if len(candidates) == 1:
            return candidates[0]

        # 충돌 발생 → target_line으로 정확 매칭
        if target_line is not None:
            zero_idx = target_line - 1
            if zero_idx in candidates:
                return zero_idx

        # target_line 미제공 시 첫 번째 반환 (하위 호환)
        return candidates[0]

    # =========================================================================
    # Single-Line Edit (V1 호환 + V2 강화)
    # =========================================================================
    @classmethod
    def apply_hashline_edit(
        cls,
        file_path: str,
        target_hash: str,
        new_content: str,
        operation: str = "replace",
        target_line: int | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        해시 기반 단일 라인 편집.
        
        V2 추가 파라미터:
          - target_line: 해시 충돌 시 정확한 라인 지정 (1-based)
          - dry_run: True이면 실제 파일을 변경하지 않고 결과만 반환
        """
        if not os.path.exists(file_path):
            err = f"[HashlineEditor] Error: File {file_path} not found."
            return {"ok": False, "error": err}

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        original_lines = list(lines)  # diff용 복사
        target_idx = cls._resolve_target(lines, target_hash, target_line)

        if target_idx == -1:
            context_hint = cls.format_file_with_hashes(file_path)
            lines_hint = context_hint.split("\n")
            if len(lines_hint) > 30:
                hint_str = "\n".join(
                    lines_hint[:15] + ["... (truncated) ..."] + lines_hint[-15:]
                )
            else:
                hint_str = context_hint
            return {
                "ok": False,
                "error": f"[HashlineEditor] Hash {target_hash} not found in {file_path}.",
                "context": hint_str,
            }

        # Ensure newline
        if new_content and not new_content.endswith("\n"):
            new_content += "\n"

        before_line = lines[target_idx].rstrip("\n") if target_idx < len(lines) else ""

        if operation == "replace":
            lines[target_idx] = new_content
        elif operation == "delete":
            del lines[target_idx]
        elif operation == "insert_before":
            lines.insert(target_idx, new_content)
        elif operation == "insert_after":
            lines.insert(target_idx + 1, new_content)
        else:
            return {"ok": False, "error": f"[HashlineEditor] Unknown operation: {operation}"}

        # Diff 생성
        diff_text = cls._generate_diff(original_lines, lines, file_path)

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "operation": operation,
                "target_hash": target_hash,
                "diff": diff_text,
                "preview": "".join(lines),
            }

        # 실제 쓰기 (원자적)
        cls._atomic_write(file_path, lines)

        # 이력 기록
        cls._record_history(file_path, operation, target_hash,
                            before_line, new_content.rstrip("\n") if new_content else "")

        return {
            "ok": True,
            "message": f"[HashlineEditor] '{operation}' applied via hash {target_hash}.",
            "diff": diff_text,
        }

    # =========================================================================
    # Multi-Line Block Edit (V2 신규)
    # =========================================================================
    @classmethod
    def apply_block_edit(
        cls,
        file_path: str,
        start_hash: str,
        end_hash: str,
        new_block: str,
        start_line: int | None = None,
        end_line: int | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        start_hash ~ end_hash 범위의 라인 블록을 new_block으로 교체합니다.
        함수/클래스 단위 리팩토링에 최적화된 블록 편집 기능.
        
        Args:
            start_hash: 블록 시작 라인의 해시
            end_hash: 블록 끝 라인의 해시
            new_block: 교체할 새 코드 블록 (멀티라인 문자열)
            start_line: 해시 충돌 시 시작 라인 보강 (1-based)
            end_line: 해시 충돌 시 끝 라인 보강 (1-based)
            dry_run: True이면 실제 파일을 변경하지 않음
        """
        if not os.path.exists(file_path):
            return {"ok": False, "error": f"[HashlineEditor] File {file_path} not found."}

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        original_lines = list(lines)

        start_idx = cls._resolve_target(lines, start_hash, start_line)
        end_idx = cls._resolve_target(lines, end_hash, end_line)

        if start_idx == -1:
            return {
                "ok": False,
                "error": f"[HashlineEditor] start_hash {start_hash} not found.",
                "context": cls.format_file_with_hashes(file_path),
            }
        if end_idx == -1:
            return {
                "ok": False,
                "error": f"[HashlineEditor] end_hash {end_hash} not found.",
                "context": cls.format_file_with_hashes(file_path),
            }
        if end_idx < start_idx:
            return {
                "ok": False,
                "error": f"[HashlineEditor] end_hash ({end_idx+1}) is before start_hash ({start_idx+1}).",
            }

        # 블록 교체 준비
        before_block = "".join(lines[start_idx : end_idx + 1]).rstrip("\n")
        block_lines = new_block.split("\n")
        # 각 라인에 줄바꿈 보장
        new_lines = [ln + "\n" if not ln.endswith("\n") else ln for ln in block_lines]
        # 마지막 빈줄 제거
        if new_lines and new_lines[-1].strip() == "" and new_block.rstrip() != "":
            pass  # keep trailing newline

        lines[start_idx : end_idx + 1] = new_lines
        replaced_count = end_idx - start_idx + 1

        diff_text = cls._generate_diff(original_lines, lines, file_path)

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "operation": "block_replace",
                "lines_replaced": replaced_count,
                "lines_inserted": len(new_lines),
                "diff": diff_text,
                "preview": "".join(lines),
            }

        cls._atomic_write(file_path, lines)
        cls._record_history(file_path, "block_replace",
                            f"{start_hash}..{end_hash}",
                            before_block, new_block.rstrip("\n"))

        return {
            "ok": True,
            "message": f"[HashlineEditor] Block replaced: lines {start_idx+1}-{end_idx+1} ({replaced_count} lines → {len(new_lines)} lines).",
            "lines_replaced": replaced_count,
            "lines_inserted": len(new_lines),
            "diff": diff_text,
        }

    # =========================================================================
    # Diff Generation
    # =========================================================================
    @classmethod
    def _generate_diff(cls, before: list[str], after: list[str], filename: str) -> str:
        """unified diff 형식의 차이를 생성합니다."""
        diff = difflib.unified_diff(
            before, after,
            fromfile=f"a/{os.path.basename(filename)}",
            tofile=f"b/{os.path.basename(filename)}",
            lineterm="",
        )
        return "\n".join(diff)

    # =========================================================================
    # Atomic Write
    # =========================================================================
    @staticmethod
    def _atomic_write(file_path: str, lines: list[str]):
        """원자적 파일 쓰기: tmp → os.replace"""
        temp_path = file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(temp_path, file_path)

    # =========================================================================
    # Edit History
    # =========================================================================
    @classmethod
    def _record_history(cls, file_path: str, operation: str,
                        hash_key: str, before: str, after: str):
        """편집 이력을 인메모리 리스트에 기록합니다."""
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "file": file_path,
            "operation": operation,
            "hash": hash_key,
            "before": before[:500],
            "after": after[:500],
        }
        cls._edit_history.append(entry)
        # 최대 100개 유지
        if len(cls._edit_history) > 100:
            cls._edit_history = cls._edit_history[-100:]

    @classmethod
    def get_edit_history(cls, file_path: str | None = None,
                         last_n: int = 20) -> list[dict]:
        """편집 이력을 조회합니다. file_path 지정 시 해당 파일만 필터."""
        history = cls._edit_history
        if file_path:
            history = [h for h in history if h.get("file") == file_path]
        return history[-last_n:]

    @classmethod
    def clear_history(cls):
        """편집 이력을 초기화합니다."""
        cls._edit_history.clear()
