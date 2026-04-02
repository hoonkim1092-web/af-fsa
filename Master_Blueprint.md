# Agent Factory — Master Blueprint
<!-- last_updated: 2026-04-02 | version: 1.2.16 -->

> **사용 목적**: 전체 코드를 다시 읽지 않고 이 파일만으로 수정·유지보수·기능 추가를 수행한다.
> 코드 수정 시 반드시 해당 섹션을 **같은 커밋**에서 업데이트할 것.

---

## 목차

- [§0 빠른 참조 테이블](#0-빠른-참조-테이블)
- [§1 아키텍처 개요](#1-아키텍처-개요)
- [§2 실행 흐름 (데이터 플로우)](#2-실행-흐름)
- [§3 핵심 서브시스템](#3-핵심-서브시스템)
- [§4 자가진화 루프](#4-자가진화-루프)
- [§5 에이전트 간 통신](#5-에이전트-간-통신)
- [§6 모델 라우팅](#6-모델-라우팅)
- [§7 안전장치](#7-안전장치)
- [§8 빌드 & 배포](#8-빌드--배포)
- [§9 설정 레퍼런스](#9-설정-레퍼런스)
- [§10 의존성 그래프 & 영향 매트릭스](#10-의존성-그래프--영향-매트릭스)
- [§11 알려진 제약·이슈](#11-알려진-제약이슈)
- [§12 변경 이력](#12-변경-이력)

---

## §0 빠른 참조 테이블

### 루트 파일

| 파일 | 역할 | 주요 클래스/함수 |
|------|------|----------------|
| `run_factory_cli.py` | CLI 진입점 | `main()`, `worker` 서브커맨드 |
| `agent_launcher.py` | AgentFactory 부트스트랩 | `AgentFactory` |
| `model_utils.py:1-845` | 모델 선택·티어 관리 | `_ROLE_ENGINE_MAP`, `_ROLE_CLI_PREFERENCE`, `pick_provider()` |
| `version.py` | 버전 문자열 | `__version__` |
| `build_exe.py` | PyInstaller 빌드 | `main()` |
| `install-af.ps1` | Windows 설치 스크립트 | — |
| `af.spec` | PyInstaller 스펙 | hiddenimports 목록 |
| `policy.yaml` | 전역 정책 | engines, skills, task_decomposition |

### core/ 파일

| 파일 | 역할 | 주요 클래스/함수 |
|------|------|----------------|
| `core/agent_runner.py:1-1411` | 에이전트 CLI 실행 | `AgentRunner`, `run()` |
| `core/agent_specializer.py` | 태스크 전용 에이전트 커스터마이즈 | `AgentSpecializer.specialize()` |
| `core/agent_worker.py` | PyInstaller worker 진입점 | `main()` |
| `core/approval_gate.py` | 실행 승인 게이트 | `ApprovalGate` |
| `core/ast_engine.py` | AST 분석 엔진 | — |
| `core/ast_memory_hub.py` | AST 기반 메모리 허브 | `AstMemoryHub` |
| `core/bootstrap_roles.py` | 프로젝트 계획 부트스트랩 에이전트 | `ProjectPlanningDirector` |
| `core/builder.py` | 스킬 코드 생성 샌드박스 | `SandboxedBuilder` |
| `core/config_paths.py` | 경로 상수 중앙화 | `PROJECT_ROOT`, `POLICIES_PATH` |
| `core/cross_verification.py:1-758` | 멀티 CLI 교차검증 | `CrossVerificationLoop` |
| `core/dashboard.py` | 실행 이력 모니터링 | `append_dashboard_run()` |
| `core/destructive_guard.py` | 위험 명령 차단 | `inject_destructive_guard_contract()` |
| `core/document_chunker.py` | 문서 청킹 (RAG) | `DocumentChunker`, `DocumentChunk` |
| `core/document_index.py` | Dense+Sparse 하이브리드 검색 | `DocumentIndex` |
| `core/documentation_policy.py` | 주석/문서화 정책 주입 | `inject_documentation_contract()` |
| `core/dynamic_orchestrator.py:1-774` | 멀티 에이전트 비동기 오케스트레이터 | `DynamicOrchestrator` |
| `core/engine_auth.py` | CLI 프로바이더 자동 감지·설정 | `auto_configure_cli_provider()` |
| `core/evaluator.py` | 실패 분석 (retry/pivot/abort) | `StrategyEvaluator` |
| `core/executor.py` | 태스크 실행 래퍼 | — |
| `core/fsa_loop.py:1-395` | FSA PDCA 자가실행 루프 | `FSALoop`, `run_mission()` |
| `core/git_manager.py` | 워크스페이스 git 연산 | `GitManager` |
| `core/hooks/event_bus.py` | 훅 라이프사이클 버스 | `HookEventBus` |
| `core/hooks/skill_self_evolution.py` | 주기적 스킬 품질 감사 | `SkillSelfEvolutionHook` |
| `core/hooks/guardrails.py` | 실행 가드레일 | `IntentGateHook` |
| `core/ingestion_pipeline.py` | 문서 인덱싱 파이프라인 | `IngestionPipeline` |
| `core/interactive_chat.py` | 대화형 PDCA 모드 | `run_interactive()` |
| `core/ise_loop.py` | 무한 자가진화 루프 | `ISELoop` |
| `core/llm_engine.py` | LLM API 호출 엔진 | `LLMEngine` |
| `core/manager.py` | 에이전트 생성·로드 | `AgentManager`, `RequirementAnalyzer` |
| `core/memory_system/facade.py` | 메모리 단일 진입점 | `UnifiedMemoryFacade` |
| `core/message_broker.py` | TCP/인메모리 메시지 브로커 | `MessageBroker` |
| `core/model_router.py` | CLI 프로바이더 선택 | `ModelRouter` |
| `core/policy_runtime.py` | 정책 런타임 래퍼 | `PolicyRuntime` |
| `core/project_mailbox.py` | 파일 기반 에이전트 간 메시지함 | `send_agent_message()`, `read_inbox()` |
| `core/project_pipeline.py:1-778` | Phase1(문서)+Phase2(실행) 파이프라인 | `ProjectPipeline` |
| `core/project_task_board.py` | 태스크 보드 상태 관리 | `update_project_board_task()` |
| `core/providers/cli.py` | CLI 프로바이더 실행 | `execute_cli_chat()` |
| `core/providers/registry.py` | 설치된 CLI 목록 | `get_requested_cli_providers()` |
| `core/security_guard.py` | AST 분석 + 격리 실행 | `quick_guard()`, `run_isolated()` |
| `core/skill_cache.py` | 스킬 관련성 LRU 캐시 | `OptimizedSkillRelevance` |
| `core/skill_creator.py` | 스킬 생성·진화 | `evolve_skill()` |
| `core/skill_enricher.py` | 스킬 메타데이터 자동 생성 | `enrich_skill_metadata()`, `bulk_enrich_all_skills()` |
| `core/skill_eval_harness.py` | 계약/숨겨진/섀도우 테스트 | `SkillEvalHarness` |
| `core/skill_evolution_bus.py:1-241` | 7단계 캐시 무효화 체인 | `SkillEvolutionBus.on_skill_evolved()` |
| `core/skill_forge.py` | 코드 생성→비평→수정 루프 | `SkillForge` |
| `core/skill_loader.py` | 런타임 스킬 동적 로딩 | `AdaptiveSkillLoader` |
| `core/skill_promotion.py` | 스킬 라이프사이클 전환 | `SkillPromotionManager` |
| `core/skill_registry.py` | 스킬 메타데이터 중앙 저장소 | `SkillRegistry` (싱글톤) |
| `core/swarm_council.py` | 다중 역할 계획·승인 | `SwarmCouncil` |
| `core/work_item_generator.py` | 마크다운 work-item 생성 | `generate_work_items()` |
| `core/work_item_parser.py` | 편집된 마크다운 재파싱 | `sync_board_from_work_items()` |
| `core/control/supervisor.py` | 유지보수 감독 루프 | `Supervisor` |

### 서브디렉토리

| 디렉토리 | 역할 |
|---------|------|
| `core/continuity/` | 오케스트레이터 체크포인트·재개 |
| `core/control/` | 유지보수·감독·롤백 파이프라인 |
| `core/hooks/` | 실행 라이프사이클 훅 |
| `core/memory_system/` | 에피소드·그래프·시맨틱 메모리 |
| `core/providers/` | CLI 프로바이더 (Claude/Gemini/Codex) |
| `skills/` | 스킬 YAML+Python 구현체 |
| `agents/` | 역할별 에이전트 YAML |
| `config/` | Pydantic 설정 스키마 |
| `syncCompyne/` | 세션 간 공유 메모리 (SQLite) |

---

## §1 아키텍처 개요

### 5-레이어 시스템

```
┌─────────────────────────────────────────────────────────┐
│  Layer 5: 사용자 인터페이스                               │
│  run_factory_cli.py ─── interactive_chat.py             │
├─────────────────────────────────────────────────────────┤
│  Layer 4: 파이프라인 오케스트레이션                        │
│  project_pipeline.py ── fsa_loop.py ── ise_loop.py      │
├─────────────────────────────────────────────────────────┤
│  Layer 3: 멀티 에이전트 실행                              │
│  dynamic_orchestrator.py ── agent_specializer.py        │
├─────────────────────────────────────────────────────────┤
│  Layer 2: 단일 에이전트 실행                              │
│  agent_runner.py ── providers/cli.py ── skill_loader.py │
├─────────────────────────────────────────────────────────┤
│  Layer 1: 공유 인프라                                     │
│  memory_system/ ── message_broker.py ── hooks/          │
│  model_router.py ── skill_evolution_bus.py              │
└─────────────────────────────────────────────────────────┘
```

### 실행 모드별 진입점

| 모드 | 플래그 | 진입 경로 | 루프 |
|------|--------|----------|------|
| Interactive (기본) | 인자 없음 | `interactive_chat.py` | 사용자 입력 반복 |
| Approval | `--mode approval` | `project_pipeline.py` | Phase1→승인→Phase2 |
| FSA | `--mode fsa` | `fsa_loop.py` | 최대 5 사이클 |
| ISE | `--mode ise` | `ise_loop.py` | 무한 자가진화 |
| Worker | `worker` 서브커맨드 | `agent_worker.py` | 단일 태스크 실행 (PyInstaller 전용) |

---

## §2 실행 흐름

### Flow A: Project Pipeline (Approval 모드)

```
run_factory_cli.py:main()
  │
  ├─ [Phase 1] ProjectPipeline.prepare()
  │   ├─ bootstrap_roles.ProjectPlanningDirector
  │   │   ├─ generate_research_brief()  → project_brief.json
  │   │   ├─ generate_role_plan()       → role_plan.json
  │   │   └─ generate_task_board()      → project_board_state.json
  │   ├─ ingestion_pipeline.run()       → document_index (RAG)
  │   ├─ work_item_generator.generate_work_items()
  │   │   → docs/work-items/{slug}/*.md
  │   └─ approval_gate.initialize()     → execution_open: false
  │
  ├─ [사용자 검토·편집]
  │
  ├─ [Phase 2] ProjectPipeline.execute()
  │   ├─ approval_gate.is_execution_open() → True
  │   ├─ work_item_parser.sync_board_from_work_items()
  │   └─ DynamicOrchestrator.run()
  │       ├─ print_startup_routing_notice()  [model_utils]
  │       ├─ MessageBroker TCP 서버 시작
  │       └─ Lilith LLM 사이클 루프 (max_cycles=50):
  │           ├─ _lilith_decide_next() → 다음 태스크 선택
  │           ├─ AgentSpecializer.specialize(agent, task)
  │           ├─ _run_agent_in_terminal() 또는 _run_agent_inline()
  │           │   ├─ frozen=True → [af.exe worker --task-file ...]
  │           │   └─ frozen=False → [python core/agent_worker.py ...]
  │           └─ _cross_verified_evaluate() on failure
  │               ├─ CrossVerificationLoop.run()
  │               └─ _try_evolve_from_patterns()
  │
  └─ dashboard.append_dashboard_run()
```

### Flow B: FSA 자가실행 루프

```
fsa_loop.FSALoop.run_mission()
  │
  └─ For cycle in 1..5:
      ├─ git.commit()                   [워크스페이스 안전 저장]
      ├─ runner.run() → result
      │   └─ ok=True → RETURN SUCCESS
      ├─ git.rollback()                 [실패 시 되돌리기]
      ├─ _run_cross_verified_evaluator()
      │   ├─ 2+ CLIs → CrossVerificationLoop
      │   └─ 1 CLI → StrategyEvaluator
      ├─ action == "abort" → RETURN FAIL
      ├─ _try_evolve_failed_skill()     [스킬 진화]
      └─ 다음 사이클 (feedback 주입)
```

### Flow C: 에이전트 단일 실행

```
AgentRunner.run(agent, task_input, workspace)
  │
  ├─ ModelRouter.pick_provider(agent)   → preferred_provider
  ├─ all_cli_providers = get_requested_cli_providers()
  ├─ cli_providers = [preferred] + [fallbacks]
  ├─ inject contracts:
  │   ├─ documentation_policy
  │   ├─ destructive_guard
  │   └─ implementation_language_policy
  ├─ AdaptiveSkillLoader.load(task) → skills (max 12)
  ├─ HookEventBus.run_pre_execute()
  ├─ providers/cli.execute_cli_chat()   → output
  ├─ HookEventBus.run_post_execute()
  └─ return {ok, output, ...}
```

---

## §3 핵심 서브시스템

### §3.1 ProjectPipeline (`core/project_pipeline.py`)
<!-- last_updated: 2026-04-02 -->

**클래스:** `ProjectPipeline`

| 메서드 | 역할 | 출력 |
|--------|------|------|
| `prepare(brief)` | Phase 1: 문서 생성 | `PreparedProject` |
| `execute(prepared)` | Phase 2: 에이전트 실행 | board state |
| `run(brief)` | prepare + execute 통합 | — |

**핵심 내부 흐름:**
- `terminal_per_agent=True` 설정 → `DynamicOrchestrator` 생성 (`project_pipeline.py:700`)
- `print_startup_routing_notice()` 호출 후 오케스트레이션 시작

---

### §3.2 DynamicOrchestrator (`core/dynamic_orchestrator.py`)
<!-- last_updated: 2026-04-02 -->

**클래스:** `DynamicOrchestrator`

**초기화 파라미터:**
```python
DynamicOrchestrator(mr, max_concurrent=5, terminal_per_agent=True, broker=..., visualizer=...)
```

**핵심 상태:**
```python
self.state_board          # 전체 프로젝트 보드 상태
self.active_assignments   # 진행 중인 태스크 {role: {subtask, result_future}}
self._task_retry_count    # 태스크별 재시도 횟수 {retry_key: count}
self._max_task_retries    # = 3 (초과 시 skip)
max_cycles                # = 50 (line 583)
```

**핵심 메서드:**

| 메서드 | 위치 | 역할 |
|--------|------|------|
| `run(board, role_plan, workspace)` | ~line 580 | 메인 실행 루프 |
| `_run_agent_in_terminal()` | line 487 | 터미널 워커 실행 |
| `_cross_verified_evaluate()` | line 382 | 교차검증 실패 평가 |
| `_try_evolve_from_patterns()` | line 418 | 실패 패턴→스킬 진화 |
| `_lilith_decide_next()` | ~line 300 | Lilith LLM 다음 태스크 결정 |

**Worker 실행 경로 (`_run_agent_in_terminal:515-521`):**
```python
if getattr(sys, "frozen", False):
    # PyInstaller exe 모드
    cmd = [sys.executable, "worker", "--task-file", ..., "--result-file", ...]
else:
    # Python 소스 모드
    cmd = [sys.executable, "core/agent_worker.py", "--task-file", ..., "--result-file", ...]
```

**완료 상태 결정 (`~line 684`):**
```python
if cycle >= max_cycles:       → "stopped_max_cycles"
elif failed_subtasks:         → "partial"
else:                         → "completed"
```

---

### §3.3 AgentRunner (`core/agent_runner.py`)
<!-- last_updated: 2026-04-02 -->

**클래스:** `AgentRunner`

**역할 기반 프로바이더 라우팅 (`line 958-969`):**
```python
preferred = self.mr.pick_provider(agent_config=agent)
cli_providers = [preferred] + [fallbacks...]
```

**스킬 로딩:** `AdaptiveSkillLoader` → 최대 12개, 관련성 점수 기반 선택

**컨트랙트 주입 순서:**
1. `documentation_policy.inject_documentation_contract()`
2. `destructive_guard.inject_destructive_guard_contract()`
3. `implementation_language_policy.inject_implementation_language_contract()`

---

### §3.4 AgentSpecializer (`core/agent_specializer.py`)
<!-- last_updated: 2026-04-02 -->

**메서드:** `specialize(base_agent, task_meta, workspace)`

시스템 프롬프트 구성 순서:
1. 역할 페르소나 (200자 요약)
2. 현재 태스크 (id, title, instruction, phase)
3. 수락 기준
4. 프로젝트 보드 다이제스트
5. 메일박스 메시지
6. 아티팩트 목록

---

### §3.5 스킬 시스템

**로딩 파이프라인:**
```
SkillRegistry (싱글톤) → AdaptiveSkillLoader → OptimizedSkillRelevance
                                 ↓
                         최대 12개 관련 스킬 선택
                         점수 = keyword(0.35) × semantic(0.40) × category(0.25)
```

**스킬 라이프사이클:**
```
draft → candidate → canary → active → archived
  ↑          ↑          ↑        ↑
계약테스트  숨겨진테스트 섀도우테스트 런타임 성공률
```

**진화 경로:**
```
evolve_skill() → quick_guard() → run_isolated() → version bump → SkillEvolutionBus
```

**캐시 무효화 (7단계, `skill_evolution_bus.py`):**
1. SkillRegistry 재로드
2. DependencyGraph 무효화
3. SemanticEmbedder 재계산
4. OptimizedSkillRelevance 캐시 클리어
5. AgentRunner 모듈 캐시 제거
6. AdaptiveSkillLoader 인스턴스 캐시 클리어
7. HookEventBus 이벤트 브로드캐스트

---

### §3.6 메모리 시스템 (`core/memory_system/`)

**진입점:** `UnifiedMemoryFacade` (싱글톤)

| 메서드 | 역할 |
|--------|------|
| `write_episodic()` | 실행 에피소드 기록 |
| `search_semantic()` | 시맨틱 검색 |
| `query_graph()` | 지식 그래프 탐색 |
| `decay()` | 메모리 자동 에이징 |

**메모리 타입:** EPISODIC, SEMANTIC, PROCEDURAL, WORKING, GRAPH

**어댑터:** ast_hub, continuity, core_memory, cortex_vector, knowledge_graph, sync_compyne, trace_log

---

### §3.7 CrossVerification (`core/cross_verification.py`)
<!-- last_updated: 2026-04-02 -->

**클래스:** `CrossVerificationLoop`

**5단계 파이프라인:**
```
Phase 1: 병렬 실행 (ThreadPoolExecutor, N CLIs)
Phase 2: 순환 피어 리뷰 (A→B→C→A)
Phase 3.1: Opus 판정 (JSON, verdict/confidence/failure_patterns)
Phase 3.2: 합성 (keep_parts 기반 머지)
Phase 4: 자가진화 트리거 (failure_patterns → evolve_skill)
```

**판정:** `pass` | `fail` | `partial` | `abort`

**레벨별 라운드:** starter=0, dynamic=1, enterprise=3

**Opus 판정 JSON 스키마:**
```json
{
  "verdict": "pass|fail|partial|abort",
  "selected_provider": "claude_cli",
  "keep_parts": ["provider: reason"],
  "discard_parts": ["provider: reason"],
  "failure_patterns": ["pattern1"],
  "confidence": 0.0-1.0
}
```

---

### §3.8 Evaluator (`core/evaluator.py`)

**클래스:** `StrategyEvaluator`

단일 CLI 환경 폴백. 판정: `retry` | `pivot` | `abort`

---

### §3.9 Continuity (`core/continuity/`)

**`manifest_store.py:OrchestratorManifestStore`**
- 저장: `.af_manifest.json` (atomic write: tmpfile→rename)
- 복구: `load_resume_state()` → 중단된 실행 재개

---

### §3.10 Control 서브시스템 (`core/control/`)

| 파일 | 역할 |
|------|------|
| `supervisor.py` | 유지보수 감독 루프 |
| `maintenance_pipeline.py` | 유지보수 파이프라인 |
| `rollback.py` | 롤백 전략 |
| `regression_gate.py` | 회귀 검증 게이트 |
| `run_ledger.py` | 실행 원장 |
| `change_impact.py` | 변경 영향 분석 |

---

## §4 자가진화 루프

### 완전한 루프 추적

```
태스크 실패 감지
  │
  ├─ [DynamicOrchestrator] _cross_verified_evaluate()
  │   └─ CrossVerificationLoop.run()
  │       ├─ 병렬 실행 → 결과 수집
  │       ├─ 순환 피어 리뷰
  │       ├─ Opus 판정 → failure_patterns 추출
  │       └─ verdict 반환
  │
  ├─ [DynamicOrchestrator] _try_evolve_from_patterns(failure_patterns)
  │   ├─ 패턴 키워드 → 스킬 이름 매핑
  │   └─ evolve_skill(skill_dir, feedback)
  │       ├─ .bak 백업 생성
  │       ├─ LLM으로 개선 코드 생성
  │       ├─ quick_guard() AST 검증
  │       ├─ run_isolated() 샌드박스 테스트
  │       └─ version bump (0.x.y → 0.x.(y+1))
  │
  ├─ [SkillEvolutionBus] on_skill_evolved() → 7단계 캐시 무효화
  │
  ├─ [재시도] _task_retry_count 확인
  │   └─ count >= 3 → SKIP (스킵 후 다음 태스크)
  │
  └─ 다음 사이클에서 진화된 스킬로 재실행
```

### 스킬 자동 품질 감사 (백그라운드)

`SkillSelfEvolutionHook` (실행 우선순위=80):
- 에이전트 10회 실행마다 → `bulk_enrich_all_skills(max_skills=5)`
- 품질 점수 < 0.5인 스킬 자동 메타데이터 개선
- 백그라운드 스레드에서 실행 (메인 파이프라인 블로킹 없음)

**품질 점수 공식:**
```
description(>10자)  +0.20
when_to_use         +0.20
keywords            +0.20
semantic_tags       +0.20
category(비기본값)   +0.10
when_NOT_to_use     +0.10
──────────────────────────
최대                  1.0
```

---

## §5 에이전트 간 통신

### 두 가지 통신 채널

| 채널 | 파일 | 특징 |
|------|------|------|
| **TCP MessageBroker** | `core/message_broker.py` | DynamicOrchestrator 내부 pub/sub, 포트 동적 할당 |
| **파일 기반 Mailbox** | `core/project_mailbox.py` | JSONL 파일, 에이전트 간 비동기 메시지 |

### Mailbox 메시지 타입 (우선순위 순)

| 타입 | 우선순위 | 용도 |
|------|---------|------|
| `blocker` | 0 | 차단 이슈, 즉시 해결 필요 |
| `decision_request` | 1 | 결정 요청 |
| `review_request` | 2 | 검토 요청 |
| `handoff` | 3 | 작업 인계 |
| `result` | 6 | 완료 결과 |

**메시지 파일 위치:** `{workspace}/data/comm/messages.jsonl`

**핵심 함수:**
```python
send_agent_message(workspace, from_role, to_role, msg_type, content, task_id)
read_inbox(workspace, role, task_id, status_filter)
ack_mailbox_message(workspace, message_id)
mailbox_prompt_digest(workspace, role)  # LLM 컨텍스트용 포맷
```

---

## §6 모델 라우팅

### 역할→엔진→프로바이더 매핑

**`model_utils.py:_ROLE_ENGINE_MAP` (우선순위 순):**
```python
["qa", "tester", "quality", "test_eng"]    → "codex"
["architect", "design", "blueprint"]        → "architect_claude"
["coder", "developer", "_dev", "engineer"]  → "coder_claude"
["researcher", "analyst", "planner"]        → "researcher_gemini"
["writer", "docs", "document"]             → "writer_claude"
```

**`model_utils.py:_ROLE_CLI_PREFERENCE` (프로바이더 우선순위):**
```python
"codex"           → ["codex_cli", "claude_cli", "gemini_cli"]
"architect_claude" → ["claude_cli", "codex_cli", "gemini_cli"]
"coder_claude"    → ["claude_cli", "codex_cli", "gemini_cli"]
"researcher_gemini"→ ["gemini_cli", "claude_cli", "codex_cli"]
```

**라우팅 결정 흐름:**
```
_infer_engine_id(role_name) → engine_id
  ↓
_ROLE_CLI_PREFERENCE[engine_id] → [provider1, provider2, ...]
  ↓
get_requested_cli_providers() 와 교집합
  ↓
첫 번째 가용 프로바이더 선택
```

**단일 프로바이더 모드:** 설치된 CLI가 1개면 모든 역할이 동일 프로바이더 사용 (startup notice 출력)

---

## §7 안전장치

### 4-레이어 보안 체계

```
Layer 1: 정책 검증
  policy.yaml → 태스크 분해 규칙, 역할 수 제한

Layer 2: 코드 정적 분석 (AST)
  security_guard.quick_guard() → 금지 import/함수 차단
  금지: os, sys, subprocess, shutil, importlib, eval, exec, __import__

Layer 3: 격리 실행
  security_guard.run_isolated() → 서브프로세스 + 타임아웃 (10초)
  파일 I/O: DATA_DIR, ARTIFACTS_DIR 만 허용

Layer 4: 위험 명령 차단
  destructive_guard → rm, del, git reset/clean/checkout 차단
  에이전트 시스템 프롬프트에 가드 컨트랙트 주입

Layer 5: 인간 승인 게이트
  ApprovalGate → SHA256 해시 기반 문서 변경 감지
  execution_open: false → 승인 후 true
```

### ApprovalGate 상태 전환

```
initialize() → execution_open: false
     ↓
[사용자 검토]
     ↓
approve() → SHA256 스냅샷 저장, execution_open: true
     ↓
[문서 수정 감지]
     ↓
invalidate() → execution_open: false (재승인 필요)
```

---

## §8 빌드 & 배포

### 빌드 프로세스

```bash
python build_exe.py
# 1. PyInstaller 확인
# 2. build/, dist/ 정리
# 3. pyinstaller af.spec
# 4. dist/af-{version}.zip 생성
```

**출력:** `dist/af/af.exe` (12.5 MB), `dist/af-1.x.x.zip` (44 MB)

### PyInstaller 핵심 설정 (`af.spec`)

```python
Analysis(
  entry='run_factory_cli.py',
  datas=[('skills', 'skills'), ('config', 'config'), ('policy.yaml', '.')],
  hiddenimports=[
    'core.agent_worker',  # worker 서브커맨드용 (중요!)
    ... 100+ 모듈
  ]
)
```

**새 core/*.py 파일 추가 시 af.spec `hiddenimports`에 반드시 추가 필요.**

### Worker 서브커맨드 (PyInstaller 전용)

`run_factory_cli.py:137-142`:
```python
if effective_argv and effective_argv[0] == "worker":
    from core.agent_worker import main as worker_main
    sys.argv = ["af-worker"] + effective_argv[1:]
    worker_main()
    return
```

`dynamic_orchestrator.py:515-521`:
```python
if getattr(sys, "frozen", False):
    cmd = [sys.executable, "worker", "--task-file", ..., "--result-file", ...]
else:
    cmd = [sys.executable, "core/agent_worker.py", ...]
```

### 배포 체인

```
1. version.py → __version__ = "1.x.x" 업데이트
2. install-af.ps1 → 버전 문자열 3곳 수정 (1.x.x)
3. python build_exe.py → dist/af-1.x.x.zip 생성
4. git add dist/af-1.x.x.zip (LFS 자동 추적)
5. git commit + git push origin 브랜치
6. git tag af-fsa_v1.x.x + git push origin refs/tags/...
7. GitHub Release 생성 (PyGithub 또는 gh CLI)
```

**설치 URL 패턴:**
```
raw URL:   https://github.com/hoonkim1092-web/af-fsa/raw/af-fsa_v1.x.x/dist/af-1.x.x.zip
installer: https://raw.githubusercontent.com/hoonkim1092-web/af-fsa/af-fsa_v1.x.x/install-af.ps1
```

---

## §9 설정 레퍼런스

### 핵심 환경 변수

| 변수 | 기본값 | 역할 |
|------|--------|------|
| `AGENT_PROJECTS_DIR` | `~/projects` | 프로젝트 루트 |
| `AGENT_PROJECT_ID` | — | 현재 프로젝트 ID |
| `AGENT_PROJECT_ROOT` | — | 현재 워크스페이스 |
| `AGENT_CHAT_MODEL` | — | 모델 오버라이드 |
| `AGENT_CHAT_PROVIDER` | — | 프로바이더 오버라이드 |
| `AGENT_TERMINAL_MODE` | false | 에이전트당 별도 터미널 |
| `AGENT_AUTO_INSTALL_CLI` | 1 | CLI 자동 설치 여부 |

### policy.yaml 구조

```yaml
engines:
  gemini_flash: {tier: 1}     # 속도 우선
  codex:        {tier: 2}     # 코드 정밀도
  research_pro: {tier: 3}     # 추론

skills:
  read_file:  {risk: LOW,      engine_id: gemini_flash}
  write_file: {risk: HIGH,     engine_id: codex}
  run_command:{risk: CRITICAL, engine_id: codex}

task_decomposition:
  roles:   {min: 2, max: 5}
  modules: {independent: true}
  tasks:   {granularity: small, require_verify: true}
  required_roles: [qa_engineer]
```

### 스킬 레지스트리 (`skills/registry.yaml`)

```yaml
skills:
  skill_id:
    name: "스킬 이름"
    path: "skills/skill_id/"
    status: active|draft|archived
    version: "0.x.x"
    updated_at: "YYYY-MM-DD"
```

**스킬 디렉토리 구조:**
```
skills/{skill_id}/
  ├── meta.yaml    (메타데이터)
  ├── skill.py     (구현체, action 스킬)
  └── SKILL.md     (구현체, knowledge 스킬)
```

---

## §10 의존성 그래프 & 영향 매트릭스

### 수정 시 영향 범위 (Blast Radius)

| 수정 대상 | 직접 영향 | 간접 영향 |
|----------|----------|----------|
| `model_utils.py` | `agent_runner.py`, `dynamic_orchestrator.py`, `model_router.py` | 모든 에이전트 실행 |
| `core/skill_loader.py` | `agent_runner.py` | `skill_cache.py`, `skill_evolution_bus.py` |
| `core/skill_evolution_bus.py` | 스킬 시스템 전체 | `agent_runner.py`, `skill_cache.py`, `skill_loader.py` |
| `core/dynamic_orchestrator.py` | `project_pipeline.py` | 전체 Phase 2 실행 |
| `core/agent_worker.py` | `dynamic_orchestrator.py`, `run_factory_cli.py` | `af.spec` hiddenimports |
| `core/agent_runner.py` | `dynamic_orchestrator.py`, `fsa_loop.py` | 모든 에이전트 실행 |
| `core/cross_verification.py` | `dynamic_orchestrator.py`, `fsa_loop.py` | 검증 결과 품질 |
| `core/project_pipeline.py` | `run_factory_cli.py`, `interactive_chat.py` | Phase 1/2 전체 |
| `core/message_broker.py` | `dynamic_orchestrator.py` | 에이전트 간 통신 |
| `core/project_mailbox.py` | `agent_runner.py`, `agent_specializer.py` | 에이전트 컨텍스트 |
| `af.spec` | 빌드 출력 | `agent_worker.py` 미포함 시 worker_exited_code_2 |
| `policy.yaml` | `bootstrap_roles.py`, `config/schema.py` | 태스크 분해 규칙 |

### 핵심 import 체인

```
run_factory_cli.py
  └─ core/project_pipeline.py
       └─ core/dynamic_orchestrator.py
            ├─ core/agent_runner.py
            │    ├─ core/skill_loader.py → core/skill_registry.py
            │    ├─ core/hooks/event_bus.py
            │    └─ core/providers/cli.py
            ├─ core/agent_specializer.py
            ├─ core/cross_verification.py
            ├─ core/message_broker.py
            └─ core/continuity/manifest_store.py
```

```
model_utils.py (독립 모듈)
  ├─ _ROLE_ENGINE_MAP → _infer_engine_id()
  ├─ _ROLE_CLI_PREFERENCE → pick_cli_provider_for_role()
  └─ pick_provider() ← agent_runner.py가 호출
```

---

## §11 알려진 제약·이슈

### 현재 제약사항

| 항목 | 내용 | 해결 방안 |
|------|------|----------|
| **Self-hosting 제한** | af.exe는 자기 소스(`core/*.py`)를 수정 불가 | 소스 모드(`python run_factory_cli.py`)로 실행 |
| **GOOGLE_API_KEY 없음** | Lilith(Gemini) LLM 실패 → 빈 응답 → cycle 낭비 | Claude CLI만으로도 동작하나 Gemini 추가 시 성능 향상 |
| **TCP Broker 미연결** | agent_worker.py가 TCP 브로커에 실제 연결 안 함 | 파일 기반 Mailbox는 정상 동작 |
| **max_cycles 소진** | Lilith LLM 오류 누적 시 50 사이클 낭비 후 종료 | Gemini API 설정 또는 Lilith 대체 로직 필요 |
| **cross_verification level** | DynamicOrchestrator에서 항상 dynamic(1라운드) 고정 | enterprise 모드 옵션 추가 가능 |
| **LFS zip 빌드 반복** | 매 버전마다 44MB zip LFS 푸시 필요 | 릴리스 asset URL 사용 시 PowerShell 리다이렉트 실패 |

### 에러 코드 해설

| 에러 | 원인 | 수정 위치 |
|------|------|----------|
| `worker_exited_code_2` | frozen exe에서 `python agent_worker.py` 실행 시도 | `dynamic_orchestrator.py:515` frozen 분기 |
| `stopped_max_cycles` | 50 사이클 내 완료 못함 | Lilith LLM 실패율, 태스크 재시도 횟수 확인 |
| `worker_timeout` | 에이전트 3600초 초과 | `dynamic_orchestrator.py:526` max_wait 조정 |
| `empty_llm_response` | LLM 호출 실패 (API 키 없음 등) | 환경 변수 및 CLI 설치 확인 |
| 다운로드 연결 끊김 | GitHub release asset 리다이렉트 실패 | raw LFS URL 사용 (`install-af.ps1:98`) |

---

## §12 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-04-02 | v1.2.16 | **Blueprint 초기 생성** — v1.2.16 기준 전체 아키텍처 문서화 |
| 2026-04-02 | v1.2.16 | fix(worker): PyInstaller frozen exe → `worker` 서브커맨드 추가 |
| 2026-04-02 | v1.2.15 | fix(installer): LFS에 zip 커밋, raw URL로 다운로드 방식 전환 |
| 2026-04-02 | v1.2.14→15 | fix(installer): release asset URL → raw URL 수정 |
| 2026-04-01 | v1.2.14 | feat(orchestrator): 6개 이슈 수정 — worker, routing, evolution, completion |
| 2026-04-01 | v1.2.13 | feat(pipeline): 문서 품질 개선, 프로젝트 경로 라우팅 수정 |

---

## 유지보수 가이드

### Blueprint 업데이트 규칙

1. **새 파일 추가** → §0 테이블에 추가
2. **클래스/메서드 변경** → §3 해당 서브시스템 `last_updated` 갱신
3. **의존성 변경** → §10 영향 매트릭스 업데이트
4. **버그 수정** → §11 에러 테이블에서 제거 또는 업데이트
5. **버전 릴리스** → §12 변경 이력에 한 줄 추가
6. **af.spec 변경** → §8 hiddenimports 설명 업데이트

### 효율적 검색법

```
기능 수정    → §3 해당 서브시스템 → 파일:라인 참조로 이동
에러 디버그  → §11 에러 코드 테이블
라우팅 변경  → §6 모델 라우팅 → model_utils.py 직접 수정
빌드 문제    → §8 빌드 & 배포
의존성 파악  → §10 Blast Radius 테이블
신규 기능    → §1 아키텍처 → §2 플로우 → §3 관련 서브시스템
```

### Self-Hosting 가능 범위

```
가능 ✅:
  - skills/ 스킬 코드 수정·진화
  - agents/*.yaml 에이전트 정의 수정
  - policy.yaml 정책 변경
  - docs/ 문서 생성·수정

소스 모드에서만 가능 ⚠️:
  - core/*.py 소스 코드 수정
    → python run_factory_cli.py로 실행 필요

불가 ❌:
  - af.exe → 자기 자신 리빌드
  - 빌드 중 빌드 (python build_exe.py는 소스 환경 필요)
```
