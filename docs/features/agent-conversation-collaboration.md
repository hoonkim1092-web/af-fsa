# Feature: Agent Conversation Collaboration (에이전트 대화형 협업)

## 목적

프로젝트 생성 시 에이전트들이 **자유로운 대화**를 통해 협업할 수 있는 시스템 설계.
기존 `MessageBroker`/`ProjectMailbox`는 구조화된 메시지 전달에 초점이 있으나,
이 Feature는 에이전트 간 **토론 → 합의 → 실행** 흐름을 지원한다.

---

## 핵심 개념

### 1. ConversationRoom (대화방)

에이전트들이 모여 특정 주제에 대해 대화하는 공간.

```
┌─────────────────────────────────────────────┐
│  ConversationRoom: "architecture_design"     │
│                                              │
│  [Tanjiro] 주문 API는 REST vs GraphQL?       │
│  [Himari]  기존 스킬 재사용 고려하면 REST 유리 │
│  [Deadbyte] 프론트 입장에서 GraphQL이 편한데..│
│  [Tanjiro] 성능/비용 비교 후 결정하자         │
│  ─── 투표: REST(2) vs GraphQL(1) → REST 채택 │
│  ─── 합의 도출 → 실행 단계 전환               │
└─────────────────────────────────────────────┘
```

### 2. ConversationProtocol (대화 프로토콜)

| 프로토콜 | 설명 | 사용 시점 |
|----------|------|----------|
| **debate** | 자유 토론 → 투표/합의 | 설계 결정, 기술 선택 |
| **review** | 발표자 → 리뷰어 피드백 | 코드 리뷰, 설계 검토 |
| **handoff** | 순차 인수인계 대화 | 단계별 작업 전달 |
| **brainstorm** | 아이디어 수집 (비판 없음) | 초기 기획, 요구사항 도출 |
| **standup** | 각자 상태 보고 → PM 조정 | 진행 상황 공유 |

### 3. ConversationManager (대화 관리자)

대화방 생성/종료, 턴 관리, 합의 도출을 총괄.

---

## 아키텍처

```
┌──────────────────────────────────────────────────────────┐
│                    AgentFactory (싱글톤 broker 소유)       │
│  broker = MessageBroker()  ← 모든 오케스트레이터 공유      │
└────────┬───────────────────────────────────────────────┘
         │ 주입(injection)
         ├──────────────────┬──────────────────┐
         ▼                  ▼                  ▼
   ConversationManager  ProjectPipeline  DynamicOrchestrator
         │
         ▼
┌──────────────────────────────────────────────────────────┐
│               ConversationManager                         │
│                                                           │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ RoomRegistry │  │ TurnManager  │  │ ConsensusEngine│  │
│  │ 방 생성/조회  │  │ 발언 순서    │  │ 합의 도출/투표  │  │
│  └─────────────┘  └──────────────┘  └────────────────┘  │
│                                                           │
│  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │ ContextBuilder   │  │ TranscriptStore              │  │
│  │ 대화 맥락 구성    │  │ 대화 기록 저장/조회           │  │
│  └──────────────────┘  └──────────────────────────────┘  │
│                                                           │
│  ┌──────────────────────────────────────────────────┐    │
│  │ ConversationBudget  (토큰/라운드 예산 관리)        │    │
│  │  max_tokens_per_room: 50_000                      │    │
│  │  max_tokens_per_turn: 2_000                       │    │
│  └──────────────────────────────────────────────────┘    │
└────────────────────────┬─────────────────────────────────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         MessageBroker  Mailbox  AgentReservationManager
         (싱글톤, 공유)          (에이전트 점유 조정)
```

---

## ⚠️ 충돌 분석 결과 (2026-03-25)

기존 시스템과의 충돌 가능성을 코드 레벨로 검증한 결과.

### 🔴 CRITICAL — 즉시 해결 필요

#### C1. DynamicOrchestrator 에이전트 점유 충돌
- **문제**: `dynamic_orchestrator.py`의 에이전트 상태가 `"idle"/"working"` 문자열로만 관리됨
- **시나리오**: ConversationManager가 Himari를 debate 라운드에 점유한 상태에서 DynamicOrchestrator가 Himari에게 build 태스크를 동시에 할당 → **두 개의 concurrent 요청**
- **근거**: `dynamic_orchestrator.py` L466, L545에 상태 문자열 존재, 잠금 메커니즘 없음
- **해결**: `AgentReservationManager` (lease 기반 예약 시스템) 신규 구현 필요

```python
class AgentReservationManager:
    def reserve(self, agent_id: str, duration: float, reason: str) -> str | None:
        # lease_id 반환, 이미 점유 시 None
    def is_reserved(self, agent_id: str) -> bool:
    def release(self, lease_id: str):
    # lease는 TTL 만료 시 자동 해제
```

---

### 🟠 HIGH — 구현 전 해결 필요

#### H1. MessageBroker 다중 인스턴스 문제
- **문제**: `dynamic_orchestrator.py` L46, `project_pipeline.py` 내에서 각각 `MessageBroker()` 인스턴스 생성
- **결과**: 교차 오케스트레이터 메시지 전달 불가 (각 broker가 독립 큐)
- **해결**: `AgentFactory`가 싱글톤 broker를 소유하고 모든 컴포넌트에 주입

```python
class AgentFactory:
    def __init__(self):
        self.broker = MessageBroker()  # 단일 인스턴스
        self.pipeline = ProjectPipeline(..., broker=self.broker)
        self.conversation_mgr = ConversationManager(..., broker=self.broker)
        self.orchestrator = DynamicOrchestrator(self.mr, broker=self.broker)
```

#### H2. correlation_id 재사용 충돌
- **문제**: `message_broker.py` L53의 `correlation_id`는 스칼라 값 — ConversationTurn의 `references: list[str]`(여러 turn 참조)와 충돌
- **해결**: 메시지 envelope에 `turn_references: list[str]` 별도 필드 추가, `correlation_id`는 기존 request-response 용도로만 유지

#### H3. MessageBroker 채널 네이밍 충돌
- **문제**: 기존 패턴 `{entity}.{id}.{purpose}` 와 충돌 가능
- **기존**: `agent.himari.inbox`, `task.status`
- **신규 (잘못된 예)**: `conversation.room_123` (스코프 불명확)
- **해결**: 3단계 네이밍 강제 → `conversation.room.{room_id}`, `conversation.turn.{room_id}`, `conversation.consensus.{room_id}`

#### H4. Pipeline TaskBoard 이중 생성 경로
- **문제**: `project_pipeline.py` `prepare()`의 `build_project_board()` + ConversationManager의 `_populate_task_board()` 모두 `project_board_state.json` 기록
- **위험**: `sync_board_from_work_items()` (L354)이 대화 합의 결과를 덮어쓸 수 있음
- **해결**:
  1. TaskBoard 스키마에 `consensus_metadata` 필드 추가
  2. `sync_board_from_work_items()` 가 `consensus_metadata` 보존하도록 수정
  3. `ConversationToTaskAdapter` 클래스로 합의→태스크 변환 명세화

```python
# TaskBoard task 스키마 확장
{
    "task_id": "...",
    # ... 기존 필드 ...
    "consensus_metadata": {
        "conversation_room_id": "room_123",
        "consensus": {"reached": True, "decision": "REST API 채택", "votes": {...}},
        "consensus_immutable": False  # 사용자 편집 허용 여부
    }
}
```

#### H5. 토큰 예산 미구현
- **문제**: 4에이전트 × 10라운드 = 40회 LLM 호출, 예산 제한 없음
- **현재**: FSALoop는 max_cycles=5, DynamicOrchestrator는 max_cycles=15로 제한 있음
- **ConversationManager**: 토큰 추적 없음
- **해결**: `ConversationBudget` 추가, 라운드 전에 토큰 예산 확인

```python
@dataclass
class ConversationBudget:
    max_tokens_per_room: int = 50_000
    max_tokens_per_turn: int = 2_000
    reserve_tokens: int = 5_000
    tokens_used: int = 0

    def can_proceed(self, estimated_tokens: int) -> bool:
        return self.tokens_used + estimated_tokens + self.reserve_tokens <= self.max_tokens_per_room
```

---

### 🟡 MEDIUM

#### M1. CLI `--chat` vs `--collaborate` 혼동
- **문제**: `--chat`(사용자↔에이전트 1:1 대화)과 `--collaborate`(에이전트↔에이전트 다자 대화)가 사용자 입장에서 혼동
- **해결**: `--collaborate` → `--conversation-protocol`로 명칭 변경, `--pipeline project`일 때만 유효

```bash
# 올바른 사용
python run_factory_cli.py --project marketplace_mvp \
  --conversation-protocol debate \
  --agents "Tanjiro,Himari,Deadbyte" \
  --task "REST vs GraphQL 결정"

# 오류: --workflow와 함께 사용 불가
python run_factory_cli.py --workflow myflow.yaml \
  --conversation-protocol debate  # → Error: conversation requires --pipeline project
```

#### M2. `--workflow` + `--conversation-protocol` 미정의
- **문제**: 정적 워크플로우 YAML + 대화 프로토콜 동시 지정 시 우선순위/동작 불명확
- **해결**: 두 플래그 동시 사용 시 에러 메시지 + "워크플로우 YAML에 `conversation_phases` 섹션으로 지정하세요" 안내

#### M3. AgentRunner 공유 상태 경쟁
- **문제**: `AgentRunner`의 `self.mr` (ModelRouter)가 공유 인스턴스 → 동시 호출 시 모델 설정 race condition
- **문제**: `self._skill_loader_cache` 캐시 무효화 알림 없음
- **해결**: 대화 중 에이전트 호출은 **순차 실행** (turn-based 특성상 병렬 불필요), SemaphorePool로 최대 N개 동시 실행 제한

#### M4. FSALoop 재시도 vs 대화 합의 의미 충돌
- **문제**: FSALoop evaluator가 "다른 방식 시도" 권고 시 대화로 확정된 합의를 뒤집을 수 있음
- **해결**: `consensus_metadata.consensus_immutable = True`인 필드는 FSALoop이 재시도 대상에서 제외

---

### 🟢 LOW

#### L1. ProjectMailbox vs ConversationTurn 의미 중복
- **문제**: Mailbox의 `decision_request` 타입과 Conversation의 `debate` 프로토콜이 의미적으로 겹침
- **영향 낮음**: 파일 분리(`messages.jsonl` vs `transcript.jsonl`)로 실제 충돌 없음
- **해결**: 문서화로 구분 — Mailbox: 1:1 명시적 요청, Conversation: 다자 그룹 토론

#### L2. transcript.jsonl 동시 쓰기 미잠금
- **문제**: ProjectMailbox는 `locked_file()` 사용하는데 ConversationTranscript는 락 없음
- **해결**: TranscriptStore에도 동일한 파일 락 적용

---

## 선행 작업 (Phase 0) — 충돌 해소 리팩토링

> **구현 시작 전 반드시 완료해야 할 작업**

| 작업 | 파일 | 우선순위 |
|------|------|----------|
| `AgentReservationManager` 신규 구현 | `core/agent_reservation.py` | 🔴 CRITICAL |
| `AgentFactory` 싱글톤 broker 주입 리팩토링 | `agent_launcher.py` | 🟠 HIGH |
| `DynamicOrchestrator` broker 파라미터 주입 수정 | `core/dynamic_orchestrator.py` | 🟠 HIGH |
| TaskBoard 스키마 `consensus_metadata` 추가 | `core/project_task_board.py` | 🟠 HIGH |
| `sync_board_from_work_items()` 보존 로직 추가 | `core/project_pipeline.py` | 🟠 HIGH |
| MessageBroker 채널 네이밍 가이드라인 추가 | `core/message_broker.py` | 🟠 HIGH |

---

## 상세 설계

### 5.1 ConversationRoom

```python
@dataclass
class ConversationRoom:
    room_id: str                    # 고유 ID
    topic: str                      # 대화 주제
    protocol: str                   # debate | review | handoff | brainstorm | standup
    participants: list[str]         # 참여 에이전트 ID 목록
    moderator: str | None           # 진행자 (보통 PM 에이전트)
    status: str                     # open | in_progress | consensus_reached | closed
    max_rounds: int = 10            # 최대 대화 라운드 (무한 루프 방지)
    current_round: int = 0
    consensus: dict | None = None   # 합의 결과
    budget: ConversationBudget = field(default_factory=ConversationBudget)
    created_at: float
    project_id: str
```

### 5.2 ConversationTurn (발언)

```python
@dataclass
class ConversationTurn:
    turn_id: str
    room_id: str
    speaker: str                    # 에이전트 ID
    content: str                    # 발언 내용
    turn_type: str                  # statement | question | proposal | vote | objection | agreement
    turn_references: list[str] = [] # 참조하는 이전 turn_id (correlation_id와 별도)
    metadata: dict = {}             # 추가 데이터 (코드 스니펫, 파일 경로 등)
    tokens_used: int = 0            # 이 턴의 토큰 사용량
    timestamp: float
```

### 5.3 ConversationManager

```python
class ConversationManager:
    def __init__(
        self,
        project_id: str,
        broker: MessageBroker,          # AgentFactory에서 주입 (싱글톤)
        agent_runner: AgentRunner,
        reservation_mgr: AgentReservationManager  # C1 해결
    ):
        self.rooms: dict[str, ConversationRoom] = {}
        self.transcripts: dict[str, list[ConversationTurn]] = {}
        self.broker = broker
        self.runner = agent_runner
        self.reservation_mgr = reservation_mgr
        self.project_id = project_id

    def create_room(
        self,
        topic: str,
        protocol: str,
        participants: list[str],
        moderator: str | None = None,
        max_rounds: int = 10,
        budget: ConversationBudget | None = None
    ) -> ConversationRoom:
        """대화방 생성 — 참여 에이전트 lease 예약"""
        # AgentReservationManager로 모든 participants 예약
        # 예약 실패 시 ConversationRoom 생성 중단

    async def run_conversation(
        self,
        room_id: str,
        initial_context: dict | None = None
    ) -> ConversationResult:
        """
        대화 실행 루프 (순차):
        1. 각 라운드마다 참여자에게 순차로 발언 기회 부여
        2. 이전 대화 맥락 + initial_context를 컨텍스트로 제공
        3. 매 턴 후 budget.can_proceed() 확인
        4. 합의 조건 충족 시 종료
        5. max_rounds 도달 시 moderator가 최종 결정
        6. 종료 시 모든 에이전트 lease 해제
        """

    async def _get_agent_response(
        self,
        agent_id: str,
        room: ConversationRoom,
        context: list[ConversationTurn]
    ) -> ConversationTurn:
        """에이전트에게 대화 컨텍스트를 주고 순차로 응답 받기"""

    async def _check_consensus(
        self,
        room: ConversationRoom,
        turns: list[ConversationTurn]
    ) -> ConsensusResult | None:
        """
        합의 도출 확인:
        - debate: 과반수 동의 또는 moderator 최종 결정
        - review: 모든 리뷰어 승인
        - brainstorm: max_rounds 소진 후 아이디어 정리
        - standup: 모든 참여자 보고 완료
        2라운드 연속 동일 입장 → 조기 수렴 종료
        """

    def get_transcript(self, room_id: str) -> list[ConversationTurn]:
        """대화 기록 조회"""

    def get_consensus(self, room_id: str) -> dict | None:
        """합의 결과 조회"""
```

### 5.4 ConsensusEngine

```python
class ConsensusEngine:
    async def evaluate(
        self,
        protocol: str,
        turns: list[ConversationTurn],
        participants: list[str]
    ) -> ConsensusResult | None:
        """
        프로토콜별 합의 판정:
        - 명시적 동의/반대 발언 추출
        - 투표 집계
        - 합의 요약 생성
        """

@dataclass
class ConsensusResult:
    reached: bool                   # 합의 도달 여부
    decision: str                   # 결정 사항 요약
    votes: dict[str, str] = {}     # 에이전트별 입장
    dissent: list[str] = []        # 반대 의견
    action_items: list[dict] = []  # 후속 작업 목록 → ConversationToTaskAdapter 입력
```

### 5.5 ConversationToTaskAdapter

합의 결과를 TaskBoard task로 변환하는 명세:

```python
class ConversationToTaskAdapter:
    def adapt(
        self,
        consensus: ConsensusResult,
        room: ConversationRoom,
        existing_board: dict
    ) -> dict:
        """
        consensus.action_items → task 목록 생성
        의존성 추론: "A 완료 후 B" 패턴 → depends_on 자동 설정
        각 task에 consensus_metadata 삽입
        기존 board와 merge (중복 제거)
        """
```

### 5.6 프로젝트 생성 시 자동 대화 흐름

```python
# ProjectPipeline.prepare()에 통합 (execute() 전 단계)
class ProjectPipeline:
    def prepare(self, ..., enable_conversation: bool = False):
        # 기존 prepare 로직 (role_plan, build_project_board)
        board = self._build_project_board(...)

        if enable_conversation:
            # ConversationManager는 AgentFactory가 주입
            conv_mgr = self.conversation_mgr

            # Phase 1: 요구사항 브레인스토밍
            brainstorm_room = conv_mgr.create_room(
                topic=f"프로젝트 요구사항 분석: {project_desc}",
                protocol="brainstorm",
                participants=agents,
                moderator="tanjiro",
                max_rounds=5,
                budget=ConversationBudget(max_tokens_per_room=20_000)
            )
            brainstorm_result = asyncio.run(
                conv_mgr.run_conversation(brainstorm_room.room_id)
            )

            # Phase 2: 아키텍처 설계 토론
            design_room = conv_mgr.create_room(
                topic="아키텍처 설계 결정",
                protocol="debate",
                participants=agents,
                moderator="tanjiro",
                max_rounds=8,
                budget=ConversationBudget(max_tokens_per_room=30_000)
            )
            design_result = asyncio.run(
                conv_mgr.run_conversation(
                    design_room.room_id,
                    initial_context=brainstorm_result.consensus
                )
            )

            # Phase 3: 역할 분담 스탠드업
            standup_room = conv_mgr.create_room(
                topic="역할 분담 및 작업 계획",
                protocol="standup",
                participants=agents,
                moderator="tanjiro",
                max_rounds=3
            )
            plan_result = asyncio.run(
                conv_mgr.run_conversation(
                    standup_room.room_id,
                    initial_context=design_result.consensus
                )
            )

            # 합의 결과 → TaskBoard에 merge (기존 board 덮어쓰지 않고 병합)
            adapter = ConversationToTaskAdapter()
            board = adapter.adapt(plan_result.consensus, standup_room, board)

        return PreparedProject(board=board, ...)
```

---

## 대화 컨텍스트 구성

```
[System] 당신은 {agent_name}입니다. {role_description}

[대화 규칙]
- 프로토콜: {protocol} (debate/review/brainstorm/standup)
- 주제: {topic}
- 참여자: {participants}
- 현재 라운드: {current_round}/{max_rounds}
- 남은 토큰 예산: {budget.remaining_tokens}
- {protocol별 규칙}

[이전 대화]
[Tanjiro] ...
[Himari] ...
[Deadbyte] ...

[당신의 차례입니다. 아래 JSON 형식으로 응답하세요.]
{"turn_type": "proposal"|"agreement"|"objection"|"vote"|"statement", "content": "..."}
```

---

## 대화 기록 저장

```
projects/{project_id}/data/conversations/
├── room_{room_id}/
│   ├── metadata.json       # 방 설정, 참여자, 프로토콜, budget
│   ├── transcript.jsonl    # 턴별 대화 기록 (append-only, 파일 락 사용)
│   └── consensus.json      # 합의 결과
└── index.json              # 전체 대화방 목록
```

---

## 무한 루프 방지

1. **max_rounds** — 방별 최대 라운드 제한 (기본 10)
2. **합의 수렴 감지** — 2라운드 연속 동일 입장이면 조기 종료
3. **ConversationBudget** — 방별 토큰 상한 (`context_window_manager` 연동)
4. **AgentReservationManager TTL** — lease 만료 시 자동 해제 (교착 방지)
5. **moderator 강제 종결** — max_rounds 소진 시 moderator가 최종 결정

---

## 사용자 개입 (HITL)

| 시점 | 동작 |
|------|------|
| 대화 시작 전 | 참여 에이전트/프로토콜 선택 가능 |
| 대화 중 | 사용자가 발언 삽입 가능 (`turn_type: "human_input"`) |
| 합의 후 | 사용자 승인 → 실행 / 거부 → 재토론 |
| 교착 시 | 사용자에게 결정권 위임 |

---

## 영향 범위

### 신규 파일

| 파일 | 역할 |
|------|------|
| `core/agent_reservation.py` | **AgentReservationManager** — lease 기반 에이전트 점유 관리 |
| `core/conversation_manager.py` | **ConversationManager** — 대화 관리 핵심 |
| `core/consensus_engine.py` | **ConsensusEngine** — 합의 도출 |
| `core/conversation_room.py` | **ConversationRoom, ConversationTurn, ConversationBudget** 데이터 모델 |
| `core/conversation_prompts.py` | 프로토콜별 프롬프트 템플릿 |
| `core/conversation_task_adapter.py` | **ConversationToTaskAdapter** — 합의→태스크 변환 |

### 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `agent_launcher.py` | 싱글톤 broker + ConversationManager 주입, `--conversation-protocol` 옵션 |
| `core/dynamic_orchestrator.py` | broker 파라미터 주입 받도록 수정, ReservationManager 연동 |
| `core/project_pipeline.py` | `prepare()` conversation 단계 추가, `sync_board` consensus_metadata 보존 |
| `core/project_task_board.py` | task 스키마에 `consensus_metadata` 필드 추가 |
| `core/message_broker.py` | 채널 네이밍 3단계 규칙 + `turn_references` 필드 추가 |
| `run_factory_cli.py` | `--conversation-protocol` 옵션 추가 (`--pipeline project` 전용) |

---

## CLI 사용 예시

```bash
# 대화형 협업으로 프로젝트 생성
python run_factory_cli.py --project marketplace_mvp \
  --conversation-protocol debate \
  --agents "Tanjiro,Himari,Deadbyte,Iguro" \
  --task "마켓플레이스 MVP 2주 개발"

# 특정 프로토콜만 지정
python run_factory_cli.py --project marketplace_mvp \
  --conversation-protocol brainstorm \
  --agents "Tanjiro,Himari" \
  --task "요구사항 도출"

# --workflow와 함께 사용 불가 (에러)
# python run_factory_cli.py --workflow myflow.yaml --conversation-protocol debate
# → Error: --conversation-protocol requires --pipeline project
```

---

## 구현 체크리스트

### Phase 0: 선행 리팩토링 (충돌 해소) ✅ 완료
- [x] `AgentReservationManager` 구현 (`core/agent_reservation.py`)
- [x] `AgentFactory` 싱글톤 broker 주입 리팩토링 (`agent_launcher.py`)
- [x] `DynamicOrchestrator` broker 파라미터 주입 수정
- [x] TaskBoard `consensus_metadata` 보존 로직 (`core/work_item_parser.py`)
- [x] MessageBroker 3단계 채널 네이밍 가이드라인 + `turn_references` 필드

### Phase 1: 핵심 구현 ✅ 완료
- [x] `ConversationRoom`, `ConversationTurn`, `ConversationBudget` 데이터 모델 (`core/conversation_room.py`)
- [x] `ConversationManager` 코어 로직 (`core/conversation_manager.py`)
- [x] `ConsensusEngine` 합의 판정 (`core/consensus_engine.py`)
- [x] 프로토콜별 프롬프트 템플릿 (`core/conversation_prompts.py`)
- [x] `TranscriptStore` 파일 락 적용 (`core/conversation_manager.py` 내부)

### Phase 2: 통합 ✅ 완료
- [x] `ConversationToTaskAdapter` 구현 (`core/conversation_task_adapter.py`)
- [x] `AgentFactory.run_with_conversation()` 연동 (`agent_launcher.py`)
- [x] `run_factory_cli.py` `--conversation-protocol` 옵션
- [x] `ProjectPipeline.prepare()` 직접 연동 (`conversation_protocol`, `conversation_agents` 파라미터)
- [x] FSALoop `consensus_immutable` 존중 (`_extract_immutable_decisions()`)

### Phase 3: 안전망 & 테스트 ✅ 완료
- [x] `ConversationBudget` 토큰 추적 (`estimate_tokens` 연동, `_estimate_round_cost()`)
- [x] HITL 개입 포인트 (`_hitl_check_start`, `_hitl_check_round`, `_hitl_approve_consensus`)
- [x] 단위 테스트 35개 (`tests/test_conversation_collaboration.py`) — 35/35 통과
- [ ] 통합 테스트 (2+ 에이전트 실제 LLM 대화) — LLM API 필요, 별도 진행
