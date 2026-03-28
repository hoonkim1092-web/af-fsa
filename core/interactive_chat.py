"""
core/interactive_chat.py
========================
대화형 채팅 모드.

Claude, ChatGPT, Gemini처럼 사용자와 계속 대화할 수 있는 REPL 세션.

설계:
- CWM (ContextWindowManager): 히스토리 압축 + 토큰 예산 관리
- Memory System: 에이전트 간 맥락 공유 (KnowledgeInjectionHook, MemoryConsolidationHook)
- CLI 제공자: 역할에 따라 claude_cli / gemini_cli / codex_cli 자동 선택 (API 키 불필요)

사용법:
    af -p my_project --chat
    af -p my_project --chat -r "Backend Dev"
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

from core.utils import safe_id, now_iso, _safe_write_json


# ── 색상 유틸 ──
def _c(text: str, code: str) -> str:
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return text
    return f"\033[{code}m{text}\033[0m"


def _print_banner(project_id: str, role: str, provider: str):
    print()
    print(_c("=" * 60, "36"))
    print(_c("  Agent Factory — Interactive Chat", "1;36"))
    print(_c("=" * 60, "36"))
    print(f"  프로젝트 : {_c(project_id, '33')}")
    print(f"  역할     : {_c(role, '33')}")
    print(f"  엔진     : {_c(provider, '33')}")
    print()
    print(f"  {_c('exit', '90')} 또는 {_c('Ctrl+C', '90')} 로 종료")
    print(f"  {_c('/clear', '90')} 로 대화 초기화")
    print(f"  {_c('/history', '90')} 로 대화 기록 보기")
    print(f"  {_c('/stats', '90')} 로 컨텍스트 통계")
    print(_c("-" * 60, "36"))
    print()


class InteractiveChat:
    """API 키 없이 CLI 제공자로 동작하는 대화형 채팅 세션.

    CWM이 히스토리를 압축하여 토큰을 절약하고,
    Memory System 훅이 에이전트 간 맥락을 공유한다.
    """

    def __init__(
        self,
        agent: dict[str, Any],
        workspace: str,
        model_name: str = "",
        auto_approve: bool = False,
    ):
        self.agent = agent
        self.workspace = workspace
        self.auto_approve = auto_approve
        self.project_id = safe_id(os.path.basename(workspace))
        self.session_id = f"chat_{int(time.time())}"
        self.turn = 0
        self.transcript: list[dict] = []

        self._model_name = model_name
        self._provider_id: str = ""
        self._sys_prompt: str = ""
        self._cwm: Any = None
        self._runner: Any = None
        self._bus: Any = None
        self._agent_state: dict = {}

    # ──────────────────────────────────────────────────────────
    # 초기화
    # ──────────────────────────────────────────────────────────

    def start(self):
        """세션 초기화: 스킬 로드, CWM 구성, Memory 훅 등록."""
        def _step(msg: str):
            print(f"  [초기화] {msg}", flush=True)

        from core.agent_runner import AgentRunner
        from core.model_router import ModelRouter
        from core.context_window_manager import ContextWindowManager
        from core.hooks.event_bus import HookEventBus
        from core.hooks.guardrails import IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator

        _step("모델 라우터 로드 중...")
        mr = ModelRouter()
        _step("에이전트 러너 초기화 중...")
        self._runner = AgentRunner(mr)

        _step("시스템 프롬프트 구성 중...")
        self._sys_prompt = self._runner._build_runtime_system_prompt(self.agent)
        self._sys_prompt += (
            "\n\n[Interactive Chat Mode]\n"
            "사용자와 대화형으로 소통하고 있습니다. "
            "이전 대화 내용을 기억하고 맥락에 맞게 응답하세요."
        )

        _step("이벤트 버스 등록 중...")
        self._bus = HookEventBus()
        self._bus.register(IntentGateHook())
        self._bus.register(TodoContinuationEnforcer())
        self._bus.register(ToolOutputTruncator())
        _step("메모리 훅 등록 중...")
        self._register_memory_hooks()

        self._agent_state = {
            "run_id": self.session_id,
            "project_id": self.project_id,
            "agent_name": str(self.agent.get("name", "")),
            "agent": self.agent,
            "task_input": "interactive chat",
            "task_id": "",
            "workspace": self.workspace,
        }

        _step("지식 컨텍스트 주입 중...")
        self._bus.run_pre_execute(self._agent_state)
        injected = self._agent_state.get("_knowledge_context", "")
        if injected:
            self._sys_prompt += f"\n\n{injected}"

        _step("CLI 제공자 탐색 중...")
        self._provider_id = self._resolve_cli_provider()

        _step("컨텍스트 윈도우 매니저 초기화 중...")
        model_name = self._model_name or self.agent.get("preferred_model") or "gemini-2.0-flash"
        self._cwm = ContextWindowManager(
            model_name=model_name,
            system_prompt=self._sys_prompt,
            all_tools=[],
            knowledge_skills=[],
            evict_after_turns=5,
            recent_window=6,
        )

        role = self.agent.get("role", "") or self.agent.get("name", "") or "Agent"
        _print_banner(self.project_id, role, self._provider_id)

    def _register_memory_hooks(self):
        """Memory System 훅 등록 (에이전트 간 맥락 공유)."""
        try:
            import asyncio
            from core.memory_system.knowledge_injection import KnowledgeInjectionHook
            from core.hooks.memory_consolidation import MemoryConsolidationHook
            from core.memory_system.facade import UnifiedMemoryFacade
            from core.memory_system.adapters.knowledge_graph import KnowledgeGraphAdapter
            from core.memory_system.adapters.core_memory import CoreMemoryAdapter

            facade = UnifiedMemoryFacade(project_id=str(self.project_id))
            graph = KnowledgeGraphAdapter(workspace=str(self.workspace))
            facade.register_adapter(CoreMemoryAdapter(agent_id=self.agent.get("name") or None))
            facade.register_adapter(graph)
            try:
                asyncio.run(facade.initialise())
            except RuntimeError:
                pass

            ki_hook = KnowledgeInjectionHook()
            mc_hook = MemoryConsolidationHook()
            ki_hook.set_graph_adapter(graph)
            mc_hook.set_facade(facade)
            mc_hook.set_graph_adapter(graph)

            UnifiedMemoryFacade.set_instance(facade)
            self._bus.register(ki_hook)
            self._bus.register(mc_hook)
        except Exception as e:
            print(_c(f"  [Memory] 초기화 건너뜀: {e}", "90"))

    def _resolve_cli_provider(self) -> str:
        """역할에 맞는 CLI 제공자를 결정한다."""
        # 런타임 레지스트리 우선 (AGENT_CHAT_PROVIDER 환경변수 불필요)
        try:
            from core.providers.registry import get_active_provider_setting, detect_installed_cli_providers
            active = get_active_provider_setting().strip()
            if active:
                # 콤마로 여러 개일 수 있으므로 첫 번째 값 사용
                return active.split(",")[0].strip()
        except Exception:
            pass

        # 역할 기반 추론 (설치된 CLI 중에서 선택)
        try:
            from core.agent_runner import _infer_engine_id
            from core.providers.registry import detect_installed_cli_providers
            role_summary = self.agent.get("role", "") or self.agent.get("name", "")
            engine_id = _infer_engine_id(role_summary)
            installed = detect_installed_cli_providers()
            if "gemini" in engine_id and "gemini_cli" in installed:
                return "gemini_cli"
            if installed:
                return installed[0]
        except Exception:
            pass

        return "claude_cli"  # 최후 기본값

    # ──────────────────────────────────────────────────────────
    # 메시지 전송
    # ──────────────────────────────────────────────────────────

    def send_message(self, user_input: str) -> str:
        """사용자 메시지를 전송하고 에이전트 응답을 반환한다."""
        self.turn += 1
        self._append_trace("user", {"text": user_input})

        # CWM에 사용자 메시지 기록 (히스토리 관리)
        self._cwm.add_user_message(user_input, turn=self.turn)

        # CWM이 압축한 히스토리로 프롬프트 구성
        prompt = self._build_cli_prompt(user_input)

        # CLI 제공자 호출
        response_text = self._call_cli(prompt)

        # CWM에 응답 기록 (다음 턴 압축 시 활용)
        self._record_response_to_cwm(response_text)
        self._append_trace("assistant", {"text": response_text})

        return response_text

    def _build_cli_prompt(self, current_input: str) -> str:
        """CWM 압축 히스토리 + 현재 메시지로 CLI 프롬프트 구성."""
        parts: list[str] = []

        # 압축된 이전 대화 기록
        history_text = self._build_history_text()
        if history_text:
            parts.append(f"[대화 기록]\n{history_text}")

        # 현재 사용자 메시지
        parts.append(f"[현재 메시지]\n사용자: {current_input}")

        return "\n\n".join(parts)

    def _build_history_text(self) -> str:
        """CWM 히스토리를 CLI용 텍스트로 변환 (압축 포함)."""
        if self.turn <= 1:
            return ""

        lines: list[str] = []
        # transcript에서 이전 대화만 추출 (현재 턴 제외)
        prev_entries = [
            e for e in self.transcript
            if e["kind"] in ("user", "assistant") and e["turn"] < self.turn
        ]

        # CWM 토큰 예산에 맞게 최근 N개만 포함
        # (CWM recent_window=6 → 최근 6턴 원문, 그 이전은 요약)
        recent_window = 6
        cutoff = max(0, self.turn - 1 - recent_window)

        for entry in prev_entries:
            t = entry["turn"]
            role_label = "사용자" if entry["kind"] == "user" else "에이전트"
            text = entry["payload"].get("text", "")

            if t <= cutoff:
                # 오래된 대화는 짧게 (CWM 압축과 동일 효과)
                text = text[:100] + "..." if len(text) > 100 else text
                lines.append(f"[이전] {role_label}: {text}")
            else:
                lines.append(f"{role_label}: {text}")

        return "\n".join(lines)

    def _record_response_to_cwm(self, text: str):
        """CLI 응답을 CWM 히스토리에 기록 (다음 턴 압축에 활용)."""
        try:
            # CWM은 Gemini 응답 객체를 받지만, 텍스트 전용 mock 사용
            class _FakeResponse:
                def __init__(self, t):
                    self.parts = [_FakePart(t)]
                    self.candidates = [True]

            class _FakePart:
                def __init__(self, t):
                    self.text = t
                    self.function_call = None

            self._cwm.record_model_response(_FakeResponse(text), self.turn)
        except Exception:
            pass  # CWM 기록 실패해도 대화는 계속

    def _call_cli(self, prompt: str) -> str:
        """CLI 제공자를 호출하고 응답 텍스트를 반환한다."""
        from core.providers.cli import CliChatRequest, execute_cli_chat as _execute

        # Windows에서 --append-system-prompt 인자에 특수문자가 포함되면
        # 명령줄 파싱이 망가져 -p 플래그에 프롬프트가 전달 안 되는 문제 발생.
        # 시스템 프롬프트를 task 안에 포함시켜 --append-system-prompt 사용을 피한다.
        sys_ctx = (self._sys_prompt or "").strip()
        if sys_ctx:
            full_input = f"[System Context]\n{sys_ctx}\n\n{prompt}"
        else:
            full_input = prompt

        request = CliChatRequest(
            provider_id=self._provider_id,
            model=self._model_name or "",
            system_prompt="",  # --append-system-prompt 플래그 제거
            task_input=full_input,
            workspace=self.workspace,
            run_id=f"{self.session_id}_t{self.turn}",
            timeout_sec=300,
            auto_approve=self.auto_approve,
        )
        result = _execute(request)
        if result.get("ok"):
            return str(result.get("text", "")).strip() or "(응답 없음)"

        reason = result.get("reason", "unknown")
        stderr = str(result.get("stderr", "")).strip()
        stdout = str(result.get("stdout", "")).strip()
        returncode = result.get("returncode", "?")
        cmd = result.get("command", [])
        cmd_preview = " ".join(str(x) for x in cmd[:2]) if cmd else "?"

        lines = [f"[오류] CLI: {reason} (exit={returncode}, cmd={cmd_preview})"]
        if stderr:
            lines.append(f"  ERR: {stderr[:400]}")
        if stdout:
            lines.append(f"  OUT: {stdout[:200]}")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────
    # 슬래시 명령어
    # ──────────────────────────────────────────────────────────

    def clear_history(self):
        from core.context_window_manager import ContextWindowManager
        model_name = self._model_name or self.agent.get("preferred_model") or "gemini-2.0-flash"
        self._cwm = ContextWindowManager(
            model_name=model_name,
            system_prompt=self._sys_prompt,
            all_tools=[],
            knowledge_skills=[],
            evict_after_turns=5,
            recent_window=6,
        )
        self.turn = 0
        print(_c("  대화 히스토리가 초기화되었습니다.", "33"))

    def show_history(self):
        if not self.transcript:
            print(_c("  대화 기록이 없습니다.", "90"))
            return
        print(_c("\n  [대화 기록]", "36"))
        for e in self.transcript:
            if e["kind"] == "user":
                label = _c("You", "1;32")
            elif e["kind"] == "assistant":
                label = _c("Agent", "1;35")
            else:
                continue
            text = e["payload"].get("text", "")[:200]
            print(f"  {label} [{e['turn']}]: {text}")
        print()

    def show_stats(self):
        stats = self._cwm.get_stats()
        h = stats.get("history", {})
        print(_c("\n  [컨텍스트 통계]", "36"))
        print(f"  대화 턴  : {self.turn}")
        print(f"  히스토리 : {h.get('total_tokens', 0)} 토큰 ({h.get('total_entries', 0)}개 항목)")
        print(f"  압축됨   : {h.get('compressed_entries', 0)}개 (절약: {h.get('saved_tokens', 0)} 토큰)")
        print(f"  엔진     : {self._provider_id}")
        print()

    # ──────────────────────────────────────────────────────────
    # 세션 저장
    # ──────────────────────────────────────────────────────────

    def save_session(self):
        runs_dir = os.path.join(self.workspace, "runs")
        os.makedirs(runs_dir, exist_ok=True)
        session_dir = os.path.join(runs_dir, self.session_id)
        os.makedirs(session_dir, exist_ok=True)

        # post_execute: MemoryConsolidationHook이 에피소드 저장
        if self._bus:
            result = {"ok": True, "reason": "interactive_chat", "latency_ms": 0}
            self._bus.run_post_execute(self._agent_state, result)

        data = {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "agent_name": self.agent.get("name", ""),
            "agent_role": self.agent.get("role", ""),
            "provider": self._provider_id,
            "total_turns": self.turn,
            "transcript": self.transcript,
            "saved_at": now_iso(),
        }
        path = os.path.join(session_dir, "chat_session.json")
        _safe_write_json(path, data)
        return path

    def _append_trace(self, kind: str, payload: dict):
        self.transcript.append({
            "ts": now_iso(),
            "turn": self.turn,
            "kind": kind,
            "payload": payload,
        })


# ──────────────────────────────────────────────────────────────
# PDCA 대화형 채팅 (BKIT 스타일)
# ──────────────────────────────────────────────────────────────

class PDCAInteractiveChat(InteractiveChat):
    """BKIT 스타일 PDCA 상태 머신이 통합된 대화형 채팅.

    기존 InteractiveChat의 REPL, CWM, 메모리 훅, CLI 프로바이더를 상속하고
    /plan, /design, /do, /check, /iterate, /report, /status, /next, /level
    슬래시 커맨드를 추가로 제공한다.
    """

    def __init__(
        self,
        workspace: str,
        model_name: str = "",
        auto_approve: bool = False,
    ):
        agent: dict = {
            "name": "af-assistant",
            "role": "General Assistant",
            "skills": [],
        }
        super().__init__(
            agent=agent,
            workspace=workspace,
            model_name=model_name,
            auto_approve=auto_approve,
        )
        self._pdca_sm: Any = None
        self._pdca_cmds: Any = None

    def start(self):
        """PDCA 상태 로드 후 기존 start() 실행."""
        self._load_pdca()
        super().start()
        if self._pdca_sm:
            self._pdca_cmds = _make_pdca_commands(self._pdca_sm, self)
            self._print_pdca_hint()

    def _load_pdca(self):
        """workspace에서 PDCAState 를 로드한다."""
        try:
            from core.pdca_state import PDCAState, PDCAStateMachine
            state = PDCAState.load(self.workspace)
            if state:
                self._pdca_sm = PDCAStateMachine(state, self.workspace)
        except Exception:
            pass

    def attach_pdca(self, sm: Any):
        """OnboardingWizard 에서 생성된 상태머신을 주입한다."""
        self._pdca_sm = sm
        self._pdca_cmds = _make_pdca_commands(sm, self)

    def _print_pdca_hint(self):
        if not self._pdca_sm:
            return
        label = self._pdca_sm.current_label_ko()
        nxt = self._pdca_sm.next_phase()
        print(_c(f"  현재 단계: {label}", "36"), end="")
        if nxt:
            print(f"  {_c('→', '90')} {_c('/next', '32')} 로 다음 단계 진행")
        else:
            print()


def _make_pdca_commands(sm: Any, chat: Any) -> Any:
    """PDCACommandRegistry 인스턴스를 생성한다 (지연 임포트)."""
    from core.pdca_commands import PDCACommandRegistry
    return PDCACommandRegistry(sm, chat)


def run_pdca_interactive(workspace: str, model: str = "", auto_approve: bool = False):
    """PDCA 대화형 모드 진입점."""
    from core.pdca_state import PDCAState, PDCAStateMachine

    state = PDCAState.load(workspace)
    if not state:
        return  # OnboardingWizard에서 미리 생성되어야 함

    chat = PDCAInteractiveChat(workspace=workspace, model_name=model, auto_approve=auto_approve)

    try:
        chat.start()
    except Exception as e:
        print(f"\n초기화 실패: {e}")
        return

    sm = PDCAStateMachine(state, workspace)
    chat.attach_pdca(sm)

    _run_pdca_repl(chat)


def _run_pdca_repl(chat: "PDCAInteractiveChat"):
    """PDCA REPL 루프."""
    try:
        while True:
            try:
                user_input = input(f"{_c('You', '1;32')}> ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            cmd = user_input.lower()
            if cmd in ("exit", "quit", "bye", "/exit", "/quit"):
                break

            # 기존 슬래시 커맨드
            if cmd == "/clear":
                chat.clear_history()
                continue
            if cmd == "/history":
                chat.show_history()
                continue
            if cmd == "/stats":
                chat.show_stats()
                continue
            if cmd == "/help":
                _print_pdca_help()
                continue

            # PDCA 슬래시 커맨드
            if chat._pdca_cmds and user_input.startswith("/"):
                if chat._pdca_cmds.dispatch(user_input):
                    continue

            # 알 수 없는 슬래시 커맨드
            if user_input.startswith("/"):
                print(_c(f"  알 수 없는 명령어: {user_input}. /help 로 확인하세요.", "31"))
                continue

            # 일반 채팅
            print()
            try:
                response = chat.send_message(user_input)
                role_name = chat.agent.get("role", "") or "Agent"
                print(f"{_c(role_name, '1;35')}> {response}")
            except KeyboardInterrupt:
                print(_c("\n  (응답 중단됨)", "33"))
            except Exception as e:
                print(_c(f"\n  오류: {e}", "31"))
            print()

    except KeyboardInterrupt:
        print(f"\n\n{_c('대화를 종료합니다.', '36')}")

    if chat.turn > 0:
        path = chat.save_session()
        print(_c(f"세션 저장: {path}", "90"))
    print()


def _print_pdca_help():
    print(f"\n  {_c('[PDCA 커맨드]', '1;36')}")
    pdca_cmds = [
        ("/plan [내용]",   "기획 문서 생성"),
        ("/design",        "3가지 아키텍처 옵션 제안"),
        ("/do",            "구현 시작"),
        ("/check",         "갭 분석 및 검증"),
        ("/iterate",       "AI 자동 수정"),
        ("/report",        "완료 보고서 생성"),
        ("/status",        "현재 PDCA 상태"),
        ("/next",          "다음 단계로 자동 진행"),
        ("/level [1/2/3]", "프로젝트 레벨 변경"),
    ]
    for cmd, desc in pdca_cmds:
        print(f"  {_c(cmd, '32'):<30} {desc}")
    print(f"\n  {_c('[기본 커맨드]', '1;36')}")
    basic_cmds = [
        ("/clear",   "대화 초기화"),
        ("/history", "대화 기록"),
        ("/stats",   "컨텍스트 통계"),
        ("exit",     "종료"),
    ]
    for cmd, desc in basic_cmds:
        print(f"  {_c(cmd, '32'):<30} {desc}")
    print()


# ──────────────────────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────────────────────

def _load_or_build_agent(role: str, workspace: str) -> dict:
    """AgentFactory 전체 초기화 없이 에이전트를 빠르게 로드/생성한다."""
    from core.utils import safe_id, read_yaml, apply_agent_overrides
    from core.config_paths import AGENTS_DIR, GLOBAL_AGENTS_DIR

    role_id = safe_id(role) or "agent"

    # 1) 워크스페이스 로컬 YAML
    local_path = os.path.join(workspace, "agents", f"{role_id}.yaml")
    if os.path.exists(local_path):
        return apply_agent_overrides(read_yaml(local_path), role)

    # 2) 글로벌 YAML
    global_path = os.path.join(GLOBAL_AGENTS_DIR, f"{role_id}.yaml")
    if os.path.exists(global_path):
        return apply_agent_overrides(read_yaml(global_path), role)

    # 3) 폴백: API 호출 없이 즉시 생성
    role_text = role.strip() or "General Assistant"
    return {
        "name": f"agent_{role_id}",
        "role": role_text,
        "tone": "calm, direct, pragmatic",
        "traits": ["practical", "concise", "execution-focused"],
        "system_ko": (
            f"당신은 {role_text} 역할의 실행 에이전트다. "
            "현재 워크스페이스 안에서 필요한 파일을 직접 만들거나 수정해 작업 결과를 남겨라."
        ),
        "signature_lines": [f"[{role_text}] 바로 실행합니다."],
    }


def run_interactive(
    project_id: str,
    workspace: str,
    role: str = "General Assistant",
    model: str = "",
    auto_approve: bool = False,
):
    """대화형 채팅 모드 진입점."""
    print(f"[Chat] 시작 중... (project={project_id})", flush=True)
    agent = _load_or_build_agent(role, workspace)
    print(f"[Chat] 에이전트 로드 완료: {agent.get('name', '?')}", flush=True)

    chat = InteractiveChat(
        agent=agent,
        workspace=workspace,
        model_name=model,
        auto_approve=auto_approve,
    )

    try:
        chat.start()
    except Exception as e:
        print(f"\n초기화 실패: {e}")
        return

    try:
        while True:
            try:
                user_input = input(f"{_c('You', '1;32')}> ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            cmd = user_input.lower()
            if cmd in ("exit", "quit", "bye", "/exit", "/quit"):
                break
            if cmd == "/clear":
                chat.clear_history()
                continue
            if cmd == "/history":
                chat.show_history()
                continue
            if cmd == "/stats":
                chat.show_stats()
                continue
            if cmd == "/help":
                print(f"\n  {_c('/clear', '32')}    대화 초기화")
                print(f"  {_c('/history', '32')}  대화 기록 보기")
                print(f"  {_c('/stats', '32')}    컨텍스트 통계")
                print(f"  {_c('exit', '32')}      종료\n")
                continue

            print()
            try:
                response = chat.send_message(user_input)
                role_name = chat.agent.get("role", "") or chat.agent.get("name", "") or "Agent"
                print(f"{_c(role_name, '1;35')}> {response}")
            except KeyboardInterrupt:
                print(_c("\n  (응답 중단됨)", "33"))
            except Exception as e:
                print(_c(f"\n  오류: {e}", "31"))
            print()

    except KeyboardInterrupt:
        print(f"\n\n{_c('대화를 종료합니다.', '36')}")

    if chat.turn > 0:
        path = chat.save_session()
        print(_c(f"세션 저장: {path}", "90"))
    print()
