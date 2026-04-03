# Agent Factory 전체 코드 리뷰

> 작성일: 2026-04-03
> 대상: core/ 175파일, 46,210줄
> 리뷰어: Claude Opus 4.6
> 상태: **스냅샷** — 코드 변경 시 해당 섹션 업데이트 필요

---

## 1. 프로젝트 통계

| 항목 | 수치 |
|------|------|
| core/ 파일 수 | 174 |
| core/ 총 라인 수 | 46,030 |
| 서브디렉토리 | 6개 (control, continuity, hooks, memory_system, providers, synergy) |
| 최대 파일 | agent_runner.py (1,419줄) |
| Dead code 파일 | 3개 (ise_loop, claim_tracer, onboarding_wizard) |

### 서브디렉토리별 규모

| 디렉토리 | 파일 수 | 줄 수 | 역할 |
|----------|---------|-------|------|
| core/ (루트) | 113 | 33,637 | 런타임, 스킬, 대화, 연구 |
| core/control/ | 14 | 3,870 | Sidecar 유지보수 레이어 |
| core/memory_system/ | 15 | 2,440 | 에피소드 메모리, KG, decay |
| core/hooks/ | 10 | 1,693 | 실행 전후 훅 체인 |
| core/providers/ | 4 | 1,676 | CLI 프로바이더 추상화 |
| core/synergy/ | 5 | 1,176 | 멀티 에이전트 협업 |
| core/continuity/ | 4 | 352 | 세션 연속성, manifest |
| core/memory_system/adapters/ | 9 | 1,186 | 메모리 백엔드 어댑터 |

---

## 2. 서브시스템별 리뷰

### 2.1 실행 엔진 (Runtime Engine)

| 파일 | 줄 | 역할 |
|------|-----|------|
| `agent_runner.py` | 1,419 | 에이전트 실행 핵심. provider failover loop (L1076), _flush_trace (L863), multi-provider ordering (L966) |
| `agent_worker.py` | 109 | Worker subprocess 진입점. task.json → result.json 파일 IPC |
| `dynamic_orchestrator.py` | 887 | asyncio 5-concurrent 병렬 실행. board 기반 task dispatch, state_board 관리 |
| `model_router.py` | 262 | 역할→모델 매핑. provider-aware 라우팅 |
| `run_budget.py` | 58 | 글로벌 토큰 예산. 4char≈1token 휴리스틱, 80% 경고, 100% 중단 |
| `executor.py` | 120 | 단일 에이전트 실행 래퍼 |
| `evaluator.py` | 68 | 실행 결과 평가 |
| `failure_classifier.py` | 46 | INFRA/IMPLEMENTATION 이분류. _INFRA_PATTERNS로 quota/rate_limit/timeout 등 매칭 |

**문제점:**
- `run_budget.py`: 글로벌 싱글톤, 에이전트별 분해 없음 → v3 Feature 1로 해결 예정
- `agent_runner.py:1076`: failover에서 INFRA/IMPLEMENTATION 분류를 안 함 → v3 Phase 7로 해결 예정
- `dynamic_orchestrator.py:567-571`: frozen/source 분기가 inline → v3 Phase 1A `build_worker_cmd()`로 해결 예정

### 2.2 Control Plane (Sidecar 유지보수)

| 파일 | 줄 | 역할 |
|------|-----|------|
| `control/supervisor.py` | 550 | RuntimeSupervisor. heartbeat 30s, stall 120s, root cause analysis |
| `control/maintenance_pipeline.py` | 456 | prepare→execute 2-phase. conflict blocking wait (max 1hr) |
| `control/run_ledger.py` | 310 | append-only JSONL 저널. LedgerEntry dataclass, thread-safe |
| `control/intake.py` | 281 | ControlPlaneIntake. work_kind 분류 → execution_policy |
| `control/change_impact.py` | 298 | 변경 영향도 분석 |
| `control/continuity_snapshot.py` | 293 | 상태 스냅샷 저장/복원 |
| `control/rollback.py` | 412 | 롤백 메커니즘 |
| `control/regression_gate.py` | 243 | 회귀 테스트 게이트 |
| `control/maintenance_state.py` | 233 | 유지보수 상태 머신 |
| `control/lifecycle_bridge.py` | 238 | 실행 라이프사이클 브릿지 |
| `control/execution_policy.py` | 184 | 실행 정책 결정 |
| `control/issue_context.py` | 197 | 이슈 컨텍스트 수집 |
| `control/work_kind.py` | 119 | 작업 종류 분류 (bugfix/feature/maintenance) |
| `control_plane_llm.py` | 173 | CLI-first, API-fallback LLM 인터페이스 |

**문제점:**
- `control_plane_llm.py:50-56`: `detect_available_cli_providers()`만 호출, `AF_CONTROL_PLANE_PROVIDERS` 환경변수 미지원 → v3에서 연동 필요
- `supervisor.py`: ISELoop 미연결 → v3 Phase 1B에서 deep_update 경로 추가
- `run_ledger.py`: 에이전트별 비용 분해 없음 → v3 Phase 2에서 cost buffer 추가

### 2.3 Provider 레이어

| 파일 | 줄 | 역할 |
|------|-----|------|
| `providers/registry.py` | 253 | CLI_PROVIDER_IDS: claude_cli, gemini_cli, codex_cli. configure_providers(), detect_installed_cli_providers() (60s 캐시) |
| `providers/cli.py` | 764 | CliChatRequest/execute_cli_chat. 실제 CLI subprocess 호출 |
| `providers/session_adapter.py` | 642 | provider별 세션 설정. codex=wrapper_bridge (L110), frozen 빌드에서 hook 건너뜀 (L434) |
| `providers/__init__.py` | 17 | re-export |

**문제점:**
- `session_adapter.py:110`: codex_cli는 `mode="wrapper_bridge"`, `hook_events=()` → native hook 불가, AF EventBus 필요
- `session_adapter.py:434-436`: frozen 빌드에서 claude_cli hook 등록 스킵 → AF-owned checkpoint가 대안
- `registry.py`: `configure_providers()` 있지만 daemon에서 auto_configure 우회 경로 미구현

### 2.4 Hook 시스템

| 파일 | 줄 | 역할 |
|------|-----|------|
| `hooks/event_bus.py` | 155 | HookEventBus. register_hook(), run_pre/post_execute() |
| `hooks/checkpoint.py` | 68 | CheckpointHook. runs/{run_id}/checkpoint.json 저장/복원. PRIORITY=90 |
| `hooks/context_fork.py` | 313 | 컨텍스트 분기 관리 |
| `hooks/memory_consolidation.py` | 217 | 메모리 통합 훅 |
| `hooks/skill_self_evolution.py` | 200 | 스킬 자기 진화 |
| `hooks/langsmith_tracing.py` | 360 | LangSmith 트레이싱 |
| `hooks/lsp_check.py` | 213 | LSP 기반 코드 검증 |
| `hooks/guardrails.py` | 87 | 안전 가드레일 |
| `hooks/human_interrupt.py` | 49 | 사람 개입 포인트 |
| `hooks/base.py` | 31 | ContinuationHook 베이스 클래스 |

**문제점:**
- `event_bus.py`: provider 이벤트 → AF 이벤트 매핑 없음 → v3 Phase 8B에서 `on_provider_event()` 추가
- `checkpoint.py`: wake_checkpoint 미구현 → v3 Phase 5A에서 추가
- checkpoint 진실의 원천이 3개: manifest_store, CheckpointHook, wake_checkpoint(미구현) → v3에서 우선순위 명시 필요

### 2.5 메모리 시스템

| 파일 | 줄 | 역할 |
|------|-----|------|
| `memory_system/facade.py` | 346 | UnifiedMemoryFacade. 프로세스 전역 singleton |
| `memory_system/knowledge_injection.py` | 156 | KnowledgeInjectionHook. 에이전트별 개별 검색 |
| `memory_system/knowledge_forger.py` | 246 | 지식 생성/정제 |
| `memory_system/models.py` | 249 | 데이터 모델 |
| `memory_system/router.py` | 215 | 메모리 라우팅 |
| `memory_system/episode_extractor.py` | 144 | 에피소드 추출 |
| `memory_system/episode_matcher.py` | 140 | 에피소드 매칭 |
| `memory_system/decay.py` | 147 | 시간 기반 decay |
| `memory_system/graph_builder.py` | 138 | Knowledge Graph 빌더 |
| `memory_system/graph_query.py` | 141 | KG 쿼리 |
| `memory_system/issue_tracker.py` | 182 | 이슈 추적 |
| `memory_system/project_lifecycle.py` | 145 | 프로젝트 라이프사이클 |
| `memory_system/cross_project.py` | 64 | 크로스 프로젝트 지식 공유 |
| `memory_system/config.py` | 97 | 메모리 설정 |

**문제점:**
- `facade.py`: 프로세스 전역 singleton → 멀티 프로젝트에서 충돌 가능. Worker 프로세스 격리로 해결 (v3 Phase 8A)
- `knowledge_injection.py`: 에이전트마다 개별 검색 → 5에이전트=5회 중복. SharedContextBuilder (v3 Phase 4)로 해결

### 2.6 ISE (Iterative Self-Enhancement)

| 파일 | 줄 | 역할 |
|------|-----|------|
| `ise_loop.py` | 469 | Level 1~5 에스컬레이션. decompose_task() 호출 가능. **Dead code — 0 import** |
| `ise_redesigner.py` | 264 | decompose_task(): 3~7 서브태스크 생성, 의존성 정렬. ISELoop에서만 호출 |
| `ise_analyzer.py` | 220 | ISE 분석 엔진 |
| `ise_stall_detector.py` | 150 | ISE 정체 감지 |
| `ise_strategy_ledger.py` | 241 | ISE 전략 기록 |

**문제점:**
- `ise_loop.py`: 완전 dead code. 어디서도 import하지 않음
- `ise_redesigner.py`: ISELoop 통해서만 도달 가능 → 역시 dead
- v3 Phase 1B에서 DynamicOrchestrator `_should_decompose()` + Supervisor ISELoop 라우팅으로 활성화 예정

### 2.7 스킬 시스템

| 파일 | 줄 | 역할 |
|------|-----|------|
| `skill_creator.py` | 1,092 | 스킬 생성 엔진 |
| `skill_procurer.py` | 876 | 외부 스킬 조달 |
| `skill_eval_harness.py` | 732 | 스킬 평가 하네스 |
| `skill_registry.py` | 536 | 스킬 레지스트리. `external_scanned` property, `should_rescan_external()` (mtime 기반) |
| `skill_preflight.py` | 470 | 스킬 사전 검증 |
| `skill_metadata_adapter.py` | 449 | 메타데이터 어댑터 |
| `skill_feedback.py` | 365 | data/skill-usage.jsonl 기록 |
| `skill_retrieval_engine.py` | 345 | 스킬 검색 엔진 |
| `skill_loader.py` | 341 | 스킬 로더 |
| `skill_enricher.py` | 311 | 스킬 보강 |
| `skill_promotion.py` | 295 | 스킬 승격 |
| `skill_spec_synthesizer.py` | 290 | 스킬 명세 합성 |
| `skill_evolution_bus.py` | 243 | 스킬 진화 버스 |
| `skill_forge.py` | 236 | 스킬 단조 |
| `skill_cache.py` | 171 | 스킬 캐시 |
| `skill_metadata.py` | 129 | 스킬 메타데이터 |
| `skill_autodiscover.py` | 115 | 스킬 자동 발견 |
| `skill_context_config.py` | 110 | 스킬 컨텍스트 설정 |
| `knowledge_skill.py` | 90 | 지식 스킬 |
| `external_skill_sources.py` | — | `CodexOfficialSkillSource`, `ClaudeOfficialSkillSource` (신규), `_extract_skill_id()` (frontmatter name 우선) |
| `external_skill_source_ids.py` | — | 소스 ID 정규화. `claude_official` 추가, `DEFAULT_EXTERNAL_SOURCE_PRIORITY` 갱신 |

**최종 수정**: 2026-04-03

**변경 사항 (cross-cli-skill-discovery v3)**:
- `skill_registry.py`: `ensure_skills_loaded()` — `count()==0` 체크 → `external_scanned` 플래그로 교체. 유지보수 시 신규 외부 스킬 미감지 버그 수정
- `skill_registry.py`: `should_rescan_external()` — 외부 디렉토리 mtime 기반 변경 감지 (기존: 디렉토리 basename 비교)
- `external_skill_sources.py`: `_extract_skill_id()` — frontmatter `name` 우선, 디렉토리명 fallback (기존: 디렉토리명만)
- `external_skill_sources.py`: `ClaudeOfficialSkillSource` — `~/.claude/skills/`, `PROJECT/.claude/skills/` 탐색
- `external_skill_source_ids.py`: `claude_official` 소스 ID + 우선순위 (`codex_official` 다음)

**참고:** 스킬 시스템은 v3 설계 범위 밖. cross-cli-skill-discovery는 완료.

### 2.8 대화/연구 엔진

| 파일 | 줄 | 역할 |
|------|-----|------|
| `interactive_chat.py` | 887 | 대화형 채팅 엔진 |
| `researcher.py` | 894 | 연구 엔진 |
| `conversation_manager.py` | 822 | 대화 관리자 |
| `context_window_manager.py` | 633 | 컨텍스트 윈도우 관리. compaction 미구현 |
| `conversation_prompts.py` | 166 | 대화 프롬프트 |
| `conversation_room.py` | 215 | 대화 룸 |
| `conversation_task_adapter.py` | 142 | 대화↔태스크 어댑터 |

**문제점:**
- `context_window_manager.py`: `should_compact()`, `compact()` 미구현 → v3 Phase 5B에서 추가
- `interactive_chat.py`: 자동 compaction 트리거 없음 → v3 Phase 5B에서 추가

### 2.9 파이프라인/보드

| 파일 | 줄 | 역할 |
|------|-----|------|
| `project_pipeline.py` | 778 | ProjectPipeline. prepare→execute 흐름 |
| `project_task_board.py` | 759 | 프로젝트 태스크 보드. next_board_tasks(), update_project_board_task() |
| `work_item_generator.py` | 541 | 작업 항목 생성 |
| `work_item_parser.py` | 294 | 작업 항목 파싱 |
| `bootstrap_roles.py` | 472 | 역할 부트스트랩 |

### 2.10 인프라/유틸리티

| 파일 | 줄 | 역할 |
|------|-----|------|
| `config_paths.py` | 101 | 모듈 레벨 상수. PROJECT_ROOT, AGENTS_DIR, RUNS_DIR 등 |
| `engine_auth.py` | 182 | auto_configure_cli_provider() — 단일 provider 자동 선택 |
| `concurrency.py` | 207 | BackgroundTaskManager, circuit breaker |
| `utils.py` | 337 | 범용 유틸리티. `get_external_skill_roots()` (신규, personal>project 순서, Claude 경로 포함), `get_codex_skill_roots` alias 유지 |
| `file_io.py` | 124 | 파일 I/O 헬퍼 |
| `file_lock.py` | 92 | 크로스 프로세스 파일 락 |
| `git_manager.py` | 94 | Git 조작 |
| `dashboard.py` | 118 | 대시보드 데이터 기록 |
| `llm_engine.py` | 219 | Gemini API 기반 LLM 엔진 |
| `policy.py` | 40 | policy.yaml 로더 |
| `policy_runtime.py` | 54 | 런타임 정책 적용 |

**문제점:**
- `config_paths.py`: 모듈 레벨 상수 → import 시점 고정. Worker 프로세스 격리로 해결 (v3)
- `engine_auth.py`: `auto_configure_cli_provider()` — daemon에서는 부적합, `configure_providers()` 직접 호출 필요

### 2.11 기타

| 파일 | 줄 | 역할 |
|------|-----|------|
| `cross_verification.py` | 760 | 멀티 LLM 교차 검증 |
| `parallel_critique.py` | 371 | 병렬 비평 |
| `consensus_engine.py` | 364 | 합의 엔진 |
| `fsa_loop.py` | 397 | 5-cycle FSA 루프 (EXECUTE→TRACE→EVAL→SUMMARIZE→REFLECT) |
| `pdca_commands.py` | 759 | PDCA 커맨드 |
| `pdca_state.py` | 246 | PDCA 상태 머신 |
| `synergy/bridge.py` | 693 | 시너지 브릿지 |
| `synergy/process.py` | 181 | 시너지 프로세스 |
| `synergy/job.py` | 107 | 시너지 작업 |
| `synergy/tools.py` | 157 | 시너지 도구 |
| `ingestion_pipeline.py` | 144 | 문서 수집 파이프라인. MIN_REINDEX_INTERVAL=60s 하드코딩 |
| `document_index.py` | 406 | 문서 인덱스 |
| `document_chunker.py` | 234 | 문서 청크 분할 |

### 2.12 진입점 및 빌드

| 파일 | 줄 | 역할 |
|------|-----|------|
| `run_factory_cli.py` | 289 | 메인 CLI 진입점. subcommand 라우팅 |
| `af.spec` | 212 | PyInstaller 빌드 명세. hiddenimports 100+개 수동 관리 |
| `version.py` | 1 | `__version__ = "1.2.17"` |
| `policy.yaml` | 99 | 스킬 리스크 분류, 모델 라우팅, 태스크 분해 규칙 |

---

## 3. 상세 버그 및 기술부채

### 3.1 Critical — 크래시 또는 데이터 손실 가능

| ID | 파일 | 라인 | 문제 | 상태 |
|----|------|------|------|------|
| C1 | `ise_loop.py` | 274-278 | 에스컬레이션 결정에 도달 불가 코드 (return 2 unreachable) | ✅ 수정됨 |
| C2 | `hooks/checkpoint.py` | 63-64 | Non-atomic 파일 쓰기. 저장 중 크래시 시 checkpoint 손상 | ✅ 수정됨 |
| C3 | `hooks/context_fork.py` | 211-233 | Thread.join(timeout)이 실제 스레드를 중단하지 않음. 좀비 스레드 가능 | ✅ 수정됨 |
| C4 | `memory_system/issue_tracker.py` | 83-91 | `gh` CLI subprocess에 shell escape 누락. injection 위험 | ✅ 수정됨 |

### 3.2 High — 잘못된 동작

| ID | 파일 | 라인 | 문제 | 상태 |
|----|------|------|------|------|
| H1 | `dynamic_orchestrator.py` | 609,722 | state_board 비동기 업데이트에 asyncio.Lock 없음. race condition | ✅ 수정됨 |
| H2 | `agent_runner.py` | 589-637 | `_skill_module_cache` 무한 증가. 폐기 메커니즘 없음 | ✅ 수정됨 |
| H3 | `ise_redesigner.py` | 111,171 | JSON 파싱 실패 시 silent fallback → 무한 retry 루프 가능 | ✅ 수정됨 |
| H4 | `control/intake.py` | 112 | `continuity_snapshot.get("overall_health")` vs 실제 키 "recovery_health" | ✅ 수정됨 |
| H5 | `dashboard.py` | 17 | 글로벌 `_DASHBOARD_CACHE` 스레드 안전하지 않음 | ✅ 수정됨 |
| H6 | `control_plane_llm.py` | 50-56 | `AF_CONTROL_PLANE_PROVIDERS` 환경변수 미지원 | ✅ 수정됨 |

### 3.2.1 검증 중 추가 발견 (2026-04-03)

수정 검증 과정에서 발견된 추가 버그 및 개선:

| ID | 파일 | 문제 | 상태 |
|----|------|------|------|
| H6a | `control_plane_llm.py:55` | H6 수정에서 `return`이 API 엔진 초기화를 건너뜀 → CLI+API 동시 사용 불가 | ✅ 수정됨 |
| H2a | `agent_runner.py:640` | H2 캐시 퇴거 시 `sys.modules` 엔트리 미정리 → 메모리 누수 | ✅ 수정됨 |
| H5a | `dashboard.py:95,106` | H5는 Lock만 추가. 파일 쓰기 자체가 non-atomic (C2와 동일 유형) | ✅ 수정됨 |
| C4a | `issue_tracker.py:88` | C4에서 `list_issues`의 `labels` 파라미터 미검증 | ✅ 수정됨 |
| C4b | `issue_tracker.py:70` | `_sanitize_arg`가 `\n`을 허용 — issue_id/title에 부적절 | ✅ 수정됨 |

### 3.3 Medium — 성능/유지보수

| ID | 파일 | 라인 | 문제 |
|----|------|------|------|
| M1 | `control/run_ledger.py` | 276-292 | `_read_all()` 전체 JSONL 매번 순회. O(n) |
| M2 | `hooks/event_bus.py` | 40-46 | 훅 중복 체크 O(n²) |
| M3 | `hooks/lsp_check.py` | 40 | Pyright 타임아웃 15초 하드코딩. 에이전트 블로킹 |
| M4 | `ise_stall_detector.py` | 35,43 | 매직넘버 threshold (0.5, 0.8) 문서화 없음 |
| M5 | `agent_runner.py` | 352-489 | OpenAI/Anthropic 응답 처리 코드 거의 동일. 중복 |
| M6 | `dynamic_orchestrator.py` | 582-598 | Terminal 모드 0.5초 폴링. 이벤트 기반 전환 권장 |
| M7 | `ise_loop.py` | 91 | 외부 while True 루프에 max meta-cycles 없음 |
| M8 | `run_budget.py` | 전체 | 동시성 Lock 없음. 멀티스레드에서 race condition |
| M9 | `af.spec` | 19-173 | hiddenimports 100+개 수동 관리. 누락 시 frozen 빌드 런타임 크래시 |
| M10 | 다수 파일 | — | Non-atomic JSON 쓰기 패턴 잔존 (아래 참조) |

**M10 상세 — Non-atomic 파일 쓰기 잔존 목록** (C2/H5a와 동일 유형, 미수정):

| 파일 | 쓰기 대상 | 위험도 |
|------|----------|--------|
| `conversation_manager.py:130,145,181` | metadata/consensus/index.json | Medium |
| `document_index.py:351` | INDEX_CACHE_FILE | Low (캐시) |
| `ise_strategy_ledger.py:196` | ise_ledger_{run_id}.json | Medium |
| `pdca_state.py:92` | pdca_state.json | Medium |
| `project_pipeline.py:135` | checkpoint/{stage}.json | Medium |

### 3.4 이미 수정된 버그 (control/ 영역)

| ID | 파일 | 내용 | 상태 |
|----|------|------|------|
| BUG-2 | `rollback.py:248` | path traversal 방어 (pathlib.is_relative_to) | ✅ 수정됨 |
| BUG-3 | `run_ledger.py:96` | 스레드/프로세스 lock 추가 | ✅ 수정됨 |
| BUG-7 | `maintenance_pipeline.py:66` | conflict 무한 대기 → 1시간 timeout | ✅ 수정됨 |
| BUG-8 | `issue_context.py:179` | issue ID 중복 → UUID suffix | ✅ 수정됨 |
| BUG-9 | `continuity_snapshot.py:116` | conflict 시 health 격하 | ✅ 수정됨 |
| BUG-10 | `rollback.py:326` | checkpoint 체크섬 검증 시 dict 변형 방지 | ✅ 수정됨 |
| BUG-14 | `maintenance_state.py:135` | transition_log metadata 타입 통일 | ✅ 수정됨 |
| BUG-15 | `maintenance_state.py:109` | 파일 손상 시 재초기화 금지 | ✅ 수정됨 |

### 3.5 신규 기능 추가 (2026-04-03)

| 파일 | 기능 | 비고 |
|------|------|------|
| `core/hooks/code_review_doc.py` | 에이전트 실행 후 자동 코드 리뷰 + 문서 업데이트 Hook | 신규 (~180줄) |

---

## 4. Dead Code 목록

| 파일 | 줄 | 상태 | 비고 |
|------|-----|------|------|
| `ise_loop.py` | 469 | **0 import** | v3 Phase 1B에서 활성화 예정 |
| `claim_tracer.py` | 214 | **0 import** | 삭제 검토 필요 |
| `onboarding_wizard.py` | 191 | **0 import** | 삭제 검토 필요 |

---

## 4. Checkpoint 진실의 원천 (Source of Truth)

현재 3개의 checkpoint 메커니즘이 **우선순위 없이** 공존:

| 메커니즘 | 파일 | 저장 위치 | 기록 내용 |
|----------|------|-----------|-----------|
| OrchestratorManifestStore | `core/continuity/manifest_store.py` | `.af_manifest.json` | 오케스트레이터 전체 상태 (board, cycle, config) |
| CheckpointHook | `core/hooks/checkpoint.py` | `runs/{run_id}/checkpoint.json` | 에이전트 상태 (cycle, current_task, eval_history) |
| wake_checkpoint (미구현) | 설계 중 | `.af_runtime/daemon/wake_checkpoint.json` | 데몬 wake 상태 (pending_costs, budget, stage) |

**v3에서 정의할 우선순위:**
1. `wake_checkpoint` — daemon crash 복구 (가장 구체적, 가장 최근)
2. `manifest_store` — 오케스트레이션 중간 상태 복원
3. `CheckpointHook` — 개별 에이전트 사이클 복원 (가장 일반적)

---

## 5. 비용 추적 진실의 원천 (Source of Truth)

현재 비용 관련 데이터가 분산:

| 데이터 | 위치 | 내용 |
|--------|------|------|
| 글로벌 토큰 합계 | `run_budget.py` 싱글톤 | in-memory, 에이전트 구분 없음 |
| 스킬별 사용량 | `data/skill-usage.jsonl` | SkillFeedback, 에이전트별 아님 |
| 실행 트레이스 | `.af_runtime/control/trace_*.jsonl` | 상세 실행 로그 |
| 실행 결과 | `run_ledger.jsonl` | 성공/실패, 메타데이터 |

**v3에서 정의할 canonical source:**
- **상세**: `trace_*.jsonl` (기존 유지, 변경 없음)
- **요약**: `run_ledger.jsonl` → `metadata.tokens_total`, `metadata.tokens_by_provider` 등
- **글로벌 카운터**: `run_budget.py` (기존 유지, 에이전트별 분해는 RunLedger가 담당)

---

## 6. Provider 연동 현황

| 컴포넌트 | claude_cli | gemini_cli | codex_cli | 비고 |
|----------|-----------|-----------|-----------|------|
| `engine_auth.py` | auto-config | auto-config | auto-config | 단일 provider 선택 |
| `agent_runner.py` | failover loop | failover loop | failover loop | INFRA 분류 미적용 |
| `session_adapter.py` | native hook (frozen 제외) | native hook | wrapper_bridge, hook 없음 | codex 가장 제한적 |
| `control_plane_llm.py` | CLI 호출 | CLI 호출 + API fallback | CLI 호출 | AF_CONTROL_PLANE_PROVIDERS 미지원 |
| `providers/registry.py` | configure_providers() | configure_providers() | configure_providers() | daemon용 우회 경로 필요 |

---

## 7. v3 설계와의 매핑

| v3 Phase | 해결하는 문제 | 영향 파일 |
|----------|-------------|-----------|
| 1A (spawn helper) | inline frozen/source 분기 | dynamic_orchestrator.py, utils.py |
| 1B (태스크 분해) | ISE dead code | dynamic_orchestrator.py, supervisor.py |
| 2 (비용 추적) | 글로벌 싱글톤 예산만 | run_ledger.py, agent_runner.py |
| 3 (context 파일) | PROJECT_CONTEXT.md 미생성 | docs/PROJECT_CONTEXT.md |
| 4 (SharedContext) | 에이전트별 중복 검색 | dynamic_orchestrator.py |
| 5A (wake checkpoint) | checkpoint 3중 무질서 | checkpoint.py |
| 5B (compaction) | context_window_manager 미구현 | context_window_manager.py, interactive_chat.py |
| 6 (af serve) | one-shot만 가능 | daemon_supervisor.py, daemon_worker.py |
| 7 (provider policy) | auto_configure 단일 선택 | control_plane_llm.py, agent_runner.py |
| 8A (멀티 프로젝트) | config_paths 모듈 상수 | Worker 프로세스 격리 |
| 8B (훅 브릿지) | codex hook 불가 | event_bus.py, session_adapter.py |

---

## 8. ISE 시스템 상세 (Dead Code 분석)

ISE(Infinite Self-Enhancement)는 5개 파일, 1,344줄로 구성된 완전한 시스템이지만 **0개 파일에서 import됨**.

| 파일 | 줄 | 역할 | 주요 버그 |
|------|-----|------|----------|
| `ise_loop.py` | 469 | Meta-loop 오케스트레이터. Level 1-5 에스컬레이션 | L274-278: unreachable code, L91: 무한 루프 가능 |
| `ise_redesigner.py` | 264 | LLM 기반 태스크 재설계/분해 | L111,171: JSON 실패 시 silent retry, L30-42: 창의성 10개 고정 |
| `ise_analyzer.py` | 220 | 실패 분석 엔진 (6개 카테고리) | L207-217: LLM 타임아웃 없음, L218: 예외 silent pass |
| `ise_strategy_ledger.py` | 241 | 전략 기록/학습/반복 방지 | L225: SHA256 16자 truncation → 충돌 가능 |
| `ise_stall_detector.py` | 150 | 엔트로피 기반 정체 감지 | L35,43: threshold 매직넘버 문서화 없음 |

### ISE 실행 흐름 (활성화 시)

```
ISELoop.run_mission()
  ├─ while True:   ← ★ max meta-cycles 없음
  │   ├─ StallDetector.check(ledger) → "continue" | "creativity_injection" | "human_escalation"
  │   ├─ FSALoop.run_mission()  ← 5-cycle 내부 루프
  │   │   ├─ AgentRunner.run()
  │   │   ├─ CrossVerificationLoop.run() [2+ CLI 시]
  │   │   └─ StrategyEvaluator.evaluate_failure()
  │   ├─ ISEAnalyzer.analyze_failure() → ISEAnalysis
  │   ├─ _decide_escalation(ledger, analysis) → Level 1-5
  │   │   ├─ Level 1: apply_retry_feedback()
  │   │   ├─ Level 2: apply_pivot()
  │   │   ├─ Level 3: redesign_task()
  │   │   ├─ Level 4: evolve_skills()
  │   │   └─ Level 5: decompose_task() → 3-7 subtasks
  │   └─ StrategyLedger.record_attempt() + save()
  └─ 성공 또는 human_escalation 시 종료
```

---

## 9. 핵심 실행 흐름

```
[사용자 요청]
    ↓
[run_factory_cli.py] → argparse → route
    │
    ├─ af run → [ControlPlaneIntake.normalize()]
    │              ├─ WorkKindClassifier → ExecutionPolicy
    │              ├─ ChangeImpactProfiler → ImpactProfile
    │              └─ RunLedger.open_run()
    │              ↓
    │           [MaintenancePipeline.prepare() → execute()]
    │              ├─ _handle_conflicts() (stale auto-close, 1hr timeout)
    │              ├─ RuntimeSupervisor.supervise()
    │              │   ├─ heartbeat 30s, stall 120s
    │              │   └─ DynamicOrchestrator.run_project()
    │              │       ├─ ControlPlaneLLM (Lilith) → task 분배
    │              │       ├─ asyncio 5-concurrent
    │              │       └─ AgentRunner.run() × N
    │              │           └─ provider failover (claude→gemini→codex)
    │              ├─ RegressionSafetyGate.check() (pytest)
    │              └─ RunLedger.close_run()
    │
    ├─ af chat → InteractiveChat (887줄)
    ├─ af serve → DaemonSupervisor (v3 Phase 6, 미구현)
    └─ af worker → AgentWorker → task.json IPC → result.json
```
