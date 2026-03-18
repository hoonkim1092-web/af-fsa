import json
import os

from core.config_paths import RUNS_DIR, SKILLS_DIR
from core.providers.cli import CliChatRequest, execute_cli_chat
from core.providers.registry import (
    default_chat_model_for_provider,
    get_engine_api_key,
    get_requested_cli_providers,
)
from core.skill_forge import SkillForge
from core.skill_spec_synthesizer import SkillSpecSynthesizer
from core.utils import (
    now_iso,
    quick_guard,
    resolve_skill_paths,
    run_isolated,
    safe_id,
    sha256_text,
    strip_code_fences,
    write_text,
    write_yaml,
)


class SandboxedBuilder:
    """Builds agent skills in a sandboxed environment."""

    def __init__(self, mr):
        self.mr = mr
        self.spec_synthesizer = SkillSpecSynthesizer()
        self.skill_forge = SkillForge(
            generate_code=self._generate_code,
            generate_text=self._generate_text,
            max_repair_rounds=1,
        )

    def _build_prompt(self, agent: dict, skill_name: str, reqs: dict, target_evidence: dict) -> str:
        lines = [
            "You are generating a reusable Python skill module.",
            f'Skill: "{skill_name}"',
            f'AgentRole: {agent.get("role")}',
            f'Goal: {reqs.get("goal")}',
            f'Constraints: {reqs.get("constraints")}',
            f"Evidence(JSON): {json.dumps(target_evidence, ensure_ascii=False)}",
            "",
            "Requirements:",
            "- Implement exactly three functions: propose(ctx)->dict, apply(ctx)->dict, test(ctx)->dict.",
            "- test(ctx) must return a dict containing ok.",
            '- Read inputs only from ctx["data_dir"] when needed.',
            '- Write outputs only under ctx["artifacts_dir"] when needed.',
            "- Return plain Python code only.",
            "",
            "Forbidden:",
            "- os, sys, subprocess, shutil, importlib, pathlib, glob, ctypes",
            "- eval, exec, __import__, compile, input",
        ]
        return "\n".join(lines)

    def _builder_cli_providers(self) -> list[str]:
        raw = str(os.getenv("AGENT_BUILDER_PROVIDER", "") or "").strip()
        if raw:
            return get_requested_cli_providers(raw)
        return get_requested_cli_providers(os.getenv("AGENT_CHAT_PROVIDER"))

    def _builder_cli_model(self, provider_id: str) -> str:
        override = str(os.getenv("AGENT_BUILDER_MODEL", "") or "").strip()
        if override:
            return override
        return default_chat_model_for_provider(provider_id)

    def _execute_cli_request(
        self,
        *,
        provider_id: str,
        prompt: str,
        system_prompt: str,
        workspace: str,
        run_id: str,
    ) -> dict:
        request = CliChatRequest(
            provider_id=provider_id,
            model=self._builder_cli_model(provider_id),
            system_prompt=system_prompt,
            task_input=prompt,
            workspace=workspace,
            run_id=run_id,
            timeout_sec=int(os.getenv("AGENT_BUILDER_CLI_TIMEOUT_SEC", "600") or "600"),
            auto_approve=False,
        )
        return execute_cli_chat(request)

    def _generate_code_via_cli(
        self,
        *,
        provider_id: str,
        prompt: str,
        workspace: str,
        run_id: str,
    ) -> dict:
        return self._execute_cli_request(
            provider_id=provider_id,
            prompt=prompt,
            system_prompt=(
                "You generate Python skill modules. "
                "Return only raw Python code. "
                "Do not use markdown fences. "
                "Do not add explanations. "
                "Do not use tools and do not edit files."
            ),
            workspace=workspace,
            run_id=run_id,
        )

    def _generate_text_via_cli(
        self,
        *,
        provider_id: str,
        prompt: str,
        system_prompt: str,
        workspace: str,
        run_id: str,
    ) -> dict:
        return self._execute_cli_request(
            provider_id=provider_id,
            prompt=prompt,
            system_prompt=system_prompt,
            workspace=workspace,
            run_id=run_id,
        )

    def _generate_code(
        self,
        *,
        prompt: str,
        workspace: str,
        run_id: str,
    ) -> tuple[str, dict]:
        cli_failures: list[dict] = []
        for provider_id in self._builder_cli_providers():
            result = self._generate_code_via_cli(
                provider_id=provider_id,
                prompt=prompt,
                workspace=workspace,
                run_id=f"{run_id}_{safe_id(provider_id)}",
            )
            if result.get("ok"):
                return str(result.get("text", "") or ""), {
                    "backend": provider_id,
                    "reason": str(result.get("reason") or provider_id),
                    "detail": "",
                }
            cli_failures.append(result)

        api_key = get_engine_api_key("google")
        if not api_key:
            if cli_failures:
                last = cli_failures[-1]
                return "", {
                    "backend": "cli",
                    "reason": str(last.get("reason") or "builder_cli_failed"),
                    "detail": str(last.get("stderr") or last.get("stdout") or "")[:300],
                }
            return "", {
                "backend": "sdk",
                "reason": "no_api_key",
                "detail": "",
            }

        from model_utils import generate_content_with_self_heal, normalize_model_name
        from google import genai

        client = genai.Client(api_key=api_key)
        model_name = normalize_model_name(self.mr.pick("builder"))
        response = generate_content_with_self_heal(client, model_name, prompt)
        return str(response.text if response else ""), {
            "backend": "sdk",
            "reason": "sdk",
            "detail": "",
        }

    def _generate_text(
        self,
        *,
        prompt: str,
        system_prompt: str,
        workspace: str,
        run_id: str,
    ) -> tuple[str, dict]:
        cli_failures: list[dict] = []
        for provider_id in self._builder_cli_providers():
            result = self._generate_text_via_cli(
                provider_id=provider_id,
                prompt=prompt,
                system_prompt=system_prompt,
                workspace=workspace,
                run_id=f"{run_id}_{safe_id(provider_id)}",
            )
            if result.get("ok"):
                return str(result.get("text", "") or ""), {
                    "backend": provider_id,
                    "reason": str(result.get("reason") or provider_id),
                    "detail": "",
                }
            cli_failures.append(result)

        api_key = get_engine_api_key("google")
        if not api_key:
            if cli_failures:
                last = cli_failures[-1]
                return "", {
                    "backend": "cli",
                    "reason": str(last.get("reason") or "builder_cli_failed"),
                    "detail": str(last.get("stderr") or last.get("stdout") or "")[:300],
                }
            return "", {
                "backend": "sdk",
                "reason": "no_api_key",
                "detail": "",
            }

        from model_utils import generate_content_with_self_heal, normalize_model_name
        from google import genai

        client = genai.Client(api_key=api_key)
        model_name = normalize_model_name(self.mr.pick("builder"))
        full_prompt = f"{system_prompt}\n\n{prompt}"
        response = generate_content_with_self_heal(client, model_name, full_prompt)
        return str(response.text if response else ""), {
            "backend": "sdk",
            "reason": "sdk",
            "detail": "",
        }

    def _synthesize_next_gen_artifacts(
        self,
        *,
        skill_id: str,
        skill_dir: str,
        run_dir: str,
        agent: dict,
        reqs: dict,
        target_evidence: dict,
    ) -> dict:
        bundle = {
            "artifacts": None,
            "spec_path": "",
            "evals_path": "",
            "has_spec": False,
            "has_evals": False,
            "synthesis_error": "",
        }
        try:
            artifacts = self.spec_synthesizer.synthesize(
                agent=agent,
                skill_name=skill_id,
                reqs=reqs,
                target_evidence=target_evidence,
                skill_kind="action",
            )
            spec_path, evals_path = artifacts.write_to_dir(skill_dir)
            write_yaml(os.path.join(run_dir, f"{skill_id}_skill_spec.yaml"), artifacts.skill_spec)
            write_yaml(os.path.join(run_dir, f"{skill_id}_evals.yml"), artifacts.eval_manifest)
            write_yaml(os.path.join(run_dir, f"{skill_id}_capability_intent.yaml"), artifacts.capability_intent)
            bundle.update(
                {
                    "artifacts": artifacts,
                    "spec_path": spec_path,
                    "evals_path": evals_path,
                    "has_spec": bool(spec_path),
                    "has_evals": bool(evals_path),
                }
            )
        except Exception as exc:
            bundle["synthesis_error"] = f"{type(exc).__name__}:{exc}"
        return bundle

    def _load_reference_candidate(self, target_evidence: dict) -> dict[str, object]:
        if not isinstance(target_evidence, dict):
            return {}
        decision = target_evidence.get("reuse_decision") if isinstance(target_evidence.get("reuse_decision"), dict) else {}
        mode = safe_id(str(decision.get("mode") or ""))
        if mode != "shadow_reuse":
            return {}

        candidate_skill_id = safe_id(str(target_evidence.get("top_candidate") or decision.get("candidate_skill_id") or ""))
        if not candidate_skill_id:
            return {}
        candidate_path, _meta_path = resolve_skill_paths(candidate_skill_id)
        if not candidate_path or not os.path.exists(candidate_path):
            return {}
        try:
            with open(candidate_path, "r", encoding="utf-8") as handle:
                code_excerpt = handle.read(4000)
        except OSError:
            code_excerpt = ""
        return {
            "candidate_skill_id": candidate_skill_id,
            "candidate_path": candidate_path,
            "confidence": float(decision.get("confidence") or 0.0),
            "score": int(decision.get("score") or 0),
            "rationale": str(decision.get("rationale") or target_evidence.get("matching_rationale") or "").strip(),
            "code_excerpt": code_excerpt,
        }

    def build_skill(
        self,
        agent: dict,
        skill_name: str,
        reqs: dict,
        run_id: str,
        evidence_pack: dict,
    ) -> tuple[bool, str | None, dict]:
        if not evidence_pack or not isinstance(evidence_pack, dict):
            print(f"[Planning-First Gate] BLOCKED: evidence_pack empty -- skill '{skill_name}' rejected")
            return False, None, {"id": skill_name, "status": "blocked", "reason": "planning_first_violated"}

        from core.utils import MAX_ITERATIONS, TEST_TIMEOUT_SEC

        skill_id = safe_id(skill_name)
        skill_dir = os.path.join(SKILLS_DIR, skill_id)
        os.makedirs(skill_dir, exist_ok=True)
        code_path = os.path.join(skill_dir, "skill.py")
        meta_path = os.path.join(skill_dir, "meta.yaml")

        run_dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(run_dir, exist_ok=True)

        evidence_targets = (evidence_pack or {}).get("targets", {}) if isinstance(evidence_pack, dict) else {}
        target_evidence = evidence_targets.get(skill_id)
        if not target_evidence:
            fail_meta = {
                "id": skill_id,
                "name": skill_name,
                "status": "disabled",
                "version": "0.1.0",
                "capabilities": [skill_name],
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "last_test_ok": False,
                "last_test_detail": {"ok": False, "reason": "missing_evidence_pack"},
            }
            write_yaml(meta_path, fail_meta)
            return False, None, fail_meta

        synthesis_bundle = self._synthesize_next_gen_artifacts(
            skill_id=skill_id,
            skill_dir=skill_dir,
            run_dir=run_dir,
            agent=agent,
            reqs=reqs,
            target_evidence=target_evidence,
        )
        base_prompt = self._build_prompt(agent, skill_name, reqs, target_evidence)
        reference_candidate = self._load_reference_candidate(target_evidence)
        last = {"ok": False, "reason": "not_started"}
        feedback_history: list[str] = []

        for i in range(MAX_ITERATIONS):
            current_prompt = base_prompt
            if feedback_history:
                feedback_lines = ["", "Previous failures to avoid:"]
                for idx, item in enumerate(feedback_history, 1):
                    feedback_lines.append(f"{idx}. {item}")
                current_prompt = current_prompt + "\n".join(feedback_lines) + "\n"

            print(f"[Builder] Building skill '{skill_id}' attempt {i + 1}/{MAX_ITERATIONS}...")
            try:
                forge_result = self.skill_forge.run(
                    skill_id=skill_id,
                    base_prompt=current_prompt,
                    workspace=run_dir,
                    run_id=f"{run_id}_{skill_id}_build_{i + 1}",
                    synthesized_artifacts=synthesis_bundle.get("artifacts"),
                    reference_candidate=reference_candidate,
                )
            except Exception as exc:
                print(f"[Builder] WARN: forge failed: {exc}")
                last = {"ok": False, "reason": f"llm_error:{type(exc).__name__}", "detail": str(exc)[:300]}
                feedback_history.append(f"Forge error: {type(exc).__name__}")
                continue

            write_yaml(
                os.path.join(run_dir, f"{skill_id}_forge_attempt_{i + 1}.yaml"),
                forge_result.to_dict(),
            )

            generation_reason = str((forge_result.implementer_meta or {}).get("reason") or "")
            generation_detail = str((forge_result.implementer_meta or {}).get("detail") or "")[:300]
            if not forge_result.code.strip():
                reason = generation_reason or "builder_codegen_failed"
                last = {"ok": False, "reason": reason, "detail": generation_detail}
                feedback_history.append(f"Code generation failed: {reason}")
                continue

            code = strip_code_fences(forge_result.code)
            ok, violations = quick_guard(code)
            if not ok:
                last = {"ok": False, "reason": "guard_block", "violations": violations}
                feedback_history.append(
                    f"Guard blocked code due to forbidden patterns: {', '.join(violations[:3])}"
                )
                continue

            write_text(code_path, code)
            test_ok, test_json, test_err = run_isolated(code_path, timeout_sec=TEST_TIMEOUT_SEC)
            last = {
                "test_ok": test_ok,
                "test_json": test_json,
                "stderr": (test_err or "")[:500],
                "critique": forge_result.critique.to_dict(),
            }

            if not test_ok:
                feedback_history.append(f"Isolated test failed: {(test_err or 'unknown error')[:200]}")
                continue

            meta = {
                "id": skill_id,
                "name": skill_name,
                "status": "draft",
                "lifecycle_stage": "draft",
                "version": "0.1.0",
                "capabilities": [skill_name],
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "code_hash": sha256_text(code),
                "last_test_ok": True,
                "last_test_detail": test_json,
                "has_spec": bool(synthesis_bundle.get("has_spec")),
                "spec_path": synthesis_bundle.get("spec_path") or "",
                "has_evals": bool(synthesis_bundle.get("has_evals")),
                "evals_path": synthesis_bundle.get("evals_path") or "",
                "forge_strategy": "implementer_critic_repair",
                "forge_repaired": bool(forge_result.repaired),
                "reference_candidate_id": str(reference_candidate.get("candidate_skill_id") or ""),
            }
            if synthesis_bundle.get("synthesis_error"):
                meta["synthesis_error"] = synthesis_bundle["synthesis_error"]
            write_yaml(meta_path, meta)
            write_text(os.path.join(run_dir, f"{skill_id}_skill.py"), code)
            write_yaml(os.path.join(run_dir, f"{skill_id}_meta.yaml"), meta)
            return True, code_path, meta

        fail_meta = {
            "id": skill_id,
            "name": skill_name,
            "status": "disabled",
            "version": "0.1.0",
            "capabilities": [skill_name],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "last_test_ok": False,
            "last_test_detail": last,
            "has_spec": bool(synthesis_bundle.get("has_spec")),
            "spec_path": synthesis_bundle.get("spec_path") or "",
            "has_evals": bool(synthesis_bundle.get("has_evals")),
            "evals_path": synthesis_bundle.get("evals_path") or "",
            "forge_strategy": "implementer_critic_repair",
            "reference_candidate_id": str(reference_candidate.get("candidate_skill_id") or ""),
        }
        if synthesis_bundle.get("synthesis_error"):
            fail_meta["synthesis_error"] = synthesis_bundle["synthesis_error"]
        write_yaml(meta_path, fail_meta)
        return False, None, fail_meta
