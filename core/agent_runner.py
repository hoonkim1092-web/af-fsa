import os
import time
import json
import subprocess
import sys
import google.generativeai as genai
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from core.config_paths import *
from core.utils import *
from core.registry import ToolRegistry
from core.tool_runtime import ToolRuntimeWrapper
from core.policy_runtime import PolicyRuntime
from core.hooks.event_bus import HookEventBus, IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator
from core.llm_engine import get_best_model

class ModelRouter:
    def pick(self, stage: str, agent_config: dict = None) -> str:
        if stage == "chat":
            forced = (os.getenv("AGENT_CHAT_MODEL") or "").strip()
            if forced:
                return forced
            provider_raw = (os.getenv("AGENT_CHAT_PROVIDER") or "").strip().lower()
            providers = [p.strip() for p in provider_raw.split(",") if p.strip()]
            for provider in providers:
                if provider == "codex" and OPENAI_API_KEY:
                    return "codex-5.3"
                if provider == "claude":
                    return "claude-4.6"
        
        # Check Agent-specific high-end preference
        if agent_config and agent_config.get("preferred_model"):
            return get_best_model([agent_config["preferred_model"], "gemini-3.1-pro-preview", "gemini-1.5-pro"])

        # Stage 3 (Requirement/Reasoning) -> 3.1 Pro
        if stage in ("requirement", "reasoning"):
            return get_best_model(["gemini-3.1-pro-preview", "gemini-2.0-pro", "gemini-1.5-pro"])
        
        # Stage 2 (Flash/Normalization) -> 3 Flash
        return get_best_model(["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"])

# =============================================================================

class GitManager:
    def push_gate(self) -> bool:
        ans = input("\n?뵶 Git commit ?좊옒? (yes/no): ").strip().lower()
        return ans == "yes"

    def commit(self):
        # ?섍꼍留덈떎 ?ㅻⅤ??理쒖냼留?
        subprocess.run(["git", "add", "skills/", "runs/"], check=False)
        subprocess.run(["git", "commit", "-m", "feat: auto-generated skills"], check=False)

class AgentRunner:
    def __init__(self, model_router: ModelRouter):
        self.mr = model_router

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
            print("\n[승인 요청]")
            print(f"- 도구: {fname}")
            print(f"- 스킬: {skill_id if skill_id else 'unknown'}")
            ans = input("위 도구 실행을 허용할까요? (yes/no): ").strip().lower()
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
        print("\n[정책 안내] 사용자 승인이 필요한 도구 목록")
        for sid, fname in needs:
            print(f"- 스킬 `{sid}` / 도구 `{fname}`")
        print("실행 시마다 yes/y로 승인해야 진행됩니다.")

    def _make_tool_wrapper(self, func, ctx: dict):
        # Deprecated: Extracted to core.tool_runtime.ToolRuntimeWrapper
        return func

    def _build_tool_functions(self, modules: list, ctx: dict, policy: dict) -> list:
        # Deprecated: Extracted to core.tool_runtime.ToolRuntimeWrapper
        return []

    def _agent_prefers_codex(self, agent: dict, model_name: str) -> bool:
        if is_codex_model(model_name):
            return True
        if is_claude_model(model_name):
            # Claude 설정 시에도 실행 가능한 Codex 경로를 우선 시도한다.
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

    def load_skills(self, agent: dict) -> list:
        # Legacy support
        loaded_skills = []
        skill_ids = agent.get("skills", [])
        for sid in skill_ids:
            sid = safe_id(str(sid))
            skill_py, _skill_meta = resolve_skill_paths(sid)
            if not skill_py:
                 continue
            try:
                spec = importlib.util.spec_from_file_location(f"skills.{sid}", skill_py)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[f"skills.{sid}"] = module
                    spec.loader.exec_module(module)
                    setattr(module, "__skill_id__", sid)
                    loaded_skills.append(module)
                    print(f"✅ [Runner] 스킬 로드 성공: {sid}")
            except Exception as e:
                print(f"⚠️ [Runner] 스킬 로드 실패 ({sid}): {e}")
        return loaded_skills

    def build_tool_registry(self, module_list: list, ctx: dict, policy: dict) -> ToolRegistry:
        """Adapts legacy modules into the precise V2 Tool Registry using ToolRuntimeWrapper"""
        wrapper = ToolRuntimeWrapper(base_dir=BASE_DIR)
        return wrapper.build_registry(module_list, ctx, policy, is_allowed_fn=self._is_tool_allowed)

    def run(self, agent: dict, task_input: str, run_id: str | None = None):
        print(f"\n🚀 [Runner] 에이전트 실행 시작: {agent.get('name')}")
        started = time.time()
        run_id = run_id or f"run_{int(started)}"
        run_dir = os.path.join(RUNS_DIR, run_id)
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
                "project_id": PROJECT_ID,
                "agent_name": str(agent.get("name", "")),
                "agent_role": str(agent.get("role", "")),
                "task": str(task_input or ""),
                "transcript": transcript,
                "result": result,
                "updated_at": now_iso(),
            }
            _safe_write_json(os.path.join(run_dir, "chat_trace.json"), data)
        approval_rejects = 0
        
        # 1. Load Skills
        modules = self.load_skills(agent)
        
        # 2. Context Setup
        # Inject context into modules if they have a 'ctx' global or similar
        ctx = {
            "agent": agent,
            "data_dir": DATA_DIR,
            "artifacts_dir": ARTIFACTS_DIR
        }
        ok_ctx, msg_ctx = validate_context_with_schema(ctx)
        if not ok_ctx:
            print(f"⚠️ [ContextSchema] 컨텍스트 검증 실패: {msg_ctx}")
            print("에이전트 실행을 중단합니다.")
            result = {"ok": False, "reason": f"context_schema:{msg_ctx}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "context_schema", "message": str(msg_ctx)})
            _flush_trace(result)
            return result
        policy_runner = PolicyRuntime(base_dir=BASE_DIR)
        policy = policy_runner.resolve_agent_policy(agent)
        
        registry = self.build_tool_registry(modules, ctx, policy)
        tool_functions = registry.get_active_tools()
        self._list_approval_required_tools(tool_functions, policy)
        
        # Continuation Hook Enforcement via Event Bus
        bus = HookEventBus()
        bus.register(IntentGateHook())
        bus.register(TodoContinuationEnforcer())
        bus.register(ToolOutputTruncator())
        
        is_complex = len(task_input) > 30 or any(k in task_input.lower() for k in ["refactor", "build", "create", "implement", "리팩토링", "구현", "만들어", "추가"])
        agent_state = {
            "task_input": task_input,
            "intent": "complex_feature" if is_complex else "trivial",
            "workspace": PROJECT_ROOT
        }
        
        if not bus.run_pre_execute(agent_state):
            result = {"ok": False, "reason": "hook_event_bus_blocked_pre"}
            _flush_trace(result)
            return result
        model_name = self.mr.pick("chat") or "gemini-2.0-flash"
        
        # System Prompt construction
        sys_prompt = self._resolve_system_prompt(agent)
        
        # Proactive Memory Instruction
        skill_ids = [safe_id(str(s)) for s in agent.get("skills", [])]
        if "core_memory" in skill_ids:
            sys_prompt += (
                "\n\n[Memory Instruction]\n"
                "당신은 `core_memory` 스킬을 장착하고 있습니다.\n"
                "대화 중 **중요한 정보**(프로젝트 명세, 사용자 선호, 일정, 결정 사항 등)가 등장하면, "
                "사용자가 명시적으로 '기억해'라고 말하지 않아도 `core_memory.store` 도구를 사용하여 **스스로 저장**하세요.\n"
                "저장할 때는 맥락에 맞는 적절한 키(key)와 카테고리(category)를 판단하여 저장합니다."
            )

        sigs = self._resolve_signature_lines(agent)
        if sigs:
            import random
            greeting = random.choice(sigs)
            print(f"💬 [Agent] {greeting}")
            sys_prompt += f"\n\n[Signature]\n{greeting}"

        if self._agent_prefers_codex(agent, model_name):
            codex_ok = self._run_with_codex(model_name, sys_prompt, task_input, tool_functions)
            if codex_ok:
                print("✅ Agent Execution Finished.")
                result = {"ok": True, "reason": "codex", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
                _append_trace("assistant", {"channel": "codex", "note": "codex path completed"})
                _flush_trace(result)
                return result
            print("⚠️ [Runner] Codex 경로 실패, Gemini 경로로 폴백합니다.")

        if not GOOGLE_API_KEY:
            print("⚠️ [Runner] GOOGLE_API_KEY가 없어 Gemini 경로를 사용할 수 없습니다.")
            print("에이전트가 응답을 생성하지 못했습니다.")
            result = {"ok": False, "reason": "missing_google_api_key", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "bootstrap", "message": "missing_google_api_key"})
            _flush_trace(result)
            return result

        gemini_model = model_name if not (is_codex_model(model_name) or is_claude_model(model_name)) else get_best_model(["gemini-2.0-flash", "gemini-1.5-flash"])
        model = genai.GenerativeModel(gemini_model, tools=tool_functions)

        chat = model.start_chat(history=[
            {"role": "user", "parts": [sys_prompt + f"\n\nTask: {task_input}"]}
        ])
        _append_trace("user", {"text": f"Task: {task_input}"})
        _append_trace("system", {"model": str(gemini_model), "skills": [str(s) for s in skill_ids]})
        
        # Helper for safe sending
        def safe_send(msg, **kwargs):
            max_retries = 3
            for i in range(max_retries):
                try:
                    return chat.send_message(msg, **kwargs)
                except Exception as e:
                    if "429" in str(e) or "quota" in str(e).lower() or "resource exhausted" in str(e).lower():
                        wait = 5 * (i + 1) # 5s, 10s, 15s
                        print(f"⏳ [Quota] API 사용량 초과 (429). {wait}초 대기 중... ({i+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    raise e
            raise Exception("API 호출 실패 (Quota Exceeded)")

        try:
            # We send an empty message to trigger the model to start working
            response = safe_send("작업을 시작해주세요. 필요한 도구가 있다면 사용하세요.", tool_config={'function_calling_config': 'AUTO'})
            
            # Basic ReAct Loop
            for _ in range(10): # Max 10 turns
                if not response.parts:
                    break
                part = response.parts[0]
                
                # 1. Output Text
                if part.text:
                    print(f"🤖 {part.text}")
                    _append_trace("assistant", {"text": str(part.text)})
                    # If model thinks it's done or asking question, we might stop
                    # But if it also has function call (rare in Gemini part[0]), check that.
                
                # 2. Function Call
                if part.function_call:
                    fc = part.function_call
                    fname = fc.name
                    fargs = dict(fc.args)
                    print(f"🛠️ [Tool] {fname}({fargs})")
                    _append_trace("tool_call", {"name": str(fname), "args": fargs})
                    
                    # Find tool wrapper
                    tool_func = next((t for t in tool_functions if t.__name__ == fname), None)
                    if tool_func:
                        try:
                            skill_id = safe_id(str(getattr(tool_func, "_skill_id", "")))
                            if self._requires_tool_approval(policy, skill_id, fname):
                                if not self._ask_tool_approval(fname, skill_id):
                                    print(f"⏭️ [Policy] 사용자 미승인으로 도구 실행을 건너뜁니다: {fname}")
                                    approval_rejects += 1
                                    _append_trace("tool_reject", {"name": str(fname), "skill_id": str(skill_id)})
                                    response = safe_send("해당 도구는 승인되지 않았습니다. 다른 방법으로 진행하세요.")
                                    continue
                            # Execute
                            res_obj = tool_func(**fargs)
                            
                            # Fire POST hooks (e.g. ToolOutputTruncator)
                            if isinstance(res_obj, dict):
                                res_obj = bus.run_post_execute(agent_state, res_obj)
                            
                            print(f"  -> Result: {str(res_obj)[:100]}...")
                            _append_trace("tool_result", {"name": str(fname), "result": str(res_obj)[:800]})
                            
                            # Send result back
                            response = safe_send(
                                genai.prototypes.Part(function_response=genai.prototypes.FunctionResponse(
                                    name=fname,
                                    response={'result': res_obj}
                                ))
                            )
                            continue # Continue loop with new response
                        except Exception as e:
                            print(f"⚠️ Tool Execution Error: {e}")
                            _append_trace("error", {"stage": "tool_execution", "tool": str(fname), "message": str(e)})
                            break
                    else:
                        print(f"⚠️ Tool not found: {fname}")
                        _append_trace("error", {"stage": "tool_lookup", "tool": str(fname), "message": "not_found"})
                        break
                
                # If no function call and simple text, we assume turn is done for this prompt
                if not part.function_call:
                    break
            
            print("✅ Agent Execution Finished.")
            result = {"ok": True, "reason": "gemini", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _flush_trace(result)
            return result

        except Exception as e:
            print(f"⚠️ [Runner] 실행 중 오류: {e}")
            # Fallback output
            print("에이전트가 응답을 생성하지 못했습니다.")
            result = {"ok": False, "reason": f"runner_error:{type(e).__name__}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "runner", "message": str(e)})
            _flush_trace(result)
            return result

# =============================================================================
