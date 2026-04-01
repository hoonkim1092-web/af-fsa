"""
core/control/issue_context.py
==============================
IssueContextManager — file-backed issue context 관리.

유지보수 요청을 추적 가능한 issue 단위로 묶는다.
외부 tracker(GitHub/Jira)는 optional adapter로 연결한다.

저장 경로: {workspace}/.af_runtime/control/issue_context.json
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any

_CONTROL_DIR = os.path.join(".af_runtime", "control")
_CONTEXT_FILE = "issue_context.json"


@dataclass
class IssueContext:
    issue_id: str                          # "local-{YYYYMMDD}-{seq:03d}" 또는 "gh-{number}"
    work_kind: str                         # "new_project" | "maintenance" | "bugfix" | ...
    issue_kind: str                        # "incident" | "planned_update" | ...
    title: str
    workspace: str
    risk_level: str = "normal"             # "low" | "normal" | "high" | "critical"
    source: str = "user_request"           # "user_request" | "github_issue" | "hook_event" | "supervisor_retry"
    parent_issue_id: str = ""              # 상위 이슈 연결 (regression → 원본 bugfix)
    affected_files: list[str] = field(default_factory=list)    # ChangeImpactProfiler 결과
    affected_modules: list[str] = field(default_factory=list)  # 영향받는 module_id 목록
    external_url: str = ""                 # GitHub/Jira 이슈 URL
    created_at: str = ""
    updated_at: str = ""
    status: str = "open"                   # "open" | "in_progress" | "closed"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IssueContext":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


class IssueContextManager:
    """file-backed issue context 관리. 외부 tracker는 adapter로 연결."""

    def __init__(self, workspace: str):
        self._workspace = workspace
        self._control_dir = os.path.join(workspace, _CONTROL_DIR)

    # ── Public API ──

    def bind(self, work_kind: str, issue_kind: str, title: str,
             risk_level: str = "normal",
             source: str = "user_request",
             affected_files: list[str] | None = None,
             affected_modules: list[str] | None = None,
             parent_issue_id: str = "",
             metadata: dict | None = None) -> IssueContext:
        """새 IssueContext를 생성하거나 기존 open 이슈에 연결한다."""
        from core.utils import now_iso

        # 기존 open 이슈가 있으면 업데이트
        existing = self._load_current()
        if existing and existing.status == "open" and existing.work_kind == work_kind:
            existing.updated_at = now_iso()
            if affected_files:
                existing.affected_files = affected_files
            if affected_modules:
                existing.affected_modules = affected_modules
            self._save(existing)
            return existing

        # 새 이슈 생성
        issue_id = self._generate_id()
        ctx = IssueContext(
            issue_id=issue_id,
            work_kind=work_kind,
            issue_kind=issue_kind,
            title=title,
            workspace=self._workspace,
            risk_level=risk_level,
            source=source,
            parent_issue_id=parent_issue_id,
            affected_files=affected_files or [],
            affected_modules=affected_modules or [],
            created_at=now_iso(),
            updated_at=now_iso(),
            status="open",
            metadata=metadata or {},
        )
        self._save(ctx)
        return ctx

    def get_current(self) -> IssueContext | None:
        """현재 workspace의 issue_context.json을 반환한다."""
        return self._load_current()

    def get_active(self) -> list[IssueContext]:
        """status가 open 또는 in_progress인 이슈 목록."""
        ctx = self._load_current()
        if ctx and ctx.status in ("open", "in_progress"):
            return [ctx]
        return []

    def update_status(self, issue_id: str, status: str) -> bool:
        """이슈 상태를 업데이트한다. Returns True if found."""
        from core.utils import now_iso
        ctx = self._load_current()
        if ctx and ctx.issue_id == issue_id:
            ctx.status = status
            ctx.updated_at = now_iso()
            self._save(ctx)
            return True
        return False

    def link_external(self, issue_id: str, external_url: str) -> bool:
        """GitHub/Jira 이슈 URL을 등록한다."""
        from core.utils import now_iso
        ctx = self._load_current()
        if ctx and ctx.issue_id == issue_id:
            ctx.external_url = external_url
            ctx.updated_at = now_iso()
            self._save(ctx)
            return True
        return False

    def update_impact(self, issue_id: str,
                      affected_files: list[str],
                      affected_modules: list[str]) -> bool:
        """ChangeImpactProfiler 결과를 이슈에 반영한다."""
        from core.utils import now_iso
        ctx = self._load_current()
        if ctx and ctx.issue_id == issue_id:
            ctx.affected_files = affected_files
            ctx.affected_modules = affected_modules
            ctx.updated_at = now_iso()
            self._save(ctx)
            return True
        return False

    # ── 내부 메서드 ──

    def _context_path(self) -> str:
        return os.path.join(self._control_dir, _CONTEXT_FILE)

    def _ensure_dir(self) -> None:
        os.makedirs(self._control_dir, exist_ok=True)

    def _load_current(self) -> IssueContext | None:
        path = self._context_path()
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return IssueContext.from_dict(json.load(f))
        except Exception:
            return None

    def _save(self, ctx: IssueContext) -> None:
        self._ensure_dir()
        path = self._context_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ctx.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[IssueContextManager] save failed: {exc}")

    def _generate_id(self) -> str:
        """local-YYYYMMDD-NNN-XXXXXX 형식의 이슈 ID를 생성한다.

        BUG-8 Fix: seq만으로는 동일 날짜 내 race condition 시 중복 가능.
        UUID 앞 6자리를 suffix로 추가해 충돌을 원천 차단.
        """
        import uuid
        from core.utils import now_iso
        date_part = now_iso()[:10].replace("-", "")
        seq = 1
        ledger_path = os.path.join(self._control_dir, "run_ledger.jsonl")
        if os.path.isfile(ledger_path):
            try:
                with open(ledger_path, encoding="utf-8") as f:
                    seq = sum(1 for line in f if date_part in line) + 1
            except Exception:
                pass
        uid = str(uuid.uuid4()).replace("-", "")[:6]
        return f"local-{date_part}-{seq:03d}-{uid}"


__all__ = ["IssueContext", "IssueContextManager"]
