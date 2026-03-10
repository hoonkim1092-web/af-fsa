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
    get_requested_cli_providers,
    strip_engine_api_keys,
)
from core.model_router import ModelRouter
from model_utils import (
    get_best_model,
    print_agent_model_summary,

    resolve_dynamic_model,
    _infer_engine_id,
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
    def __init__(self, model_router: ModelRouter):
        self.mr = model_router
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
        return "당신은 유용한 AI 어시스턴트입니다."

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

    def load_skills(self, agent: dict) -> list:
        # Legacy support + Caching
        if not hasattr(self, '_skill_module_cache'):
            self._skill_module_cache = {}
        self._knowledge_skills = []
            
        loaded_skills = []
        skill_ids = agent.get("skills", [])
        for sid in skill_ids:
            sid = safe_id(str(sid))
            skill_py, skill_meta = resolve_skill_paths(sid)
            
            # Action (Python) 처리
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
                        _safe_print(f"✅ [Runner] Action 스킬 로드 성공: {sid}")
                except Exception as e:
                    _safe_print(f"⚠️ [Runner] Action 스킬 로드 실패 ({sid}): {e}")
                    
            # Knowledge (Markdown) 처리
            else:
                # WAREHOUSE_DIR / FORGE_DIR 순으로 .md 스캔 (보통 WAREHOUSE/sid/skill.md)
                from core.skill_procurer import WAREHOUSE_DIR, FORGE_DIR
                from core.knowledge_skill import parse_skill_md
                
                md_path = resolve_knowledge_skill_path(sid, extra_roots=[WAREHOUSE_DIR, FORGE_DIR])
                        
                if md_path:
                    try:
                        cur_mtime = os.path.getmtime(md_path)
                        # 캐시 갱신 확인
                        existing_k = next((k for k in self._knowledge_skills if k.id == sid), None)
                        if existing_k and existing_k.updated_at == cur_mtime:
                            pass # 캐시 유지
                        else:
                            k_skill = parse_skill_md(md_path)
                            if k_skill:
                                if existing_k:
                                    self._knowledge_skills.remove(existing_k)
                                self._knowledge_skills.append(k_skill)
                                _safe_print(f"✅ [Runner] Knowledge 스킬 로드 성공: {sid}")
                    except Exception as e:
                        _safe_print(f"⚠️ [Runner] Knowledge 스킬 로드 실패 ({sid}): {e}")
                else:
                    _safe_print(f"⚠️ [Runner] 스킬 소스(.py/.md)를 찾을 수 없음: {sid}")

        return loaded_skills

    def build_tool_registry(self, module_list: list, ctx: dict, policy: dict) -> ToolRegistry:
        """Adapts legacy modules into the precise V2 Tool Registry using ToolRuntimeWrapper"""
        wrapper = ToolRuntimeWrapper(base_dir=BASE_DIR)
        return wrapper.build_registry(module_list, ctx, policy, is_allowed_fn=self._is_tool_allowed)

    def run(self, agent: dict, task_input: str, run_id: str | None = None, auto_approve: bool = False, workspace: str | None = None):
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
            _safe_print(f"⚠️ [ContextSchema] 컨텍스트 검증 실패: {msg_ctx}")
            print("에이전트 실행을 중단합니다.")
            result = {"ok": False, "reason": f"context_schema:{msg_ctx}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "context_schema", "message": str(msg_ctx)})
            _flush_trace(result)
            return result

        # 1. Load Skills
        modules = self.load_skills(agent)
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
        # AI Funnel & Smart Routing: 복잡도 판별
        task_text = str(task_input or "")
        cli_providers = get_requested_cli_providers(os.getenv("AGENT_CHAT_PROVIDER"))
        
        # 1. 역할 기반 방어: 아키텍트, 리서처는 아무리 짧아도 항상 주력 고성능 모델 유지
        role_summary = agent.get("role", "") or (agent.get("identity", {}) or {}).get("role_summary", "")
        agent_name = agent.get("name", "")
        engine_id = _infer_engine_id(role_summary or agent_name)
        role_hint = f"{role_summary} {agent_name}".lower()
        force_complex_role = engine_id == "architect_claude" or (
            engine_id == "researcher_gemini"
            and any(token in role_hint for token in ("research", "researcher", "analyst", "study"))
        )
        
        if force_complex_role:
            is_complex = True
            _safe_print(f"🔍 [Router] '{engine_id}' 핵심 역할 감지 -> 고성능 엔진 강제 유지")
        else:
            # 2. 지능형 분류기 (Stage 1 AI Funnel) - 하드코딩 배제
            # 단순 길이/단어 배열 매칭이 아닌 Flash 모델을 통한 진짜 "의도" 판별
            try:
                if not GOOGLE_API_KEY:
                    raise RuntimeError("google_api_key_disabled_or_missing")
                from google import genai
                client = genai.Client(api_key=GOOGLE_API_KEY)
                prompt = (
                    f"에이전트 역할: {role_summary or agent_name}\n"
                    f"사용자 요청: {task_text}\n\n"
                    "위 요청과 역할을 보고, 애니메이션 구현, UI/UX 설계, 새로운 비즈니스 로직 적용 등 고성능 지능이 필요한 'complex' 작업인지, "
                    "단순 오타 수정, 터미널 에러 해결, 패키지 설치 등 빠른 처리가 필요한 'simple' 작업인지 판별하시오.\n"
                    "참고: 프론트엔드 관련 작업은 품질이 중요하므로 대부분 'complex'를 요구합니다.\n"
                    "대답은 부연 설명 없이 오직 'complex' 또는 'simple' 단어 하나만 하시오."
                )
                resp = generate_content_with_self_heal(
                    client,
                    normalize_model_name("gemini-1.5-flash"),
                    prompt,
                )
                ans = resp.text.strip().lower()
                is_complex = "complex" in ans
                if is_complex:
                    _safe_print(f"🔍 [Router] 🧠 AI분류: 고품질/프론트엔드 작업 감지 -> 고급 엔진(Pro/Sonnet) 배정")
                else:
                    _safe_print(f"⚡ [Router] 🧠 AI분류: 단순 반복 작업 감지 -> 경량(Lightweight) 문지기 배치")
            except Exception as e:
                _safe_print(f"⚠️ [Router] 분류기 예외 발생({e}), 안전망 가동 -> 고급 엔진 강제 유지")
                is_complex = True

        if cli_providers and not GOOGLE_API_KEY and not force_complex_role:
            _safe_print("??[Router] CLI-only 紐⑤뱶濡??ㅽ뻾?⑸땲?? Google 遺꾨쪟湲??놁뼱??濡쒖뼵 媛?대뱶?덉씪 湲곕컲?쇰줈 吏꾪뻾?⑸땲??")
            is_complex = False

        agent_state = {
            "task_input": task_input,
            "intent": "complex_feature" if is_complex else "trivial",
            "workspace": target_workspace
        }
        
        if not bus.run_pre_execute(agent_state):
            result = {"ok": False, "reason": "hook_event_bus_blocked_pre"}
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
                _safe_print(f"✅ [Runner] 관련 Knowledge 스킬 주입 완료 ({len(rel_knowledge)}건)")

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

        cli_failures = []
        if cli_providers:
            for provider_id in cli_providers:
                cli_result = self._run_with_cli_provider(
                    provider_id,
                    model_name,
                    sys_prompt,
                    task_input,
                    target_workspace,
                    run_id=run_id,
                    auto_approve=auto_approve,
                )
                if cli_result.get("ok"):
                    cli_text = str(cli_result.get("text", "") or "").strip()
                    if cli_text:
                        print(f"?ì¨¼ {cli_text}")
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
                            "command": cli_result.get("command", []),
                        },
                    )
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

            if cli_failures and not GOOGLE_API_KEY and not OPENAI_API_KEY:
                last_failure = cli_failures[-1]
                result = {
                    "ok": False,
                    "reason": str(last_failure.get("reason") or "cli_provider_failed"),
                    "latency_ms": int((time.time() - started) * 1000),
                    "approval_rejects": approval_rejects,
                }
                _flush_trace(result)
                return result

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

        gemini_model = normalize_model_name(model_name if not (is_codex_model(model_name) or is_claude_model(model_name)) else get_best_model(["gemini-2.5-flash", "gemini-2.5-pro"]))
        try:
            # [신규 SDK] genai.Client 기반 채팅 세션 생성
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
            print(f"❌ [Runner] SDK Chat Session Create 실패: {str(e)}")
            traceback.print_exc()
            result = {"ok": False, "reason": "sdk_init_failed", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "sdk_init", "message": str(e)})
            _flush_trace(result)
            return result

        _append_trace("user", {"text": f"Task: {task_input}"})
        _append_trace("system", {"model": str(gemini_model), "skills": [str(s) for s in skill_ids]})
        
        # 안전한 메시지 전송 헬퍼 (429 Quota 자동 재시도)
        def safe_send(msg):
            max_retries = 3
            for i in range(max_retries):
                try:
                    return chat.send_message(msg)
                except Exception as e:
                    if "429" in str(e) or "quota" in str(e).lower() or "resource exhausted" in str(e).lower():
                        wait = 5 * (i + 1)
                        print(f"⏳ [Quota] API 사용량 초과 (429). {wait}초 대기 중... ({i+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    raise e
            raise Exception("API 호출 실패 (Quota Exceeded)")

        try:
            # [신규 SDK] 첫 메시지 전송
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
                        print(f"🤖 {part.text}", flush=True)
                        _append_trace("assistant", {"text": str(part.text)})
                    
                    # 2. Function Call
                    if hasattr(part, "function_call") and part.function_call:
                        has_action = True
                        fc = part.function_call
                        fname = fc.name
                        fargs = dict(fc.args)
                        print(f"🛠️ [Tool] {fname}({fargs})", flush=True)
                        _append_trace("tool_call", {"name": str(fname), "args": fargs})
                    
                        # Find tool wrapper
                        tool_func = next((t for t in tool_functions if t.__name__ == fname), None)
                        if tool_func:
                            try:
                                skill_id = safe_id(str(getattr(tool_func, "_skill_id", "")))
                                if self._requires_tool_approval(policy, skill_id, fname):
                                    if not self._ask_tool_approval(fname, skill_id):
                                        print(f"⏭️ [Policy] 사용자 미승인으로 도구 실행을 건너뜁니다: {fname}", flush=True)
                                        approval_rejects += 1
                                        _append_trace("tool_reject", {"name": str(fname), "skill_id": str(skill_id)})
                                        response = safe_send("해당 도구는 승인되지 않았습니다. 다른 방법으로 진행하세요.")
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

                                # Fire POST hooks (e.g. ToolOutputTruncator)
                                res_obj = bus.run_post_execute(agent_state, res_obj)
                                
                                print(f"  -> Result: {str(res_obj)[:100]}...", flush=True)
                                _append_trace("tool_result", {"name": str(fname), "result": str(res_obj)[:800]})
                                
                                # Send result back
                                # [신규 SDK] 도구 실행 결과를 모델에 반환
                                response = safe_send(
                                    genai_types.Part.from_function_response(
                                        name=fname,
                                        response={'result': res_obj}
                                    )
                                )
                            except Exception as e:
                                print(f"❌ [Tool Error] {fname}: {e}", flush=True)
                                _append_trace("tool_error", {"name": str(fname), "message": str(e)})
                                response = safe_send(f"도구 실행 중 오류가 발생했습니다: {e}")
                        else:
                            print(f"⚠️ [Runner] 알 수 없는 도구 호출: {fname}", flush=True)
                            response = safe_send(f"알 수 없는 도구입니다: {fname}")

                if not has_action:
                    # 만약 텍스트만 있고 액션이 없으면 루프 종료 (질문을 한 상태일 수 있음)
                    break
            
            print("✅ Agent Execution Finished.")
            result = {"ok": True, "reason": "gemini", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _flush_trace(result)
            return result

        except Exception as e:
            import traceback
            print(f"⚠️ [Runner] 실행 중 오류: {e}")
            traceback.print_exc()
            # Fallback output
            print("에이전트가 응답을 생성하지 못했습니다.")
            result = {"ok": False, "reason": f"runner_error:{type(e).__name__}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "runner", "message": str(e)})
            _flush_trace(result)
            return result

# =============================================================================
# 7) Factory
# =============================================================================
