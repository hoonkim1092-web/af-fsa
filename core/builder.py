import json
import os
from core.config_paths import RUNS_DIR, SKILLS_DIR
from core.providers.cli import CliChatRequest, execute_cli_chat
from core.providers.registry import (
    default_chat_model_for_provider,
    get_engine_api_key,
    get_requested_cli_providers,
)
from core.utils import (
    now_iso,
    quick_guard,
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

    def _build_prompt(self, agent: dict, skill_name: str, reqs: dict, target_evidence: dict) -> str:
        return (
            "You are generating a reusable Python skill module.\n"
            f'Skill: "{skill_name}"\n'
            f'AgentRole: {agent.get("role")}\n'
            f'Goal: {reqs.get("goal")}\n'
            f'Constraints: {reqs.get("constraints")}\n'
            f"Evidence(JSON): {json.dumps(target_evidence, ensure_ascii=False)}\n\n"
            "Requirements:\n"
            "- Implement exactly three functions: propose(ctx)->dict, apply(ctx)->dict, test(ctx)->dict.\n"
            "- test(ctx) must return a dict containing ok.\n"
            '- Read inputs only from ctx["data_dir"] when needed.\n'
            '- Write outputs only under ctx["artifacts_dir"] when needed.\n'
            "- Return plain Python code only.\n\n"
            "Forbidden:\n"
            "- os, sys, subprocess, shutil, importlib, pathlib, glob, ctypes\n"
            "- eval, exec, __import__, compile, input\n"
        )

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

    def _generate_code_via_cli(
        self,
        *,
        provider_id: str,
        prompt: str,
        workspace: str,
        run_id: str,
    ) -> dict:
        request = CliChatRequest(
            provider_id=provider_id,
            model=self._builder_cli_model(provider_id),
            system_prompt=(
                "You generate Python skill modules. "
                "Return only raw Python code. "
                "Do not use markdown fences. "
                "Do not add explanations. "
                "Do not use tools and do not edit files."
            ),
            task_input=prompt,
            workspace=workspace,
            run_id=run_id,
            timeout_sec=int(os.getenv("AGENT_BUILDER_CLI_TIMEOUT_SEC", "600") or "600"),
            auto_approve=False,
        )
        return execute_cli_chat(request)

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

        base_prompt = self._build_prompt(agent, skill_name, reqs, target_evidence)
        last = {"ok": False, "reason": "not_started"}
        feedback_history: list[str] = []

        for i in range(MAX_ITERATIONS):
            if feedback_history:
                feedback_lines = ["", "Previous failures to avoid:"]
                for idx, item in enumerate(feedback_history, 1):
                    feedback_lines.append(f"{idx}. {item}")
                current_prompt = base_prompt + "\n".join(feedback_lines) + "\n"
            else:
                current_prompt = base_prompt

            print(f"[Builder] Building skill '{skill_id}' attempt {i + 1}/{MAX_ITERATIONS}...")
            try:
                code_text, generation_meta = self._generate_code(
                    prompt=current_prompt,
                    workspace=run_dir,
                    run_id=f"{run_id}_{skill_id}_build_{i + 1}",
                )
            except Exception as exc:
                print(f"[Builder] WARN: LLM call failed: {exc}")
                last = {"ok": False, "reason": f"llm_error:{type(exc).__name__}", "detail": str(exc)[:300]}
                feedback_history.append(f"LLM call error: {type(exc).__name__}")
                continue

            if not code_text.strip():
                reason = str(generation_meta.get("reason") or "builder_codegen_failed")
                detail = str(generation_meta.get("detail") or "")[:300]
                print(f"[Builder] WARN: code generation failed via {generation_meta.get('backend')}: {reason}")
                last = {"ok": False, "reason": reason, "detail": detail}
                feedback_history.append(f"Code generation failed: {reason}")
                continue

            code = strip_code_fences(code_text)
            ok, violations = quick_guard(code)
            if not ok:
                last = {"ok": False, "reason": "guard_block", "violations": violations}
                feedback_history.append(
                    f"Guard blocked code due to forbidden patterns: {', '.join(violations[:3])}"
                )
                continue

            write_text(code_path, code)
            test_ok, test_json, test_err = run_isolated(code_path, timeout_sec=TEST_TIMEOUT_SEC)
            last = {"test_ok": test_ok, "test_json": test_json, "stderr": (test_err or "")[:500]}

            if not test_ok:
                feedback_history.append(f"Isolated test failed: {(test_err or 'unknown error')[:200]}")
                continue

            meta = {
                "id": skill_id,
                "name": skill_name,
                "status": "active",
                "version": "0.1.0",
                "capabilities": [skill_name],
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "code_hash": sha256_text(code),
                "last_test_ok": True,
                "last_test_detail": test_json,
            }
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
        }
        write_yaml(meta_path, fail_meta)
        return False, None, fail_meta
