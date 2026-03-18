from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.utils import now_iso, safe_id


@dataclass
class CapabilityNeed:
    id: str
    required: bool = True
    source: str = "derived"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapabilityIntentAnalyzer:
    """Normalize a capability request into a reusable intent payload."""

    def analyze(
        self,
        *,
        agent: dict[str, Any] | None,
        skill_name: str,
        reqs: dict[str, Any] | None,
        target_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        agent = agent if isinstance(agent, dict) else {}
        reqs = reqs if isinstance(reqs, dict) else {}
        skill_id = safe_id(skill_name)
        goal = str(reqs.get("goal") or f"Provide the {skill_id} capability").strip()
        role = str(agent.get("role") or "").strip()
        constraints = dedupe_strings(reqs.get("constraints") or [])
        risk_level = normalize_risk(reqs.get("risk_level"))
        capabilities = self._derive_capabilities(skill_id=skill_id, goal=goal, reqs=reqs)
        evidence = self._summarize_evidence(target_evidence)
        return {
            "skill_id": skill_id,
            "goal": goal,
            "role": role,
            "capabilities": [item.to_dict() for item in capabilities],
            "constraints": constraints,
            "risk_level": risk_level,
            "evidence": evidence,
            "generated_at": now_iso(),
        }

    def _derive_capabilities(self, *, skill_id: str, goal: str, reqs: dict[str, Any]) -> list[CapabilityNeed]:
        capabilities: list[CapabilityNeed] = []
        explicit = reqs.get("capabilities") or []
        for raw_value in explicit:
            if isinstance(raw_value, dict):
                cap_id = safe_id(str(raw_value.get("id") or ""))
                if not cap_id:
                    continue
                capabilities.append(
                    CapabilityNeed(
                        id=cap_id,
                        required=bool(raw_value.get("required", True)),
                        source="reqs.capabilities",
                        description=str(raw_value.get("description") or "").strip(),
                    )
                )
            else:
                cap_id = safe_id(str(raw_value))
                if cap_id:
                    capabilities.append(CapabilityNeed(id=cap_id, required=True, source="reqs.capabilities"))

        goal_tokens = tokenize(skill_id, goal)
        for raw_missing in reqs.get("missing_skills") or []:
            missing_id = safe_id(str(raw_missing))
            if not missing_id:
                continue
            if missing_id == skill_id or token_overlap(goal_tokens, tokenize(missing_id)):
                capabilities.append(CapabilityNeed(id=missing_id, required=True, source="reqs.missing_skills"))

        heuristic_map = {
            "repo_read": {"repo", "repository", "codebase", "inspect", "analyze"},
            "python_edit": {"python", "edit", "patch", "write", "update", "code"},
            "pytest_regression": {"pytest", "test", "regression", "unit", "integration"},
            "document_research": {"research", "summary", "document", "docs", "report"},
        }
        for cap_id, trigger_tokens in heuristic_map.items():
            if goal_tokens.intersection(trigger_tokens):
                capabilities.append(CapabilityNeed(id=cap_id, required=True, source="goal_heuristic"))

        capabilities.append(CapabilityNeed(id=skill_id, required=True, source="skill_name"))
        return dedupe_capabilities(capabilities)

    def _summarize_evidence(self, target_evidence: dict[str, Any] | None) -> dict[str, Any]:
        evidence = target_evidence if isinstance(target_evidence, dict) else {}
        score = evidence.get("top_score", evidence.get("score", 0.0))
        try:
            reuse_confidence = float(score or 0.0)
        except (TypeError, ValueError):
            reuse_confidence = 0.0
        return {
            "verified": bool(evidence.get("verified", False)),
            "top_candidate": safe_id(str(evidence.get("top_candidate") or "")) if evidence.get("top_candidate") else "",
            "reuse_confidence": reuse_confidence,
            "external_attempts": len(evidence.get("external_attempts") or []),
        }


def dedupe_strings(values: list[Any]) -> list[str]:
    merged: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def dedupe_capabilities(values: list[CapabilityNeed]) -> list[CapabilityNeed]:
    merged: list[CapabilityNeed] = []
    seen: set[str] = set()
    for item in values:
        cap_id = safe_id(item.id)
        if not cap_id or cap_id in seen:
            continue
        seen.add(cap_id)
        merged.append(CapabilityNeed(id=cap_id, required=bool(item.required), source=item.source, description=item.description))
    return merged


def normalize_risk(value: Any) -> str:
    text = str(value or "normal").strip().lower()
    if text not in {"normal", "elevated", "strict"}:
        return "normal"
    return text


def tokenize(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        text = safe_id(str(value or ""))
        for token in text.split("_"):
            if token:
                tokens.add(token)
    return tokens


def token_overlap(left: set[str], right: set[str]) -> bool:
    return bool(left and right and left.intersection(right))
