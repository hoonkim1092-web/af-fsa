"""
Phase 15 — Project Lifecycle + Issue Tracker 테스트.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.memory_system.project_lifecycle import ProjectLifecycleManager, ProjectState
from core.memory_system.issue_tracker import (
    GitHubIssuesAdapter,
    Issue,
    IssueTrackerAdapter,
    JiraAdapter,
)


def run(coro):
    return asyncio.run(coro)


# ── ProjectLifecycleManager ───────────────────────────────────────────

class TestProjectLifecycle:
    def test_initial_state(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        assert mgr.state == ProjectState.CREATED

    def test_valid_transition(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        assert mgr.transition(ProjectState.ACTIVE, "project started")
        assert mgr.state == ProjectState.ACTIVE

    def test_invalid_transition(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        # CREATED → ARCHIVED is not allowed
        assert not mgr.transition(ProjectState.ARCHIVED)
        assert mgr.state == ProjectState.CREATED

    def test_full_lifecycle(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        assert mgr.transition(ProjectState.ACTIVE)
        assert mgr.transition(ProjectState.MAINTAINING)
        assert mgr.transition(ProjectState.ARCHIVED)
        assert mgr.state == ProjectState.ARCHIVED

    def test_pause_and_resume(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        mgr.transition(ProjectState.ACTIVE)
        mgr.transition(ProjectState.PAUSED)
        assert mgr.state == ProjectState.PAUSED
        mgr.transition(ProjectState.ACTIVE)
        assert mgr.state == ProjectState.ACTIVE

    def test_history_tracked(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        mgr.transition(ProjectState.ACTIVE, "start")
        mgr.transition(ProjectState.MAINTAINING, "stable release")
        history = mgr.history
        assert len(history) == 2
        assert history[0]["from"] == "created"
        assert history[0]["to"] == "active"
        assert history[1]["reason"] == "stable release"

    def test_persistence(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        mgr.transition(ProjectState.ACTIVE)
        mgr.transition(ProjectState.MAINTAINING)

        # Reload
        mgr2 = ProjectLifecycleManager(workspace=str(tmp_path))
        assert mgr2.state == ProjectState.MAINTAINING
        assert len(mgr2.history) == 2

    def test_is_maintaining(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        mgr.transition(ProjectState.ACTIVE)
        assert not mgr.is_maintaining()
        mgr.transition(ProjectState.MAINTAINING)
        assert mgr.is_maintaining()

    def test_metadata(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        mgr.set_metadata("version", "2.0.0")
        assert mgr.get_metadata("version") == "2.0.0"
        assert mgr.get_metadata("missing", "default") == "default"

        # Persists
        mgr2 = ProjectLifecycleManager(workspace=str(tmp_path))
        assert mgr2.get_metadata("version") == "2.0.0"

    def test_can_transition(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        assert mgr.can_transition(ProjectState.ACTIVE)
        assert not mgr.can_transition(ProjectState.ARCHIVED)

    def test_unarchive(self, tmp_path):
        mgr = ProjectLifecycleManager(workspace=str(tmp_path))
        mgr.transition(ProjectState.ACTIVE)
        mgr.transition(ProjectState.MAINTAINING)
        mgr.transition(ProjectState.ARCHIVED)
        assert mgr.transition(ProjectState.ACTIVE, "reactivated")
        assert mgr.state == ProjectState.ACTIVE


# ── GitHubIssuesAdapter ───────────────────────────────────────────────

class TestGitHubIssuesAdapter:
    def test_list_issues_mock(self):
        adapter = GitHubIssuesAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([
            {
                "number": 42,
                "title": "Login bug",
                "body": "Users can't login",
                "state": "OPEN",
                "labels": [{"name": "bug"}],
                "assignees": [{"login": "dev1"}],
                "url": "https://github.com/org/repo/issues/42",
                "createdAt": "2026-03-17T10:00:00Z",
                "closedAt": None,
            }
        ])
        with patch("core.memory_system.issue_tracker.subprocess.run", return_value=mock_result):
            issues = run(adapter.list_issues())
            assert len(issues) == 1
            assert issues[0].issue_id == "42"
            assert issues[0].title == "Login bug"
            assert issues[0].labels == ["bug"]
            assert issues[0].assignee == "dev1"

    def test_get_issue_mock(self):
        adapter = GitHubIssuesAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "number": 42,
            "title": "Login bug",
            "body": "desc",
            "state": "OPEN",
            "labels": [],
            "assignees": [],
            "url": "",
            "createdAt": "",
            "closedAt": None,
        })
        with patch("core.memory_system.issue_tracker.subprocess.run", return_value=mock_result):
            issue = run(adapter.get_issue("42"))
            assert issue is not None
            assert issue.issue_id == "42"

    def test_create_issue_mock(self):
        adapter = GitHubIssuesAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/org/repo/issues/43\n"
        with patch("core.memory_system.issue_tracker.subprocess.run", return_value=mock_result):
            issue = run(adapter.create_issue("New bug", "Description"))
            assert issue is not None
            assert issue.title == "New bug"

    def test_close_issue_mock(self):
        adapter = GitHubIssuesAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("core.memory_system.issue_tracker.subprocess.run", return_value=mock_result):
            assert run(adapter.close_issue("42", "Fixed"))

    def test_list_issues_failure(self):
        adapter = GitHubIssuesAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "auth error"
        with patch("core.memory_system.issue_tracker.subprocess.run", return_value=mock_result):
            issues = run(adapter.list_issues())
            assert issues == []


# ── JiraAdapter ───────────────────────────────────────────────────────

class TestJiraAdapter:
    def test_placeholder_returns_empty(self):
        adapter = JiraAdapter()
        assert run(adapter.list_issues()) == []
        assert run(adapter.get_issue("PROJ-1")) is None
        assert run(adapter.create_issue("title", "body")) is None
        assert run(adapter.close_issue("PROJ-1")) is False
