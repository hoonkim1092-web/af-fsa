from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Any

from core.capability_intent import (
    CapabilityIntentAnalyzer,
    CapabilityNeed,
    dedupe_strings as _dedupe_strings,
    normalize_risk as _normalize_risk,
    tokenize as _tokenize,
)
from core.utils import now_iso, resolve_skill_paths, safe_id, write_yaml


SPEC_FILENAME = "skill-spec.yaml"
EVALS_FILENAME = "evals.yml"
SUPPORTED_PROVIDERS = ["claude_cli", "gemini_cli", "codex_cli"]
DESTRUCTIVE_KEYWORDS = {"delete", "drop", "destroy", "purge", "remove", "reset"}


@dataclass
class SynthesizedSkillArtifacts:
    capability_intent: dict[str, Any]
    skill_spec: dict[str, Any]
    eval_manifest: dict[str, Any]
    spec_path: str = ""
    evals_path: str = ""
    written_at: str = ""

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "capability_intent": self.capability_intent,
            "skill_spec": self.skill_spec,
            "eval_manifest": self.eval_manifest,
        }

    def write_to_dir(self, skill_dir: str) -> tuple[str, str]:
        os.makedirs(skill_dir, exist_ok=True)
        self.spec_path = os.path.join(skill_dir, SPEC_FILENAME)
        self.evals_path = os.path.join(skill_dir, EVALS_FILENAME)
        self.written_at = now_iso()
        write_yaml(self.spec_path, self.skill_spec)
        write_yaml(self.evals_path, self.eval_manifest)
        return self.spec_path, self.evals_path


class SkillSpecSynthesizer:
    """Create next-generation capability intent, skill spec, and eval manifests."""

    def __init__(self, intent_analyzer: CapabilityIntentAnalyzer | None = None):
        self.intent_analyzer = intent_analyzer or CapabilityIntentAnalyzer()

    def synthesize(
        self,
        *,
        agent: dict[str, Any] | None,
        skill_name: str,
        reqs: dict[str, Any] | None,
        target_evidence: dict[str, Any] | None = None,
        skill_kind: str = "action",
    ) -> SynthesizedSkillArtifacts:
        intent = self.analyze_capability_intent(
            agent=agent,
            skill_name=skill_name,
            reqs=reqs,
            target_evidence=target_evidence,
        )
        spec = self._build_skill_spec(intent=intent, skill_kind=skill_kind)
        eval_manifest = self._build_eval_manifest(skill_id=intent["skill_id"], skill_spec=spec, target_evidence=target_evidence)
        return SynthesizedSkillArtifacts(
            capability_intent=intent,
            skill_spec=spec,
            eval_manifest=eval_manifest,
        )

    def analyze_capability_intent(
        self,
        *,
        agent: dict[str, Any] | None,
        skill_name: str,
        reqs: dict[str, Any] | None,
        target_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.intent_analyzer.analyze(
            agent=agent,
            skill_name=skill_name,
            reqs=reqs,
            target_evidence=target_evidence,
        )

    def _build_skill_spec(self, *, intent: dict[str, Any], skill_kind: str) -> dict[str, Any]:
        skill_id = safe_id(str(intent.get("skill_id") or "skill"))
        goal = str(intent.get("goal") or f"Provide the {skill_id} capability").strip()
        role = safe_id(str(intent.get("role") or ""))
        constraints = _dedupe_strings(intent.get("constraints") or [])
        risk_level = _normalize_risk(intent.get("risk_level"))
        capability_ids = [safe_id(str(item.get("id") or "")) for item in (intent.get("capabilities") or []) if safe_id(str(item.get("id") or ""))]
        destructive = _looks_destructive(skill_id, goal, constraints)
        allowed_tools = _infer_allowed_tools(skill_id, goal, constraints)
        approval_required_tools = ["delete_file"] if destructive else []
        success_criteria = [
            "apply(ctx) returns a dict with an ok field.",
            "isolated self-test passes before promotion.",
            "implementation stays within the declared tool and filesystem policy.",
        ]
        if any(token in _tokenize(skill_id, goal) for token in {"pytest", "test", "regression"}):
            success_criteria.insert(0, "Regression-oriented behavior remains executable through apply(ctx).")
        failure_modes = [
            "Self-test passes but runtime usage fails on realistic project inputs.",
            "Forbidden imports or destructive side effects bypass the declared policy.",
            "The implementation writes outside the allowed workspace or artifact directory.",
        ]
        return {
            "id": skill_id,
            "name": skill_id.replace("_", " ").title(),
            "version": 2,
            "kind": skill_kind,
            "goal": goal,
            "description": f"Next-generation generated skill for {goal}",
            "when_to_use": [goal],
            "entrypoints": ["skill.py"],
            "capabilities": capability_ids,
            "constraints": constraints,
            "inputs": ["task context", "ctx data", "workspace files when needed"],
            "outputs": ["result dict", "artifacts under ctx['artifacts_dir'] when needed"],
            "success_criteria": success_criteria,
            "failure_modes": failure_modes,
            "invocation": {
                "auto": True,
                "user_invocable": True,
                "planner_invocable": True,
                "context_mode": "fork" if risk_level == "strict" else "inline",
            },
            "policy": {
                "allowed_tools": allowed_tools,
                "approval_required_tools": approval_required_tools,
                "destructive": destructive,
            },
            "compatibility": {
                "providers": list(SUPPORTED_PROVIDERS),
                "roles": [role] if role else [],
            },
            "distribution": {
                "visibility": "project",
                "maturity": "draft",
                "source": "builder",
            },
            "observability": {
                "emit_usage_event": True,
                "collect_runtime_feedback": True,
            },
            "capability_intent": intent,
            "provenance": {
                "generated_at": now_iso(),
                "generator": "skill_spec_synthesizer",
            },
        }

    def _build_eval_manifest(
        self,
        *,
        skill_id: str,
        skill_spec: dict[str, Any],
        target_evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        goal = str(skill_spec.get("goal") or "").strip()
        constraints = _dedupe_strings(skill_spec.get("constraints") or [])
        contract_case = {
            "name": "contract-basic-ok",
            "ctx": {
                "skill_id": skill_id,
                "goal": goal,
                "constraints": constraints,
                "mode": "contract",
            },
            "expect_ok": True,
            "expect_contains": "ok",
        }
        hidden_case = {
            "name": "hidden-policy-stability",
            "ctx": {
                "skill_id": skill_id,
                "goal": goal,
                "constraints": constraints,
                "mode": "hidden",
                "dry_run": True,
            },
            "expect_ok": True,
        }
        manifest: dict[str, Any] = {
            "contract": [contract_case],
            "hidden": [hidden_case],
        }
        baseline_skill_path = _resolve_baseline_skill_path(skill_id, target_evidence)
        if baseline_skill_path:
            manifest["shadow"] = {
                "baseline_skill_path": baseline_skill_path,
                "replay": {
                    "enabled": True,
                    "max_runs": 5,
                },
                "cases": [
                    {
                        "name": "shadow-basic-parity",
                        "ctx": {
                            "skill_id": skill_id,
                            "goal": goal,
                            "constraints": constraints,
                            "mode": "shadow",
                        },
                        "expect_ok": True,
                    }
                ],
            }
        return manifest


def _resolve_baseline_skill_path(skill_id: str, target_evidence: dict[str, Any] | None) -> str:
    evidence = target_evidence if isinstance(target_evidence, dict) else {}
    candidate_id = safe_id(str(evidence.get("top_candidate") or evidence.get("installed_skill_id") or ""))
    if not candidate_id or candidate_id == safe_id(skill_id):
        return ""
    skill_path, _meta_path = resolve_skill_paths(candidate_id)
    return skill_path or ""


def _looks_destructive(skill_id: str, goal: str, constraints: list[str]) -> bool:
    tokens = _tokenize(skill_id, goal, *constraints)
    return bool(tokens.intersection(DESTRUCTIVE_KEYWORDS))


def _infer_allowed_tools(skill_id: str, goal: str, constraints: list[str]) -> list[str]:
    tokens = _tokenize(skill_id, goal, *constraints)
    allowed: list[str] = []
    tool_rules = [
        ("read_file", {"repo", "repository", "codebase", "read", "inspect", "research", "analyze", "document", "docs"}),
        ("write_file", {"edit", "write", "patch", "update", "create", "generate", "code", "file"}),
        ("run_pytest", {"pytest", "test", "regression", "unit", "integration"}),
    ]
    for tool_name, trigger_tokens in tool_rules:
        if tokens.intersection(trigger_tokens) and tool_name not in allowed:
            allowed.append(tool_name)
    if not allowed:
        allowed.append("read_file")
    return allowed


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthesize next-generation skill spec artifacts")
    parser.add_argument("skill_name", help="Skill id or display name")
    parser.add_argument("--goal", required=True, help="Goal the generated skill should satisfy")
    parser.add_argument("--role", default="", help="Role requesting the skill")
    parser.add_argument("--constraint", action="append", dest="constraints", default=[], help="Constraint to carry into the spec")
    parser.add_argument("--risk", choices=["normal", "elevated", "strict"], default="normal", help="Risk level for the generated skill")
    parser.add_argument("--kind", choices=["action", "knowledge", "bundle", "hybrid"], default="action", help="Skill kind")
    parser.add_argument("--output-dir", dest="output_dir", help="Optional directory that should receive skill-spec.yaml and evals.yml")
    parser.add_argument("--json", action="store_true", help="Print the synthesized payload as JSON")
    args = parser.parse_args(argv)

    artifacts = SkillSpecSynthesizer().synthesize(
        agent={"role": args.role},
        skill_name=args.skill_name,
        reqs={
            "goal": args.goal,
            "constraints": args.constraints,
            "risk_level": args.risk,
        },
        skill_kind=args.kind,
    )
    if args.output_dir:
        artifacts.write_to_dir(os.path.abspath(args.output_dir))

    payload = artifacts.to_prompt_payload()
    payload["spec_path"] = artifacts.spec_path
    payload["evals_path"] = artifacts.evals_path
    if args.json:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[SkillSpecSynthesizer] {artifacts.skill_spec['id']} -> {artifacts.spec_path or SPEC_FILENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())

