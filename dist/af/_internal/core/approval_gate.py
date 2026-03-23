"""
core/approval_gate.py
=====================
work-item approval-gate.md를 프로그램적으로 관리.

승인 흐름:
  1. prepare() → generate_work_items() 로 approval-gate.md 초기화 (execution_open: false)
  2. 사용자가 문서 검토/편집
  3. approve() 호출 → 현재 문서 해시를 스냅샷에 저장 + execution_open: true
  4. execute() 호출 시 is_execution_open() + check_validity() 통과 필요
  5. 승인 후 문서 변경 감지 시 invalidate() → 재승인 필요
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from core.file_io import write_text
from core.utils import now_iso

GATE_FILENAME = "approval-gate.md"

_DOC_FILES = {
    "feature_plan": "feature-plan.md",
    "feature_spec": "feature-spec.md",
    "bug_fix_spec": "bug-fix-spec.md",
    "implementation_design": "implementation-design.md",
    "implementation_tasks": "implementation-tasks.md",
}


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()[:16]  # 앞 16자리만 저장 (가독성)


def _clean(value: Any) -> str:
    return str(value or "").strip()


class ApprovalGate:
    """work-item 폴더의 approval-gate.md를 읽고 씁니다."""

    def __init__(self, workspace: str, slug: str):
        self.workspace = os.path.abspath(workspace)
        self.slug = slug
        self.work_item_dir = os.path.join(
            self.workspace, "docs", "work-items", slug
        )
        self.gate_path = os.path.join(self.work_item_dir, GATE_FILENAME)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def initialize(self, work_item_id: str = "") -> None:
        """approval-gate.md 최초 생성 (execution_open: false)."""
        os.makedirs(self.work_item_dir, exist_ok=True)
        content = self._render(
            work_item=work_item_id or self.slug,
            approver="",
            status="review_pending",
            snapshots={},
            gate_statuses={},
            execution_open=False,
            review_notes="",
        )
        write_text(self.gate_path, content)

    def approve(self, approver: str = "user") -> bool:
        """
        현재 문서 해시를 스냅샷에 저장하고 execution_open을 true로 설정.
        gate_path가 없으면 False 반환.
        """
        if not os.path.exists(self.gate_path):
            return False
        current = self._parse()
        snapshots = self.compute_snapshots()
        gate_statuses = {key: "approved" for key in _DOC_FILES}
        content = self._render(
            work_item=_clean(current.get("work_item") or self.slug),
            approver=approver,
            status="approved",
            snapshots=snapshots,
            gate_statuses=gate_statuses,
            execution_open=True,
            review_notes=_clean(current.get("review_notes")),
        )
        write_text(self.gate_path, content)
        return True

    def invalidate(self, reason: str = "") -> None:
        """문서 변경 감지 시 호출 — 승인을 무효화한다."""
        if not os.path.exists(self.gate_path):
            return
        current = self._parse()
        note = _clean(current.get("review_notes"))
        if reason:
            note = f"{note}\n[무효화] {now_iso()}: {reason}".strip()
        gate_statuses = {key: "review_pending" for key in _DOC_FILES}
        content = self._render(
            work_item=_clean(current.get("work_item") or self.slug),
            approver="",
            status="review_pending",
            snapshots={},
            gate_statuses=gate_statuses,
            execution_open=False,
            review_notes=note,
        )
        write_text(self.gate_path, content)

    def is_execution_open(self) -> bool:
        """execution_open == true 이고 승인 상태인지 확인."""
        if not os.path.exists(self.gate_path):
            return False
        data = self._parse()
        return bool(data.get("execution_open")) and _clean(data.get("status")) == "approved"

    def check_validity(self) -> tuple[bool, list[str]]:
        """
        승인 스냅샷과 현재 문서 해시를 비교.

        Returns:
            (유효 여부, 변경된 문서 목록)
        """
        if not os.path.exists(self.gate_path):
            return False, ["approval-gate.md not found"]
        data = self._parse()
        snapshots = dict(data.get("snapshots") or {})
        if not snapshots:
            return False, ["no snapshot recorded"]

        # 스냅샷 값이 모두 빈 문자열이면 실제로 해시가 기록되지 않은 것
        if not any(snapshots.values()):
            return False, ["snapshot hashes are empty — no documents were hashed at approval time"]

        changed: list[str] = []
        current = self.compute_snapshots()
        for key, filename in _DOC_FILES.items():
            saved = _clean(snapshots.get(key))
            now_hash = _clean(current.get(key))
            if not saved:
                continue  # 해당 문서 없음 — 선택적
            if saved != now_hash:
                changed.append(filename)
        return len(changed) == 0, changed

    def compute_snapshots(self) -> dict[str, str]:
        """각 work-item 문서의 현재 해시를 계산."""
        result: dict[str, str] = {}
        for key, filename in _DOC_FILES.items():
            path = os.path.join(self.work_item_dir, filename)
            result[key] = _sha256_file(path) if os.path.exists(path) else ""
        return result

    def get_status(self) -> dict[str, Any]:
        """현재 gate 상태를 딕셔너리로 반환."""
        if not os.path.exists(self.gate_path):
            return {"exists": False}
        data = self._parse()
        return {
            "exists": True,
            "status": _clean(data.get("status")),
            "approver": _clean(data.get("approver")),
            "execution_open": bool(data.get("execution_open")),
            "work_item": _clean(data.get("work_item") or self.slug),
            "gate_path": self.gate_path,
            "work_item_dir": self.work_item_dir,
        }

    # ------------------------------------------------------------------
    # 내부: 파싱
    # ------------------------------------------------------------------

    def _parse(self) -> dict[str, Any]:
        """approval-gate.md를 파싱하여 딕셔너리로 반환."""
        try:
            with open(self.gate_path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return {}

        result: dict[str, Any] = {}
        snapshots: dict[str, str] = {}
        gate_statuses: dict[str, str] = {}

        # Metadata 섹션 (work_item, approver, status, last_updated)
        # execution_open 은 Gate Status 섹션에서만 파싱한다
        meta_block = re.search(r"## Metadata\n(.*?)(?=\n##|\Z)", text, re.S)
        if meta_block:
            for line in meta_block.group(1).splitlines():
                m = re.match(r"-\s+(\w+):\s*(.*)", line.strip())
                if m:
                    key, val = m.group(1).strip(), m.group(2).strip()
                    result[key] = val

        # Approved Snapshot 섹션
        snap_block = re.search(r"## Approved Snapshot\n(.*?)(?=\n##|\Z)", text, re.S)
        if snap_block:
            for line in snap_block.group(1).splitlines():
                m = re.match(r"-\s+(\w+)_version:\s*(.*)", line.strip())
                if m:
                    snapshots[m.group(1).strip()] = m.group(2).strip()
        result["snapshots"] = snapshots

        # Gate Status 섹션
        gate_block = re.search(r"## Gate Status\n(.*?)(?=\n##|\Z)", text, re.S)
        if gate_block:
            for line in gate_block.group(1).splitlines():
                m = re.match(r"-\s+(\w+)_status:\s*(.*)", line.strip())
                if m:
                    gate_statuses[m.group(1).strip()] = m.group(2).strip()
                ex = re.match(r"-\s+execution_open:\s*(.*)", line.strip())
                if ex:
                    result["execution_open"] = ex.group(1).strip().lower() in ("true", "yes", "1")
        result["gate_statuses"] = gate_statuses

        # Review Notes 섹션
        notes_block = re.search(r"## Review Notes\n(.*?)(?=\n##|\Z)", text, re.S)
        if notes_block:
            result["review_notes"] = notes_block.group(1).strip()

        return result

    # ------------------------------------------------------------------
    # 내부: 렌더링
    # ------------------------------------------------------------------

    def _render(
        self,
        work_item: str,
        approver: str,
        status: str,
        snapshots: dict[str, str],
        gate_statuses: dict[str, str],
        execution_open: bool,
        review_notes: str,
    ) -> str:
        snap_lines = "\n".join(
            f"- {key}_version: {snapshots.get(key, '')}"
            for key in _DOC_FILES
        )
        gate_lines = "\n".join(
            f"- {key}_status: {gate_statuses.get(key, 'review_pending')}"
            for key in ("feature_plan", "feature_spec", "implementation_design", "implementation_tasks")
        )
        return (
            "# Approval Gate\n"
            "\n"
            "## Metadata\n"
            "\n"
            f"- work_item: {work_item}\n"
            f"- approver: {approver}\n"
            f"- status: {status}\n"
            f"- last_updated: {now_iso()}\n"
            "\n"
            "## Approved Snapshot\n"
            "\n"
            f"{snap_lines}\n"
            "\n"
            "## Gate Status\n"
            "\n"
            f"{gate_lines}\n"
            f"- execution_open: {'true' if execution_open else 'false'}\n"
            "\n"
            "## Review Notes\n"
            "\n"
            f"{review_notes}\n"
            "\n"
            "## Invalidation Rules\n"
            "\n"
            "- 승인 후 문서가 바뀌면 기존 승인은 무효다.\n"
            "- 최신 문서 상태와 승인 스냅샷이 다르면 구현할 수 없다.\n"
        )
