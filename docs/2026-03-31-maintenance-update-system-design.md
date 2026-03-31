# Agent Factory 유지보수/업데이트 운영 시스템 상세 설계 v1

작성일: 2026-03-31
상태: draft
범위: `agent-factory` 자체의 장기 유지보수, 업데이트, 버그 수정, 기능 확장 요청을 안정적으로 이어서 처리하는 운영 시스템 설계

## 1. 문서 목적

이 문서는 `agent-factory`에 이미 존재하는 실행 자산을 최대한 재사용하면서, `장기 유지보수/업데이트 운영 시스템`을 안전하게 도입하기 위한 상세 설계를 정의한다.

핵심 목표는 세 가지다.

- 기존 코드와 의미 충돌 없이 도입 가능해야 한다.
- 유지보수 요청도 신규 프로젝트와 같은 수준의 문서 품질과 실행 통제를 가져야 한다.
- 세션 중단, 훅 이벤트, 작업 재개, 승인, 역할 배정, 이슈 추적을 하나의 운영 흐름으로 묶어야 한다.

이 문서는 구현 문서가 아니라 `도입 경계가 명확한 설계 문서`다. 즉 무엇을 어디에 얹을지, 무엇은 건드리면 안 되는지, 도입 순서를 어떻게 가져가야 하는지를 명시한다.

## 2. 분석 기준과 실제 확인한 현재 코드

이번 설계는 아래 현재 구현을 기준으로 작성했다.

- `core/request_router.py`
  현재 최상위 라우팅 결과는 `single` 또는 `project` 둘뿐이다.
- `core/intent.py`
  현재 intent 분류는 `trivial`, `question`, `refactoring`, `greenfield`, `debugging` 다섯 종류다.
- `core/project_pipeline.py`
  현재 파이프라인은 `prepare -> approval -> execute` 구조이며, 이미 evidence, brief, role plan, task board, work-item 문서, approval gate를 생성한다.
- `core/project_task_board.py`
  현재 작업 상태의 source of truth는 `project_board_state.json` 이고, 핵심 status 집합은 `pending`, `in_progress`, `completed`, `failed`, `blocked`다.
- `core/dynamic_orchestrator.py`
  현재 오케스트레이터는 `state_board`, `.af_manifest.json`, mailbox, board dependency, max cycle 기반 실행을 담당한다.
- `core/providers/session_adapter.py`
  현재 외부 CLI 세션의 continuity context, hook event 저장, provider별 hook 이름 차이를 담당한다.
- `core/continuity/resume_brief.py`
  현재 재개 요약은 `resume_brief.md` 하나로 정리된다.
- `core/continuity/manifest_store.py`
  현재 오케스트레이터 재개 상태는 `.af_manifest.json` 을 통해 복원된다.
- `core/approval_gate.py`, `core/work_item_generator.py`, `core/work_item_parser.py`
  현재 문서 승인 계약과 work-item 문서 흐름은 이미 살아 있다.
- `core/memory_system/issue_tracker.py`
  GitHub Issue/Jira 어댑터는 존재하지만, 아직 core pipeline의 기본 실행 흐름에는 강제 결합돼 있지 않다.

## 3. 결론 요약

결론은 단순하다.

- `치명적인 구조 충돌은 없다.`
- 다만 `그대로 갈아끼우는 방식`으로 구현하면 높은 확률로 기존 런타임 의미가 깨진다.

즉, 유지보수/업데이트 운영 시스템은 `교체형`이 아니라 `가산형(additive)`으로 들어가야 한다.

정확히는 아래 원칙이 필요하다.

1. 현재 `single/project` 라우팅 의미는 유지한다.
2. 유지보수 여부는 별도의 `work_kind` 또는 `issue_kind` 로 추가 분류한다.
3. 기존 `project_board_state.json` 의 status 의미는 유지한다.
4. 기존 `resume_brief.md`, `.af_manifest.json`, CLI session state는 당장 대체하지 않고 먼저 `집계 계층`으로 묶는다.
5. provider hook 이름은 기존 이름을 보존하고, 정규화는 bridge 레이어에서 한다.
6. approval/work-item 계약은 재사용하고, 유지보수용 문서가 필요하면 optional sidecar로 추가한다.

## 4. 현재 코드와의 충돌 분석

| 영역 | 현재 구현 | 그대로 덮어쓸 때의 충돌 | 위험도 | 권장 대응 |
| --- | --- | --- | --- | --- |
| 최상위 라우팅 | `RequestRouter` 는 `single/project` 만 반환 | `maintenance` 같은 새 pipeline 값을 직접 추가하면 기존 호출부와 테스트 가정이 흔들린다 | 높음 | 기존 pipeline 값은 유지하고 `route_metadata.work_kind` 를 추가한다 |
| intent 분류 | `IntentGate` 는 5개 카테고리만 안다 | `maintenance`, `feature_update`, `ops` 를 바로 intent 본값으로 넣으면 fallback, guardrail, router 규칙과 충돌한다 | 높음 | intent는 유지하고 2차 분류기 `WorkKindClassifier` 를 별도 둔다 |
| 프로젝트 파이프라인 | `ProjectPipeline.prepare/execute` 는 신규 프로젝트 생성 중심이지만 문서/보드/승인 흐름은 이미 갖고 있다 | 별도 유지보수 파이프라인을 완전히 새로 만들면 문서 계약과 승인 흐름이 이중화된다 | 높음 | `MaintenancePipeline` 은 신규 구현이 아니라 `ProjectPipeline` 의 래퍼 또는 확장으로 만든다 |
| 작업 보드 스키마 | `project_board_state.json` status 집합과 의존성 계산이 이미 연결돼 있다 | `queued`, `triaged`, `waiting_user` 같은 상태를 기존 status 필드에 직접 추가하면 `next_board_tasks()` 와 summary 계산이 깨진다 | 높음 | 기존 `status` 는 유지하고, 추가 상태는 `control_status` 또는 sidecar ledger로 뺀다 |
| 오케스트레이터 책임 | `DynamicOrchestrator` 는 finite run, max cycle, assignment, board update에 집중한다 | scheduler/daemon/supervisor 책임까지 안으로 넣으면 실행기와 운영기가 섞인다 | 높음 | supervisor는 오케스트레이터 바깥의 Control Plane으로 둔다 |
| continuity 저장 | `resume_brief`, manifest, CLI session state, events jsonl 이 각각 쓰이고 있다 | 새 ContinuityManager가 기존 writer를 한 번에 교체하면 세 경로가 서로 다른 truth를 만들 수 있다 | 높음 | 1단계는 `집계기(read model)`만 추가하고 writer 교체는 뒤로 미룬다 |
| hook 이벤트 이름 | Claude는 `PreCompact`, Gemini는 `PreCompress`, 내부는 `pre_execute/pre_tool_call` 중심 | 이름을 즉시 통일하려고 기존 provider 설정을 바꾸면 현재 hook bridge와 세션 복원이 깨질 수 있다 | 중간 | 정규화된 canonical event는 bridge 내부에서만 만든다 |
| 승인 계약 | approval gate는 현재 `feature_plan/feature_spec/implementation_design/implementation_tasks` 묶음에 맞춰져 있다 | 유지보수 전용 필수 문서를 새로 강제하면 gate 유효성 규칙과 해시 스냅샷이 깨진다 | 중간 | 초기 단계에서는 기존 work-item 문서 세트를 그대로 쓴다 |
| 이슈 트래커 연동 | GitHub/Jira 어댑터는 optional capability다 | core 흐름이 이슈 트래커를 필수 가정하면 로컬 워크스페이스 모드가 깨진다 | 중간 | file-backed issue context를 기본으로 두고 external tracker는 adapter로 붙인다 |
| run/worktree 추적 | 현재는 `.af_manifest.json` + `.af_runtime/cli_sessions/*` + workspace 자체로 추적한다 | worktree registry를 기존 manifest 대체로 넣으면 resume 경로가 분열된다 | 중간 | `run_ledger.jsonl` 을 sidecar로 추가하고 manifest는 그대로 유지한다 |

## 5. 설계 원칙

### 5.1 교체가 아니라 사이드카

새 유지보수/업데이트 시스템은 기존 러너, 오케스트레이터, 보드, 승인, continuity 자산을 대체하지 않는다.
우선은 그 위에 `Control Plane sidecar` 를 얹는다.

### 5.2 1차 분류와 2차 분류를 분리

현재 `single/project` 와 `IntentGate` 는 유지한다.
대신 아래 추가 메타데이터를 도입한다.

- `work_kind`
  `new_project | maintenance | bugfix | feature_update | refactor`
- `issue_kind`
  `incident | planned_update | regression | backlog_item | research_task`
- `execution_policy`
  `quick_fix | standard_update | deep_update | full_bootstrap`

### 5.3 source of truth를 함부로 바꾸지 않는다

초기 도입 단계의 source of truth는 아래 그대로 둔다.

- 실행 진행 상태: `project_board_state.json`
- 오케스트레이터 복구 상태: `.af_manifest.json`
- 세션 재개 요약: `resume_brief.md`
- 외부 CLI 세션 상태: `.af_runtime/cli_sessions/*.json`
- 승인 상태: `docs/work-items/<slug>/approval-gate.md`

새 시스템은 먼저 이것들을 `읽어서 묶는 역할`을 해야 한다.

### 5.4 status 확장은 sidecar로

기존 task `status` 는 오케스트레이터가 사용하므로 즉시 확장하지 않는다.
운영 전용 상태가 필요하면 아래처럼 분리한다.

- 실행 상태: 기존 `status`
- 운영 상태: 신규 `control_status`
- 승인 상태: `approval_status`
- 재개 상태: `resume_state`

### 5.5 provider hook 이름은 boundary에서만 정규화

현재 provider별 hook 이름 차이는 살아 있는 계약이다.
그러므로 canonical lifecycle 이름은 아래처럼 bridge 내부에서만 쓴다.

- `SessionStart` / `BeforeAgent` -> `session_start`
- `UserPromptSubmit` -> `prompt_submit`
- `PreCompact` / `PreCompress` -> `pre_compact`
- `Stop` / `SessionEnd` -> `session_end`

## 6. 목표 아키텍처

```text
User / Trigger / Issue Event
            |
            v
+----------------------------------------+
| Control Plane                          |
| intake -> work_kind -> issue_context   |
| continuity_snapshot -> execution_policy|
| approval -> scheduler -> run_ledger    |
+----------------------------------------+
            |
            v
+----------------------------------------+
| Quality Plane                          |
| evidence -> brief/spec -> critique     |
| task_board -> QA -> final artifact     |
+----------------------------------------+
            |
            v
+----------------------------------------+
| Memory Plane                           |
| manifest -> resume_brief -> episodes   |
| semantic recall -> issue history       |
+----------------------------------------+
```

현재 코드에 맞춘 책임 분리는 아래와 같다.

| 평면 | 재사용 자산 | 신규로 필요한 것 |
| --- | --- | --- |
| Control Plane | `RequestRouter`, `ApprovalGate`, `DynamicOrchestrator`, `session_adapter`, `manifest_store` | `WorkKindClassifier`, `IssueContext`, `ExecutionPolicy`, `RunLedger`, `Supervisor` |
| Quality Plane | `ProjectPipeline`, `ResearchVerifier`, `work_item_generator`, `project_task_board`, `pipeline_quality`, `rubric_compiler` | 유지보수 전용 prepare 래퍼, change impact profiler |
| Memory Plane | `resume_brief`, `manifest_store`, `session_bridge`, continuity adapter, memory system | continuity snapshot aggregator, issue history consolidation |

## 7. 신규 데이터 객체와 저장 위치

초기 도입은 파일 기반으로 한다. 이유는 현재 런타임도 file-first 구조이기 때문이다.

### 7.1 신규 제안 아티팩트

- `.af_runtime/control/issue_context.json`
- `.af_runtime/control/execution_policy.json`
- `.af_runtime/control/run_ledger.jsonl`
- `.af_runtime/control/checkpoints/<run_id>.json`
- `.af_runtime/control/maintenance_queue.json`
- `docs/work-items/<slug>/maintenance-runbook.md`
  초기 단계에서는 optional

### 7.2 필수 필드

#### `IssueContext`

```json
{
  "issue_id": "local-20260331-001",
  "work_kind": "maintenance",
  "issue_kind": "planned_update",
  "title": "agent-factory 유지보수 운영 시스템 설계",
  "workspace": "C:/Project/agent-factory",
  "risk_level": "high",
  "comparison_mode": true,
  "requested_role": "",
  "source": "user_request",
  "created_at": "2026-03-31T00:00:00Z"
}
```

#### `ExecutionPolicy`

```json
{
  "policy_id": "policy-20260331-001",
  "execution_policy": "deep_update",
  "requires_approval": true,
  "requires_work_item_docs": true,
  "requires_agent_qa": true,
  "requires_supervisor_resume": true
}
```

#### `RunLedger` 레코드

```json
{
  "run_id": "project_run_123",
  "issue_id": "local-20260331-001",
  "workspace": "C:/Project/agent-factory",
  "pipeline": "project",
  "work_item_slug": "agent-factory-maintenance-update",
  "status": "running",
  "board_summary": {
    "pending_tasks": 4,
    "in_progress_tasks": 1,
    "completed_tasks": 2
  },
  "updated_at": "2026-03-31T00:00:00Z"
}
```

## 8. 명시적 단계 설계

아래 단계는 실제 도입 기준의 권장 기준 흐름이다.
핵심은 `모든 유지보수 요청이 문서화된 승인 흐름과 재개 가능한 상태를 갖고, 그 위에서 실행기와 QA가 움직이는 것`이다.

### 1단계. Intake Normalization

- 목적:
  사용자 요청, 외부 이슈, 내부 재시도 요청을 공통 입력으로 정규화한다.
- 현재 재사용:
  `RequestRouter.route()`
- 신규 추가:
  `ControlPlaneIntake.normalize()`
- 출력:
  `NormalizedRequest`
- 충돌 회피:
  이 단계에서는 기존 `pipeline` 값을 바꾸지 않는다.

### 2단계. Work Kind Classification

- 목적:
  요청이 신규 생성인지, 유지보수인지, 버그 수정인지, 계획된 업데이트인지 2차 분류한다.
- 현재 재사용:
  `IntentGate.classify()` 결과
- 신규 추가:
  `WorkKindClassifier`
- 출력:
  `route_metadata.work_kind`, `route_metadata.issue_kind`
- 충돌 회피:
  `IntentGate` 의 canonical intent 집합은 그대로 둔다.

### 3단계. Issue Context Binding

- 목적:
  유지보수 요청을 추적 가능한 issue 단위로 묶는다.
- 현재 재사용:
  optional `IssueTrackerAdapter`
- 신규 추가:
  로컬 file-backed issue 생성기
- 출력:
  `.af_runtime/control/issue_context.json`
- 충돌 회피:
  외부 이슈 트래커를 필수 전제로 두지 않는다.

### 4단계. Continuity Snapshot Rehydration

- 목적:
  이전 실행 상태를 하나의 읽기 모델로 합친다.
- 현재 재사용:
  `.af_manifest.json`, `resume_brief.md`, `.af_runtime/cli_sessions/*.json`, `project_board_state.json`
- 신규 추가:
  `ContinuitySnapshotBuilder`
- 출력:
  `ContinuitySnapshot`
- 충돌 회피:
  기존 writer를 교체하지 않고 집계만 수행한다.

### 5단계. Execution Policy Selection

- 목적:
  이번 요청이 `quick_fix`, `standard_update`, `deep_update` 중 어느 경로를 타야 하는지 결정한다.
- 현재 재사용:
  `route`, risk metadata, comparison mode
- 신규 추가:
  `ExecutionPolicyResolver`
- 출력:
  `.af_runtime/control/execution_policy.json`
- 충돌 회피:
  policy는 새 파일로 저장하고 기존 planner 입력에 주입한다.

### 6단계. Quality Preparation

- 목적:
  유지보수 요청도 evidence, brief, role plan, board를 가진 실행 단위로 만든다.
- 현재 재사용:
  `ProjectPipeline.prepare()`, `ResearchVerifier`, `generate_work_items()`
- 신규 추가:
  `MaintenancePipeline.prepare()` 또는 `ProjectPipeline.prepare()` wrapper
- 출력:
  `project_brief.json`, `role_plan.json`, `project_board_state.json`, work-item 문서
- 충돌 회피:
  별도 문서 체계를 만들지 않고 현재 work-item 문서를 재사용한다.

### 7단계. Approval and Human Review

- 목적:
  유지보수/업데이트 설계 변경을 승인된 실행 단위로 전환한다.
- 현재 재사용:
  `ApprovalGate`
- 신규 추가:
  optional `maintenance-runbook.md`
- 출력:
  승인된 work-item
- 충돌 회피:
  gate의 필수 문서 집합은 초기 단계에서 늘리지 않는다.

### 8단계. Board Materialization and Sync

- 목적:
  승인된 문서를 실제 실행 가능한 board/task 상태로 반영한다.
- 현재 재사용:
  `sync_board_from_work_items()`, `write_project_board()`
- 신규 추가:
  `control_status` overlay 또는 `run_ledger`
- 출력:
  실행 가능한 board + 운영 sidecar 상태
- 충돌 회피:
  기존 `status` 의미를 바꾸지 않는다.

### 9단계. Execution Dispatch

- 목적:
  역할별 작업을 안전하게 병렬 실행한다.
- 현재 재사용:
  `DynamicOrchestrator.run_project()`
- 신규 추가:
  outer `RuntimeSupervisor`
- 출력:
  completed / failed / interrupted 상태
- 충돌 회피:
  scheduler는 오케스트레이터 바깥에서 관리한다.

### 10단계. Lifecycle Hook and Session Continuity

- 목적:
  compaction, 세션 종료, 재개 지점에서 상태 손실을 막는다.
- 현재 재사용:
  `session_adapter.handle_hook_event()`, `prepare_cli_session()`, `finalize_cli_session()`
- 신규 추가:
  canonical lifecycle bridge
- 출력:
  hook-normalized event log, continuity snapshot refresh
- 충돌 회피:
  provider별 native hook 이름은 보존한다.

### 11단계. Verification and Closeout

- 목적:
  유지보수 결과가 단순 편집 성공이 아니라 요구사항 충족과 회귀 안전성을 만족하는지 닫는다.
- 현재 재사용:
  `pipeline_quality`, `run_structural_gate()`, Agent QA 경로
- 신규 추가:
  change impact checklist
- 출력:
  verification summary, final verdict
- 충돌 회피:
  QA 조건은 `execution_policy` 로 조절하고, 모든 요청에 frontier 비용을 강제하지 않는다.

### 12단계. Memory Consolidation and Re-entry

- 목적:
  이번 유지보수 경험을 다음 세션 재개와 다음 이슈 처리에 바로 쓰게 만든다.
- 현재 재사용:
  `resume_brief`, `session_bridge`, continuity adapter, memory system
- 신규 추가:
  issue-linked episode consolidation
- 출력:
  업데이트된 `resume_brief.md`, issue history, ledger closeout
- 충돌 회피:
  기존 continuity 파일 포맷은 유지한 채 추가 메타데이터를 별도 저장한다.

## 9. 권장 구현 순서

### Phase 1. 무충돌 메타데이터 도입

먼저 아래만 추가한다.

- `work_kind`
- `issue_context`
- `execution_policy`
- `run_ledger`

이 단계에서는 기존 `RequestRouter`, `IntentGate`, `ProjectPipeline`, `DynamicOrchestrator` 의 리턴 타입과 의미를 바꾸지 않는다.

### Phase 2. Continuity Snapshot 집계기

그 다음 아래를 추가한다.

- `ContinuitySnapshotBuilder`
- canonical lifecycle bridge
- session + manifest + resume brief + board 통합 읽기

이 단계에서도 기존 writer는 유지한다.

### Phase 3. MaintenancePipeline 래퍼

세 번째 단계에서 유지보수 요청이 `ProjectPipeline.prepare()` 를 재사용하도록 래퍼를 둔다.
핵심은 `문서화 + 승인 + board 생성` 흐름을 공통화하는 것이다.

### Phase 4. Supervisor 도입

네 번째 단계에서만 outer supervisor를 추가한다.
이 시점부터 재시도, 대기, 후속 실행, heartbeat를 Control Plane이 담당한다.

### Phase 5. Issue Tracker / Memory Consolidation 확장

마지막으로 외부 issue tracker, episode consolidation, cross-project reuse를 확장한다.
이 단계는 optional integration으로 유지한다.

## 10. 초기 구현에서 건드리면 안 되는 것

아래는 초기에 바꾸면 안 된다.

- `RequestRouter.route()` 의 `pipeline` 반환 집합
- `IntentGate.INTENT_CATEGORIES`
- `project_board_state.json` 의 기존 `status` 의미
- `DynamicOrchestrator.run_project()` 의 핵심 책임 범위
- `resume_brief.md` 기본 포맷
- `session_adapter` 가 provider native hook 이름을 쓰는 방식
- `ApprovalGate` 의 필수 문서 해시 검증 방식

이 항목을 초기에 건드리면 테스트 회귀보다 더 큰 의미 회귀가 먼저 발생한다.

## 11. 구현 권장 파일 구조

아래처럼 `control` 하위에 신규 계층을 두는 것이 가장 안전하다.

```text
core/
  control/
    intake.py
    work_kind.py
    issue_context.py
    continuity_snapshot.py
    execution_policy.py
    run_ledger.py
    supervisor.py
    lifecycle_bridge.py
    maintenance_pipeline.py
```

이유는 현재 코드의 중심 실행기와 오케스트레이터를 건드리지 않고, 바깥에서 운영 계층을 얹을 수 있기 때문이다.

## 12. 최종 판단

현재 `agent-factory` 위에 유지보수/업데이트 운영 시스템을 얹는 것은 충분히 가능하다.
다만 안전한 방법은 하나뿐이다.

- `실행기 교체`가 아니라 `운영 계층 추가`
- `status 의미 확장`이 아니라 `sidecar 상태 추가`
- `writer 교체`가 아니라 `집계기 도입`
- `provider hook 변경`이 아니라 `bridge 정규화`

즉, 이 설계의 핵심은 새로운 시스템을 크게 만드는 것이 아니라, `현재 시스템의 살아 있는 계약을 깨지 않는 방식으로 운영 지속성을 붙이는 것`이다.
