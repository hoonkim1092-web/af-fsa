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
from core.documentation_policy import inject_documentation_contract
from core.destructive_guard import inject_destructive_guard_contract
from core.hooks.event_bus import HookEventBus
from core.hooks.guardrails import IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator
from core.providers.cli import CliChatRequest, execute_cli_chat
from core.providers.registry import (
    default_chat_model_for_provider,
    get_configured_engine_api_key,
    get_requested_cli_providers,
    strip_engine_api_keys,
)
from core.model_router import ModelRouter
from model_utils import (
    get_best_model,
    print_agent_model_summary,

    resolve_dynamic_model,
    _infer_engine_id,
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

# [繞벿살탮????蹂ㅽ깴 ?熬곣뫁?? quick_guard, BANNED_*, build_child_env, run_isolated ->
# core/security_guard.py???筌먦끉踰? core/utils.py???????re-export??

# 4) Agent / Requirements
# =============================================================================
class AgentRunner:
    def __init__(self, model_router: ModelRouter | None = None):
        self.mr = model_router or ModelRouter()
        self._knowledge_skills = []

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
        return "?獄???? ??ル‘???AI ??怨룸뻣???꾩돇?筌뤾쑴肉???덈펲."

    def _resolve_signature_lines(self, agent: dict) -> list[str]:
        lines = agent.get("signature_lines")
        if not lines and isinstance(agent.get("persona"), dict):
            lines = agent["persona"].get("signature_lines")
        if isinstance(lines, list):
            return [str(x) for x in lines if str(x).strip()]
        return []

    def _build_runtime_system_prompt(self, agent: dict) -> str:
        prompt = inject_documentation_contract(self._resolve_system_prompt(agent))
        return inject_destructive_guard_contract(prompt)

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
            print("\n[?獄?????븐슙??")
            print(f"- ?熬곥룗?? {fname}")
            print(f"- ???꾪뀬: {skill_id if skill_id else 'unknown'}")
            ans = input("???熬곥룗?????덈뺄?????깅뮔??ル맪??? (yes/no): ").strip().lower()
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
                payload = json.dumps(
                    {
                        "model": candidate,
                        "max_tokens": 4096,
                        "system": sys_prompt,
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
            # Claude ???깆젧 ??戮?뱺?????덈뺄 ?띠럾??繞③뇡?Codex ?롪퍔?δ빳?꾨ご???⑥ろ맖 ??類ｌ┣??類ｋ펲.
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
            print("??ル쵑??[Runner] OPENAI_API_KEY?띠럾? ??怨룹꽑 Codex ?롪퍔?δ빳?꾨ご??????????怨룸????덈펲.")
            return False
        if OpenAI is None:
            print("??ル쵑??[Runner] openai ????뺟춯?뼿?띠럾? ??怨룹꽑 Codex ?롪퍔?δ빳?꾨ご??????????怨룸????덈펲.")
            return False

        codex_model = model_name if is_codex_model(model_name) else "codex-5.3"
        tools = ", ".join(sorted({t.__name__ for t in tool_functions})) if tool_functions else "none"
        prompt = (
            f"{sys_prompt}\n\n"
            f"[Task]\n{task_input}\n\n"
            f"[Available Tools]\n{tools}\n"
            "?熬곥룗???筌뤾쑵??? ?熬곣뫗??Codex ?롪퍔?δ빳??????????繹먮봿???琉우꽑 ???깅さ?? ???덈뺄 ?띠럾??繞③뇡?嶺뚯솘???? ??釉붋???깅굵 ??⑥ろ맖 ??戮?뻣??琉얠돪??"
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
                    print(f"?鸚?{text.strip()}")
                    return True
                return False
            except Exception as e:
                msg = str(e).lower()
                if "429" in msg or "rate" in msg or "quota" in msg:
                    wait = 5 * (i + 1)
                    print(f"??[Quota] Codex API ????????ル┰. {wait}??????繞?.. ({i+1}/3)")
                    time.sleep(wait)
                    continue
                print(f"??ル쵑??[Runner] Codex ???덈뺄 ???댁쾼: {e}")
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

    def load_skills(self, agent: dict, task_input: str = "") -> list:
        MAX_ACTIVE_SKILLS = 12

        # Legacy support + Caching
        if not hasattr(self, '_skill_module_cache'):
            self._skill_module_cache = {}
        self._knowledge_skills = []

        loaded_skills = []
        skill_ids = agent.get("skills", [])

        # 스킬이 명시되지 않았으면 DynamicSkillLoader로 자동 선택
        if not skill_ids and task_input:
            try:
                from core.skill_loader import DynamicSkillLoader
                loader = DynamicSkillLoader()
                selected, scores = loader.load_skills_for_task(task_input, verbose=True)
                skill_ids = [s.skill_id for s in selected]
                if skill_ids:
                    _safe_print(f"🔄 [Runner] 작업 기반 자동 스킬 선택: {len(skill_ids)}개")
                    for sid in skill_ids:
                        sc = scores.get(sid, 0.0)
                        _safe_print(f"   - {sid} (관련성: {sc:.2f})")
            except Exception as e:
                _safe_print(f"⚠️ [Runner] 자동 스킬 선택 실패, 스킬 없이 진행: {e}")
        for sid in skill_ids:
            sid = safe_id(str(sid))
            skill_py, skill_meta = resolve_skill_paths(sid)
            
            # Action (Python) 嶺뚳퐣瑗??
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
                        loaded_skills.append(module)
                        _safe_print(f"??[Runner] Action ???꾪뀬 ?β돦裕녻キ??繹먭퍓沅? {sid}")
                except Exception as e:
                    _safe_print(f"??ル쵑??[Runner] Action ???꾪뀬 ?β돦裕녻キ????덉넮 ({sid}): {e}")
                    
            # Knowledge (Markdown) 嶺뚳퐣瑗??
            else:
                # WAREHOUSE_DIR / FORGE_DIR ??戮곕さ??.md ???노뼌 (?곌랜???WAREHOUSE/sid/skill.md)
                from core.skill_procurer import WAREHOUSE_DIR, FORGE_DIR
                from core.knowledge_skill import parse_skill_md
                
                md_path = resolve_knowledge_skill_path(sid, extra_roots=[WAREHOUSE_DIR, FORGE_DIR])
                        
                if md_path:
                    try:
                        cur_mtime = os.path.getmtime(md_path)
                        # 嶺?흮???띠룄????筌먦끉逾?
                        existing_k = next((k for k in self._knowledge_skills if k.id == sid), None)
                        if existing_k and existing_k.updated_at == cur_mtime:
                            pass # 嶺?흮?????
                        else:
                            k_skill = parse_skill_md(md_path)
                            if k_skill:
                                if existing_k:
                                    self._knowledge_skills.remove(existing_k)
                                self._knowledge_skills.append(k_skill)
                                _safe_print(f"??[Runner] Knowledge ???꾪뀬 ?β돦裕녻キ??繹먭퍓沅? {sid}")
                    except Exception as e:
                        _safe_print(f"??ル쵑??[Runner] Knowledge ???꾪뀬 ?β돦裕녻キ????덉넮 ({sid}): {e}")
                else:
                    _safe_print(f"??ル쵑??[Runner] ???꾪뀬 ???裕?.py/.md)??嶺뚢돦堉??????怨몃쾳: {sid}")

        # LangChain BaseTool detection & wrapping
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

        # 12-Cap enforcement: keep only top-N skills by declared order (yaml order = priority)
        if len(loaded_skills) > MAX_ACTIVE_SKILLS:
            _safe_print(f"⚠️ [Runner] 스킬 {len(loaded_skills)}개 → 상위 {MAX_ACTIVE_SKILLS}개만 로드")
            loaded_skills = loaded_skills[:MAX_ACTIVE_SKILLS]

        return loaded_skills

    def build_tool_registry(self, module_list: list, ctx: dict, policy: dict) -> ToolRegistry:
        """Adapts legacy modules into the precise V2 Tool Registry using ToolRuntimeWrapper"""
        wrapper = ToolRuntimeWrapper(base_dir=BASE_DIR)
        return wrapper.build_registry(module_list, ctx, policy, is_allowed_fn=self._is_tool_allowed)

    def run(self, agent: dict, task_input: str, run_id: str | None = None, auto_approve: bool = False, workspace: str | None = None):
        print(f"\n?? [Runner] ???逾?熬곥굥諭????덈뺄 ??戮곗굚: {agent.get('name')}")
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

        def _append_trace(kind: str, payload: dict):
            transcript.append({
                "ts": now_iso(),
                "kind": str(kind),
                "payload": payload if isinstance(payload, dict) else {"value": str(payload)},
            })

        def _flush_trace(result: dict):
            data = {
                "run_id": run_id,
                "project_id": project_id,
                "agent_name": str(agent.get("name", "")),
                "agent_role": str(agent.get("role", "")),
                "task": str(task_input or ""),
                "transcript": transcript,
                "result": result,
                "updated_at": now_iso(),
            }
            _safe_write_json(os.path.join(run_dir, "chat_trace.json"), data)
        approval_rejects = 0
        
        ctx = {
            "agent": agent,
            "data_dir": data_dir,
            "artifacts_dir": artifacts_dir,
            "workspace": target_workspace,
            "project_id": project_id,
        }
        ok_ctx, msg_ctx = validate_context_with_schema(ctx)
        if not ok_ctx:
            _safe_print(f"??ル쵑??[ContextSchema] ???쳜????덈콦 ?롪틵?嶺????덉넮: {msg_ctx}")
            print("???逾?熬곥굥諭????덈뺄??繞벿살탮???紐껊퉵??")
            result = {"ok": False, "reason": f"context_schema:{msg_ctx}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "context_schema", "message": str(msg_ctx)})
            _flush_trace(result)
            return result

        # 1. Load Skills (task_input 전달 → 스킬 미선언 시 자동 선택)
        modules = self.load_skills(agent, task_input=task_input)
        policy_runner = PolicyRuntime(base_dir=BASE_DIR)
        policy = policy_runner.resolve_agent_policy(agent)
        agent_name = agent.get("name", "")
        
        registry = self.build_tool_registry(modules, ctx, policy)
        tool_functions = registry.get_active_tools()
        self._list_approval_required_tools(tool_functions, policy)
        
        # Continuation Hook Enforcement via Event Bus
        bus = HookEventBus()
        bus.register(IntentGateHook())
        bus.register(TodoContinuationEnforcer())
        bus.register(ToolOutputTruncator())
        from core.hooks.context_fork import ContextForkHook
        bus.register(ContextForkHook())

        # 시작 시 라우팅 상태 노티 (세션 당 1회)
        from core.model_router import print_startup_routing_notice
        print_startup_routing_notice()

        # 역할 기반 복잡도 판별 (API 호출 없이 정적으로 결정)
        cli_providers = get_requested_cli_providers(os.getenv("AGENT_CHAT_PROVIDER"))
        role_summary = agent.get("role", "") or (agent.get("identity", {}) or {}).get("role_summary", "")
        agent_name = agent.get("name", "")
        engine_id = _infer_engine_id(role_summary or agent_name)

        # 아키텍처/리서치/코더/추론 역할은 항상 complex로 판정
        is_complex = engine_id in ("architect_claude", "researcher_gemini", "coder_claude", "reasoner_o")
        if is_complex:
            _safe_print(f"[Router] '{engine_id}' -> complex task (role-based)")
        else:
            _safe_print(f"[Router] '{engine_id}' -> standard task")


        agent_state = {
            "run_id": run_id,
            "agent": agent,
            "task_input": task_input,
            "intent": "complex_feature" if is_complex else "trivial",
            "workspace": target_workspace
        }

        if not bus.run_pre_execute(agent_state):
            result = {"ok": False, "reason": "hook_event_bus_blocked_pre"}
            # Phase 3: 훅 post-execute 호출 (로깅)
            result = bus.run_post_execute(agent_state, result)
            _flush_trace(result)
            return result
        
        from model_utils import get_dynamic_default_model
        model_name = normalize_model_name(agent.get("preferred_model") or self.mr.pick("chat", agent_config=agent, is_complex=is_complex) or get_dynamic_default_model("flash"))
        
        # System Prompt construction
        sys_prompt = self._build_runtime_system_prompt(agent)

        # Knowledge Skill Injection (Progressive Disclosure)
        if hasattr(self, '_knowledge_skills') and self._knowledge_skills:
            from core.knowledge_skill import filter_relevant_knowledge, build_knowledge_prompt
            rel_knowledge = filter_relevant_knowledge(self._knowledge_skills, task_input)
            if rel_knowledge:
                sys_prompt += build_knowledge_prompt(rel_knowledge)
                _safe_print(f"??[Runner] ??㉱??Knowledge ???꾪뀬 ?낅슣????熬곣뫁??({len(rel_knowledge)}濾?")

        # Proactive Memory Instruction
        skill_ids = [safe_id(str(s)) for s in agent.get("skills", [])]
        if "core_memory" in skill_ids:
            sys_prompt += (
                "\n\n[Memory Instruction]\n"
                "?獄???? `core_memory` ???꾪뀬????쒎첎???겶????곕????덈펲.\n"
                "????繞?**繞벿살탳????筌먲퐢沅?*(?熬곣뫁夷??釉띾콦 嶺뚮ㅏ援욆땻? ???????ル쪇源? ??源놁젧, ?롪퍒??????????띠럾? ?繹먮냱???濡?듆, "
                "?????? 嶺뚮ㅏ援???⑤챷紐드슖?'?リ옇?ｅ젆?????┑?嶺뚮씭???彛? ???욱닡??`core_memory.store` ?熬곥룗????????琉우뿰 **???곕츩??????*??琉얠돪??\n"
                "???繞③뇡????裕?嶺뚮쓽?대뎅??嶺뚮씮?????⑤챷?????key)?? ?곸궠??誘ㅒ?μ쪚??category)?????堉??琉우뿰 ???繞③뜮????덈펲."
            )

        sigs = self._resolve_signature_lines(agent)
        if sigs:
            import random
            greeting = random.choice(sigs)
            print(f"?獒?[Agent] {greeting}")
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
                    # Phase 5: CLI 경로에서도 훅 post-execute 호출 (트레이싱/통계)
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
                # Phase 5: CLI 실패 경로에서도 훅 post-execute 호출
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
            # [??ル맪??SDK] genai.Client ?リ옇?↑?嶺?????筌뤾쑬????諛댁뎽
            from google import genai
            from google.genai import types as genai_types
            gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
            chat = create_chat_with_self_heal(
                gemini_client,
                gemini_model,
                config=genai_types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    tools=tool_functions,
                ),
            )
        except Exception as e:
            import traceback
            print(f"??[Runner] SDK Chat Session Create ???덉넮: {str(e)}")
            traceback.print_exc()
            result = {"ok": False, "reason": "sdk_init_failed", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "sdk_init", "message": str(e)})
            _flush_trace(result)
            return result

        _append_trace("user", {"text": f"Task: {task_input}"})
        _append_trace("system", {"model": str(gemini_model), "skills": [str(s) for s in skill_ids]})
        
        # ???깆쓧??嶺뚮∥???낆?? ?熬곣뫖苑?????(429 Quota ???吏??????
        def safe_send(msg):
            max_retries = 3
            for i in range(max_retries):
                try:
                    return chat.send_message(msg)
                except Exception as e:
                    if "429" in str(e) or "quota" in str(e).lower() or "resource exhausted" in str(e).lower():
                        wait = 5 * (i + 1)
                        print(f"??[Quota] API ??????貫???(429). {wait}??????繞?.. ({i+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    raise e
            raise Exception("API ?筌뤾쑵?????덉넮 (Quota Exceeded)")

        try:
            # [??ル맪??SDK] 嶺?嶺뚮∥???낆?? ?熬곣뫖苑?
            response = safe_send(f"Task: {task_input}")
            
            # Basic ReAct Loop
            for turn in range(10): # Max 10 turns
                if not response.parts:
                    if not response.candidates:
                        pass # No debug print here
                    break

                has_action = False
                for part in response.parts:
                    # 1. Output Text
                    if hasattr(part, "text") and part.text:
                        print(f"?鸚?{part.text}", flush=True)
                        _append_trace("assistant", {"text": str(part.text)})
                    
                    # 2. Function Call
                    if hasattr(part, "function_call") and part.function_call:
                        has_action = True
                        fc = part.function_call
                        fname = fc.name
                        fargs = dict(fc.args)
                        print(f"??湲몃떬?[Tool] {fname}({fargs})", flush=True)
                        _append_trace("tool_call", {"name": str(fname), "args": fargs})
                    
                        # Find tool wrapper
                        tool_func = next((t for t in tool_functions if t.__name__ == fname), None)
                        if tool_func:
                            try:
                                skill_id = safe_id(str(getattr(tool_func, "_skill_id", "")))
                                if self._requires_tool_approval(policy, skill_id, fname):
                                    if not self._ask_tool_approval(fname, skill_id):
                                        print(f"????[Policy] ?????亦껋꼶梨??筌뤾쑴紐드슖??熬곥룗?????덈뺄??濾곌쑬????⑤８鍮?? {fname}", flush=True)
                                        approval_rejects += 1
                                        _append_trace("tool_reject", {"name": str(fname), "skill_id": str(skill_id)})
                                        response = safe_send("??????熬곥룗????獄????? ???용┃???鍮?? ???섎??꾩렮維뽬떋??怨쀬Ŧ 嶺뚯쉳?듸쭛??琉얠돪??")
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
                                    response = safe_send(
                                        f"Tool call blocked by runtime hook: {tool_decision.reason or 'blocked_by_hook'}"
                                    )
                                    continue

                                # Execute
                                res_obj = tool_func(**dict(tool_decision.tool_args or fargs))
                                res_obj = bus.run_post_tool_call(agent_state, fname, res_obj)
                                
                                print(f"  -> Result: {str(res_obj)[:100]}...", flush=True)
                                _append_trace("tool_result", {"name": str(fname), "result": str(res_obj)[:800]})
                                
                                # Send result back
                                # [??ル맪??SDK] ?熬곥룗?????덈뺄 ?롪퍒???좊ご?嶺뚮ㅄ維????꾩룇瑗??
                                response = safe_send(
                                    genai_types.Part.from_function_response(
                                        name=fname,
                                        response={'result': res_obj}
                                    )
                                )
                            except Exception as e:
                                print(f"??[Tool Error] {fname}: {e}", flush=True)
                                _append_trace("tool_error", {"name": str(fname), "message": str(e)})
                                response = safe_send(f"?熬곥룗?????덈뺄 繞????댁쾼?띠럾? ?꾩룇裕뉑틦???곕????덈펲: {e}")
                        else:
                            print(f"??ル쵑??[Runner] ???????⑸츎 ?熬곥룗???筌뤾쑵?? {fname}", flush=True)
                            response = safe_send(f"???????⑸츎 ?熬곥룗????낅퉵?? {fname}")

                if not has_action:
                    # 嶺뚮씭?ｉ뜮????⑸츩?筌뤾퍔異????쇑????떷????怨몃さ嶺??猷먮쳜????リ턁筌?(嶺뚯쉶?꾣룇??????⑤객臾???????깅쾳)
                    break
            
            print("??Agent Execution Finished.")
            result = {"ok": True, "reason": "gemini", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            # Phase 3: 훅 post-execute 호출 (로깅)
            result = bus.run_post_execute(agent_state, result)
            _flush_trace(result)
            return result

        except Exception as e:
            import traceback
            print(f"??ル쵑??[Runner] ???덈뺄 繞????댁쾼: {e}")
            traceback.print_exc()
            # Fallback output
            print("???逾?熬곥굥諭쒏뤆?쎛 ??얜Ŧ堉????諛댁뎽??? 嶺뚮쪇沅?쭛???鍮??")
            result = {"ok": False, "reason": f"runner_error:{type(e).__name__}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "runner", "message": str(e)})
            # Phase 3: 훅 post-execute 호출 (로깅)
            result = bus.run_post_execute(agent_state, result)
            _flush_trace(result)
            return result

# =============================================================================
# 7) Factory
# =============================================================================
