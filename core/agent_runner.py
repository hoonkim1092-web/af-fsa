import os
import time
import json
import glob
import subprocess
import sys
import builtins
import importlib.util
from google import genai  # [New SDK]

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from core.config_paths import *
from core.utils import *
from core.registry import ToolRegistry
# from core.tool_runtime import ToolRuntimeWrapper # (Checked in Step 644, this import wasn't there exactly, but registry was)
from core.policy_runtime import PolicyRuntime
from core.hooks.event_bus import HookEventBus, IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator
from model_utils import get_best_model, print_agent_model_summary  # [Skeleton Principle] model_utils??Triad ?怨쀪퐨??뽰맄 ????

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
    """
    [Plan A] ????癒? ?대Ŋ媛???媛??椰꾧퀡???됱뱽 ??獄쏆뮇源?
    ??쎈뻬??筌앸맩??餓λ쵎???랁??怨몄맄嚥??袁る솁??몃빍??
    """
    def __init__(self, engine_id: str, tier: str, reason: str):
        self.engine_id = engine_id
        self.tier = tier
        super().__init__(
            f"[{engine_id}] ????癒? ??媛??椰꾧퀡???됰뮸??덈뼄 (tier={tier}). ?臾믩씜??餓λ쵎???몃빍??\n"
            f"?癒?뼊 域뱀눊援? {reason}"
        )


class ModelRouter:
    # ?癒?짗 ?諭??筌뤴뫀諭?癒?퐣????????類ㅼ뵥 ??곸뵠 ??媛?筌욊쑵六?    AUTO_APPROVE_FALLBACK: bool = False
    def __init__(self):
        self._last_selection = {"model": "", "tier": "", "reason": ""}

    def get_last_selection(self) -> dict:
        return dict(self._last_selection)

    def _set_last_selection(self, model: str, tier: str = "", reason: str = "") -> None:
        self._last_selection = {
            "model": str(model or ""),
            "tier": str(tier or ""),
            "reason": str(reason or ""),
        }


    def _confirm_fallback(self, selection, auto_approve: bool = False) -> str:
        """
        Confirm cross/free fallback selections and optionally ask user approval.
        Returns selected model string or raises FallbackRejectedError.
        """
        model, tier, reason = selection
        # Keep metadata even if logs fail or selection is rejected.
        self._set_last_selection(model, tier, reason)

        if tier == "primary":
            return model

        if tier == "uncallable":
            _safe_print("")
            _safe_print("!" * 60)
            _safe_print("  [FATAL] Selected model is not callable")
            _safe_print("!" * 60)
            _safe_print(f"  {reason}")
            _safe_print("  Register required API key(s) in .env to proceed.")
            _safe_print("!" * 60)
            _safe_print("")
            raise FallbackRejectedError(model, tier, reason)

        icon = "  [CROSS]" if tier == "cross_fallback" else "  [FREE] "
        _safe_print("")
        _safe_print("=" * 60)
        _safe_print(f"{icon} Fallback warning")
        _safe_print("=" * 60)
        _safe_print(f"  {reason}")
        _safe_print(f"  Selected model: {model}")
        _safe_print("=" * 60)

        if auto_approve or self.AUTO_APPROVE_FALLBACK:
            _safe_print(f"  [Auto-Approve] proceeding with: {model}")
            return model

        try:
            ans = input("  Proceed with this fallback model? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"

        if ans in ("y", "yes"):
            _safe_print(f"  [Approved] {model}")
            _safe_print("")
            return model

        raise FallbackRejectedError(model, tier, reason)
    def pick(self, stage: str, agent_config: dict = None, auto_approve: bool = False) -> str:
        from model_utils import resolve_dynamic_model, resolve_preferred_model, _infer_engine_id, ModelSelection, TIER_PRIMARY

        forced = (os.getenv("AGENT_CHAT_MODEL") or "").strip()
        if forced:
            self._set_last_selection(forced, TIER_PRIMARY, "Forced by AGENT_CHAT_MODEL")
            return forced

        # ???? [1] YAML??preferred_model??筌뤿굞???野껋럩???怨쀪퐨 ??????????????????????????????????????
        if isinstance(agent_config, dict):
            rr = agent_config.get("runtime_rules", {}) if isinstance(agent_config.get("runtime_rules"), dict) else {}
            preferred = str(rr.get("preferred_model", "")).strip()
            if preferred:
                # ?袁⑹삺 API ??살쨮 ?紐꾪뀱 揶쎛?館釉놂쭪? ?類ㅼ뵥
                sel = ModelSelection(preferred, TIER_PRIMARY, f"YAML preferred_model: {preferred}")
                return self._confirm_fallback(sel, auto_approve=auto_approve)

        # ???? [2] role 疫꿸퀡而??癒?짗 ?遺우춭 ?醫뤾문 ????????????????????????????????????????????????????????????????????????????
        if isinstance(agent_config, dict):
            role = str(agent_config.get("role", "") or agent_config.get("identity", {}).get("role_summary", "")).strip()
            if role:
                engine_id = _infer_engine_id(role)
                sel = resolve_dynamic_model(engine_id)
                return self._confirm_fallback(sel, auto_approve=auto_approve)

        # ???? [3] stage 疫꿸퀡而?Skeleton Triad ??깆뒭??(疫꿸퀡?? ??????????????????????????????????????????????
        if stage in ("requirement", "research", "agent_create"):
            sel = resolve_dynamic_model("researcher_gemini")
        elif stage == "reasoning":
            sel = resolve_dynamic_model("architect_claude")
        elif stage in ("builder", "code_gen"):
            sel = resolve_dynamic_model("coder_claude")
        elif stage == "verification":
            sel = resolve_dynamic_model("manager_gpt")
        else:
            # Release/Output Agents
            sel = resolve_dynamic_model("gemini_flash")

        return self._confirm_fallback(sel, auto_approve=auto_approve)



# =============================================================================

class AgentRunner:
    def __init__(self, model_router: ModelRouter):
        self.mr = model_router
        self._skill_module_cache: dict[str, tuple[str, float, object]] = {}

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
        return "?諭??? ?醫롮뒠??AI ??곷뻻??쎄쉘?紐꾩뿯??덈뼄."

    def _resolve_signature_lines(self, agent: dict) -> list[str]:
        lines = agent.get("signature_lines")
        if not lines and isinstance(agent.get("persona"), dict):
            lines = agent["persona"].get("signature_lines")
        if isinstance(lines, list):
            return [str(x) for x in lines if str(x).strip()]
        return []

    def _normalize_brief_text(self, value: object, limit: int) -> str:
        text = str(value if value is not None else "")
        text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
        if len(text) > max(0, limit):
            return text[: max(0, limit - 3)].rstrip() + "..."
        return text

    def _build_global_context_brief(self, agent: dict, max_items: int = 10) -> str:
        if not GLOBAL_USER_KEY or not os.path.isdir(GLOBAL_MEMORY_DIR):
            return ""
        agent_name = str(agent.get("name") or agent.get("role") or "").strip()
        agent_id = safe_id(agent_name) or "general"
        candidate_roots = [
            os.path.join(GLOBAL_MEMORY_DIR, agent_id),
            os.path.join(GLOBAL_MEMORY_DIR, "general"),
            GLOBAL_MEMORY_DIR,
        ]
        rows: list[tuple[float, str]] = []
        seen_files: set[str] = set()
        seen_items: set[tuple[str, str]] = set()
        for root in candidate_roots:
            if not os.path.isdir(root): continue
            try:
                files = sorted(glob.glob(os.path.join(root, "**", "*.json"), recursive=True), key=lambda p: os.path.getmtime(p), reverse=True)
            except Exception: files = []
            for file_path in files:
                abs_path = os.path.abspath(file_path)
                if abs_path in seen_files: continue
                seen_files.add(abs_path)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception: continue
                category = self._normalize_brief_text(data.get("category") or os.path.basename(os.path.dirname(file_path)) or "general", 30) or "general"
                key = self._normalize_brief_text(data.get("key") or os.path.splitext(os.path.basename(file_path))[0], 90)
                value = self._normalize_brief_text(data.get("value", ""), 210)
                if not key and not value: continue
                dedupe = (category.lower(), key.lower())
                if dedupe in seen_items: continue
                seen_items.add(dedupe)
                line = f"- [{category}] {key}: {value}" if key and value else (f"- [{category}] {key}" if key else f"- [{category}] {value}")
                try: mtime = float(os.path.getmtime(file_path))
                except Exception: mtime = 0.0
                rows.append((mtime, line))
        if not rows: return ""
        rows.sort(key=lambda item: item[0], reverse=True)
        lines = [line for _mtime, line in rows[: max(1, int(max_items or 10))]]
        return "\n".join(lines)

    def _build_policy(self, agent: dict, loaded_skill_ids: list[str]) -> dict:
        rr = agent.get("runtime_rules", {}) if isinstance(agent, dict) else {}
        if not isinstance(rr, dict): rr = {}
        loaded = {safe_id(s) for s in loaded_skill_ids if safe_id(s)}
        declared = {safe_id(str(x)) for x in (agent.get("skills") or []) if str(x).strip()} if isinstance(agent, dict) else set()
        allowed_skills_raw = {safe_id(str(x)) for x in (rr.get("allowed_skills") or []) if str(x).strip()}
        approval_skills_raw = {safe_id(str(x)) for x in (rr.get("approval_required_skills") or []) if str(x).strip()}
        allowed_tools = {safe_id(str(x)) for x in (rr.get("allowed_tools") or []) if str(x).strip()}
        approval_tools = {safe_id(str(x)) for x in (rr.get("approval_required_tools") or []) if str(x).strip()}
        known_local = loaded | declared
        allowed_local = allowed_skills_raw & known_local
        default_deny = bool(rr.get("default_deny", False))
        enforce_allow = default_deny and bool(loaded or allowed_tools or allowed_local)
        baseline_tools = {"propose", "apply", "test"}
        allow_all_local = bool(rr.get("allow_all_local", False))
        if default_deny and allowed_skills_raw and not allowed_local and not allowed_tools:
            allowed_tools.update([x for x in allowed_skills_raw if x and x not in known_local])
        return {
            "enforce_allow": enforce_allow,
            "allowed_local": allowed_local,
            "approval_local": approval_skills_raw & known_local,
            "allowed_tools": allowed_tools,
            "approval_tools": approval_tools,
            "loaded_local": loaded,
            "baseline_tools": baseline_tools,
            "allow_all_local": allow_all_local,
        }

    def _is_tool_allowed(self, policy: dict, skill_id: str, tool_name: str) -> bool:
        if not policy.get("enforce_allow", False): return True
        sid, tname = safe_id(skill_id), safe_id(tool_name)
        if tname and tname in policy.get("allowed_tools", set()): return True
        if sid and sid in policy.get("allowed_local", set()): return True
        if sid and sid in policy.get("loaded_local", set()) and tname in policy.get("baseline_tools", set()) and not policy.get("allowed_local", set()) and not policy.get("allowed_tools", set()): return True
        return False

    def _requires_tool_approval(self, policy: dict, skill_id: str, tool_name: str) -> bool:
        sid, tname = safe_id(skill_id), safe_id(tool_name)
        return (sid and sid in policy.get("approval_local", set())) or (tname and tname in policy.get("approval_tools", set()))

    def _ask_tool_approval(self, fname: str, skill_id: str) -> bool:
        try:
            _safe_print("\n[?諭???遺욧퍕]")
            _safe_print(f"- ?袁㏓럡: {fname}")
            _safe_print(f"- ??쎄텢: {skill_id if skill_id else 'unknown'}")
            ans = input("???袁㏓럡 ??쎈뻬????됱뒠?醫됲돱?? (yes/no): ").strip().lower()
            return ans in ("y", "yes")
        except Exception: return False

    def _list_approval_required_tools(self, tool_functions: list, policy: dict):
        needs = []
        for fn in tool_functions:
            fname = str(getattr(fn, "__name__", "unknown"))
            sid = safe_id(str(getattr(fn, "_skill_id", "")))
            if self._requires_tool_approval(policy, sid, fname):
                needs.append((sid or "unknown", fname))
        if not needs: return
        _safe_print("\n[Policy] Tools requiring user approval")
        for sid, fname in needs:
            _safe_print(f"- ??쎄텢 `{sid}` / ?袁㏓럡 `{fname}`")
        _safe_print("??쎈뻬 ??뺤춳??yes/y嚥??諭???곷튊 筌욊쑵六??몃빍??")

    def _agent_prefers_codex(self, agent: dict, model_name: str) -> bool:
        if is_codex_model(model_name) or is_claude_model(model_name): return True
        engine = str(agent.get("engine", "")).strip().lower()
        if "codex" in engine: return True
        rr = agent.get("runtime_rules", {}) if isinstance(agent, dict) else {}
        return bool(rr.get("codex_enabled", False)) or "codex" in str(rr.get("codex_directive", "")).lower()

    def _run_with_codex(self, model_name: str, sys_prompt: str, task_input: str, tool_functions: list) -> bool:
        if not OPENAI_API_KEY or OpenAI is None:
            _safe_print("?醫묓닔 [Runner] Codex 野껋럥以덄몴??????????곷뮸??덈뼄.")
            return False
        codex_model = model_name if is_codex_model(model_name) else "codex-5.3"
        tools = ", ".join(sorted({t.__name__ for t in tool_functions})) if tool_functions else "none"
        prompt = f"{sys_prompt}\n\n[Task]\n{task_input}\n\n[Available Tools]\n{tools}\n?袁㏓럡 ?紐꾪뀱?? ?袁⑹삺 Codex 野껋럥以?癒?퐣 ??쑵??源딆넅??뤿선 ??됱몵?? ??쎈뻬 揶쎛?館釉?筌왖??? ??블??됱뱽 ?怨쀪퐨 ??뽯뻻??뤾쉭??"
        client = OpenAI(api_key=OPENAI_API_KEY)
        for i in range(3):
            try:
                resp = client.responses.create(model=codex_model, input=prompt)
                text = getattr(resp, "output_text", "") or ""
                if text.strip():
                    # [Constitution] Signature First 揶쏅벡??野꺜筌?
                    if "[Intelligence:" not in text:
                        _safe_print("[Signature Guard] Codex response missing signature; auto-injecting header")
                        text = f"[Intelligence: {codex_model}] {text.strip()}"
                    _safe_print(f"?夷?{text.strip()}")
                    return True
                return False
            except Exception as e:
                if "429" in str(e).lower(): time.sleep(5 * (i+1)); continue
                _safe_print(f"?醫묓닔 [Runner] Codex ??쎈뻬 ??살첒: {e}")
                return False
        return False

    def load_skills(self, agent: dict) -> list:
        loaded_skills = []
        # Ensure hash_edit skill is always loaded first
        declared_skills = agent.get("skills", [])
        active_skill_ids = [safe_id(str(s)) for s in declared_skills]
        if "hash_edit" not in active_skill_ids:
            active_skill_ids.insert(0, "hash_edit")

        for sid in active_skill_ids:
            sid = safe_id(str(sid))
            skill_py, _ = resolve_skill_paths(sid)
            if not skill_py: continue
            try: cur_mtime = float(os.path.getmtime(skill_py))
            except Exception: cur_mtime = -1.0
            cached = self._skill_module_cache.get(sid)
            if cached and cached[0] == skill_py and cached[1] == cur_mtime:
                loaded_skills.append(cached[2])
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"skills.{sid}", skill_py)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[f"skills.{sid}"] = module
                    spec.loader.exec_module(module)
                    setattr(module, "__skill_id__", sid)
                    self._skill_module_cache[sid] = (skill_py, cur_mtime, module)
                    loaded_skills.append(module)
                    _safe_print(f"??[Runner] ??쎄텢 嚥≪뮆諭??源껊궗: {sid}")
            except Exception as e: _safe_print(f"?醫묓닔 [Runner] ??쎄텢 嚥≪뮆諭???쎈솭 ({sid}): {e}")
        return loaded_skills

    def build_tool_registry(self, module_list: list, ctx: dict, policy: dict) -> ToolRegistry:
        from core.tool_runtime import ToolRuntimeWrapper
        return ToolRuntimeWrapper(base_dir=BASE_DIR).build_registry(module_list, ctx, policy, is_allowed_fn=self._is_tool_allowed)

    def _apply_runtime_intel(self, agent: dict, sys_prompt: str, synergy_tools: list) -> str:
        """Injects Memory and Synergy briefings into the system prompt."""
        # 1. Tiered Memory Briefing (Local -> Global)
        from core.utils import read_core_memory
        agent_id = agent.get("name") or agent.get("role")
        core_mem = read_core_memory(agent_id, max_items=10, max_files_per_root=240)
        
        if core_mem:
            mem_summary = "\n".join([f"- {k}: {v}" for k, v in list(core_mem.items())[:10]])
            sys_prompt += f"\n\n[Global & Local Memory Summary]\n{mem_summary}"

        # 2. Oh My OpenCode Synergy Briefing
        from core.synergy_runner import get_synergy_context
        tool_names = [str(getattr(fn, "__name__", "")) for fn in synergy_tools if str(getattr(fn, "__name__", "")).strip()]
        sys_prompt += get_synergy_context(tool_names)

        # 3. Legacy Memory Instruction
        skill_ids = [safe_id(str(s)) for s in agent.get("skills", [])]
        if "core_memory" in skill_ids:
            sys_prompt += "\n\n[Memory Instruction]\n餓λ쵐????類ｋ궖??`core_memory.store`嚥???쇰뮞嚥????館釉?紐꾩뒄."
            
        return sys_prompt

    def run(self, agent: dict, task_input: str, run_id: str | None = None, auto_approve: bool = False):
        _safe_print(f"\n?? [Runner] ?癒?뵠?袁る뱜 ??쎈뻬 ??뽰삂: {agent.get('name')}")
        if auto_approve:
            _safe_print("?醫묓닔 [FSA Mode] ?癒?짗 ?諭?????뽮쉐?遺얜┷??됰뮸??덈뼄. 筌뤴뫀諭??袁㏓럡揶쎛 筌앸맩????쎈뻬??몃빍??")
        started = time.time()
        run_id = run_id or f"run_{int(started)}"
        run_dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(run_dir, exist_ok=True)
        transcript = []

        def _append_trace(kind: str, payload: dict):
            transcript.append({"ts": now_iso(), "kind": str(kind), "payload": payload if isinstance(payload, dict) else {"value": str(payload)}})

        def _flush_trace(result: dict):
            try:
                data = {"run_id": run_id, "project_id": PROJECT_ID, "agent_name": str(agent.get("name", "")), "agent_role": str(agent.get("role", "")), "task": str(task_input or ""), "transcript": transcript, "result": result, "updated_at": now_iso()}
                _safe_write_json(os.path.join(run_dir, "chat_trace.json"), data)
            except Exception as e:
                _safe_print(f"[Trace] flush failed: {e}")

        approval_rejects = 0

        def _result(ok: bool, reason: str, **extra) -> dict:
            base = {
                "ok": bool(ok),
                "reason": str(reason),
                "latency_ms": int((time.time() - started) * 1000),
                "approval_rejects": int(approval_rejects),
            }
            base.update(extra)
            return base

        def _finalize(result: dict) -> dict:
            _flush_trace(result)
            return result

        _append_trace("user", {"text": str(task_input or "")})
        modules = self.load_skills(agent)
        _append_trace("system", {"loaded_skills": [safe_id(str(getattr(m, "__skill_id__", ""))) for m in modules]})

        ctx = {
            "agent": agent,
            "project_id": PROJECT_ID,
            "data_dir": DATA_DIR,
            "artifacts_dir": ARTIFACTS_DIR,
            "global_user_key": GLOBAL_USER_KEY,
            "global_data_dir": GLOBAL_DATA_DIR,
            "global_artifacts_dir": GLOBAL_ARTIFACTS_DIR
        }
        ok_ctx, msg_ctx = validate_context_with_schema(ctx)
        if not ok_ctx:
            _safe_print(f"?醫묓닔 [ContextSchema] ?뚢뫂???쎈뱜 野꺜筌???쎈솭: {msg_ctx}")
            _append_trace("error", {"stage": "context_schema", "message": str(msg_ctx)})
            return _finalize(_result(False, f"context_schema:{msg_ctx}"))

        policy = PolicyRuntime(base_dir=BASE_DIR).resolve_agent_policy(agent)
        registry = self.build_tool_registry(modules, ctx, policy)
        tool_functions = registry.get_active_tools()
        synergy_tools = []
        try:
            from core.synergy_runner import build_synergy_tools
            synergy_tools = build_synergy_tools(policy=policy, is_allowed_fn=self._is_tool_allowed)
            if synergy_tools:
                tool_functions.extend(synergy_tools)
        except Exception as e:
            _safe_print(f"[Synergy] tool attach skipped: {e}")
            _append_trace("warn", {"stage": "synergy_attach", "message": str(e)})

        self._list_approval_required_tools(tool_functions, policy)
        bus = HookEventBus()
        bus.register(IntentGateHook()); bus.register(TodoContinuationEnforcer()); bus.register(ToolOutputTruncator())
        agent_state = {"task_input": task_input, "intent": "complex" if len(task_input) > 30 else "trivial", "workspace": PROJECT_ROOT}
        if not bus.run_pre_execute(agent_state):
            _append_trace("error", {"stage": "pre_execute_hook", "message": "hook_blocked"})
            return _finalize(_result(False, "hook_blocked"))

        try:
            model_name = self.mr.pick("chat", agent_config=agent, auto_approve=auto_approve) or "gemini-2.0-flash"
        except FallbackRejectedError as e:
            _safe_print(f"\n[Runner] ?臾믩씜 餓λ쵎?? ????癒? ?대Ŋ媛???媛??椰꾧퀡???됰뮸??덈뼄.")
            _safe_print(f"  ???: {e}")
            _append_trace("error", {"stage": "model_selection", "message": str(e)})
            return _finalize(_result(False, f"fallback_rejected:{e.tier}"))
        except Exception as e:
            _safe_print(f"[Runner] 筌뤴뫀???醫뤾문 ??살첒: {e}")
            _append_trace("error", {"stage": "model_selection", "message": str(e)})
            return _finalize(_result(False, str(e)))

        selection_meta = self.mr.get_last_selection() if hasattr(self.mr, 'get_last_selection') else {}
        print_agent_model_summary(
            agent,
            selected_model=model_name,
            selected_tier=str(selection_meta.get('tier', '')).strip(),
            selected_reason=str(selection_meta.get('reason', '')).strip(),
        )


        # Determine actual model tag for signature
        if is_codex_model(model_name):
            intel_version = model_name
        elif is_claude_model(model_name):
            intel_version = model_name
        else:
            intel_version = str(model_name).replace("models/", "")

        # System Prompt construction
        sys_prompt = self._resolve_system_prompt(agent)

        # 4. Prompt Enhancement (Memory + Synergy)
        sys_prompt = self._apply_runtime_intel(agent, sys_prompt, synergy_tools)
        
        # [Hallucination Protection & Signature] Inject mandatory engine version signature instruction
        signature_directive = (
            f"\n\n[MANDATORY SIGNATURE RULE]\n"
            f"?諭??? ?袁⑹삺 '{intel_version}' ?遺우춭??곗쨮 ?닌됰짗 餓λ쵐???덈뼄.\n"
            f"筌뤴뫀諭??臾믩씜??筌??臾먮뼗?? 獄쏆꼶諭????쇱벉 ?類ㅻ뻼????釉??롫뮉 ??볥젃??됱퓗 ????以???뽰삂??뤾쉭??\n"
            f"\"[Intelligence: {intel_version}] (?諭?????볥젃??됱퓗 ????\"\n"
        )
        sys_prompt = signature_directive + sys_prompt

        if self._agent_prefers_codex(agent, model_name):
            if self._run_with_codex(model_name, sys_prompt, task_input, tool_functions):
                _append_trace("assistant", {"channel": "codex", "status": "completed"})
                _safe_print("??Agent Execution Finished.")
                return _finalize(_result(True, "codex"))

        gemini_model = model_name if not (is_codex_model(intel_version) or is_claude_model(intel_version)) else get_best_model(["gemini-3.1-pro", "gemini-3.0-flash"])
        
        _safe_print(f"?彛?[Intelligence] Engine: {intel_version}")
        _append_trace("system", {"channel": "gemini", "model": str(gemini_model), "tool_count": len(tool_functions)})
        
        # [New SDK] Client 疫꿸퀡而?筌렺?怨좉쉘 筌?쑵???룐뫂遊?(function calling ??釉?
        _api_key = os.getenv("GOOGLE_API_KEY")
        _client = genai.Client(api_key=_api_key) if _api_key else None
        if _client is None:
            _safe_print("?醫묓닔 [Runner] GOOGLE_API_KEY ??곸벉 ??Gemini 野껋럥以??????븍뜃?")
            return _finalize(_result(False, "no_api_key"))

        chat_config = genai.types.GenerateContentConfig(
            tools=tool_functions,
            system_instruction=sys_prompt,
        )

        chat = _client.chats.create(
            model=gemini_model,
            config=chat_config,
            history=[],
        )

        try:
            response = chat.send_message(f"Task: {task_input}\n\n?臾믩씜????뽰삂??곻폒?紐꾩뒄.")
            for _ in range(10):
                # hash_edit ?怨쀪퐨??뽰맄 ?類ｌ졊 (??됱젟???類ｋ궖)
                current_turn_tool_functions = []
                hash_edit_tools = []
                other_tools = []
                for tool in tool_functions:
                    if "hash_edit" in str(getattr(tool, "_skill_id", "")):
                        hash_edit_tools.append(tool)
                    else:
                        other_tools.append(tool)
                current_turn_tool_functions.extend(hash_edit_tools)
                current_turn_tool_functions.extend(other_tools)

                if not response.candidates or not response.candidates[0].content.parts:
                    break
                part = response.candidates[0].content.parts[0]
                if hasattr(part, 'text') and part.text:
                    _safe_print(f"?夷?{part.text}")
                    _append_trace("assistant", {"text": str(part.text)})
                if hasattr(part, 'function_call') and part.function_call:
                    fc = part.function_call
                    fname = fc.name
                    fargs = dict(fc.args) if fc.args else {}
                    _safe_print(f"??길닼?[Tool] {fname}({fargs})")
                    _append_trace("tool_call", {"name": str(fname), "args": fargs})
                    tool_func = next((t for t in tool_functions if t.__name__ == fname), None)
                    if tool_func:
                        sid = safe_id(str(getattr(tool_func, "_skill_id", "")))
                        needs_approval = self._requires_tool_approval(policy, sid, fname)
                        if needs_approval and not auto_approve and not self._ask_tool_approval(fname, sid):
                            approval_rejects += 1
                            _append_trace("tool_reject", {"name": str(fname), "skill_id": str(sid)})
                            response = chat.send_message("?????袁㏓럡???諭???? ??녿릭??щ빍??")
                            continue
                        res_obj = tool_func(**fargs)
                        if isinstance(res_obj, dict):
                            res_obj = bus.run_post_execute(agent_state, res_obj)
                        _safe_print(f"  -> Result: {str(res_obj)[:100]}...")
                        _append_trace("tool_result", {"name": str(fname), "result": str(res_obj)[:800]})
                        # [New SDK] function_response??genai.types嚥??袁⑸꽊
                        fn_response_part = genai.types.Part.from_function_response(
                            name=fname,
                            response={'result': res_obj}
                        )
                        response = chat.send_message(fn_response_part)
                    else:
                        _append_trace("error", {"stage": "tool_lookup", "tool": str(fname), "message": "not_found"})
                        break
                else:
                    break

            _append_trace("assistant", {"channel": "gemini", "status": "completed"})
            _safe_print("??Agent Execution Finished.")
            return _finalize(_result(True, "gemini"))
        except Exception as e:
            _safe_print(f"?醫묓닔 [Runner] ??쎈뻬 餓???살첒: {e}")
            _append_trace("error", {"stage": "runner", "message": str(e)})
            return _finalize(_result(False, str(e)))
# =============================================================================

