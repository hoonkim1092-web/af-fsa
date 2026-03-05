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

from core.config_paths import *
from core.utils import *
from core.utils import _safe_write_json
from core.registry import ToolRegistry
from core.tool_runtime import ToolRuntimeWrapper
from core.policy_runtime import PolicyRuntime
from core.hooks.event_bus import HookEventBus, IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator
from model_utils import (
    get_best_model,
    print_agent_model_summary,
    resolve_dynamic_model,
    _infer_engine_id,
    normalize_model_name,
    generate_content_with_self_heal,
    create_chat_with_self_heal,
)
from google import genai
from google.genai import types as genai_types

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

class ModelRouter:
    def pick(self, stage: str, agent_config: dict = None, is_complex: bool = True) -> str:
        if stage == "chat":
            # [우선순위 1] 환경변수로 강제 지정 시 무조건 우선
            forced = (os.getenv("AGENT_CHAT_MODEL") or "").strip()
            if forced:
                return forced

            # [우선순위 2] simple 작업 → 비용 최적화(Flash/Lightweight 계열)
            if not is_complex:
                sel = resolve_dynamic_model("lightweight")
                return sel.model

            # [우선순위 3] complex 작업 → 에이전트 역할 기반 동적 최적 모델
            # (provider 환경변수로 Claude/Codex 강제 가능)
            provider_raw = (os.getenv("AGENT_CHAT_PROVIDER") or "").strip().lower()
            providers = [p.strip() for p in provider_raw.split(",") if p.strip()]
            for provider in providers:
                if provider == "codex" and OPENAI_API_KEY:
                    return "codex-5.3"
                if provider == "claude":
                    from model_utils import _pick_anthropic_model
                    return _pick_anthropic_model("sonnet") or "claude-4.6"

            # [우선순위 4] 역할 기반 동적 모델 선택 (model_utils.resolve_dynamic_model)
            role = ""
            if agent_config:
                role = (agent_config.get("role") or
                        (agent_config.get("identity") or {}).get("role_summary") or
                        agent_config.get("name") or "")
            sel = resolve_dynamic_model(_infer_engine_id(role) if role else "researcher_gemini")
            return sel.model

        # 기획/추론 단계 → 실시간 가용 고성능 모델 반환 (하드코딩 배제)
        if stage in ("requirement", "reasoning"):
            return get_best_model(["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-pro"])

        # 기본(정규화/Flash 단계) → API에서 최신 Flash 계열 동적 선택
        from model_utils import get_dynamic_default_model
        return get_dynamic_default_model("flash")

# =============================================================================
# 2) Quick Guard (AST) - 치명적인 보안 취약점 차단
# =============================================================================
BANNED_IMPORT_TOPS = {
    "os", "sys", "subprocess", "shutil", "importlib",
    "pathlib", "glob",
    "ctypes",
    "multiprocessing", "threading", "concurrent", "asyncio",
}

BANNED_CALLS = {"eval", "exec", "__import__", "compile", "input"}
        # open 허용 (Runner 컨텍스트에서 안전하게 처리)

def quick_guard(code: str) -> tuple[bool, list[str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"SyntaxError: {e}"]

    vios: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in BANNED_IMPORT_TOPS:
                    vios.append(f"Forbidden import: {alias.name}")

        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in BANNED_IMPORT_TOPS:
                vios.append(f"Forbidden import: {node.module}")

        elif isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in BANNED_CALLS:
                vios.append(f"Forbidden call: {name}")

    return (len(vios) == 0), vios

def build_child_env() -> dict:
    child = {}
    for k in CHILD_ENV_PASSTHROUGH:
        v = os.environ.get(k)
        if v:
            child[k] = v
    return child

# =============================================================================
# 3) Isolated Run (Lite) - -I ?좎?, -S ?쒓굅(pandas ?덉슜)
# =============================================================================
def run_isolated(skill_py_path: str, timeout_sec: int = TEST_TIMEOUT_SEC) -> tuple[bool, dict, str]:
    skill_abs = os.path.abspath(skill_py_path)
    data_abs = os.path.abspath(DATA_DIR)
    art_abs = os.path.abspath(ARTIFACTS_DIR)

    # Windows 경로 안전 처리: repr 사용
    SKILL_PATH = repr(skill_abs)
    DATA_ROOT = repr(data_abs)
    ART_ROOT = repr(art_abs)

    runner = f"""
import json, os, sys, builtins, importlib.util

SKILL_PATH = {SKILL_PATH}
DATA_ROOT  = {DATA_ROOT}
ART_ROOT   = {ART_ROOT}

            # 1) sys.path 에서 CWD 제거 (모듈 하이재킹 방지)
try:
    cwd = os.getcwd()
    sys.path = [p for p in sys.path if p not in ("", ".", cwd)]
except Exception:
    pass

try:
    import socket as _socket
    AUDIT_PATH = os.path.join(ART_ROOT, "network_audit.log")

    def _audit(line: str):
        try:
            ts = __import__("datetime").datetime.utcnow().isoformat()
            with builtins.open(AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {line}\\n")
        except Exception:
            pass

    _orig_connect = _socket.socket.connect
    def _blocked_connect(self, address):
        _audit(f"BLOCKED socket.connect address={address}")
        raise PermissionError(f"Network access is blocked in the sandbox. (address={address})")
    _socket.socket.connect = _blocked_connect

    _orig_create_connection = _socket.create_connection
    def _blocked_create_connection(address, *args, **kwargs):
        _audit(f"BLOCKED socket.create_connection address={address}")
        raise PermissionError(f"Network access is blocked in the sandbox. (address={address})")
    _socket.create_connection = _blocked_create_connection
except Exception:
    pass

            # 3) 파일 경로 통제 (Chroot-ish): data/artifacts 밖의 접근 차단
_real_open = builtins.open
BLOCK_EXT = (".py", ".pth", ".so", ".dll", ".exe")

def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))

DATA_N = _norm(DATA_ROOT)
ART_N  = _norm(ART_ROOT)

def _is_within(path: str, root_norm: str) -> bool:
    p = _norm(path)
    return p == root_norm or p.startswith(root_norm + os.sep)

def _safe_open(file, mode="r", *args, **kwargs):
    if isinstance(file, int):
        raise PermissionError("fd open blocked")
    path = _norm(str(file))

    # read: data/artifacts만 접근 허용
    if not (_is_within(path, DATA_N) or _is_within(path, ART_N)):
        raise PermissionError(f"open blocked: {path}")

    # write: artifacts만 허용, 실행파일 작성 차단
    if any(x in mode for x in ("w","a","x","+")):
        if not _is_within(path, ART_N):
            raise PermissionError(f"write blocked: {path}")
        if path.endswith(BLOCK_EXT):
            raise PermissionError(f"write ext blocked: {path}")

    return _real_open(path, mode, *args, **kwargs)

builtins.open = _safe_open

# 4) Load skill
try:
    spec = importlib.util.spec_from_file_location("skill", SKILL_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
except Exception as e:
    print(json.dumps({{"ok": False, "reason": "import_failed", "error": str(e)}}, ensure_ascii=False))
    raise SystemExit(1)

missing = [fn for fn in ("propose","apply","test") if not hasattr(m, fn)]
if missing:
    print(json.dumps({{"ok": False, "reason": "missing_interface", "missing": missing}}, ensure_ascii=False))
    raise SystemExit(2)

ctx = {{"dry_run": True, "data_dir": DATA_ROOT, "artifacts_dir": ART_ROOT}}

try:
    res = m.test(ctx)
    if not isinstance(res, dict):
        res = {{"ok": False, "reason": "return_not_dict"}}
    print(json.dumps(res, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"ok": False, "reason": "runtime_error", "error": str(e)}}, ensure_ascii=False))
"""

    try:
        # Save runner script to a temporary file
        temp_runner_path = os.path.join(ARTIFACTS_DIR, f"temp_runner_{int(time.time())}.py")
        with open(temp_runner_path, "w", encoding="utf-8") as f:
            f.write(runner)

        # Execute using Safe Action Executor
        exec_result = run_skill_safely(
            role="Skill_Test",
            skill_path=temp_runner_path,
            args=[],
            timeout=timeout_sec,
            workdir=ARTIFACTS_DIR,
        )
        
        # Clean up temp file
        if os.path.exists(temp_runner_path):
            os.remove(temp_runner_path)

        if exec_result["status"] == "failed" and "timeout" in exec_result["error"].lower():
            return False, {"ok": False, "reason": "timeout"}, "timeout"
        elif exec_result["status"] == "failed":
            return False, {"ok": False, "reason": "runner_error", "error": exec_result["error"]}, exec_result["error"]
            
        p_stdout = exec_result["stdout"]
        p_stderr = exec_result["stderr"]
        
    except Exception as e:
        return False, {"ok": False, "reason": "runner_error", "error": str(e)}, str(e)

    out = p_stdout.strip()
    err = p_stderr.strip()
    if not out:
        return False, {"ok": False, "reason": "empty_output"}, err

    try:
        j = json.loads(out.splitlines()[-1])
        return bool(j.get("ok")), j, err
    except Exception:
        return False, {"ok": False, "reason": "non_json_output", "stdout": out}, err

# =============================================================================
# 4) Agent / Requirements
# =============================================================================
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
        # Legacy support + Caching
        if not hasattr(self, '_skill_module_cache'):
            self._skill_module_cache = {}
            
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
                
                md_path = None
                base_skills_dir = os.path.join(os.getcwd(), 'skills')
                for base in [base_skills_dir, WAREHOUSE_DIR, FORGE_DIR]:
                    candidate = os.path.join(base, sid, "skill.md")
                    if os.path.exists(candidate):
                        md_path = candidate
                        break
                        
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
            "artifacts_dir": ARTIFACTS_DIR,
            "workspace": workspace or os.getcwd()
        }
        ok_ctx, msg_ctx = validate_context_with_schema(ctx)
        if not ok_ctx:
            _safe_print(f"⚠️ [ContextSchema] 컨텍스트 검증 실패: {msg_ctx}")
            print("에이전트 실행을 중단합니다.")
            result = {"ok": False, "reason": f"context_schema:{msg_ctx}", "latency_ms": int((time.time() - started) * 1000), "approval_rejects": approval_rejects}
            _append_trace("error", {"stage": "context_schema", "message": str(msg_ctx)})
            _flush_trace(result)
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
        
        # 1. 역할 기반 방어: 아키텍트, 리서처는 아무리 짧아도 항상 주력 고성능 모델 유지
        role_summary = agent.get("role", "") or (agent.get("identity", {}) or {}).get("role_summary", "")
        agent_name = agent.get("name", "")
        engine_id = _infer_engine_id(role_summary or agent_name)
        
        if engine_id in ("architect_claude", "researcher_gemini"):
            is_complex = True
            _safe_print(f"🔍 [Router] '{engine_id}' 핵심 역할 감지 -> 고성능 엔진 강제 유지")
        else:
            # 2. 지능형 분류기 (Stage 1 AI Funnel) - 하드코딩 배제
            # 단순 길이/단어 배열 매칭이 아닌 Flash 모델을 통한 진짜 "의도" 판별
            try:
                client = genai.Client(api_key=GOOGLE_API_KEY) if GOOGLE_API_KEY else genai.Client()
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

        agent_state = {
            "task_input": task_input,
            "intent": "complex_feature" if is_complex else "trivial",
            "workspace": workspace or PROJECT_ROOT
        }
        
        if not bus.run_pre_execute(agent_state):
            result = {"ok": False, "reason": "hook_event_bus_blocked_pre"}
            _flush_trace(result)
            return result
        
        from model_utils import get_dynamic_default_model
        model_name = normalize_model_name(agent.get("preferred_model") or self.mr.pick("chat", agent_config=agent, is_complex=is_complex) or get_dynamic_default_model("flash"))
        
        # System Prompt construction
        sys_prompt = self._resolve_system_prompt(agent)

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
            gemini_client = genai.Client(api_key=GOOGLE_API_KEY) if GOOGLE_API_KEY else genai.Client()
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
                                # Execute
                                res_obj = tool_func(**fargs)
                                
                                # Fire POST hooks (e.g. ToolOutputTruncator)
                                if isinstance(res_obj, dict):
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
