"""
core/terminal_visualizer.py
============================
터미널 에이전트 시각화 엔진.

에이전트 작업 페이즈(설계/코딩/리뷰 등)와 에이전트 간 대화 내용을
터미널에 실시간으로 시각화한다.

환경변수:
  AGENT_VISUAL_MODE  = dashboard | timeline | minimal | off  (기본: timeline)
  AGENT_VISUAL_REFRESH = 초 단위 정수 (대시보드 갱신 주기, 기본: 2)
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.conversation_room import ConversationRoom, ConversationTurn


# ── ANSI 색상 ─────────────────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
MAGENTA = "\033[35m"
BLUE    = "\033[34m"
GRAY    = "\033[90m"
RED     = "\033[31m"
WHITE   = "\033[97m"

# Windows 터미널 ANSI 활성화 (레거시 cmd.exe 대응)
if sys.platform == "win32":
    os.system("")


# ── AgentPhase ────────────────────────────────────────────────────────────────

class AgentPhase(Enum):
    """에이전트 작업 페이즈."""
    DESIGNING  = ("📐", "DESIGNING ",  CYAN)
    CODING     = ("💻", "CODING    ",  GREEN)
    REVIEWING  = ("🔍", "REVIEWING ",  YELLOW)
    TESTING    = ("🧪", "TESTING   ",  MAGENTA)
    CONVERSING = ("💬", "CONVERSING",  BLUE)
    WAITING    = ("⏳", "WAITING   ",  GRAY)
    COMPLETED  = ("✅", "COMPLETED ",  GREEN)
    FAILED     = ("❌", "FAILED    ",  RED)

    @property
    def icon(self) -> str:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]

    @property
    def color(self) -> str:
        return self.value[2]


# DynamicOrchestrator state_board["agents_status"] 문자열 → Phase 매핑
_STATUS_TO_PHASE: dict[str, AgentPhase] = {
    "idle":    AgentPhase.WAITING,
    "working": AgentPhase.CODING,
}

# FSA 사이클 단계 이름 → Phase 매핑
_FSA_STEP_TO_PHASE: dict[str, AgentPhase] = {
    "execute":   AgentPhase.CODING,
    "trace":     AgentPhase.REVIEWING,
    "eval":      AgentPhase.REVIEWING,
    "summarize": AgentPhase.DESIGNING,
    "reflect":   AgentPhase.DESIGNING,
}

# ConversationTurn turn_type → 아이콘
TURN_TYPE_ICONS: dict[str, str] = {
    "proposal":       "📋",
    "statement":      "💡",
    "question":       "❓",
    "agreement":      "✅",
    "objection":      "🚫",
    "vote":           "🗳 ",
    "summary":        "📝",
    "human_input":    "👤",
    "final_decision": "⚖ ",
    "unstructured":   "💭",
}


# ── VisualMode ────────────────────────────────────────────────────────────────

class VisualMode(Enum):
    DASHBOARD = "dashboard"   # 전체 화면 에이전트 상태 박스 (주기적 갱신)
    TIMELINE  = "timeline"    # 시간순 한 줄 로그 + 대화 스트림
    MINIMAL   = "minimal"     # 대화 발언 미리보기만 한 줄로 출력
    OFF       = "off"         # 시각화 비활성


# ── AgentVisualState ──────────────────────────────────────────────────────────

@dataclass
class AgentVisualState:
    agent_name: str
    phase: AgentPhase = AgentPhase.WAITING
    progress: float = 0.0
    current_cycle: int = 0
    max_cycles: int = 0
    task_summary: str = ""
    started_at: float = field(default_factory=time.time)
    conversation_room_id: Optional[str] = None


# ── TerminalVisualizer ────────────────────────────────────────────────────────

class TerminalVisualizer:
    """
    에이전트 작업 상태 + 대화 내용 터미널 시각화 엔진.

    사용법:
        viz = TerminalVisualizer()

        # 에이전트 상태 업데이트
        viz.update_phase("Himari", AgentPhase.CODING, progress=0.5,
                         task_summary="auth-module", cycle=3, max_cycles=6)

        # 대화 이벤트
        viz.on_conversation_start(room)
        viz.on_conversation_turn(turn, room)
        viz.on_consensus_reached(room, "REST API 채택")
    """

    def __init__(self, mode: VisualMode | None = None):
        env_mode = os.environ.get("AGENT_VISUAL_MODE", "timeline").lower()
        try:
            self.mode = mode or VisualMode(env_mode)
        except ValueError:
            self.mode = VisualMode.TIMELINE

        self._refresh = float(os.environ.get("AGENT_VISUAL_REFRESH", "2"))
        self.agents: dict[str, AgentVisualState] = {}
        self._start_time = time.time()
        self._completed_tasks = 0
        self._total_tasks = 0
        self._active_conversations = 0
        self._last_dashboard_render = 0.0

    # ── 에이전트 상태 관리 ─────────────────────────────────────────────────

    def register_agent(self, name: str) -> None:
        if name not in self.agents:
            self.agents[name] = AgentVisualState(agent_name=name)

    def update_phase(
        self,
        name: str,
        phase: AgentPhase,
        progress: float = 0.0,
        task_summary: str = "",
        cycle: int = 0,
        max_cycles: int = 0,
    ) -> None:
        """에이전트 페이즈 업데이트 — 변경 시 타임라인 출력."""
        self.register_agent(name)
        state = self.agents[name]
        old_phase = state.phase
        state.phase = phase
        state.progress = max(0.0, min(1.0, progress))
        if task_summary:
            state.task_summary = task_summary
        if cycle:
            state.current_cycle = cycle
        if max_cycles:
            state.max_cycles = max_cycles

        if self.mode == VisualMode.TIMELINE and old_phase != phase:
            self._print_phase_transition(name, old_phase, phase)
        elif self.mode == VisualMode.DASHBOARD:
            self._maybe_render_dashboard()

    def update_from_status(self, name: str, status: str, task_summary: str = "") -> None:
        """DynamicOrchestrator state_board 문자열 status로 phase 업데이트."""
        phase = _STATUS_TO_PHASE.get(status, AgentPhase.WAITING)
        self.update_phase(name, phase, task_summary=task_summary)

    def update_from_fsa_step(self, name: str, step: str, cycle: int, max_cycles: int) -> None:
        """FSA Loop 단계명(execute/trace/eval 등)으로 phase 업데이트."""
        phase = _FSA_STEP_TO_PHASE.get(step.lower(), AgentPhase.CODING)
        progress = cycle / max_cycles if max_cycles else 0.0
        self.update_phase(name, phase, progress=progress, cycle=cycle, max_cycles=max_cycles)

    def mark_completed(self, name: str) -> None:
        self.update_phase(name, AgentPhase.COMPLETED, progress=1.0)
        self._completed_tasks += 1

    def mark_failed(self, name: str) -> None:
        self.update_phase(name, AgentPhase.FAILED)

    def set_task_counts(self, completed: int, total: int) -> None:
        self._completed_tasks = completed
        self._total_tasks = total

    # ── 대화 이벤트 ────────────────────────────────────────────────────────

    def on_conversation_start(self, room: "ConversationRoom") -> None:
        """대화방 시작 시 호출 — 참여 에이전트를 CONVERSING으로 전환."""
        self._active_conversations += 1
        for p in room.participants:
            self.update_phase(p, AgentPhase.CONVERSING,
                              task_summary=room.topic[:30])
        if self.mode == VisualMode.OFF:
            return

        if self.mode == VisualMode.DASHBOARD:
            self._print_conversation_header(room)
        else:
            moderator_str = f"{room.moderator}(진행)" if room.moderator else ""
            parts = ", ".join(
                [moderator_str] + [p for p in room.participants if p != room.moderator]
            ) if room.moderator else ", ".join(room.participants)
            self._print_timeline_entry(
                room.moderator or (room.participants[0] if room.participants else ""),
                f"💬 대화방 생성: \"{room.topic}\" [{room.protocol}]  참여: {parts}"
            )

    def on_conversation_turn(self, turn: "ConversationTurn", room: "ConversationRoom") -> None:
        """대화 턴 발생 시 호출 — 발언 내용을 터미널에 출력."""
        if self.mode == VisualMode.OFF:
            return

        icon = TURN_TYPE_ICONS.get(turn.turn_type, "💭")

        if self.mode in (VisualMode.DASHBOARD, VisualMode.TIMELINE):
            self._print_conversation_turn(turn.speaker, icon, turn.turn_type, turn.content)
        elif self.mode == VisualMode.MINIMAL:
            preview = turn.content[:80] + ("..." if len(turn.content) > 80 else "")
            self._print_timeline_entry(turn.speaker, f"{icon} {preview}")

    def on_consensus_reached(self, room: "ConversationRoom", decision: str) -> None:
        """합의 도달 시 호출."""
        self._active_conversations = max(0, self._active_conversations - 1)
        if self.mode != VisualMode.OFF:
            votes_str = ""
            if room.consensus and room.consensus.votes:
                agree = sum(1 for v in room.consensus.votes.values() if v == "agree")
                total = len(room.consensus.votes)
                votes_str = f" ({agree}/{total} 찬성)"
            msg = f"🤝 합의 도달: {decision}{votes_str}"
            if self.mode == VisualMode.DASHBOARD:
                self.safe_print(f"  {'─'*10} {msg} {'─'*10}")
            else:
                self._print_timeline_entry("", msg)

        for p in room.participants:
            if p in self.agents and self.agents[p].phase == AgentPhase.CONVERSING:
                self.update_phase(p, AgentPhase.WAITING)

    def on_conversation_end(self, room: "ConversationRoom") -> None:
        """대화방 종료 (합의 없이 닫힘 포함)."""
        self._active_conversations = max(0, self._active_conversations - 1)
        for p in room.participants:
            if p in self.agents and self.agents[p].phase == AgentPhase.CONVERSING:
                self.update_phase(p, AgentPhase.WAITING)

    # ── 대시보드 렌더링 ────────────────────────────────────────────────────

    def render_dashboard(self) -> str:
        """전체 에이전트 상태 대시보드 문자열 생성."""
        elapsed = time.time() - self._start_time
        m, s = divmod(int(elapsed), 60)
        w = 62

        lines: list[str] = []
        lines.append("╔" + "═" * w + "╗")
        title = "Agent Factory — Dynamic Orchestrator v3"
        # ljust는 시각적 폭 기준: ANSI 코드 길이만큼 보정하여 정확한 패딩 적용
        title_colored = f"  {BOLD}{title}{RESET}"
        title_padding = " " * max(0, w - 2 - len(title))
        lines.append("║" + title_colored + title_padding + "║")
        lines.append("╠" + "═" * w + "╣")

        for name, state in self.agents.items():
            bar = _progress_bar(state.progress, 8)
            pct = f"{int(state.progress * 100):>3}%"
            name_display = name[:10]  # 10자 초과 시 잘라냄 → 행 넘침 방지
            phase_col = f"{state.phase.color}{state.phase.icon} {state.phase.label}{RESET}"
            task = (state.task_summary or "—")[:16]
            # 색상 코드는 표시 길이에 포함되지 않으므로 raw(색상 없음)로 패딩 계산
            raw = f"  [{name_display:<10}] {state.phase.icon} {state.phase.label} {bar} {pct}  {task}"
            col_raw = f"  [{name_display:<10}] {phase_col} {bar} {pct}  {task}"
            padding = w - len(raw)
            lines.append("║" + col_raw + " " * max(0, padding) + "║")

        lines.append("╠" + "═" * w + "╣")
        summary = (
            f"  Tasks: {self._completed_tasks}/{self._total_tasks} done"
            f" | Conversations: {self._active_conversations} active"
            f" | Elapsed: {m}m {s:02d}s"
        )
        lines.append("║" + summary.ljust(w) + "║")
        lines.append("╚" + "═" * w + "╝")
        return "\n".join(lines)

    def print_dashboard(self) -> None:
        if self.mode == VisualMode.OFF:
            return
        self.safe_print(self.render_dashboard())

    # ── 내부 출력 함수 ─────────────────────────────────────────────────────

    def _maybe_render_dashboard(self) -> None:
        now = time.time()
        if now - self._last_dashboard_render >= self._refresh:
            self._last_dashboard_render = now
            self.safe_print(self.render_dashboard())

    def _print_timeline_entry(self, agent: str, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        # 에이전트 이름 14자 초과 시 잘라내어 컬럼 너비 고정 (ANSI 코드는 시각적 폭에 불포함)
        name_display = agent[:14] if agent else ""
        # 시각적 너비 기준으로 패딩 계산 (ANSI 코드 추가 후 패딩이 틀어지는 버그 방지)
        VISUAL_WIDTH = 16  # "[name]" + 공백의 시각적 목표 너비
        visible = f"[{name_display}]" if name_display else ""
        padding = " " * max(0, VISUAL_WIDTH - len(visible))
        agent_col = f"{DIM}{visible}{RESET}{padding}" if name_display else " " * VISUAL_WIDTH
        self.safe_print(f"{DIM}{ts}{RESET} ├─ {agent_col} {msg}")

    def _print_phase_transition(self, name: str, old: AgentPhase, new: AgentPhase) -> None:
        msg = (f"{old.color}{old.icon} {old.label.strip()}{RESET}"
               f" → {new.color}{new.icon} {new.label.strip()}{RESET}")
        self._print_timeline_entry(name, msg)

    def _print_conversation_header(self, room: "ConversationRoom") -> None:
        w = 62
        topic_line = f'  💬 ConversationRoom: "{room.topic}" [{room.protocol}]'
        moderator_str = f"{room.moderator}(진행)" if room.moderator else ""
        parts_str = ", ".join(
            [moderator_str] + [p for p in room.participants if p != room.moderator]
        ) if room.moderator else ", ".join(room.participants)
        meta_line = f"  참여: {parts_str}  |  Round 0/{room.max_rounds}"

        lines = [
            "╔" + "═" * w + "╗",
            "║" + topic_line.ljust(w) + "║",
            "║" + meta_line.ljust(w) + "║",
            "╠" + "═" * w + "╣",
        ]
        self.safe_print("\n".join(lines))

    def _print_conversation_turn(
        self, speaker: str, icon: str, turn_type: str, content: str
    ) -> None:
        header = f"{BLUE}{BOLD}  [{speaker}]{RESET} {icon} {DIM}{turn_type}{RESET}"
        self.safe_print(header)
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped:
                self.safe_print(f"  {GRAY}│{RESET} {stripped}")
        self.safe_print(f"  {GRAY}│{RESET}")

    def safe_print(self, text: str) -> None:
        """유니코드 인코딩 오류를 안전하게 처리하며 출력."""
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe = text.encode(enc, errors="replace").decode(enc, errors="replace")
            print(safe)


# ── 전역 싱글톤 (선택적 사용) ─────────────────────────────────────────────────

_global_visualizer: TerminalVisualizer | None = None


def get_visualizer() -> TerminalVisualizer:
    """전역 TerminalVisualizer 인스턴스를 반환 (없으면 생성)."""
    global _global_visualizer
    if _global_visualizer is None:
        _global_visualizer = TerminalVisualizer()
    return _global_visualizer


def set_visualizer(viz: TerminalVisualizer) -> None:
    """전역 TerminalVisualizer 인스턴스를 교체한다."""
    global _global_visualizer
    _global_visualizer = viz


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _progress_bar(ratio: float, width: int = 16) -> str:
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)
