"""
KnowledgeForger — LLM 기반 에피소드 쌍 → 지식 노드 압축.

Stage 6: EpisodeMatcher가 찾은 (failure, success) 쌍을
graph_builder.extract_triple()로 구조화한 뒤,
LLM을 통해 인간 친화적인 인사이트 문장으로 압축하고
Knowledge Graph에 저장한다.

LLM 호출이 불가능하면 rule-based fallback으로 동작.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.memory_system.graph_builder import extract_triple
from core.memory_system.models import (
    EpisodeRecord,
    KnowledgeNode,
)

logger = logging.getLogger(__name__)

# ── LLM compression prompt ───────────────────────────────────────────

_FORGE_PROMPT = """\
You are a knowledge extraction system. Given a failure→success episode pair,
produce a concise, actionable insight in JSON format.

## Failure Episode
- Task: {fail_task}
- Error: {fail_error}
- Actions: {fail_actions}

## Success Episode
- Task: {success_task}
- Actions: {success_actions}

## Extracted Triple
- Problem: {problem_label}
- Cause: {cause_label}
- Solution: {solution_label}

## Instructions
Return ONLY a JSON object with these keys:
- "insight": A single sentence (max 200 chars) summarizing what was learned
- "confidence": Float 0.0-1.0 (how generalizable is this lesson?)
- "tags": List of 2-5 keyword tags

Example:
{{"insight": "Use retry with exponential backoff when API rate limits occur", "confidence": 0.85, "tags": ["api", "retry", "rate-limit"]}}
"""


class KnowledgeForger:
    """Forge knowledge from failure→success episode pairs.

    Orchestrates: extract_triple → LLM compression → KnowledgeGraph storage.
    Falls back to rule-based extraction when LLM is unavailable.
    """

    def __init__(self, graph_adapter: Any) -> None:
        """
        Args:
            graph_adapter: KnowledgeGraphAdapter instance for storing results.
        """
        self._graph = graph_adapter
        self._llm_client: Any = None  # cached genai.Client

    async def forge(
        self,
        failure: EpisodeRecord,
        success: EpisodeRecord,
    ) -> dict[str, Any] | None:
        """Full pipeline: extract triple → compress → store.

        Returns dict with stored node IDs and insight, or None on failure.
        """
        # Step 1: Extract P→C→S triple
        problem, cause, solution, edges = extract_triple(failure, success)

        # Step 2: LLM compression (with fallback)
        insight_data = await self._compress_with_llm(
            failure, success, problem, cause, solution,
        )

        # Step 3: Enrich nodes with insight
        if insight_data:
            insight_text = insight_data.get("insight", "")
            llm_confidence = max(0.0, min(1.0, float(insight_data.get("confidence", 0.7))))
            tags = insight_data.get("tags", [])

            if insight_text:
                solution.metadata["insight"] = insight_text
                solution.metadata["tags"] = tags
                # Blend LLM confidence with structural confidence
                solution.confidence = min(
                    solution.confidence * 0.5 + llm_confidence * 0.5, 1.0,
                )

        # Step 4: Store in Knowledge Graph
        self._graph.add_node(problem)
        self._graph.add_node(cause)
        self._graph.add_node(solution)
        for edge in edges:
            self._graph.add_edge(edge)

        logger.info(
            "KnowledgeForger: stored P(%s)→C(%s)→S(%s) from episodes %s→%s",
            problem.node_id[:8],
            cause.node_id[:8],
            solution.node_id[:8],
            failure.episode_id[:8],
            success.episode_id[:8],
        )

        return {
            "problem_id": problem.node_id,
            "cause_id": cause.node_id,
            "solution_id": solution.node_id,
            "edge_ids": [e.edge_id for e in edges],
            "insight": solution.metadata.get("insight", solution.label),
        }

    # ── LLM compression ──────────────────────────────────────────────

    async def _compress_with_llm(
        self,
        failure: EpisodeRecord,
        success: EpisodeRecord,
        problem: KnowledgeNode,
        cause: KnowledgeNode,
        solution: KnowledgeNode,
    ) -> dict[str, Any] | None:
        """Try LLM compression, fall back to rule-based if unavailable."""
        try:
            result = await self._call_llm(
                failure, success, problem, cause, solution,
            )
            # _call_llm returns None when API key/SDK unavailable — use fallback
            if result is not None:
                return result
        except Exception as exc:
            logger.debug("LLM compression failed (%s), using fallback", exc)
        return self._rule_based_fallback(problem, cause, solution)

    async def _call_llm(
        self,
        failure: EpisodeRecord,
        success: EpisodeRecord,
        problem: KnowledgeNode,
        cause: KnowledgeNode,
        solution: KnowledgeNode,
    ) -> dict[str, Any] | None:
        """Call Gemini Flash for lightweight compression."""
        import json as json_mod
        import os

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return None

        try:
            from google import genai
        except ImportError:
            return None

        prompt = _FORGE_PROMPT.format(
            fail_task=failure.task_input[:300],
            fail_error=failure.error_info[:200],
            fail_actions=", ".join(
                a.get("skill_name", "?") for a in failure.actions[:8]
            ),
            success_task=success.task_input[:300],
            success_actions=", ".join(
                a.get("skill_name", "?") for a in success.actions[:8]
            ),
            problem_label=problem.label,
            cause_label=cause.label,
            solution_label=solution.label,
        )

        if self._llm_client is None:
            self._llm_client = genai.Client(api_key=api_key)
        client = self._llm_client
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )

        raw_text = response.text
        if not raw_text:
            return None
        text = raw_text.strip()
        # Extract JSON from response (handle markdown code blocks)
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]

        return json_mod.loads(text)

    @staticmethod
    def _rule_based_fallback(
        problem: KnowledgeNode,
        cause: KnowledgeNode,
        solution: KnowledgeNode,
    ) -> dict[str, Any]:
        """Deterministic fallback when LLM is unavailable."""
        insight = (
            f"When '{problem.label[:60]}' fails due to '{cause.label[:60]}', "
            f"fix: {solution.label[:60]}"
        )
        return {
            "insight": insight[:200],
            "confidence": 0.6,
            "tags": _extract_tags(problem, cause, solution),
        }


def _extract_tags(
    problem: KnowledgeNode,
    cause: KnowledgeNode,
    solution: KnowledgeNode,
) -> list[str]:
    """Extract keyword tags from triple labels."""
    all_text = f"{problem.label} {cause.label} {solution.label}".lower()
    # Common patterns
    tag_patterns = {
        "api": ["api", "endpoint", "request", "response"],
        "error": ["error", "exception", "fail", "crash"],
        "timeout": ["timeout", "timed out", "deadline"],
        "import": ["import", "module", "package"],
        "config": ["config", "setting", "env", "variable"],
        "permission": ["permission", "access", "auth"],
        "dependency": ["dependency", "require", "install"],
        "syntax": ["syntax", "parse", "format"],
    }
    tags = []
    for tag, keywords in tag_patterns.items():
        if any(re.search(r"\b" + re.escape(kw) + r"\b", all_text) for kw in keywords):
            tags.append(tag)
    return tags[:5] if tags else ["general"]
