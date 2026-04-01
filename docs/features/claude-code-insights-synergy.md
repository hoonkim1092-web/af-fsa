# Claude Code 인사이트 × Agent-Factory 시너지 분석

**작성일:** 2026-04-01  
**최종 수정:** 2026-04-01 (멀티 프로바이더 호환성 분석 + 메모리 기반 캐시 공유 전략 추가)  
**상태:** 분석 완료, 구현 대기

---

## 개요

Claude Code 소스코드 분석에서 발견된 10개 인사이트를 agent-factory에 적용했을 때의 시너지와 구체적 구현 단계를 정리한 문서.

> **중요:** agent-factory는 Claude/Codex/Gemini 멀티 프로바이더 시스템이므로, 모든 설계는 특정 프로바이더에 종속되지 않는 추상화를 전제로 한다.

---

## 현재 상태 요약

agent-factory는 이미 상당히 고도화된 시스템:
- `DynamicOrchestrator`: asyncio 기반 5-concurrent 에이전트 병렬 처리
- `core/hooks/`: 7개 이벤트 훅 (pre/post_execute, pre/post_tool_call 등)
- `core/memory_system/`: episode 기반 학습, decay, cross-project 공유
- `core/control/`: sidecar 아키텍처의 유지보수 레이어
- `core/concurrency.py`: circuit breaker, heartbeat 모니터링

**하지만 CLAUDE.md가 없고**, Claude Code 자체 기능과의 직접 연동은 아직 미개발.

---

## 시너지 매핑 (10개 인사이트 → 적용 단계)

| # | 인사이트 | 현재 상태 | 시너지 레벨 |
|---|---------|----------|-----------|
| 2 | CLAUDE.md 최적화 | **없음** — 최대 기회 | ★★★★★ |
| 3 | 병렬 캐시 공유 | DynamicOrchestrator 존재, 캐시 최적화 미적용 | ★★★★★ |
| 4 | 권한 자동화 | settings.local.json에 150+규칙 이미 있음 | ★★★☆☆ |
| 5 | /compact 전략 | context_window_manager.py 존재 | ★★★★☆ |
| 6 | 훅 시스템 | core/hooks/ 7종 존재, Claude Code 훅과 미연동 | ★★★★★ |
| 7 | 세션 연속성 | continuity_snapshot.py 존재 | ★★★★☆ |
| 8 | 메모리 정제 | memory_system/ 풍부, decay 있음 | ★★★☆☆ |
| 9 | 자율 에이전트 플래그 | FSALoop 존재, KAIROS형 데몬 미구현 | ★★★★★ |
| 10 | 브라우저/토큰 예산 | MCP adapter 존재, 확장 가능 | ★★★☆☆ |

---

## 멀티 프로바이더 호환성 분석

agent-factory는 `core/providers/registry.py`에서 3개 프로바이더를 지원한다:
- `claude_cli` (Claude Code) — `CLAUDE.md` 읽음
- `codex_cli` (OpenAI Codex) — `AGENTS.md` 읽음 (`scripts/generate_agents_md.py` 이미 존재)
- `gemini_cli` (Gemini CLI) — `GEMINI.md` 읽음

각 인사이트의 프로바이더별 호환성:

| Phase | 인사이트 | Claude | Codex | Gemini | 범용 전략 |
|-------|---------|--------|-------|--------|----------|
| 0 | Context 파일 | `CLAUDE.md` | `AGENTS.md` | `GEMINI.md` | Provider별 파일 생성 |
| 1 | 프롬프트 캐시 | `cache_control` API 파라미터 | 자동(prefix 동일시) | Context Cache API(별도 생성) | **메모리 시스템 기반 공유 prefix** |
| 2 | 훅 브릿지 | `settings.json` hooks | codex 전용 | gemini 전용 | EventBus를 단일 허브로 |
| 3 | /compact | `/compact` 명령어 | 없음 | 없음 | `context_window_manager`로 추상화 |
| 4 | 자율 데몬 | KAIROS 개념 | provider-agnostic | provider-agnostic | FSALoop 확장 |
| 5 | 토큰 예산 | claude tokenizer | tiktoken | SentencePiece | `TokenizerFactory` 추상화 |

### 설계 원칙: Provider-Agnostic 추상화

```
Claude 전용 표현 (기존)              →  Provider-Agnostic 표현 (수정)
─────────────────────────────       ─────────────────────────────────────
"CLAUDE.md 생성"                  →  "Provider별 Context 파일 전략 구현"
"Claude Code 훅 브릿지"            →  "agent-factory EventBus를 단일 허브로 사용"
"/compact 전략"                   →  "context_window_manager 토큰 임계값 기반 압축"
"프롬프트 캐시 공유"                →  "메모리 시스템 기반 SharedContextBuilder"
```

---

## 구현 단계 (Phase 0 ~ Phase 5)

### Phase 0: Provider별 Context 파일 전략 — 즉시 효과, 비용 0
> 인사이트 #2 적용

agent-factory에는 CLAUDE.md가 **아예 없음**. 각 프로바이더별 context 파일을 생성하면 "맞춤형 비서"로 동작.

**프로바이더별 파일:**
```
루트/
├── CLAUDE.md              ← Claude Code용 (신규)
├── AGENTS.md              ← Codex용 (generate_agents_md.py 이미 존재)
├── GEMINI.md              ← Gemini CLI용 (신규)
└── docs/PROJECT_CONTEXT.md ← 공통 소스 (3개 파일이 참조)
```

**작성할 공통 내용 (`PROJECT_CONTEXT.md`):**
```
├── 프로젝트 아키텍처 요약 (sidecar control plane, 2-phase pipeline)
├── 핵심 파일 매핑 (진입점, 오케스트레이터, 러너)
├── 코딩 규칙 (asyncio 패턴, hook 등록 방식, skill 메타데이터 포맷)
├── 절대 금지 사항 (workspace 밖 git 조작, 12-skill 컨텍스트 캡 초과)
├── 테스트 패턴 (pytest 경로, py_compile 우선)
└── 빌드/실행 명령어
```

**기대 효과:** 어떤 프로바이더로 작업해도 동일한 프로젝트 이해를 유지

---

### Phase 1: 메모리 시스템 기반 공유 컨텍스트 (SharedContextBuilder)
> 인사이트 #3 적용 + **메모리 시스템을 프롬프트 캐시 소스로 활용**

#### 핵심 발견: 이미 70%가 구현되어 있음

현재 `KnowledgeInjectionHook`(`core/memory_system/knowledge_injection.py`)은 이미
에이전트 실행 전에 Knowledge Graph에서 관련 지식을 검색하여 컨텍스트에 주입한다.
이 메커니즘을 **병렬 에이전트 전체가 공유하는 prefix**로 확장하면 프롬프트 캐시 공유가 자연스럽게 구현된다.

#### Claude Code 방식 vs agent-factory 메모리 방식 비교

```
Claude Code 방식:
  부모 대화 전체(200K 토큰)를 바이트 동일 복사
  → 대화 잡음, 시행착오, 실패 로그까지 전부 포함
  → 캐시는 공유되지만 "쓸모없는 컨텍스트"도 같이 복사

agent-factory 메모리 방식:
  메모리 시스템이 관련 지식만 선별 → 정제된 컨텍스트 생성
  → 에피소드 decay로 오래된 정보는 이미 걸러짐
  → Cross-project 지식도 포함 가능
  → 토큰 사용량이 훨씬 적으면서 품질은 더 높음
```

| | Claude Code Fork | agent-factory 메모리 기반 공유 |
|---|---|---|
| 공유 대상 | 대화 전체 (무차별) | 선별된 지식 (정제됨) |
| 크기 | 100-200K 토큰 | 2-10K 토큰 |
| 캐시 절감 | 입력 비용 절감 | 입력 자체가 작으므로 이중 절감 |
| 프로바이더 | Claude API 전용 | **Claude/Codex/Gemini 모두 동작** |
| 구현 상태 | - | `KnowledgeInjectionHook` 70% 완성 |

#### 현재 vs 개선 흐름

```
현재 (에이전트별 독립 주입):
  Agent A → KnowledgeInjectionHook.pre_execute() → 개별 검색
  Agent B → KnowledgeInjectionHook.pre_execute() → 개별 검색  (중복!)
  Agent C → KnowledgeInjectionHook.pre_execute() → 개별 검색  (중복!)

개선 (SharedContextBuilder 1회 생성 → 전체 배포):
  DynamicOrchestrator._build_shared_context()
    ├── UnifiedMemoryFacade.search_semantic(project_description)
    ├── CrossProjectRecall.recall_global(project_description)
    ├── KnowledgeInjectionHook._format_injection(triples)
    └── → shared_context (결정론적 텍스트, 정렬 순서 고정)
        ├── Agent A: [shared_context] + "보안 감사해줘"
        ├── Agent B: [shared_context] + "리팩토링해줘"
        └── Agent C: [shared_context] + "테스트 작성해줘"
```

#### 프로바이더별 캐시 동작

shared_context가 바이트 동일하면 각 프로바이더의 캐시가 자동으로 작동한다:

| 프로바이더 | 캐시 메커니즘 | agent-factory 측 조치 |
|-----------|-------------|---------------------|
| Claude | `cache_control: {"type": "ephemeral"}` 헤더 | `model_router.py`에서 prefix에 헤더 부착 |
| Codex/OpenAI | prefix 동일 시 자동 캐시 (제어 불필요) | shared_context 바이트 동일성만 보장 |
| Gemini | Context Cache API로 사전 생성 후 `cache_id` 참조 | 캐시 생성 → ID를 에이전트들에 전달 |

#### 구체적 작업 (나머지 30%)

1. **`core/shared_context_builder.py` 신규** — 메모리에서 결정론적 텍스트 생성
   - `facade.search_semantic()` + `cross_project.recall_global()` 결합
   - 정렬 순서 고정 (record_id/알파벳순)으로 바이트 동일성 보장
   - `_format_injection()` 패턴 재사용
2. **`DynamicOrchestrator` 연동** — 에이전트 생성 전에 1회 빌드, 전체 공유
   - `_assign_task_to_agent()`에 shared_context 파라미터 추가
3. **`model_router.py` 캐시 힌트** — 프로바이더별 캐시 마킹
   - Claude: `cache_control` 헤더 부착
   - OpenAI: 별도 조치 불필요 (자동)
   - Gemini: Context Cache API 호출 후 `cache_id` 전달

---

### Phase 2: Provider-Agnostic 훅 브릿지
> 인사이트 #6 적용

agent-factory의 `EventBus`(core/hooks/event_bus.py)를 **단일 허브**로 사용하고, 각 프로바이더의 훅 시스템은 어댑터로 연결한다.

```
Provider별 Hook 시스템                    agent-factory EventBus (단일 허브)
┌─────────────────────┐                   ┌──────────────────────┐
│ Claude: settings.json│                   │                      │
│  UserPromptSubmit   │──┐                │ pre_execute          │
│  PreToolUse         │  ├── 어댑터 ────→ │ pre_tool_call        │
│  PostToolUse        │──┘                │ post_tool_call       │
├─────────────────────┤                   │ on_skill_evolved     │
│ Codex: 자체 훅       │──── 어댑터 ────→ │                      │
├─────────────────────┤                   │                      │
│ Gemini: 자체 훅      │──── 어댑터 ────→ │                      │
└─────────────────────┘                   └──────────────────────┘
```

**구체적 작업:**
1. `core/hooks/provider_bridge.py` 신규 — 프로바이더 감지 후 적절한 어댑터 로드
2. 각 프로바이더 어댑터에서 최근 git diff + 테스트 상태를 자동 주입
3. 양방향: agent-factory 실행 결과를 해당 프로바이더 세션에 피드백
4. `lifecycle_bridge.py`(이미 존재)의 패턴을 재사용

---

### Phase 3: 세션 연속성 + 컨텍스트 압축 전략 통합
> 인사이트 #5, #7 적용

`/compact`는 Claude Code 전용 명령어이지만, `context_window_manager.py`(이미 존재)를 통해 provider-agnostic하게 구현 가능.

현재 존재하는 모듈들:
- `core/control/continuity_snapshot.py` — 시스템 상태 체크포인트
- `core/context_window_manager.py` (26,716 bytes) — 컨텍스트 관리

**개선 방향:**
```
세션 시작
  │
  ├── --continue: continuity_snapshot에서 마지막 상태 복원
  │                + Provider별 context 파일 재로드
  │
  ├── 작업 진행 중: context_window_manager가 토큰 사용량 모니터링
  │                 ↳ 임계값(80%) 도달 시 자동 compaction 트리거
  │                 ↳ 핵심 메모리만 보존 (episode_extractor 활용)
  │                 ↳ 어떤 프로바이더든 동일하게 동작
  │
  └── 세션 종료: continuity_snapshot 자동 저장
                + memory_system에 학습 에피소드 추출
```

**구체적 작업:**
1. `context_window_manager.py`에 토큰 임계값 기반 compaction 트리거 추가
2. compaction 시 `episode_extractor.py`를 호출해 핵심 학습만 보존
3. `--continue` / `--fork-session` 시 `continuity_snapshot` 자동 로드 로직

---

### Phase 4: 자율 에이전트 데몬 (KAIROS형)
> 인사이트 #9 적용

agent-factory에는 이미 `FSALoop`(5-cycle 자동화)가 있지만 사용자 트리거 방식.

**개선 방향: 상시 실행 데몬**
```
AgentFactoryDaemon (신규)
├── 감시 대상: Git changes, Issue tracker, 테스트 실패
├── 판단 엔진: StrategyEvaluator (이미 존재)
├── 실행: FSALoop 자동 트리거
├── 보고: project_mailbox.py로 결과 전달
└── 안전장치: approval_gate.py (파괴적 변경 시 승인 요청)
```

**구체적 작업:**
1. `core/control/daemon.py` 신규 — watchdog 기반 파일 감시
2. `StrategyEvaluator`를 트리거 판단에 활용
3. `FSALoop`을 데몬 모드로 확장 (주기적 실행)
4. `approval_gate.py` 연동으로 HITL(Human-in-the-Loop) 유지

---

### Phase 5: 확장 — 토큰 예산 + 팀 메모리
> 인사이트 #10 적용

**토큰 예산 시스템:**
```
core/token_budget.py (신규)
├── 프로젝트별 일일/월간 토큰 한도 설정
├── model_router.py 연동 → 예산 초과 시 저비용 모델 자동 전환
├── run_ledger.py에 토큰 사용량 기록
└── dashboard.json에 사용량 시각화
```

**팀 메모리 동기화:**
```
core/memory_system/team_sync.py (신규)
├── cross_project.py 확장 → 팀원 간 에피소드 공유
├── Supabase 동기화 (이미 인프라 존재)
└── 충돌 해소: episode 타임스탬프 기반 merge
```

---

## 우선순위 요약

```
Phase 0  Provider별 Context 파일 전략   ← 즉시, 30분 소요, ROI 최대
Phase 1  메모리 기반 SharedContextBuilder ← 비용 절감 + 멀티 프로바이더 호환
Phase 2  Provider-Agnostic 훅 브릿지     ← 개발 워크플로우 자동화
Phase 3  세션 연속성 + 컨텍스트 압축      ← 컨텍스트 유실 방지
Phase 4  자율 데몬                      ← FSALoop 진화
Phase 5  토큰 예산 + 팀 메모리           ← 운영 레벨 성숙
```

---

## 핵심 인사이트: 메모리 시스템 = Provider-Agnostic 캐시 공유 레이어

Claude Code는 "부모 대화 전체를 바이트 동일 복사"하여 캐시를 공유하지만,
agent-factory는 **메모리 시스템이 정제한 지식만 공유**하므로:

1. **더 작다** — 200K 토큰(전체 대화) vs 2-10K 토큰(정제 지식)
2. **더 정확하다** — decay로 오래된 정보 제거, relevance scoring으로 관련성 높은 것만 선별
3. **프로바이더 독립** — 어떤 LLM API든 동일한 텍스트 prefix를 주입하면 됨
4. **이미 70% 구현됨** — `KnowledgeInjectionHook`, `UnifiedMemoryFacade`, `CrossProjectRecall`

이는 agent-factory만의 **구조적 우위**이며, 특정 프로바이더에 종속되지 않는 범용 캐시 공유 전략이다.

---

## 참고 — 인사이트 원문 출처

choi.openai 트위터 스레드 (2026-03-31) — Claude Code 소스코드 분석:
- #2: CLAUDE.md가 세션 컨텍스트에 강하게 주입되고 compaction 시 재로드
- #3: 서브에이전트 분기 시 바이트 단위 동일 복사본 → 프롬프트 캐시 공유
- #4: settings.json glob 패턴 + auto 모드로 승인 피로도 해소
- #5: 5가지 압축 전략 → /compact 수동 저장 활용
- #6: 25개 이상 훅 이벤트 → UserPromptSubmit으로 컨텍스트 자동 주입
- #7: --continue / --fork-session으로 축적 컨텍스트 활용
- #8: MEMORY.md 포인터 구조 + autoDream 기억 정제
- #9: KAIROS, PROACTIVE, COORDINATOR_MODE 자율 에이전트 플래그
- #10: WEB_BROWSER_TOOL, TOKEN_BUDGET, TEAMMEM 미출시 기능
