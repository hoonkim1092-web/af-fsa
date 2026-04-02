"""
IssueTrackerAdapter — ABC + GitHub Issues / Jira 구현.

Phase 15: 이슈 트래커 연동 — 이슈 조회/생성, 해결 시 Knowledge Graph 노드 생성.
"""

from __future__ import annotations

import json
import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Issue:
    issue_id: str = ""
    title: str = ""
    body: str = ""
    state: str = "open"  # open, closed
    labels: list[str] = field(default_factory=list)
    assignee: str = ""
    url: str = ""
    created_at: str = ""
    closed_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class IssueTrackerAdapter(ABC):
    """Abstract base for issue tracker integrations."""

    @abstractmethod
    async def list_issues(
        self,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int = 20,
    ) -> list[Issue]:
        """List issues from the tracker."""

    @abstractmethod
    async def get_issue(self, issue_id: str) -> Issue | None:
        """Get a single issue by ID."""

    @abstractmethod
    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Issue | None:
        """Create a new issue."""

    @abstractmethod
    async def close_issue(self, issue_id: str, comment: str = "") -> bool:
        """Close an issue."""


class GitHubIssuesAdapter(IssueTrackerAdapter):
    """GitHub Issues via `gh` CLI."""

    def __init__(self, repo: str = "") -> None:
        self._repo = repo  # e.g. "owner/repo" or "" for current repo

    @staticmethod
    def _sanitize_arg(value: str) -> str:
        """CLI 인자 주입 방지: 선행 하이픈 제거, 제어 문자 제거."""
        sanitized = value.lstrip("-")
        # 제어 문자 제거 (ord < 32: 탭, 줄바꿈 등 모두 포함)
        sanitized = "".join(ch for ch in sanitized if ord(ch) >= 32)
        return sanitized

    def _gh_cmd(self, *args: str) -> list[str]:
        cmd = ["gh"]
        if self._repo:
            cmd.extend(["-R", self._repo])
        cmd.extend(args)
        return cmd

    async def list_issues(self, state="open", labels=None, limit=20):
        cmd = self._gh_cmd(
            "issue", "list",
            "--state", state,
            "--limit", str(limit),
            "--json", "number,title,body,state,labels,assignees,url,createdAt,closedAt",
        )
        if labels:
            cmd.extend(["--label", ",".join(self._sanitize_arg(str(l)) for l in labels)])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error("gh issue list failed: %s", result.stderr)
                return []
            data = json.loads(result.stdout)
            return [self._parse_gh_issue(item) for item in data]
        except Exception as exc:
            logger.error("GitHubIssuesAdapter.list_issues: %s", exc)
            return []

    async def get_issue(self, issue_id: str):
        issue_id = self._sanitize_arg(str(issue_id))
        cmd = self._gh_cmd(
            "issue", "view", issue_id,
            "--json", "number,title,body,state,labels,assignees,url,createdAt,closedAt",
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return None
            return self._parse_gh_issue(json.loads(result.stdout))
        except Exception:
            return None

    async def create_issue(self, title, body, labels=None):
        title = self._sanitize_arg(str(title))
        body = str(body)  # body는 줄바꿈 허용, 하이픈 제거 불필요 (--body 값으로 전달)
        cmd = self._gh_cmd("issue", "create", "--title", title, "--body", body)
        if labels:
            for lbl in labels:
                cmd.extend(["--label", self._sanitize_arg(str(lbl))])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error("gh issue create failed: %s", result.stderr)
                return None
            # gh returns URL of created issue
            url = result.stdout.strip()
            return Issue(title=title, body=body, url=url, state="open")
        except Exception as exc:
            logger.error("GitHubIssuesAdapter.create_issue: %s", exc)
            return None

    async def close_issue(self, issue_id, comment=""):
        issue_id = self._sanitize_arg(str(issue_id))
        cmd = self._gh_cmd("issue", "close", issue_id)
        if comment:
            cmd.extend(["--comment", str(comment)])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception:
            return False

    def _parse_gh_issue(self, data: dict) -> Issue:
        labels = []
        for lbl in data.get("labels", []):
            if isinstance(lbl, dict):
                labels.append(lbl.get("name", ""))
            else:
                labels.append(str(lbl))
        assignees = data.get("assignees", [])
        assignee = ""
        if assignees:
            a = assignees[0]
            assignee = a.get("login", "") if isinstance(a, dict) else str(a)

        return Issue(
            issue_id=str(data.get("number", "")),
            title=data.get("title", ""),
            body=data.get("body", ""),
            state=data.get("state", "open").lower(),
            labels=labels,
            assignee=assignee,
            url=data.get("url", ""),
            created_at=data.get("createdAt", ""),
            closed_at=data.get("closedAt", ""),
        )


class JiraAdapter(IssueTrackerAdapter):
    """Jira REST API adapter (placeholder — requires jira-python or requests)."""

    def __init__(self, base_url: str = "", project_key: str = "", api_token: str = "") -> None:
        self._base_url = base_url
        self._project_key = project_key
        self._api_token = api_token

    async def list_issues(self, state="open", labels=None, limit=20):
        # TODO: Implement with Jira REST API
        logger.warning("JiraAdapter.list_issues not yet implemented")
        return []

    async def get_issue(self, issue_id):
        logger.warning("JiraAdapter.get_issue not yet implemented")
        return None

    async def create_issue(self, title, body, labels=None):
        logger.warning("JiraAdapter.create_issue not yet implemented")
        return None

    async def close_issue(self, issue_id, comment=""):
        logger.warning("JiraAdapter.close_issue not yet implemented")
        return False
