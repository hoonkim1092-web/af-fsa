"""
GraphBuilder — 실패→성공 에피소드 쌍에서 Problem→Cause→Solution 삼중 추출.

Phase 13: 실패 에피소드 E1 + 성공 에피소드 E2 (같은 task)로부터
지식 그래프 삼중(triple)을 자동 생성.
"""

from __future__ import annotations

import logging
from typing import Any

from core.memory_system.models import (
    EpisodeRecord,
    KnowledgeEdge,
    KnowledgeNode,
    EdgeType,
    NodeType,
)

logger = logging.getLogger(__name__)


def extract_triple(
    failure: EpisodeRecord,
    success: EpisodeRecord,
) -> tuple[KnowledgeNode, KnowledgeNode, KnowledgeNode, list[KnowledgeEdge]]:
    """
    Extract Problem→Cause→Solution triple from a failure+success pair.

    Returns (problem_node, cause_node, solution_node, edges).
    """
    # Problem node: what was the task?
    problem = KnowledgeNode(
        node_type=NodeType.PROBLEM,
        label=_truncate(failure.task_input, 150),
        description=failure.task_input,
        project_id=failure.project_id or None,
        metadata={
            "source_episode": failure.episode_id,
            "agent": failure.agent_name,
        },
    )

    # Cause node: why did it fail?
    cause_desc = failure.error_info or "Unknown error"
    cause = KnowledgeNode(
        node_type=NodeType.CAUSE,
        label=_truncate(cause_desc, 150),
        description=cause_desc,
        project_id=failure.project_id or None,
        metadata={
            "source_episode": failure.episode_id,
            "failed_actions": [a.get("skill_name", "?") for a in failure.actions[:10]],
        },
    )

    # Solution node: what changed between failure and success?
    diff = _diff_actions(failure.actions, success.actions)
    solution = KnowledgeNode(
        node_type=NodeType.SOLUTION,
        label=_truncate(diff, 150),
        description=diff,
        project_id=success.project_id or None,
        metadata={
            "source_episode_fail": failure.episode_id,
            "source_episode_success": success.episode_id,
            "success_actions": [a.get("skill_name", "?") for a in success.actions[:10]],
        },
    )

    # Edges
    edges = [
        KnowledgeEdge(
            edge_type=EdgeType.CAUSED_BY,
            source_id=problem.node_id,
            target_id=cause.node_id,
        ),
        KnowledgeEdge(
            edge_type=EdgeType.SOLVED_BY,
            source_id=problem.node_id,
            target_id=solution.node_id,
        ),
    ]

    return problem, cause, solution, edges


def promote_to_global(
    node: KnowledgeNode,
    min_projects: int = 2,
    project_hits: list[str] | None = None,
) -> bool:
    """
    Promote a node to global knowledge if seen in min_projects+ projects.
    Returns True if promoted.
    """
    if node.project_id is None:
        return False  # Already global
    if project_hits and len(set(project_hits)) >= min_projects:
        node.project_id = None  # Global
        node.boost_confidence(0.2)
        return True
    return False


# ── Helpers ────────────────────────────────────────────────────────────

def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _diff_actions(fail_actions: list[dict], success_actions: list[dict]) -> str:
    """Describe what changed between failure and success actions."""
    fail_skills = set(a.get("skill_name", "?") for a in fail_actions)
    success_skills = set(a.get("skill_name", "?") for a in success_actions)

    added = success_skills - fail_skills
    removed = fail_skills - success_skills
    common = fail_skills & success_skills

    parts = []
    if added:
        parts.append(f"Added skills: {', '.join(sorted(added))}")
    if removed:
        parts.append(f"Removed skills: {', '.join(sorted(removed))}")
    if not added and not removed and common:
        parts.append(f"Same skills used ({', '.join(sorted(common))}) but with different args/sequence")
    if not parts:
        parts.append("Action sequence changed (details in metadata)")

    return "; ".join(parts)
