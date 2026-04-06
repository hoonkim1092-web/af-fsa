import os
import time
import json
import ast
import yaml
import importlib.util
import inspect
import functools
import shutil
import builtins
import sys
import urllib.error
import urllib.request
from datetime import datetime
from typing import Dict

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from core.config_paths import (
    BASE_DIR, PROJECT_ROOT, PROJECT_ID,
    SKILLS_DIR, PROJECT_SKILLS_DIR, RUNS_DIR, DATA_DIR, ARTIFACTS_DIR,
    GOOGLE_API_KEY, OPENAI_API_KEY,
    AGENTS_DIR, EXTERNAL_CACHE_DIR,
    GLOBAL_MEMORY_DIR, GLOBAL_AGENTS_DIR,
)
from core.utils import (
    safe_id, now_iso, read_yaml, write_yaml, safe_json_load,
    read_core_memory, get_random_signature, print_agent_msg,
    is_codex_model, is_claude_model,
    run_skill_safely, validate_context_with_schema, resolve_knowledge_skill_path, resolve_skill_paths,
)
from core.utils import _safe_write_json
from core.registry import ToolRegistry
from core.tool_runtime import ToolRuntimeWrapper
from core.policy_runtime import PolicyRuntime
from core.documentation_policy import inject_documentation_contract, inject_thinking_contract
from core.implementation_language_policy import inject_implementation_language_contract
from core.destructive_guard import inject_destructive_guard_contract
from core.project_mailbox import (
    ack_mailbox_message as project_ack_mailbox_message,
    mailbox_prompt_digest as project_mailbox_prompt_digest,
    read_inbox as project_read_mailbox_inbox,
    send_agent_message as project_send_mailbox_message,
)
from core.hooks.event_bus import HookEventBus
from core.hooks.guardrails import IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator
from core.providers.cli import CliChatRequest, execute_cli_chat
from core.providers.registry import (
    default_chat_model_for_provider,
    detect_available_cli_providers,
    get_configured_engine_api_key,
    get_requested_cli_providers,
    strip_engine_api_keys,
)
from core.model_router import ModelRouter
from core.skill_loader import AdaptiveSkillLoader  # runtime skill loader
from core.skill_feedback import SkillFeedbackLoop
from model_utils import (
    get_best_model,
    print_agent_model_summary,

    resolve_dynamic_model,
    _infer_engine_id,
    resolve_engine_for_available,
    _pick_anthropic_model,
    _pick_openai_model,
    get_dynamic_default_model,
    normalize_model_name,
    generate_content_with_self_heal,
    create_chat_with_self_heal,
)

def _safe_print(*args, **kwargs):
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    parts = []
    for a in args:
        text = str(a)
        try:
            text.encode(enc)
        except Exception:
            text = text.encode(enc, errors="replace").decode(enc, errors="replace")
        parts.append(text)
    builtins.print(*parts, **kwargs)

class FallbackRejectedError(RuntimeError):
    pass

# [중복 제거 완료] quick_guard, BANNED_*, build_child_env, run_isolated ->
# core/security_guard.py에 정의, core/utils.py를 통해 re-export됨.

# 4) Agent / Requirements
# =============================================================================
class AgentRunner:
    def __init__(self, model_router: ModelRouter | None = None):
        self.mr = model_router or ModelRouter()
        self._knowledge_skills = []
        self._skill_loader_cache: Dict[str, "AdaptiveSkillLoader"] = {}  # per-model loader cache
        self._current_model_name: str = "default"  # current model name

    def _resolve_system_prompt(self, agent: dict) -> str:
        direct = str(agent.get("system_ko", "")).strip()
        if direct:
            return direct
        prompt_obj = agent.get("prompt", {}) if isinstance(agent.get("prompt"), dict) else {}
        nested = str(prompt_obj.get("system_ko", "")).strip()
        if nested:
            return nested
        legacy = str(agent.get("system_prompt", "")).strip()
        if legacy:
            return legacy
        return "You are a helpful AI assistant."

    def _resolve_signature_lines(self, agent: dict) -> list[str]:
        lines = agent.get("signature_lines")
        if not lines and isinstance(agent.get("persona"), dict):
            lines = agent["persona"].get("signature_lines")
        if isinstance(lines, list):
            return [str(x) for x in lines if str(x).strip()]
        return []

    def _build_runtime_system_prompt(self, agent: dict) -> str:
        prompt = inject_documentation_contract(self._resolve_system_prompt(agent))
        prompt = inject_implementation_language_contract(prompt)
        prompt = inject_destructive_guard_contract(prompt)
        return inject_thinking_contract(prompt)

    def _build_policy(self, agent: dict, loaded_skill_ids: list[str]) -> dict:
        rr = agent.get("runtime_rules", {}) if isinstance(agent, dict) else {}
        if not isinstance(rr, dict):
            rr = {}

        loaded = {safe_id(s) for s in loaded_skill_ids if safe_id(s)}
        declared = {safe_id(str(x)) for x in (agent.get("skills") or []) if str(x).strip()} if isinstance(agent, dict) else set()

        allowed_skills_raw = {safe_id(str(x)) for x in (rr.get("allowed_skills") or []) if str(x).strip()}
        approval_skills_raw = {safe_id(str(x)) for x in (rr.get("approval_required_skills") or []) if str(x).strip()}
        explicit_approval_tools = {safe_id(str(x)) for x in (rr.get("explicit_approval_required") or []) if str(x).strip()}
        allowed_tools = {safe_id(str(x)) for x in (rr.get("allowed_tools") or []) if str(x).strip()}
        approval_tools = {safe_id(str(x)) for x in (rr.get("approval_required_tools") or []) if str(x).strip()}

        known_local = loaded | declared
        allowed_local = allowed_skills_raw & known_local
        approval_local = approval_skills_raw & known_local

        default_deny = bool(rr.get("default_deny", False))
        enforce_allow = default_deny and bool(loaded or allowed_tools or allowed_local)

        # Safe baseline: when default_deny is on, allow only canonical skill entrypoints
        # unless explicitly opened via allowed_tools or allow_all_local.
        baseline_tools = {"propose", "apply", "test"}
        allow_all_local = bool(rr.get("allow_all_local", False))

        if default_deny and allowed_skills_raw and not allowed_local and not allowed_tools:
            unresolved = sorted([x for x in allowed_skills_raw if x and x not in known_local])
            if unresolved:
                # Fallback: Treat unresolved allowed_skills as allowed_tools (function names)
                allowed_tools.update(unresolved)

        if default_deny and approval_skills_raw and not approval_local and not approval_tools:
            unresolved_app = sorted([x for x in approval_skills_raw if x and x not in known_local])
            if unresolved_app:
                approval_tools.update(unresolved_app)

        return {
            "enforce_allow": enforce_allow,
            "allowed_local": allowed_local,
            "approval_local": approval_local,
            "allowed_tools": allowed_tools,
            "approval_tools": approval_tools | explicit_approval_tools,
            "loaded_local": loaded,
            "baseline_tools": baseline_tools,
            "allow_all_local": allow_all_local,
        }

    def _is_tool_allowed(self, policy: dict, skill_id: str, tool_name: str) -> bool:
        if not policy.get("enforce_allow", False):
            return True

        sid = safe_id(skill_id)
        tname = safe_id(tool_name)
        allowed_tools = policy.get("allowed_tools", set())
        baseline_tools = policy.get("baseline_tools", {"propose", "apply", "test"})
        allow_all_local = bool(policy.get("allow_all_local", False))
        allowed_local = policy.get("allowed_local", set())

        # Global tool allow-list has highest priority.
        if tname and tname in allowed_tools:
            return True

        # Explicitly allowed local skills unlock all their functions.
        if sid and sid in allowed_local:
            return True

        # Safe fallback: loaded skills can execute canonical entrypoints only
        # when no explicit allow-list was configured.
        if (
            sid
            and sid in policy.get("loaded_local", set())
            and tname in baseline_tools
            and not allowed_local
            and not allowed_tools
        ):
            return True

        return False

    def _requires_tool_approval(self, policy: dict, skill_id: str, tool_name: str) -> bool:
        sid = safe_id(skill_id)
        tname = safe_id(tool_name)
        if sid and sid in policy.get("approval_local", set()):
            return True
        if tname and tname in policy.get("approval_tools", set()):
            return True
        return False

    def _ask_tool_approval(self, fname: str, skill_id: str) -> bool:
        try:
            print("\n[Approval Request]")
            print(f"- Tool: {fname}")
            print(f"- Skill: {skill_id if skill_id else 'unknown'}")
            ans = input("Allow this tool execution? (yes/no): ").strip().lower()
            return ans in ("y", "yes")
        except Exception:
            return False

    def _list_approval_required_tools(self, tool_functions: list, policy: dict):
        needs = []
        for fn in tool_functions:
            fname = str(getattr(fn, "__name__", "unknown"))
            sid = safe_id(str(getattr(fn, "_skill_id", "")))
            if self._requires_tool_approval(policy, sid, fname):
                needs.append((sid or "unknown", fname))
        if not needs:
            return
        print("[Approval Required] The following tools require approval before execution:")
        for sid, fname in needs:
            print(f"- skill  / tool ")
        print("Respond with yes/y to allow execution.")

    def _model_family(self, model_name: str) -> str:
        normalized = str(model_name or "").strip().lower()
        if not normalized:
            return ""
        if normalized.startswith("models/"):
            normalized = normalized[7:]
        if normalized.startswith("claude"):
            return "anthropic"
        if normalized.startswith("codex") or normalized.startswith("gpt"):
            return "openai"
        if len(normalized) > 1 and normalized[0] == "o" and normalized[1].isdigit():
            return "openai"
        if normalized.startswith("gemini"):
            return "google"
        return ""

    def _resolve_cli_model(self, provider_id: str, requested_model: str) -> str:
        family = self._model_family(requested_model)
        provider_key = str(provider_id or "").strip().lower()
        if provider_key == "claude_cli" and family == "anthropic":
            return str(requested_model).strip()
        if provider_key == "codex_cli" and family == "openai":
            return str(requested_model).strip()
        if provider_key == "gemini_cli" and family == "google":
            return normalize_model_name(requested_model)
        return default_chat_model_for_provider(provider_key)

    def _preferred_native_model(self, backend: str, requested_model: str, agent: dict, is_complex: bool) -> str:
        family = self._model_family(requested_model)
        role_summary = agent.get("role", "") or (agent.get("identity", {}) or {}).get("role_summary", "") or agent.get("name", "")
        engine_id = _infer_engine_id(role_summary)
        if backend == "openai":
            if family == "openai" and str(requested_model).strip():
                return str(requested_model).strip()
            if engine_id == "reasoner_o":
                return _pick_openai_model(prefer_reasoning=True) or "gpt-5"
            return _pick_openai_model(prefer_reasoning=False, prefer_mini=not is_complex) or ("gpt-5-mini" if not is_complex else "gpt-5")
        if backend == "anthropic":
            if family == "anthropic" and str(requested_model).strip():
                return str(requested_model).strip()
            tier = "sonnet"
            if engine_id == "architect_claude":
                tier = "opus"
            elif not is_complex:
                tier = "haiku"
            return _pick_anthropic_model(tier) or "claude"
        if family == "google" and str(requested_model).strip():
            return normalize_model_name(requested_model)
        return normalize_model_name(get_dynamic_default_model("pro" if is_complex else "flash"))

    def _native_backend_order(self, requested_model: str, agent: dict) -> list[str]:
        requested_family = self._model_family(requested_model)
        role_summary = agent.get("role", "") or (agent.get("identity", {}) or {}).get("role_summary", "") or agent.get("name", "")
        engine_id = _infer_engine_id(role_summary)
        if engine_id in {"architect_claude", "coder_claude"}:
            preferred = ["anthropic", "openai", "google"]
        elif engine_id in {"manager_gpt", "reasoner_o", "codex"}:
            preferred = ["openai", "anthropic", "google"]
        else:
            preferred = ["google", "anthropic", "openai"]
        ordered = []
        for backend in [requested_family, *preferred, "anthropic", "openai", "google"]:
            if backend and backend not in ordered:
                ordered.append(backend)
        return ordered

    def _build_native_api_plan(self, agent: dict, requested_model: str, is_complex: bool, native_keys: dict[str, str]) -> list[dict]:
        plan = []
        seen = set()
        for backend in self._native_backend_order(requested_model, agent):
            if not native_keys.get(backend):
                continue
            candidate_model = self._preferred_native_model(backend, requested_model, agent, is_complex)
            key = (backend, str(candidate_model).strip())
            if key in seen or not key[1]:
                continue
            seen.add(key)
            plan.append({"backend": backend, "model": candidate_model})
        return plan

    def _extract_openai_text(self, response) -> str:
        text = str(getattr(response, "output_text", "") or "").strip()
        if text:
            return text
        parts = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", "") != "message":
                continue
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", "") in {"output_text", "text"}:
                    value = str(getattr(content, "text", "") or "").strip()
                    if value:
                        parts.append(value)
        return "\n".join(parts).strip()

    def _is_model_access_error(self, message: str) -> bool:
        text = str(message or "").lower()
        markers = (
            "not found",
            "does not exist",
            "unknown model",
            "unsupported model",
            "invalid model",
            "access to model",
            "do not have access",
            "not available for your account",
        )
        return any(marker in text for marker in markers)

    def _run_with_openai_responses(
        self,
        model_name: str,
        sys_prompt: str,
        task_input: str,
        tool_functions: list,
        *,
        api_key: str,
        fallback_models: list[str] | None = None,
    ) -> str:
        if not api_key or OpenAI is None:
            return ""

        tools = ", ".join(sorted({t.__name__ for t in tool_functions})) if tool_functions else "none"
        prompt = (
            f"{sys_prompt}\n\n"
            f"[Task]\n{task_input}\n\n"
            f"[Available Tools]\n{tools}\n"
            "Tools are disabled on the OpenAI fallback path. Provide executable steps and results in text."
        )
        client = OpenAI(api_key=api_key)
        candidates = []
        for raw_model in [model_name, *(fallback_models or [])]:
            candidate = str(raw_model or "").strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            for attempt in range(3):
                try:
                    response = client.responses.create(model=candidate, input=prompt)
                    text = self._extract_openai_text(response)
                    if text.strip():
                        _safe_print(text.strip())
                        return text.strip()
                    break
                except Exception as exc:
                    msg = str(exc).lower()
                    if "429" in msg or "rate" in msg or "quota" in msg:
                        time.sleep(5 * (attempt + 1))
                        continue
                    if self._is_model_access_error(msg):
                        break
                    _safe_print(f"[Runner] OpenAI execution error: {exc}")
                    return ""
        return ""

    def _extract_anthropic_text(self, payload: dict) -> str:
        parts = []
        for item in payload.get("content", []) or []:
            if str(item.get("type", "")).strip() != "text":
                continue
            value = str(item.get("text", "") or "").strip()
            if value:
                parts.append(value)
        return "\n".join(parts).strip()

    def _run_with_anthropic_api(
        self,
        model_name: str,
        sys_prompt: str,
        task_input: str,
        tool_functions: list,
        *,
        api_key: str,
        fallback_models: list[str] | None = None,
    ) -> str:
        if not api_key:
            return ""

        tools = ", ".join(sorted({t.__name__ for t in tool_functions})) if tool_functions else "none"
        prompt = (
            f"{sys_prompt}\n\n"
            f"[Task]\n{task_input}\n\n"
            f"[Available Tools]\n{tools}\n"
            "Tools are disabled on the Anthropic fallback path. Provide executable steps and results in text."
        )
        candidates = []
        for raw_model in [model_name, *(fallback_models or [])]:
            candidate = str(raw_model or "").strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            for attempt in range(3):
                # system 블록에 cache_control 추가 — Anthropic prompt caching 활용
                # 변경되지 않는 시스템 프롬프트를 캐싱하여 비용 및 지연 절감
                system_blocks = [
                    {
                        "type": "text",
                        "text": sys_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
                payload = json.dumps(
                    {
                        "model": candidate,
                        "max_tokens": 4096,
                        "system": system_blocks,
                        "messages": [{"role": "user", "content": task_input}],
                    }
                ).encode("utf-8")
                request = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=payload,
                    headers={
                        "content-type": "application/json",
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "anthropic-beta": "prompt-caching-2024-07-31",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=60) as response:
                        data = json.loads(response.read().decode("utf-8"))
                    text = self._extract_anthropic_text(data)
                    if text.strip():
                        _safe_print(text.strip())
                        return text.strip()
                    break
                except urllib.error.HTTPError as exc:
                    body = str(exc.read().decode("utf-8", errors="replace") or "")
                    msg = f"{exc} {body}".lower()
                    if exc.code == 429:
                        time.sleep(5 * (attempt + 1))
                        continue
                    if self._is_model_access_error(msg):
                        break
                    _safe_print(f"[Runner] Anthropic execution error: {exc}")
                    return ""
                except Exception as exc:
                    msg = str(exc).lower()
                    if self._is_model_access_error(msg):
                        break
                    _safe_print(f"[Runner] Anthropic execution error: {exc}")
                    return ""
        return ""

    def _agent_prefers_codex(self, agent: dict, model_name: str) -> bool:
        if is_codex_model(model_name):
            return True
        if is_claude_model(model_name):
            # Prefer Codex when a usable Codex execution path exists for this agent.
            return True
        engine = str(agent.get("engine", "")).strip().lower()
        if "codex" in engine:
            return True
        runtime_rules = agent.get("runtime_rules", {}) if isinstance(agent, dict) else {}
        if bool(runtime_rules.get("codex_enabled", False)):
            return True
        directive = str(runtime_rules.get("codex_directive", "")).strip().lower()
        return "codex" in directive

    def _run_with_codex(self, model_name: str, sys_prompt: str, task_input: str, tool_functions: list) -> bool:
        if not OPENAI_API_KEY:
            print("⚠️ [Runner] OPENAI_API_KEY가 없어 Codex 경로를 사용할 수 없습니다.")
            return False
        if OpenAI is None:
            print("⚠️ [Runner] openai 패키지가 없어 Codex 경로를 사용할 수 없습니다.")
            return False

        codex_model = model_name if is_codex_model(model_name) else "codex-5.3"
        tools = ", ".join(sorted({t.__name__ for t in tool_functions})) if tool_functions else "none"
        prompt = (
            f"{sys_prompt}\n\n"
            f"[Task]\n{task_input}\n\n"
            f"[Available Tools]\n{tools}\n"
            "도구 호출은 현재 Codex 경로에서 비활성화되어 있으니, 실행 가능한 지시와 설계안을 우선 제시하세요."
        )

        client = OpenAI(api_key=OPENAI_API_KEY)
        for i in range(3):
            try:
                resp = client.responses.create(model=codex_model, input=prompt)
                text = getattr(resp, "output_text", "") or ""
                if not text:
                    try:
                        chunks = []
                        for item in getattr(resp, "output", []) or []:
                            if getattr(item, "type", "") != "message":
                                continue
                            for c in getattr(item, "content", []) or []:
                                c_type = getattr(c, "type", "")
                                if c_type in ("output_text", "text"):
                                    chunks.append(getattr(c, "text", ""))
                        text = "\n".join([c for c in chunks if c])
                    except Exception:
                        text = ""

                if text.strip():
                    print(f"🤖 {text.strip()}")
                    return True
                return False
            except Exception as e:
                msg = str(e).lower()
                if "429" in msg or "rate" in msg or "quota" in msg:
                    wait = 5 * (i + 1)
                    print(f"⏳ [Quota] Codex API 사용량 제한. {wait}초 대기 중... ({i+1}/3)")
                    time.sleep(wait)
                    continue
                print(f"⚠️ [Runner] Codex 실행 오류: {e}")
                return False
        return False

    def _run_with_cli_provider(
        self,
        provider_id: str,
        model_name: str,
        sys_prompt: str,
        task_input: str,
        workspace: str,
        run_id: str,
        auto_approve: bool = False,
    ) -> dict:
        return execute_cli_chat(
            CliChatRequest(
                provider_id=provider_id,
                model=model_name,
                system_prompt=sys_prompt,
                task_input=task_input,
                workspace=workspace,
                run_id=run_id,
                auto_approve=auto_approve,
            )
        )

    def _get_skill_loader(self, model_name: str):
        """
        Resolve an AdaptiveSkillLoader lazily and cache it by model name.

        Args:
            model_name: target model name

        Returns:
            AdaptiveSkillLoader instance for the requested model
        """
        if model_name not in self._skill_loader_cache:
            # MIN-4: avoid repeated lazy-loader import setup
            self._skill_loader_cache[model_name] = AdaptiveSkillLoader.for_model(model_name)
        return self._skill_loader_cache[model_name]

    _SKILL_CACHE_MAX = 64

    def load_skills(self, agent: dict, task_input: str = "") -> list:
        # Legacy support + caching (bounded to _SKILL_CACHE_MAX entries)
        if not hasattr(self, "_skill_module_cache"):
            self._skill_module_cache = {}
        self._knowledge_skills = []

        loaded_skills = []
        skill_ids = agent.get("skills", [])

        if not skill_ids and task_input:
            try:
                loader = self._get_skill_loader(self._current_model_name)
                selected, scores = loader.load_skills_for_task(task_input, verbose=True)
                skill_ids = [s.skill_id for s in selected]
                if skill_ids:
                    _safe_print(f"[Runner] Task-based auto skill selection: {len(skill_ids)}")
                    for sid in skill_ids:
                        sc = scores.get(sid, 0.0)
                        _safe_print(f"   - {sid} (score: {sc:.2f})")
            except Exception as e:
                _safe_print(f"[Runner] auto skill selection failed; falling back to declared skills: {e}")

        for sid in skill_ids:
            sid = safe_id(str(sid))
            skill_py, skill_meta = resolve_skill_paths(sid)

            if skill_py and os.path.exists(skill_py):
                try:
                    cur_mtime = os.path.getmtime(skill_py)
                    if sid in self._skill_module_cache:
                        cached_py, cached_mtime, cached_mod = self._skill_module_cache[sid]
                        if cached_py == skill_py and cached_mtime == cur_mtime:
                            loaded_skills.append(cached_mod)
                            continue

                    spec = importlib.util.spec_from_file_location(f"skills.{sid}", skill_py)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[f"skills.{sid}"] = module
                        spec.loader.exec_module(module)
                        setattr(module, "__skill_id__", sid)

                        self._skill_module_cache[sid] = (skill_py, cur_mtime, module)
                        # 캐시 크기 제한: 초과 시 가장 오래된 항목 제거
                        if len(self._skill_module_cache) > self._SKILL_CACHE_MAX:
                            oldest_key = next(iter(self._skill_module_cache))
                            self._skill_module_cache.pop(oldest_key, None)
                            sys.modules.pop(f"skills.{oldest_key}", None)
                        loaded_skills.append(module)
                        _safe_print(f"[Runner] Action skill loaded: {sid}")
                except Exception as e:
                    _safe_print(f"[Runner] Action skill load failed ({sid}): {e}")
            else:
                from core.skill_procurer import WAREHOUSE_DIR, FORGE_DIR
                from core.knowledge_skill import parse_skill_md

                md_path = resolve_knowledge_skill_path(sid, extra_roots=[WAREHOUSE_DIR, FORGE_DIR])
                if md_path:
                    try:
                        cur_mtime = os.path.getmtime(md_path)
                        existing_k = next((k for k in self._knowledge_skills if k.id == sid), None)
                        if existing_k and existing_k.updated_at == cur_mtime:
                            pass
                        else:
                            k_skill = parse_skill_md(md_path)
                            if k_skill:
                                if existing_k:
                                    self._knowledge_skills.remove(existing_k)
                                self._knowledge_skills.append(k_skill)
                                _safe_print(f"[Runner] Knowledge skill loaded: {sid}")
                    except Exception as e:
                        _safe_print(f"[Runner] Knowledge skill load failed ({sid}): {e}")
                else:
                    _safe_print(f"[Runner] Skill source (.py/.md) not found: {sid}")

        from core.langchain_adapter import LANGCHAIN_AVAILABLE, LangChainToolAdapter
        if LANGCHAIN_AVAILABLE:
            try:
                from langchain_core.tools import BaseTool as _LCBaseTool
                wrapped = []
                for mod in loaded_skills:
                    if isinstance(mod, _LCBaseTool):
                        wrapped.append(LangChainToolAdapter(mod))
                    else:
                        wrapped.append(mod)
                loaded_skills = wrapped
            except ImportError:
                pass

        return loaded_skills

    def _collect_loaded_skill_ids(self, module_list: list) -> list[str]:
        loaded: list[str] = []
        for module in module_list or []:
            skill_id = safe_id(str(getattr(module, "__skill_id__", "") or getattr(module, "skill_id", "")))
            if skill_id and skill_id not in loaded:
                loaded.append(skill_id)
        for knowledge_skill in getattr(self, "_knowledge_skills", []) or []:
            skill_id = safe_id(str(getattr(knowledge_skill, "id", "")))
            if skill_id and skill_id not in loaded:
                loaded.append(skill_id)
        return loaded

    def _resolve_runtime_feedback_targets(
        self,
        used_skill_ids: list[str] | set[str],
        loaded_skill_ids: list[str],
        requested_skill_ids: list[str],
    ) -> list[str]:
        preferred = [safe_id(str(skill_id)) for skill_id in (used_skill_ids or []) if safe_id(str(skill_id))]
        deduped_preferred: list[str] = []
        for skill_id in preferred:
            if skill_id not in deduped_preferred:
                deduped_preferred.append(skill_id)
        if deduped_preferred:
            return deduped_preferred

        loaded = [safe_id(str(skill_id)) for skill_id in (loaded_skill_ids or []) if safe_id(str(skill_id))]
        requested = [safe_id(str(skill_id)) for skill_id in (requested_skill_ids or []) if safe_id(str(skill_id))]
        combined: list[str] = []
        for skill_id in loaded + requested:
            if skill_id and skill_id not in combined:
                combined.append(skill_id)
        if len(combined) == 1:
            return combined
        return []

    def _mailbox_actor_role(self, agent: dict) -> str:
        return safe_id(agent.get("id") or agent.get("role") or agent.get("name") or "agent")

    def _mount_builtin_tools(self, registry: ToolRegistry, ctx: dict) -> ToolRegistry:
        workspace = str(ctx.get("workspace") or PROJECT_ROOT)
        agent = ctx.get("agent") if isinstance(ctx.get("agent"), dict) else {}
        actor_role = self._mailbox_actor_role(agent)
        current_task_id = safe_id(str(ctx.get("task_id") or ""))

        def _split_related_files(value: str) -> list[str]:
            raw_items = str(value or "").replace("\r", "\n").replace(",", "\n").split("\n")
            cleaned: list[str] = []
            for item in raw_items:
                text = str(item or "").strip().replace("\\", "/")
                if not text or text in cleaned:
                    continue
                cleaned.append(text)
            return cleaned

        def read_mailbox(task_id: str = "") -> str:
            """Read pending mailbox messages for this role. Optionally filter by task_id."""
            inbox = project_read_mailbox_inbox(
                workspace,
                actor_role,
                task_id=task_id or current_task_id,
                limit=20,
            )
            if not inbox:
                return "No pending mailbox messages."
            lines = [f"Pending mailbox messages for {actor_role}: {len(inbox)}"]
            for message in inbox:
                related = ", ".join(message.get("related_files") or []) or "-"
                ack_required = "yes" if bool(message.get("requires_ack")) else "no"
                lines.append(
                    f"- id={message.get('message_id')} [{message.get('type')}] from={message.get('from_role')} "
                    f"ack={ack_required} task={message.get('task_id') or '-'} files={related} body={message.get('body')}"
                )
            return "\n".join(lines)

        def send_mailbox_message(
            to_role: str,
            message_type: str,
            body: str,
            related_files: str = "",
            requires_ack: bool = False,
            task_id: str = "",
        ) -> str:
            """Send a structured mailbox message to another role."""
            message = project_send_mailbox_message(
                workspace=workspace,
                from_role=actor_role,
                to_role=to_role,
                message_type=message_type,
                body=body,
                task_id=task_id or current_task_id,
                related_files=_split_related_files(related_files),
                requires_ack=requires_ack,
            )
            return json.dumps(
                {
                    "ok": True,
                    "message_id": message.get("message_id"),
                    "thread_id": message.get("thread_id"),
                    "task_id": message.get("task_id"),
                    "to_role": message.get("to_role"),
                    "type": message.get("type"),
                },
                ensure_ascii=False,
            )

        def ack_mailbox_message(message_id: str) -> str:
            """Acknowledge a mailbox message that was addressed to this role."""
            ok = project_ack_mailbox_message(workspace, message_id, role=actor_role)
            if not ok:
                return json.dumps({"ok": False, "message_id": message_id}, ensure_ascii=False)
            return json.dumps({"ok": True, "message_id": message_id, "status": "acknowledged"}, ensure_ascii=False)

        registry.mount_tool("read_mailbox", read_mailbox)
        registry.mount_tool("send_mailbox_message", send_mailbox_message)
        registry.mount_tool("ack_mailbox_message", ack_mailbox_message)
        return registry

    def build_tool_registry(self, module_list: list, ctx: dict, policy: dict) -> ToolRegistry:
        """Adapts legacy modules into the precise V2 Tool Registry using ToolRuntimeWrapper"""
        wrapper = ToolRuntimeWrapper(base_dir=BASE_DIR)
        registry = wrapper.build_registry(module_list, ctx, policy, is_allowed_fn=self._is_tool_allowed)
        return self._mount_builtin_tools(registry, ctx)

    def run(self, agent: dict, task_input: str, run_id: str | None = None, auto_approve: bool = False, workspace: str | None = None, task_id: str = ""):
        print(f"\n🚀 [Runner] 에이전트 실행 시작: {agent.get('name')}")
        started = time.time()
        run_id = run_id or f"run_{int(started)}"
        target_workspace = os.path.abspath(workspace) if workspace else PROJECT_ROOT
        project_id = safe_id(os.path.basename(target_workspace)) if workspace else PROJECT_ID
        runs_dir = os.path.join(target_workspace, "runs") if workspace else RUNS_DIR
        data_dir = os.path.join(target_workspace, "data") if workspace else DATA_DIR
        artifacts_dir = os.path.join(target_workspace, "artifacts") if workspace else ARTIFACTS_DIR
        os.makedirs(runs_dir, exist_ok=True)
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(artifacts_dir, exist_ok=True)
        run_dir = os.path.join(runs_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)
        transcript = []
        requested_skill_ids = [safe_id(str(s)) for s in agent.get("skills", []) if str(s).strip()]
        loaded_skill_ids_runtime: list[str] = []
        used_skill_ids_runtime: set[str] = set()
        runtime_feedback_written = False
        feedback_loop = SkillFeedbackLoop.for_workspace(target_workspace, project_id=project_id)

        def _append_trace(kind: str, payload: dict):
            transcript.append({
                "ts": now_iso(),
                "kind": str(kind),
                "payload": payload if isinstance(payload, dict) else {"value": str(payload)},
            })

        def _record_runtime_feedback(result: dict):
            nonlocal runtime_feedback_written
            if runtime_feedback_written:
                return
            runtime_feedback_written = True
            target_skill_ids = self._resolve_runtime_feedback_targets(
                list(used_skill_ids_runtime),
                loaded_skill_ids_runtime,
                requested_skill_ids,
            )
            if not target_skill_ids:
                return
            for skill_id in target_skill_ids:
                try:
                    feedback_loop.record_runtime_result(
                        skill_id=skill_id,
                        ok=bool(result.get("ok", False)),
                        run_id=run_id,
                        agent_role=str(agent.get("role", "")),
                        payload={
                            "reason": str(result.get("reason") or ""),
                            "latency_ms": int(result.get("latency_ms") or 0),
                            "approval_rejects": int(result.get("approval_rejects") or 0),
                            "loaded_skill_ids": list(target_skill_ids),
                            "workspace": target_workspace,
                        },
                    )
                except Exception as exc:
                    _safe_print(f"[Feedback] runtime event write failed: {exc}")
                    break

        def _flush_trace(result: dict):
            # 글로벌 토큰 예산 기록
            try:
                from core.run_budget import get_run_budget
                _text = str(result.get("text", "") or "")
                if _text:
                    get_run_budget().record(_text)
            except Exception:
                pass
            data = {
                "run_id": run_id,
                "project_id": project_id,
                "agent_name": str(agent.get("name", "")),
                "agent_role": str(agent.get("role", "")),
                "task": str(task_input or ""),
                "task_id": safe_id(task_id),
                "transcript": transcript,
                "result": result,
                "updated_at": now_iso(),
            }
            _safe_write_json(os.path.join(run_dir, "chat_trace.json"), data)
            _record_runtime_feedback(result)
        approval_rejects = 0
        
        ctx = {
            "agent": agent,
            "data_dir": data_dir,
            "artifacts_dir": artifacts_dir,
            "workspace": target_workspace,
            "project_id": project_id,
            "task_input": task_input,
            "task_id": safe_id(task_id),
        }
        ok_ctx, msg_ctx = validate_context_with_schema(ctx)
        if not ok_ctx:
            _safe_print(f"[ContextSchema] validation failed: {msg_ctx}")
            print("Agent execution stopped.")
            result = {"ok": False, "reason": f"context_schema:{msg_ctx}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "context_schema", "message": str(msg_ctx)})
            _flush_trace(result)
            return result

        # Continuation Hook Enforcement via Event Bus
        bus = HookEventBus()
        bus.register(IntentGateHook())
        bus.register(TodoContinuationEnforcer())
        bus.register(ToolOutputTruncator())
        from core.hooks.context_fork import ContextForkHook
        bus.register(ContextForkHook())

        try:
            from core.hooks.lsp_check import LSPCheckHook
            bus.register(LSPCheckHook())
        except Exception as _lsp_err:
            _safe_print(f"[Runner] LSPCheckHook registration failed: {_lsp_err}")

        try:
            from core.hooks.skill_self_evolution import SkillSelfEvolutionHook
            from core.skill_evolution_bus import SkillEvolutionBus
            _sse_hook = SkillSelfEvolutionHook(check_interval=10)
            bus.register(_sse_hook)
            _evo_bus = SkillEvolutionBus.get_instance()
            _evo_bus.bind_runner(self)
            _evo_bus.bind_event_bus(bus)
        except Exception as _sse_err:
            _safe_print(f"[Runner] SkillSelfEvolutionHook registration failed: {_sse_err}")

        try:
            from core.hooks.code_review_doc import CodeReviewDocHook
            bus.register(CodeReviewDocHook())
        except Exception as _cr_err:
            _safe_print(f"[Runner] CodeReviewDocHook registration failed: {_cr_err}")

        try:
            from core.hooks.design_review_hook import DesignReviewHook
            bus.register(DesignReviewHook())
        except Exception as _dr_err:
            _safe_print(f"[Runner] DesignReviewHook registration failed: {_dr_err}")

        _mem_ki_hook = None
        _mem_mc_hook = None
        try:
            import asyncio as _asyncio
            from core.memory_system.knowledge_injection import KnowledgeInjectionHook
            from core.hooks.memory_consolidation import MemoryConsolidationHook
            from core.memory_system.facade import UnifiedMemoryFacade
            from core.memory_system.adapters.knowledge_graph import KnowledgeGraphAdapter
            from core.memory_system.adapters.core_memory import CoreMemoryAdapter

            _mem_ki_hook = KnowledgeInjectionHook()
            _mem_mc_hook = MemoryConsolidationHook()

            _agent_name = str(agent.get("name", ""))
            _mem_facade = UnifiedMemoryFacade(project_id=str(project_id or "agent_factory"))
            _mem_graph_adapter = KnowledgeGraphAdapter(workspace=str(target_workspace))
            _mem_facade.register_adapter(CoreMemoryAdapter(agent_id=_agent_name or None))
            _mem_facade.register_adapter(_mem_graph_adapter)
            try:
                _asyncio.run(_mem_facade.initialise())
            except RuntimeError:
                pass

            _mem_mc_hook.set_facade(_mem_facade)
            _mem_ki_hook.set_graph_adapter(_mem_graph_adapter)
            _mem_mc_hook.set_graph_adapter(_mem_graph_adapter)
            UnifiedMemoryFacade.set_instance(_mem_facade)

            bus.register(_mem_ki_hook)
            bus.register(_mem_mc_hook)
        except Exception as _mem_err:
            _safe_print(f"[Runner] Memory hooks registration failed: {_mem_err}")

        from core.model_router import print_startup_routing_notice
        print_startup_routing_notice()

        all_cli_providers = get_requested_cli_providers()
        role_summary = agent.get("role", "") or (agent.get("identity", {}) or {}).get("role_summary", "")
        agent_name = agent.get("name", "")

        # 역할 기반 최적 프로바이더를 첫 번째에, 나머지를 폴백으로 정렬
        if len(all_cli_providers) > 1:
            preferred = self.mr.pick_provider(agent_config=agent)
            if preferred and preferred in all_cli_providers:
                cli_providers = [preferred] + [p for p in all_cli_providers if p != preferred]
            else:
                cli_providers = all_cli_providers
        else:
            cli_providers = all_cli_providers
        engine_id = _infer_engine_id(role_summary or agent_name)

        # preferred provider 미구독 시 가용 provider 기반 engine으로 fallback
        _available = detect_available_cli_providers()
        _resolved = resolve_engine_for_available(engine_id, _available)
        if _resolved != engine_id:
            _safe_print(f"[Router] '{engine_id}' 선호 프로바이더 미구독 -> '{_resolved}'로 fallback")
            engine_id = _resolved

        is_complex = engine_id in ("architect_claude", "researcher_gemini", "coder_claude", "reasoner_o")
        if is_complex:
            _safe_print(f"[Router] '{engine_id}' -> complex task (role-based)")
        else:
            _safe_print(f"[Router] '{engine_id}' -> standard task")

        agent_state = {
            "run_id": run_id,
            "project_id": project_id,
            "agent_name": str(agent.get("name", "")),
            "agent": agent,
            "task_input": task_input,
            "task_id": safe_id(task_id),
            "intent": "complex_feature" if is_complex else "trivial",
            "workspace": target_workspace,
        }

        if not bus.run_pre_execute(agent_state):
            result = {"ok": False, "reason": "hook_event_bus_blocked_pre"}
            # Phase 3: run post-execute hooks even for blocked runs
            result = bus.run_post_execute(agent_state, result)
            _flush_trace(result)
            return result

        model_name = normalize_model_name(agent.get("preferred_model") or self.mr.pick("chat", agent_config=agent, is_complex=is_complex) or get_dynamic_default_model("flash"))
        self._current_model_name = model_name

        modules = self.load_skills(agent, task_input=task_input)
        loaded_skill_ids_runtime = self._collect_loaded_skill_ids(modules)
        policy_runner = PolicyRuntime(base_dir=BASE_DIR)
        policy = policy_runner.resolve_agent_policy(agent)

        registry = self.build_tool_registry(modules, ctx, policy)
        tool_functions = registry.get_active_tools()
        self._list_approval_required_tools(tool_functions, policy)

        # System Prompt construction
        sys_prompt = self._build_runtime_system_prompt(agent)

        # If KnowledgeInjectionHook populated agent_state, reflect it in sys_prompt.
        # Missing knowledge context is fine and should behave as a no-op.
        _knowledge_ctx = agent_state.get("_knowledge_context", "")
        if _knowledge_ctx:
            sys_prompt += f"\n\n{_knowledge_ctx}"

        # 특화 에이전트는 이미 system_ko에 메일박스 컨텍스트가 포함되어 있으므로
        # 프로토콜 안내만 추가하고 다이제스트 이중 주입을 방지한다.
        _mailbox_protocol = (
            "\n\n[Mailbox Protocol]\n"
            "Use `read_mailbox` at the start of the task and whenever you are blocked.\n"
            "Use `send_mailbox_message` for structured handoff, blockers, review requests, decisions, and results.\n"
            "`read_mailbox` includes each message_id. After you consume a message, call `ack_mailbox_message(message_id)`.\n"
            "Acknowledgement is required when requires_ack=true and recommended for all consumed messages so the inbox can clear."
        )
        sys_prompt += _mailbox_protocol
        if not agent.get("_specialized"):
            agent_role_id = self._mailbox_actor_role(agent)
            mailbox_digest = project_mailbox_prompt_digest(target_workspace, role=agent_role_id, task_id=task_id)
            if mailbox_digest != "No mailbox messages.":
                sys_prompt += f"\n\n[Agent Mailbox]\n{mailbox_digest}"

        # Knowledge Skill Injection -> CWM handles on-demand (legacy fallback preserved)
        _knowledge_for_cwm = getattr(self, '_knowledge_skills', []) or []

        # Proactive memory instruction
        skill_ids = [safe_id(str(s)) for s in agent.get("skills", [])]
        if "core_memory" in skill_ids:
            sys_prompt += (
                "\n\n[Memory Instruction]\n"
                "You have the `core_memory` skill available.\n"
                "When important project facts, preferences, decisions, or schedules appear, use `core_memory.store` proactively instead of waiting for an explicit reminder.\n"
                "Choose a sensible key and category based on the current context."
            )

        sigs = self._resolve_signature_lines(agent)
        if sigs:
            import random
            greeting = random.choice(sigs)
            print(f"[Agent] {greeting}")
            sys_prompt += f"\n\n[Signature]\n{greeting}"

        cli_failures = []
        native_keys = {
            "google": get_configured_engine_api_key("google"),
            "openai": get_configured_engine_api_key("openai"),
            "anthropic": get_configured_engine_api_key("anthropic"),
        }
        if cli_providers:
            for provider_id in cli_providers:
                cli_model = self._resolve_cli_model(provider_id, model_name)
                cli_result = self._run_with_cli_provider(
                    provider_id,
                    cli_model,
                    sys_prompt,
                    task_input,
                    target_workspace,
                    run_id=run_id,
                    auto_approve=auto_approve,
                )
                if cli_result.get("ok"):
                    cli_text = str(cli_result.get("text", "") or "").strip()
                    if cli_text:
                        print(f"{cli_text}")
                    result = {
                        "ok": True,
                        "reason": provider_id,
                        "latency_ms": int((time.time() - started) * 1000),
                        "approval_rejects": approval_rejects,
                    }
                    _append_trace(
                        "assistant",
                        {
                            "channel": provider_id,
                            "text": cli_text,
                            "model": cli_model,
                            "command": cli_result.get("command", []),
                        },
                    )
                    result = bus.run_post_execute(agent_state, result)
                    _flush_trace(result)
                    return result

                cli_failures.append(cli_result)
                _append_trace(
                    "error",
                    {
                        "stage": provider_id,
                        "message": str(cli_result.get("reason") or cli_result.get("stderr") or "cli_provider_failed"),
                    },
                )

            if cli_failures and not any(native_keys.values()):
                last_failure = cli_failures[-1]
                result = {
                    "ok": False,
                    "reason": str(last_failure.get("reason") or "cli_provider_failed"),
                    "latency_ms": int((time.time() - started) * 1000),
                    "approval_rejects": approval_rejects,
                }
                # Phase 5: run post-execute hooks for failed CLI execution
                result = bus.run_post_execute(agent_state, result)
                _flush_trace(result)
                return result

        gemini_model = ""
        for api_candidate in self._build_native_api_plan(agent, model_name, is_complex, native_keys):
            backend = str(api_candidate.get("backend") or "")
            candidate_model = str(api_candidate.get("model") or "")
            if backend == "openai":
                openai_text = self._run_with_openai_responses(
                    candidate_model,
                    sys_prompt,
                    task_input,
                    tool_functions,
                    api_key=native_keys["openai"],
                    fallback_models=[self._preferred_native_model("openai", "", agent, is_complex), "gpt-5"],
                )
                if openai_text:
                    print("Agent Execution Finished.")
                    result = {
                        "ok": True,
                        "reason": "openai",
                        "latency_ms": int((time.time() - started) * 1000),
                        "approval_rejects": approval_rejects,
                    }
                    _append_trace(
                        "assistant",
                        {
                            "channel": "openai",
                            "text": openai_text,
                            "model": candidate_model,
                        },
                    )
                    _flush_trace(result)
                    return result
                _append_trace("error", {"stage": "openai", "message": f"openai_failed:{candidate_model}"})
                continue

            if backend == "anthropic":
                anthropic_text = self._run_with_anthropic_api(
                    candidate_model,
                    sys_prompt,
                    task_input,
                    tool_functions,
                    api_key=native_keys["anthropic"],
                    fallback_models=[self._preferred_native_model("anthropic", "", agent, is_complex), "claude"],
                )
                if anthropic_text:
                    print("Agent Execution Finished.")
                    result = {
                        "ok": True,
                        "reason": "anthropic",
                        "latency_ms": int((time.time() - started) * 1000),
                        "approval_rejects": approval_rejects,
                    }
                    _append_trace(
                        "assistant",
                        {
                            "channel": "anthropic",
                            "text": anthropic_text,
                            "model": candidate_model,
                        },
                    )
                    _flush_trace(result)
                    return result
                _append_trace("error", {"stage": "anthropic", "message": f"anthropic_failed:{candidate_model}"})
                continue

            if backend == "google":
                gemini_model = normalize_model_name(candidate_model)
                break

        if not gemini_model:
            failure_reason = "no_callable_backend"
            if cli_failures:
                failure_reason = str(cli_failures[-1].get("reason") or "cli_provider_failed")
            result = {
                "ok": False,
                "reason": failure_reason,
                "latency_ms": int((time.time() - started) * 1000),
                "approval_rejects": approval_rejects,
            }
            _append_trace("error", {"stage": "bootstrap", "message": failure_reason})
            _flush_trace(result)
            return result
        try:
            # [Gemini SDK + CWM] ContextWindowManager-based initialization
            from google import genai
            from google.genai import types as genai_types
            from core.context_window_manager import ContextWindowManager

            gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

            # FIX #7: register get_knowledge so the model can request full knowledge-skill content
            _cwm_placeholder: list = []  # placeholder to keep the local name defined before _cwm exists

            def get_knowledge(skill_id: str) -> str:
                """Load the full content of a knowledge skill by its ID or name.
                Use this when you need detailed procedures from an available knowledge skill."""
                return _cwm.get_knowledge_content(skill_id)

            _tool_functions_with_knowledge = list(tool_functions) + [get_knowledge]

            # Initialize CWM with both the knowledge-skill set and tool-eviction policy
            _cwm = ContextWindowManager(
                model_name=str(gemini_model),
                system_prompt=sys_prompt,
                all_tools=_tool_functions_with_knowledge,
                knowledge_skills=_knowledge_for_cwm,
                evict_after_turns=3,
                recent_window=4,
            )
            # Mark get_knowledge as active in the tracker for the initial turn
            _cwm.tool_tracker.record_use("get_knowledge", turn=0)
        except Exception as e:
            import traceback
            print(f"[Runner] SDK/CWM Init Error: {str(e)}")
            traceback.print_exc()
            result = {"ok": False, "reason": "sdk_init_failed", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "sdk_init", "message": str(e)})
            _flush_trace(result)
            return result

        _append_trace("user", {"text": f"Task: {task_input}"})
        _append_trace("system", {"model": str(gemini_model), "skills": [str(s) for s in skill_ids]})

        # 429 Quota retry loop for generate_content()
        # FIX #5: keep track of the last exception for final surfacing
        def safe_generate(contents, config):
            if not contents:
                raise ValueError("Empty contents list passed to generate_content()")  # FIX #12
            max_retries = 3
            last_error: Exception | None = None
            for i in range(max_retries):
                try:
                    return gemini_client.models.generate_content(
                        model=gemini_model,
                        contents=contents,
                        config=config,
                    )
                except Exception as exc:
                    last_error = exc
                    if "429" in str(exc) or "quota" in str(exc).lower() or "resource exhausted" in str(exc).lower():
                        wait = 5 * (i + 1)
                        print(f"[Quota] API Rate Limit (429). {wait}s wait... ({i+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    raise  # Re-raise non-429 errors immediately
            raise last_error or Exception("API Rate Limit Exceeded (Quota)")

        try:
            # Record the initial user message in CWM history
            _cwm.add_user_message(f"Task: {task_input}", turn=0)

            # CWM-based ReAct loop
            for turn in range(10):
                # Fetch generation settings for this turn
                gen_config = _cwm.get_generate_config(turn, genai_types=genai_types)
                response = safe_generate(
                    contents=gen_config["contents"],
                    config=genai_types.GenerateContentConfig(
                        system_instruction=gen_config["system_instruction"],
                        tools=gen_config["tools"],
                        # agent_runner가 수동으로 함수 호출을 처리하므로
                        # SDK 자동 실행(automatic function calling)을 비활성화
                        # 비활성화하지 않으면 SDK가 미등록 도구 호출 시 KeyError 발생
                        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                )

                if not response.parts:
                    if not response.candidates:
                        pass
                    break

                        # 캐시 갱신 확인
                _cwm.record_model_response(response, turn)

                has_action = False
                for part in response.parts:
                    # 1. Output Text
                    if hasattr(part, "text") and part.text:
                        print(f"{part.text}", flush=True)
                        _append_trace("assistant", {"text": str(part.text)})

                    # 2. Function Call
                    if hasattr(part, "function_call") and part.function_call:
                        has_action = True
                        fc = part.function_call
                        fname = fc.name
                        fargs = dict(fc.args)
                        print(f"[Tool] {fname}({fargs})", flush=True)
                        _append_trace("tool_call", {"name": str(fname), "args": fargs})

                        # Tool lookup across runtime tools plus the synthetic knowledge helper
                        tool_func = next((t for t in _tool_functions_with_knowledge if t.__name__ == fname), None)
                        if tool_func:
                            try:
                                skill_id = safe_id(str(getattr(tool_func, "_skill_id", "")))
                                if self._requires_tool_approval(policy, skill_id, fname):
                                    if not self._ask_tool_approval(fname, skill_id):
                                        print(f"[Policy] Approval rejected: {fname}", flush=True)
                                        approval_rejects += 1
                                        _append_trace("tool_reject", {"name": str(fname), "skill_id": str(skill_id)})
                                        # FIX #4: mirror function_response into CWM (avoid role mismatch)
                                        _cwm.record_tool_call(fname, turn)
                                        _cwm.record_tool_result(fname, "[rejected: approval denied]", turn)
                                        continue
                                tool_decision = bus.run_pre_tool_call(agent_state, fname, fargs)
                                if not tool_decision.allowed:
                                    approval_rejects += 1
                                    _append_trace(
                                        "tool_reject",
                                        {
                                            "name": str(fname),
                                            "skill_id": str(skill_id),
                                            "reason": str(tool_decision.reason or "blocked_by_hook"),
                                        },
                                    )
                                    # FIX #4: mirror function_response into CWM (avoid role mismatch)
                                    _cwm.record_tool_call(fname, turn)
                                    _cwm.record_tool_result(
                                        fname,
                                        f"[blocked: {tool_decision.reason or 'blocked_by_hook'}]",
                                        turn,
                                    )
                                    continue

                                # Execute
                                if skill_id:
                                    used_skill_ids_runtime.add(skill_id)
                                res_obj = tool_func(**dict(tool_decision.tool_args or fargs))
                                res_obj = bus.run_post_tool_call(agent_state, fname, res_obj)

                                print(f"  -> Result: {str(res_obj)[:100]}...", flush=True)
                                _append_trace("tool_result", {"name": str(fname), "result": str(res_obj)[:800]})

                                # Record both tool invocation and tool result in CWM
                                _cwm.record_tool_call(fname, turn)
                                _cwm.record_tool_result(fname, res_obj, turn)
                            except Exception as e:
                                print(f"[Tool Error] {fname}: {e}", flush=True)
                                _append_trace("tool_error", {"name": str(fname), "message": str(e)})
                                # FIX #4: mirror function_response into CWM (avoid role mismatch)
                                _cwm.record_tool_result(fname, f"[error: {e}]", turn)
                        else:
                            print(f"[Runner] Unknown tool: {fname}", flush=True)
                            # FIX #4: mirror function_response into CWM (avoid role mismatch)
                            _cwm.record_tool_result(fname, f"[error: unknown tool '{fname}']", turn)

                if not has_action:
                    break

            # Print CWM stats
            _cwm_stats = _cwm.get_stats()
            _safe_print(f"[CWM] history={_cwm_stats['history']['total_tokens']}tok "
                        f"compressed={_cwm_stats['history']['compressed_entries']} "
                        f"saved={_cwm_stats['history']['saved_tokens']}tok")
            print("Agent Execution Finished.")
            result = {"ok": True, "reason": "gemini", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            # Phase 3: post-execute hook
            result = bus.run_post_execute(agent_state, result)
            _flush_trace(result)
            return result

        except Exception as e:
            import traceback
            print(f"[Runner] Execution error: {e}")
            traceback.print_exc()
            print("Execution failed. Check logs for details.")
            result = {"ok": False, "reason": f"runner_error:{type(e).__name__}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "runner", "message": str(e)})
            # Phase 3: post-execute hook
            result = bus.run_post_execute(agent_state, result)
            _flush_trace(result)
            return result


# =============================================================================
# 7) Factory
# =============================================================================









