from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from core.utils import safe_json_load, strip_code_fences


@dataclass
class ForgeCritique:
    issues: list[str] = field(default_factory=list)
    should_repair: bool = False
    summary: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ForgeRunResult:
    code: str
    implementer_meta: dict[str, Any] = field(default_factory=dict)
    critique: ForgeCritique = field(default_factory=ForgeCritique)
    repair_meta: dict[str, Any] = field(default_factory=dict)
    repaired: bool = False
    reference_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["critique"] = self.critique.to_dict()
        return data


class SkillForge:
    """Run implementer, critic, and repair passes for generated skills."""

    def __init__(
        self,
        *,
        generate_code: Callable[..., tuple[str, dict[str, Any]]],
        generate_text: Callable[..., tuple[str, dict[str, Any]]] | None = None,
        max_repair_rounds: int = 1,
    ):
        self.generate_code = generate_code
        self.generate_text = generate_text
        self.max_repair_rounds = max(0, int(max_repair_rounds))

    def run(
        self,
        *,
        skill_id: str,
        base_prompt: str,
        workspace: str,
        run_id: str,
        synthesized_artifacts=None,
        reference_candidate: dict[str, Any] | None = None,
    ) -> ForgeRunResult:
        implementer_prompt = self._build_implementer_prompt(
            base_prompt=base_prompt,
            synthesized_artifacts=synthesized_artifacts,
            reference_candidate=reference_candidate,
        )
        code_text, implementer_meta = self.generate_code(
            prompt=implementer_prompt,
            workspace=workspace,
            run_id=f"{run_id}_implementer",
        )
        current_code = strip_code_fences(code_text)
        critique = self._critic(
            skill_id=skill_id,
            code=current_code,
            synthesized_artifacts=synthesized_artifacts,
            reference_candidate=reference_candidate,
            workspace=workspace,
            run_id=f"{run_id}_critic",
        )

        repair_meta: dict[str, Any] = {}
        repaired = False
        for repair_idx in range(self.max_repair_rounds):
            if not critique.should_repair:
                break
            repair_prompt = self._build_repair_prompt(
                base_prompt=base_prompt,
                code=current_code,
                critique=critique,
                synthesized_artifacts=synthesized_artifacts,
                reference_candidate=reference_candidate,
            )
            repaired_text, repair_meta = self.generate_code(
                prompt=repair_prompt,
                workspace=workspace,
                run_id=f"{run_id}_repair_{repair_idx + 1}",
            )
            repaired_code = strip_code_fences(repaired_text)
            if repaired_code.strip():
                current_code = repaired_code
                repaired = True
            critique = self._critic(
                skill_id=skill_id,
                code=current_code,
                synthesized_artifacts=synthesized_artifacts,
                reference_candidate=reference_candidate,
                workspace=workspace,
                run_id=f"{run_id}_critic_{repair_idx + 1}",
            )

        return ForgeRunResult(
            code=current_code,
            implementer_meta=implementer_meta,
            critique=critique,
            repair_meta=repair_meta,
            repaired=repaired,
            reference_used=bool(reference_candidate),
        )

    def _critic(
        self,
        *,
        skill_id: str,
        code: str,
        synthesized_artifacts=None,
        reference_candidate: dict[str, Any] | None,
        workspace: str,
        run_id: str,
    ) -> ForgeCritique:
        if self.generate_text is None:
            return self._local_critic(skill_id=skill_id, code=code)
        prompt = self._build_critic_prompt(
            skill_id=skill_id,
            code=code,
            synthesized_artifacts=synthesized_artifacts,
            reference_candidate=reference_candidate,
        )
        system_prompt = (
            "You are the critic pass for a next-generation skill forge. "
            "Review the proposed Python skill against the supplied spec and return JSON only with keys: "
            "issues (array of strings), should_repair (bool), summary (string)."
        )
        text, _meta = self.generate_text(
            prompt=prompt,
            system_prompt=system_prompt,
            workspace=workspace,
            run_id=run_id,
        )
        critique = self._parse_critique(text)
        if critique is None:
            return self._local_critic(skill_id=skill_id, code=code)
        if critique.should_repair and not critique.issues:
            critique.issues = ["Critic requested repair without a concrete issue."]
        return critique

    def _parse_critique(self, text: str) -> ForgeCritique | None:
        try:
            payload = safe_json_load(text or "")
        except Exception:
            return None
        if not isinstance(payload, dict) or not payload:
            return None
        issues = [str(item).strip() for item in (payload.get("issues") or []) if str(item).strip()]
        should_repair = bool(payload.get("should_repair", False) or issues)
        summary = str(payload.get("summary") or "").strip()
        return ForgeCritique(
            issues=issues,
            should_repair=should_repair,
            summary=summary,
            raw_text=text or "",
        )

    def _local_critic(self, *, skill_id: str, code: str) -> ForgeCritique:
        issues: list[str] = []
        for fn_name in ("propose", "apply", "test"):
            if f"def {fn_name}(" not in code:
                issues.append(f"Missing required function: {fn_name}()")
        if "TODO" in code:
            issues.append("Implementation still contains TODO placeholders.")
        if not code.strip():
            issues.append(f"{skill_id} generated empty code.")
        return ForgeCritique(
            issues=issues,
            should_repair=bool(issues),
            summary="local_critic" if issues else "local_critic_ok",
            raw_text=json.dumps({"issues": issues, "should_repair": bool(issues)}, ensure_ascii=False),
        )

    def _build_implementer_prompt(self, *, base_prompt: str, synthesized_artifacts=None, reference_candidate: dict[str, Any] | None) -> str:
        lines = [base_prompt, "", "[Forge Pass] Implementer"]
        if synthesized_artifacts is not None:
            payload = synthesized_artifacts.to_prompt_payload()
            lines.append(f"SkillSpec(JSON): {json.dumps(payload.get('skill_spec') or {}, ensure_ascii=False)}")
            lines.append(f"EvalManifest(JSON): {json.dumps(payload.get('eval_manifest') or {}, ensure_ascii=False)}")
        if reference_candidate:
            lines.append(f"ReferenceSkill(JSON): {json.dumps(reference_candidate, ensure_ascii=False)}")
            code_excerpt = str(reference_candidate.get("code_excerpt") or "").strip()
            if code_excerpt:
                lines.append("ReferenceSkillCode:")
                lines.append(code_excerpt)
        lines.append("Implement the skill directly. Return raw Python code only.")
        return "\n".join(lines)

    def _build_critic_prompt(self, *, skill_id: str, code: str, synthesized_artifacts=None, reference_candidate: dict[str, Any] | None) -> str:
        lines = [f"SkillId: {skill_id}"]
        if synthesized_artifacts is not None:
            payload = synthesized_artifacts.to_prompt_payload()
            lines.append(f"SkillSpec(JSON): {json.dumps(payload.get('skill_spec') or {}, ensure_ascii=False)}")
            lines.append(f"EvalManifest(JSON): {json.dumps(payload.get('eval_manifest') or {}, ensure_ascii=False)}")
        if reference_candidate:
            lines.append(f"ReferenceSkill(JSON): {json.dumps(reference_candidate, ensure_ascii=False)}")
        lines.append("CandidateCode:")
        lines.append(code)
        return "\n".join(lines)

    def _build_repair_prompt(
        self,
        *,
        base_prompt: str,
        code: str,
        critique: ForgeCritique,
        synthesized_artifacts=None,
        reference_candidate: dict[str, Any] | None,
    ) -> str:
        lines = [base_prompt, "", "[Forge Pass] Repair"]
        if synthesized_artifacts is not None:
            payload = synthesized_artifacts.to_prompt_payload()
            lines.append(f"SkillSpec(JSON): {json.dumps(payload.get('skill_spec') or {}, ensure_ascii=False)}")
            lines.append(f"EvalManifest(JSON): {json.dumps(payload.get('eval_manifest') or {}, ensure_ascii=False)}")
        if reference_candidate:
            lines.append(f"ReferenceSkill(JSON): {json.dumps(reference_candidate, ensure_ascii=False)}")
        lines.append(f"Critique(JSON): {json.dumps(critique.to_dict(), ensure_ascii=False)}")
        lines.append("CurrentCode:")
        lines.append(code)
        lines.append("Repair the code to satisfy the critique. Return raw Python code only.")
        return "\n".join(lines)

