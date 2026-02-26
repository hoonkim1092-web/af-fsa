import os
import time
import json
import glob
import subprocess
import sys
import builtins
import importlib.util
import google.generativeai as genai
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
from core.llm_engine import get_best_model


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


class ModelRouter:
    def pick(self, stage: str, agent_config: dict = None) -> str:
        from model_utils import resolve_dynamic_model
        forced = (os.getenv("AGENT_CHAT_MODEL") or "").strip()
        if forced:
            return forced
        if stage in ("requirement", "reasoning", "agent_create"):
            return resolve_dynamic_model("research_pro")
        if stage in ("builder", "chat"):
            return resolve_dynamic_model("codex")
        return resolve_dynamic_model("gemini_flash")

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
        return "당신은 유용한 AI 어시스턴트입니다."

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
            _safe_print("\n[승인 요청]")
            _safe_print(f"- 도구: {fname}")
            _safe_print(f"- 스킬: {skill_id if skill_id else 'unknown'}")
            ans = input("위 도구 실행을 허용할까요? (yes/no): ").strip().lower()
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
        _safe_print("\n[정책 안내] 사용자 승인이 필요한 도구 목록")
        for sid, fname in needs:
            _safe_print(f"- 스킬 `{sid}` / 도구 `{fname}`")
        _safe_print("실행 시마다 yes/y로 승인해야 진행됩니다.")

    def _agent_prefers_codex(self, agent: dict, model_name: str) -> bool:
        if is_codex_model(model_name) or is_claude_model(model_name): return True
        engine = str(agent.get("engine", "")).strip().lower()
        if "codex" in engine: return True
        rr = agent.get("runtime_rules", {}) if isinstance(agent, dict) else {}
        return bool(rr.get("codex_enabled", False)) or "codex" in str(rr.get("codex_directive", "")).lower()

    def _run_with_codex(self, model_name: str, sys_prompt: str, task_input: str, tool_functions: list) -> bool:
        if not OPENAI_API_KEY or OpenAI is None:
            _safe_print("⚠️ [Runner] Codex 경로를 사용할 수 없습니다.")
            return False
        codex_model = model_name if is_codex_model(model_name) else "codex-5.3"
        tools = ", ".join(sorted({t.__name__ for t in tool_functions})) if tool_functions else "none"
        prompt = f"{sys_prompt}\n\n[Task]\n{task_input}\n\n[Available Tools]\n{tools}\n도구 호출은 현재 Codex 경로에서 비활성화되어 있으니, 실행 가능한 지시와 설계안을 우선 제시하세요."
        client = OpenAI(api_key=OPENAI_API_KEY)
        for i in range(3):
            try:
                resp = client.responses.create(model=codex_model, input=prompt)
                text = getattr(resp, "output_text", "") or ""
                if text.strip():
                    _safe_print(f"🤖 {text.strip()}")
                    return True
                return False
            except Exception as e:
                if "429" in str(e).lower(): time.sleep(5 * (i+1)); continue
                _safe_print(f"⚠️ [Runner] Codex 실행 오류: {e}")
                return False
        return False

    def load_skills(self, agent: dict) -> list:
        loaded_skills = []
        # 기본 스킬(hash_edit) 자동 주입
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
                    _safe_print(f"✅ [Runner] 스킬 로드 성공: {sid}")
            except Exception as e: _safe_print(f"⚠️ [Runner] 스킬 로드 실패 ({sid}): {e}")
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
            sys_prompt += "\n\n[Memory Instruction]\n중요한 정보는 `core_memory.store`로 스스로 저장하세요."
            
        return sys_prompt

    def run(self, agent: dict, task_input: str, run_id: str | None = None, auto_approve: bool = False):
        _safe_print(f"\n🚀 [Runner] 에이전트 실행 시작: {agent.get('name')}")
        if auto_approve:
            _safe_print("⚠️ [UltraMode] 자동 승인이 활성화되었습니다. 모든 도구가 즉시 실행됩니다.")
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
            _safe_print(f"⚠️ [ContextSchema] 컨텍스트 검증 실패: {msg_ctx}")
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

        model_name = self.mr.pick("chat") or "gemini-2.0-flash"

        # System Prompt construction
        sys_prompt = self._resolve_system_prompt(agent)

        # 4. Prompt Enhancement (Memory + Synergy)
        sys_prompt = self._apply_runtime_intel(agent, sys_prompt, synergy_tools)

        if self._agent_prefers_codex(agent, model_name):
            if self._run_with_codex(model_name, sys_prompt, task_input, tool_functions):
                _append_trace("assistant", {"channel": "codex", "status": "completed"})
                _safe_print("✅ Agent Execution Finished.")
                return _finalize(_result(True, "codex"))

        gemini_model = model_name if not (is_codex_model(model_name) or is_claude_model(model_name)) else get_best_model(["gemini-2.0-flash", "gemini-1.5-flash"])
        _append_trace("system", {"channel": "gemini", "model": str(gemini_model), "tool_count": len(tool_functions)})
        model = genai.GenerativeModel(gemini_model, tools=tool_functions)
        chat = model.start_chat(history=[{"role": "user", "parts": [sys_prompt + f"\n\nTask: {task_input}"]}])

        try:
            response = chat.send_message("작업을 시작해주세요.", tool_config={'function_calling_config': 'AUTO'})
            for _ in range(10):
                # Prepare tool functions (hash_edit comes first for stability)
                # Re-prioritize tool_functions for each turn to ensure hash_edit is always checked first
                # This is a temporary reordering for the current turn's tool selection logic.
                # The original tool_functions list (used for model initialization) remains unchanged.
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

                if not response.parts:
                    break
                part = response.parts[0]
                if part.text:
                    _safe_print(f"🤖 {part.text}")
                    _append_trace("assistant", {"text": str(part.text)})
                if part.function_call:
                    fc = part.function_call
                    fname, fargs = fc.name, dict(fc.args)
                    _safe_print(f"🛠️ [Tool] {fname}({fargs})")
                    _append_trace("tool_call", {"name": str(fname), "args": fargs})
                    tool_func = next((t for t in tool_functions if t.__name__ == fname), None)
                    if tool_func:
                        sid = safe_id(str(getattr(tool_func, "_skill_id", "")))
                        needs_approval = self._requires_tool_approval(policy, sid, fname)
                        if needs_approval and not auto_approve and not self._ask_tool_approval(fname, sid):
                            approval_rejects += 1
                            _append_trace("tool_reject", {"name": str(fname), "skill_id": str(sid)})
                            response = chat.send_message("해당 도구는 승인되지 않았습니다.")
                            continue
                        res_obj = tool_func(**fargs)
                        if isinstance(res_obj, dict):
                            res_obj = bus.run_post_execute(agent_state, res_obj)
                        _safe_print(f"  -> Result: {str(res_obj)[:100]}...")
                        _append_trace("tool_result", {"name": str(fname), "result": str(res_obj)[:800]})
                        response = chat.send_message(genai.prototypes.Part(function_response=genai.prototypes.FunctionResponse(name=fname, response={'result': res_obj})))
                    else:
                        _append_trace("error", {"stage": "tool_lookup", "tool": str(fname), "message": "not_found"})
                        break
                else:
                    break

            _append_trace("assistant", {"channel": "gemini", "status": "completed"})
            _safe_print("✅ Agent Execution Finished.")
            return _finalize(_result(True, "gemini"))
        except Exception as e:
            _safe_print(f"⚠️ [Runner] 실행 중 오류: {e}")
            _append_trace("error", {"stage": "runner", "message": str(e)})
            return _finalize(_result(False, str(e)))
# =============================================================================
