# Feature: Terminal Agent Visualizer (터미널 에이전트 시각화)

## 목적

에이전트가 설계/코딩/리뷰 작업 중일 때, 그리고 에이전트 간 대화(ConversationRoom)가 진행될 때
터미널에서 **실시간 시각화**를 제공한다.

현재 문제:
- `print_agent_msg()`는 단순 텍스트 출력만 지원
- 에이전트 간 대화(debate/review 등)가 진행되어도 내부 로그에만 기록되고 터미널에 표시되지 않음
- 여러 에이전트가 동시에 작업할 때 전체 상태 파악이 어려움

---

## 핵심 시각화 요소

### 1. 작업 페이즈 표시 (Phase Indicator)

각 에이전트의 현재 작업 상태를 아이콘+색상으로 구분:

```
Phase          Icon   Color     설명
──────────────────────────────────────────
DESIGNING      📐     Cyan      설계/분석 중
CODING         💻     Green     코드 작성 중
REVIEWING      🔍     Yellow    코드 리뷰/평가 중
TESTING        🧪     Magenta   테스트 실행 중
CONVERSING     💬     Blue      에이전트 간 대화 참여 중
WAITING        ⏳     Gray      대기/블록 중
COMPLETED      ✅     Green     완료
FAILED         ❌     Red       실패
```

FSA 사이클 → Phase 매핑:
```
EXECUTE   → CODING
TRACE     → REVIEWING
EVAL      → REVIEWING
SUMMARIZE → DESIGNING
REFLECT   → DESIGNING
```

### 2. 진행률 바 (Progress Bar)

```
[Himari]    💻 CODING    ████████░░░░░░░░ 50%  (cycle 3/6)  auth-module
[DeadByte]  🔍 REVIEWING ████████████░░░░ 75%  (cycle 4/6)  api-endpoint
[Iguro]     ⏳ WAITING   ░░░░░░░░░░░░░░░░  0%  —
```

### 3. 대화 실시간 스트림 (Conversation Stream)

ConversationRoom에서 에이전트 간 대화가 진행될 때 터미널에 실시간 출력:

```
╔══════════════════════════════════════════════════════════════╗
║  💬 ConversationRoom: "아키텍처 설계" [debate]               ║
║  참여: Tanjiro(진행), Himari, DeadByte  |  Round 2/8        ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  [Tanjiro] 📋 proposal                                      ║
║  │ 주문 API는 REST vs GraphQL 중 하나를 선택해야 합니다.     ║
║  │ 기존 스킬과의 호환성, 프론트 편의성 두 가지 기준으로       ║
║  │ 논의해 주세요.                                            ║
║  │                                                           ║
║  [Himari] 💡 statement                                       ║
║  │ 기존 스킬이 REST 기반이라 재사용 비용이 낮습니다.          ║
║  │ GraphQL로 가면 어댑터 레이어가 추가로 필요해요.            ║
║  │                                                           ║
║  [DeadByte] ❓ question                                      ║
║  │ 프론트 입장에서 over-fetching 이슈가 있는데,              ║
║  │ REST + BFF 패턴으로 해결 가능한가요?                       ║
║  │                                                           ║
║  [Himari] ✅ agreement                                       ║
║  │ REST + BFF면 충분합니다. 동의합니다.                       ║
║  │                                                           ║
║  [DeadByte] ✅ agreement                                     ║
║  │ 그러면 REST로 가죠. 찬성합니다.                            ║
║  │                                                           ║
║  ─── 🤝 합의 도달: REST + BFF 패턴 채택 (3/3 찬성) ───      ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

### 4. 멀티 에이전트 대시보드 (Dashboard)

DynamicOrchestrator 실행 시 상단에 전체 상태 요약:

```
╔══════════════════════════════════════════════════════════════╗
║  Agent Factory — Dynamic Orchestrator v3                     ║
╠══════════════════════════════════════════════════════════════╣
║  [Tanjiro]   💬 CONVERSING ██░░░░░░ 25%  architecture       ║
║  [Himari]    💬 CONVERSING ██░░░░░░ 25%  architecture       ║
║  [DeadByte]  💬 CONVERSING ██░░░░░░ 25%  architecture       ║
║  [Iguro]     ⏳ WAITING    ░░░░░░░░  0%  —                  ║
╠══════════════════════════════════════════════════════════════╣
║  Tasks: 0/8 done | Conversations: 1 active | Elapsed: 1m12s ║
╚══════════════════════════════════════════════════════════════╝
```

### 5. 타임라인 로그 (Timeline)

작업 + 대화 흐름을 시간순으로 통합 시각화:

```
14:23:01 ├─ [Tanjiro]   💬 대화방 생성: "아키텍처 설계" [debate]
14:23:01 ├─ [Tanjiro]   📋 "주문 API는 REST vs GraphQL..."
14:23:15 ├─ [Himari]    💡 "기존 스킬이 REST 기반이라..."
14:23:28 ├─ [DeadByte]  ❓ "프론트 입장에서 over-fetching..."
14:23:42 ├─ [Himari]    ✅ "REST + BFF면 충분합니다"
14:23:55 ├─ [DeadByte]  ✅ "REST로 가죠. 찬성합니다"
14:24:00 ├─ 🤝 합의 도달: REST + BFF 패턴 채택
14:24:01 ├─ [Himari]    📐 DESIGNING → 💻 CODING (auth-module)
14:24:02 ├─ [DeadByte]  📐 DESIGNING → 💻 CODING (api-endpoint)
14:25:30 ├─ [Himari]    💻 CODING    → 🔍 REVIEWING
14:26:00 └─ [Himari]    ✅ COMPLETED (1m 59s)
```

---

## 구현 구조

### 핵심 모듈: `core/terminal_visualizer.py`

```python
from enum import Enum
from dataclasses import dataclass, field
import time
import os
import sys
from typing import Optional

from core.conversation_room import ConversationTurn, ConversationRoom


# ── Phase 정의 ────────────────────────────────────────────────

class AgentPhase(Enum):
    DESIGNING   = ("📐", "DESIGNING",   "\033[36m")  # Cyan
    CODING      = ("💻", "CODING",      "\033[32m")  # Green
    REVIEWING   = ("🔍", "REVIEWING",   "\033[33m")  # Yellow
    TESTING     = ("🧪", "TESTING",     "\033[35m")  # Magenta
    CONVERSING  = ("💬", "CONVERSING",  "\033[34m")  # Blue
    WAITING     = ("⏳", "WAITING",     "\033[90m")  # Gray
    COMPLETED   = ("✅", "COMPLETED",   "\033[32m")  # Green
    FAILED      = ("❌", "FAILED",      "\033[31m")  # Red

    @property
    def icon(self) -> str:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]

    @property
    def color(self) -> str:
        return self.value[2]


# turn_type → 아이콘 매핑
TURN_TYPE_ICONS = {
    "proposal":       "📋",
    "statement":      "💡",
    "question":       "❓",
    "agreement":      "✅",
    "objection":      "🚫",
    "vote":           "🗳️",
    "summary":        "📝",
    "human_input":    "👤",
    "final_decision": "⚖️",
    "unstructured":   "💭",
}

RESET = "\033[0m"


# ── Agent Visual State ────────────────────────────────────────

@dataclass
class AgentVisualState:
    agent_name: str
    phase: AgentPhase = AgentPhase.WAITING
    progress: float = 0.0          # 0.0 ~ 1.0
    current_cycle: int = 0
    max_cycles: int = 0
    task_summary: str = ""
    started_at: float = field(default_factory=time.time)
    conversation_room_id: Optional[str] = None


# ── 시각화 모드 ───────────────────────────────────────────────

class VisualMode(Enum):
    DASHBOARD = "dashboard"
    TIMELINE  = "timeline"
    MINIMAL   = "minimal"
    OFF       = "off"


# ── TerminalVisualizer ────────────────────────────────────────

class TerminalVisualizer:
    """에이전트 작업 상태 + 대화 내용 터미널 시각화 엔진."""

    def __init__(self, mode: VisualMode | None = None):
        env_mode = os.environ.get("AGENT_VISUAL_MODE", "timeline").lower()
        self.mode = mode or VisualMode(env_mode)
        self.agents: dict[str, AgentVisualState] = {}
        self._start_time = time.time()
        self._completed_tasks = 0
        self._total_tasks = 0
        self._active_conversations = 0

    # ── 에이전트 상태 관리 ─────────────────────────────────

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
        self.register_agent(name)
        old = self.agents[name]
        old_phase = old.phase
        old.phase = phase
        old.progress = max(0.0, min(1.0, progress))
        old.task_summary = task_summary or old.task_summary
        old.current_cycle = cycle
        old.max_cycles = max_cycles or old.max_cycles

        if self.mode == VisualMode.TIMELINE and old_phase != phase:
            self._print_timeline_transition(name, old_phase, phase)

    def mark_completed(self, name: str) -> None:
        self.update_phase(name, AgentPhase.COMPLETED, progress=1.0)
        self._completed_tasks += 1

    def set_task_counts(self, completed: int, total: int) -> None:
        self._completed_tasks = completed
        self._total_tasks = total

    # ── 대화 시각화 ────────────────────────────────────────

    def on_conversation_start(self, room: ConversationRoom) -> None:
        """대화방 시작 시 호출."""
        self._active_conversations += 1
        for p in room.participants:
            self.update_phase(p, AgentPhase.CONVERSING,
                              task_summary=room.topic[:30])

        if self.mode == VisualMode.OFF:
            return

        if self.mode == VisualMode.DASHBOARD:
            self._print_conversation_header(room)
        else:
            self._print_timeline_entry(
                room.moderator or room.participants[0],
                f"💬 대화방 생성: \"{room.topic}\" [{room.protocol}]"
            )

    def on_conversation_turn(self, turn: ConversationTurn, room: ConversationRoom) -> None:
        """대화 턴 발생 시 호출 — 에이전트 발언 내용 출력."""
        if self.mode == VisualMode.OFF:
            return

        icon = TURN_TYPE_ICONS.get(turn.turn_type, "💭")
        content_preview = turn.content[:80]
        if len(turn.content) > 80:
            content_preview += "..."

        if self.mode in (VisualMode.DASHBOARD, VisualMode.TIMELINE):
            self._print_conversation_turn(turn.speaker, icon, turn.turn_type,
                                          turn.content, room)
        elif self.mode == VisualMode.MINIMAL:
            self._print_timeline_entry(
                turn.speaker, f"{icon} {content_preview}"
            )

    def on_consensus_reached(self, room: ConversationRoom, decision: str) -> None:
        """합의 도달 시 호출."""
        self._active_conversations = max(0, self._active_conversations - 1)

        if self.mode == VisualMode.OFF:
            return

        votes_str = ""
        if room.consensus and room.consensus.votes:
            agree = sum(1 for v in room.consensus.votes.values() if v == "agree")
            total = len(room.consensus.votes)
            votes_str = f" ({agree}/{total} 찬성)"

        if self.mode == VisualMode.DASHBOARD:
            self._print_consensus_box(decision, votes_str)
        else:
            self._print_timeline_entry("", f"🤝 합의 도달: {decision}{votes_str}")

        # 참여자 phase 복원
        for p in room.participants:
            if self.agents.get(p) and self.agents[p].phase == AgentPhase.CONVERSING:
                self.update_phase(p, AgentPhase.WAITING)

    def on_conversation_end(self, room: ConversationRoom) -> None:
        """대화방 종료 (합의 없이 닫힘 포함)."""
        self._active_conversations = max(0, self._active_conversations - 1)
        for p in room.participants:
            if self.agents.get(p) and self.agents[p].phase == AgentPhase.CONVERSING:
                self.update_phase(p, AgentPhase.WAITING)

    # ── 대시보드 렌더링 ────────────────────────────────────

    def render_dashboard(self) -> str:
        """전체 에이전트 상태 대시보드 문자열 생성."""
        elapsed = time.time() - self._start_time
        m, s = divmod(int(elapsed), 60)

        lines = []
        w = 62
        lines.append("╔" + "═" * w + "╗")
        lines.append("║" + "  Agent Factory — Dynamic Orchestrator v3".ljust(w) + "║")
        lines.append("╠" + "═" * w + "╣")

        for name, state in self.agents.items():
            bar = self._progress_bar(state.progress, 8)
            pct = f"{int(state.progress * 100):>3}%"
            phase_str = f"{state.phase.icon} {state.phase.label:<12}"
            task = (state.task_summary or "—")[:16]
            line = f"  [{name:<10}] {phase_str} {bar} {pct}  {task}"
            lines.append("║" + line.ljust(w) + "║")

        lines.append("╠" + "═" * w + "╣")
        summary = (f"  Tasks: {self._completed_tasks}/{self._total_tasks} done"
                   f" | Conversations: {self._active_conversations} active"
                   f" | Elapsed: {m}m {s:02d}s")
        lines.append("║" + summary.ljust(w) + "║")
        lines.append("╚" + "═" * w + "╝")

        return "\n".join(lines)

    def print_dashboard(self) -> None:
        if self.mode == VisualMode.OFF:
            return
        self._safe_print(self.render_dashboard())

    # ── 내부 출력 함수 ─────────────────────────────────────

    def _progress_bar(self, ratio: float, width: int = 16) -> str:
        filled = int(ratio * width)
        return "█" * filled + "░" * (width - filled)

    def _print_timeline_entry(self, agent: str, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        agent_str = f"[{agent}]" if agent else "       "
        self._safe_print(f"{ts} ├─ {agent_str:<14} {msg}")

    def _print_timeline_transition(
        self, name: str, old: AgentPhase, new: AgentPhase
    ) -> None:
        self._print_timeline_entry(
            name,
            f"{old.icon} {old.label} → {new.icon} {new.label}"
        )

    def _print_conversation_header(self, room: ConversationRoom) -> None:
        w = 62
        parts = ", ".join(room.participants)
        mod = f"{room.moderator}(진행)" if room.moderator else ""
        lines = [
            "╔" + "═" * w + "╗",
            "║" + f'  💬 ConversationRoom: "{room.topic}" [{room.protocol}]'.ljust(w) + "║",
            "║" + f"  참여: {mod} {parts}  |  Round {room.current_round}/{room.max_rounds}".ljust(w) + "║",
            "╠" + "═" * w + "╣",
        ]
        self._safe_print("\n".join(lines))

    def _print_conversation_turn(
        self, speaker: str, icon: str, turn_type: str,
        content: str, room: ConversationRoom
    ) -> None:
        color = AgentPhase.CONVERSING.color
        header = f"{color}  [{speaker}] {icon} {turn_type}{RESET}"
        self._safe_print(header)

        # 내용을 줄 단위로 들여쓰기 출력
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped:
                self._safe_print(f"  │ {stripped}")
        self._safe_print("  │")

    def _print_consensus_box(self, decision: str, votes_str: str) -> None:
        msg = f"  ─── 🤝 합의 도달: {decision}{votes_str} ───"
        self._safe_print(msg)

    def _safe_print(self, text: str) -> None:
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe = text.encode(enc, errors="replace").decode(enc, errors="replace")
            print(safe)
```

---

## 통합 포인트

### 1. `core/utils.py` — `print_agent_msg()` 확장

```python
def print_agent_msg(name, msg, signature="", phase=None, visualizer=None):
    # 기존 로직 유지 (하위호환)
    ...
    # phase가 있고 visualizer가 있으면 시각화 업데이트
    if visualizer and phase:
        visualizer.update_phase(name, phase)
```

### 2. `core/fsa_loop.py` — 각 사이클에서 phase 업데이트

```python
# run_mission() 내부
self.visualizer.update_phase(agent_name, AgentPhase.CODING,
    progress=cycle/max_cycles, cycle=cycle, max_cycles=max_cycles)
# TRACE/EVAL 단계
self.visualizer.update_phase(agent_name, AgentPhase.REVIEWING, ...)
```

### 3. `core/conversation_manager.py` — 대화 시각화 콜백

```python
class ConversationManager:
    def __init__(self, ..., visualizer: TerminalVisualizer | None = None):
        self.visualizer = visualizer

    async def run_conversation(self, room_id, ...):
        room = self.rooms[room_id]
        if self.visualizer:
            self.visualizer.on_conversation_start(room)

        for round in range(room.max_rounds):
            for participant in room.participants:
                turn = await self._get_agent_response(participant, room, ...)
                if self.visualizer:
                    self.visualizer.on_conversation_turn(turn, room)

            consensus = await self._check_consensus(room, turns)
            if consensus and consensus.reached:
                if self.visualizer:
                    self.visualizer.on_consensus_reached(room, consensus.decision)
                break

        if self.visualizer:
            self.visualizer.on_conversation_end(room)
```

### 4. `core/dynamic_orchestrator.py` — 대시보드 주기적 갱신

```python
class DynamicOrchestrator:
    def __init__(self, ..., visualizer=None):
        self.visualizer = visualizer or TerminalVisualizer()

    async def _sync_loop(self):
        # 매 사이클마다 대시보드 갱신
        if self.visualizer.mode == VisualMode.DASHBOARD:
            self.visualizer.print_dashboard()
```

### 5. `agent_launcher.py` — Visualizer 인스턴스 생성 & 주입

```python
class AgentFactory:
    def __init__(self):
        self.visualizer = TerminalVisualizer()
        self.conversation_mgr = ConversationManager(..., visualizer=self.visualizer)
        self.orchestrator = DynamicOrchestrator(..., visualizer=self.visualizer)
```

---

## 환경변수 설정

| 변수 | 값 | 기본값 | 설명 |
|------|---|--------|------|
| `AGENT_VISUAL_MODE` | `dashboard` / `timeline` / `minimal` / `off` | `timeline` | 시각화 모드 |
| `AGENT_VISUAL_REFRESH` | 초 단위 숫자 | `2` | 대시보드 갱신 주기 |

---

## 영향 범위

### 신규 파일

| 파일 | 역할 |
|------|------|
| `core/terminal_visualizer.py` | 시각화 엔진 (Phase, Dashboard, Timeline, 대화 스트림) |

### 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `core/utils.py` | `print_agent_msg()` phase/visualizer 파라미터 추가 (하위호환) |
| `core/fsa_loop.py` | 사이클별 `visualizer.update_phase()` 호출 |
| `core/dynamic_orchestrator.py` | visualizer 주입, 대시보드 갱신 |
| `core/conversation_manager.py` | visualizer 주입, 대화 이벤트 콜백 |
| `agent_launcher.py` | TerminalVisualizer 인스턴스 생성 & 주입 |

---

## 충돌 분석 결과 (2026-03-26)

기존 시스템과의 충돌 가능성을 코드 레벨로 검증한 결과.

### 🟢 충돌 없음 — 안전한 통합 포인트

#### S1. `print_agent_msg()` 시그니처 확장 (하위호환)
- **현재**: `print_agent_msg(name, msg, signature="")`
- **변경**: `print_agent_msg(name, msg, signature="", phase=None, visualizer=None)`
- **검증**: 기존 모든 호출(fsa_loop.py, dynamic_orchestrator.py, agent_runner.py 등)이 positional 2~3개로 호출 → keyword 추가는 하위호환 유지
- **결론**: 안전

#### S2. `ConversationManager.__init__()` 파라미터 추가
- **현재**: `__init__(self, project_id, workspace, broker, agent_runner, agent_mgr, reservation_mgr, mr, llm_engine)`
- **변경**: `visualizer=None` keyword 추가
- **검증**: 기존 호출부(`agent_launcher.py`)가 keyword로 주입 → 기본값 None으로 안전
- **결론**: 안전

#### S3. `DynamicOrchestrator.__init__()` 파라미터 추가
- **현재**: `__init__(self, mr, max_concurrent, terminal_per_agent, broker)`
- **변경**: `visualizer=None` keyword 추가
- **검증**: 기존 호출이 keyword로 전달 → 기본값 None으로 안전
- **결론**: 안전

#### S4. `FSALoop.__init__()` 파라미터 추가
- **현재**: `__init__(self, runner, agent_mgr=None)`
- **변경**: `visualizer=None` keyword 추가
- **결론**: 안전

---

### 🟡 MEDIUM — 주의 필요

#### M1. 이중 출력 문제 (ConversationManager 기존 print + Visualizer 출력)
- **문제**: `conversation_manager.py:379`에 이미 `print(f"  [{agent_id}] ({turn.turn_type}) {turn.content[:100]}")` 존재
- **Visualizer도** `on_conversation_turn()`에서 동일 내용 출력 → **중복 출력**
- **해결**: Visualizer가 주입되었을 때는 기존 `print()` 출력을 스킵하도록 조건 분기
```python
# conversation_manager.py L379
if not self.visualizer:
    print(f"  [{agent_id}] ({turn.turn_type}) {turn.content[:100]}")
# visualizer가 있으면 on_conversation_turn()이 대신 출력
```
- **영향 범위**: `conversation_manager.py` L291, L360, L366, L379, L418, L437 (총 6곳)

#### M2. FSALoop 기존 print()와 Visualizer 타임라인 이중 출력
- **문제**: `fsa_loop.py:76`의 `print(f"\n🔄 [Cycle {cycle}/{self.max_cycles}]...")` 등 기존 출력과 Visualizer의 timeline 이중 출력
- **해결**: M1과 동일 — Visualizer 존재 시 기존 print 스킵 또는 Visualizer를 통해 출력
- **영향 범위**: `fsa_loop.py` L60, L76, L94, L98, L99 (총 5곳)

#### M3. DynamicOrchestrator `agents_status` 문자열과 AgentPhase Enum 불일치
- **문제**: `dynamic_orchestrator.py:467`의 `self.state_board["agents_status"][role] = "working"` (문자열)
- **Visualizer**: `AgentPhase.CODING` (Enum)
- **해결**: Visualizer는 독립적으로 Phase를 관리하되, `state_board["agents_status"]`와 양방향 매핑 함수 제공
```python
_STATUS_TO_PHASE = {
    "idle": AgentPhase.WAITING,
    "working": AgentPhase.CODING,  # 기본값, 세부 phase는 FSALoop에서 갱신
}
```
- **위험도**: 낮음 — 기존 상태 문자열은 그대로 유지, Visualizer는 별도 레이어

#### M4. 대시보드 모드에서 터미널 커서 제어 문제
- **문제**: `VisualMode.DASHBOARD`는 전체 화면을 갱신하는 방식 → 기존 print 출력과 섞이면 화면 깨짐
- **해결**: 대시보드 모드에서는 ANSI escape `\033[H\033[J` (화면 클리어)를 사용하되, 로그는 별도 영역에 스크롤
- **대안**: 대시보드는 별도 스레드에서 주기적 갱신, 로그 출력은 대시보드 아래 영역에 append
- **Windows 호환**: Windows Terminal은 ANSI 지원, 레거시 cmd.exe는 `colorama` 필요 → `os.system("")` 트릭으로 ANSI 활성화

#### M5. `terminal_per_agent=True` 모드에서 Visualizer 무의미
- **문제**: `dynamic_orchestrator.py:490` — 에이전트가 각각 별도 터미널 창에서 실행될 때, 메인 프로세스의 Visualizer가 해당 에이전트 출력을 볼 수 없음
- **해결**: `terminal_per_agent=True`일 때는 대시보드만 유지 (phase 상태는 result 파일 폴링으로 업데이트), 대화 스트림은 메인 터미널에 표시하지 않음
- **영향**: 기존 터미널 모드 동작 변경 없음

---

### 🟢 LOW — 영향 미미

#### L1. `_safe_print` vs `_safe_out` 중복
- **문제**: `utils.py:print_agent_msg`의 `_safe_out()`과 Visualizer의 `_safe_print()`가 동일 로직
- **해결**: 공통 함수 `safe_print()`를 `core/utils.py`에 추출 → 양쪽에서 재사용

#### L2. asyncio 이벤트 루프 안에서 Visualizer 호출
- **문제**: `ConversationManager._run_conversation_async()`는 async → Visualizer의 `_safe_print()`는 동기 I/O → 블로킹 가능
- **실제 영향**: `print()`는 버퍼링되어 실질적 블로킹 무시 가능 수준
- **해결**: 필요시 `asyncio.to_thread(self.visualizer.on_conversation_turn, ...)` 래핑

---

### 해결 우선순위 요약

| 항목 | 우선순위 | 작업 |
|------|----------|------|
| M1 | 구현 시 필수 | ConversationManager 기존 print 조건 분기 |
| M2 | 구현 시 필수 | FSALoop 기존 print 조건 분기 |
| M3 | 구현 시 해결 | status→phase 매핑 테이블 |
| M4 | 대시보드 모드 사용 시 | ANSI 커서 제어 + Windows 호환 |
| M5 | terminal_per_agent 사용 시 | 대시보드 only 모드 폴백 |
| L1 | 리팩토링 시 | safe_print 공통화 |
| L2 | 성능 이슈 발생 시 | async 래핑 |

---

## 체크리스트

- [x] `core/terminal_visualizer.py` 구현 (AgentPhase, AgentVisualState, TerminalVisualizer)
- [x] `print_agent_msg()` phase 파라미터 추가 + `safe_print()` 공통화 (하위호환 유지)
- [x] FSA Loop 통합 (사이클→phase 매핑, visualizer 주입)
- [x] ConversationManager 대화 시각화 콜백 통합 (이중 출력 M1 해결)
- [x] DynamicOrchestrator 대시보드 통합 (status→phase 매핑 M3 해결)
- [x] ProjectPipeline visualizer 주입
- [x] agent_launcher.py Visualizer 인스턴스 생성 & 전체 주입
- [x] Windows 터미널 ANSI 색상 호환 (`os.system("")` 트릭 적용)
- [x] 환경변수 설정 지원 (`AGENT_VISUAL_MODE`, `AGENT_VISUAL_REFRESH`)
