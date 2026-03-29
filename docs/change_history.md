# 변경 이력

설계, 아키텍처, 워크플로, 구현 전략이 바뀔 때마다 항목을 하나씩 추가한다.

## 항목 템플릿
### YYYY-MM-DD HH:MM:SS
- 요약:
- 이유:
- 영향 파일:
- 후속 작업:

## 이력
### 2026-03-10 18:40:00
- 요약: `DynamicOrchestrator`의 manifest resume 복원과 workspace-local runtime 로그 저장을 실제 실행 경로에 다시 연결하고, Flash 자동 업그레이드 테스트를 환경변수 계약 기준으로 정렬했다.
- 이유: Step 1~6 적용 뒤 import 검증은 `agent_runner.py`의 잘못된 상수 import 때문에 멈췄고, 이후에도 orchestrator가 manifest를 읽고 쓰지 않아 resume 회귀 테스트가 계속 실패했기 때문이다. 동시에 `llm_engine` 테스트는 이미 제거된 `minesweeper` 특례를 계속 기대하고 있었다.
- 영향 파일: `core/agent_runner.py`, `core/dynamic_orchestrator.py`, `tests/test_llm_engine_auto_upgrade_scope.py`, `docs/change_history.md`
- 후속 작업: 전체 `tests/` 풀런은 아직 하지 않았으므로, 다음 단계에서는 전체 회귀를 한 번 더 돌리고 Step 6 범위 파일들의 구조 정리와 남은 import 정리를 이어간다.

### 2026-03-10 18:05:00
- 요약: 운영체제 언어 코드 기준 문서 언어 정책을 중앙 문서 정책과 자동 생성 문서 경로에 반영했다.
- 이유: 앞으로 생성하거나 수정하는 모든 문서를 OS 언어 코드에 맞는 언어로 일관되게 작성하도록 런타임 규칙을 고정하기 위해.
- 영향 파일: `core/documentation_policy.py`, `core/project_pipeline.py`, `core/continuity/resume_brief.py`, `tests/test_documentation_policy.py`, `tests/test_runner_contracts.py`, `tests/test_resume_brief.py`, `docs/architecture.md`, `docs/change_history.md`
- 후속 작업: 남아 있는 기존 영문 문서는 필요 시 한국어로 순차 정리하되, 새로 생성되거나 수정되는 문서부터는 운영체제 언어 코드 규칙을 우선 적용한다.

### 2026-03-10 17:46:45
- 요약: `code_review_report.md`를 현재 브랜치 기준으로 재검증한 문서를 추가했고, 실제 런타임 구조와 항목별 판정, 단계별 수정 순서를 정리했다.
- 이유: 기존 리뷰 리포트는 방향성은 유효하지만 현재 코드 상태를 완전히 반영하진 않아, 실제 실행용 계획을 현재 코드와 테스트 기준으로 다시 맞출 필요가 있었다.
- 영향 파일: `docs/plans/2026-03-10-code-review-report-validation.md`, `docs/change_history.md`
- 후속 작업: 새 검증 문서의 우선순위를 기준으로 작업하고, `code_review_report.md`를 그대로 패치 체크리스트처럼 적용하지 않는다. 오케스트레이터 resume 회귀도 활성 수정 큐에 포함한다.

### 2026-03-10 17:30:00
- 요약: Codex 보호 강화를 위해 destructive shell 명령과 PATH 해석 명령을 실행 전에 가로채는 런타임 shell proxy wrapper를 추가했다.
- 이유: 프롬프트 계약만으로는 Claude/Gemini의 네이티브 deny 규칙보다 약했기 때문에, 승인 프롬프트를 되살리지 않으면서 delete/reset 명령을 막는 Codex 전용 런타임 가드가 필요했다.
- 영향 파일: `core/destructive_guard.py`, `core/providers/session_adapter.py`, `scripts/destructive_guard_proxy.py`, `tests/test_destructive_guard.py`, `tests/test_cli_session_adapter.py`, `docs/architecture.md`, `docs/change_history.md`
- 후속 작업: 향후 Codex 릴리스가 provider-native deny 규칙을 지원하면 shell-proxy 우회를 기본 방식에서 제외하고, wrapper는 defense in depth 용도로만 유지한다.

### 2026-03-10 17:05:00
- 요약: Claude와 Gemini에 대해 delete/reset shell 명령을 하드 차단하는 destructive action guard를 추가하고, 모든 런타임 경로에 공용 guard 계약을 주입했다.
- 이유: 무프롬프트 자동 실행을 유지하면서도 delete/reset 명령을 막으려면 별도 안전 계층이 필요했다.
- 영향 파일: `core/destructive_guard.py`, `core/agent_runner.py`, `core/providers/cli.py`, `core/providers/session_adapter.py`, `tests/test_destructive_guard.py`, `tests/test_runner_contracts.py`, `tests/test_cli_providers.py`, `tests/test_cli_session_adapter.py`, `docs/architecture.md`, `docs/change_history.md`
- 후속 작업: 이후 Codex CLI가 provider-native deny 정책을 지원하는지 다시 확인하고, 가능해지면 prompt-only fallback보다 강한 기본 강제 방식으로 대체한다.

### 2026-03-10 16:20:00
- 요약: Claude CLI와 Gemini CLI의 기본 실행 모드를 무프롬프트 자동 실행 쪽으로 바꿔, 세 CLI 프로바이더가 같은 자동 실행 의미를 따르도록 맞췄다.
- 이유: `acceptEdits`와 `auto_edit`는 편집만 자동 승인할 뿐이라, 자동 실행 중에도 비편집 승인 프롬프트가 나타날 수 있었다.
- 영향 파일: `core/providers/cli.py`, `tests/test_cli_providers.py`, `docs/architecture.md`, `docs/change_history.md`
- 후속 작업: 프로바이더 기본값은 각 벤더 CLI 의미와 계속 맞추고, destructive action 차단은 승인 프롬프트를 부활시키지 않는 별도 정책으로 관리한다.

### 2026-03-10 16:05:00
- 요약: Codex CLI 자동 실행 기본값을 top-level `--ask-for-approval never --sandbox workspace-write` 뒤에 `exec`를 붙이는 형태로 변경했다.
- 이유: 설치된 Codex CLI 기준으로 `--full-auto`는 여전히 `on-request` 성격이 있어 자동 실행 중 승인 프롬프트를 띄울 수 있었다.
- 영향 파일: `core/providers/cli.py`, `tests/test_cli_providers.py`, `docs/architecture.md`, `docs/change_history.md`
- 후속 작업: Codex CLI 플래그 계약이 다시 바뀌면 `codex --help`와 `codex exec --help`를 다시 확인한 뒤 provider 사양을 조정한다.

### 2026-03-10 15:30:00
- 요약: 아키텍처 문서를 훅, 세션, 프로젝트 생성, 프롬프트 흐름, 출력 흐름, 파일 수정 경로까지 따라갈 수 있는 실행 경로 수준 가이드로 확장했다.
- 이유: 기존 문서는 상위 수준 placeholder에 가까워 현재 런타임 구조를 추적하기에 충분하지 않았다.
- 영향 파일: `docs/architecture.md`, `docs/change_history.md`
- 후속 작업: 러너, 프로바이더 브리지, 훅 모델, 프로젝트 파이프라인이 바뀔 때마다 실행 경로 섹션도 같이 맞춘다.

### 2026-03-10 00:00:00
- 요약: 설계 변경 시 `docs/architecture.md`와 `docs/change_history.md`를 반드시 갱신하는 문서 계약을 도입했다.
- 이유: 세션과 PC를 옮겨도 아키텍처/워크플로 변경 이력을 안정적으로 남기기 위해.
- 영향 파일: `core/agent_runner.py`, `core/project_init.py`, `core/project_pipeline.py`, `core/documentation_policy.py`, `docs/architecture.md`, `docs/change_history.md`
- 후속 작업: 앞으로 설계 수준 변경이 생기면 같은 작업 안에서 `docs/architecture.md`와 이 파일을 함께 갱신한다.

### 2026-03-21 13:30:00
- Summary: Added a module/task planning board to the project pipeline and let the orchestrator assign work from `project_board_state.json`.
- Reason: Role-level TODO lists were not enough to support stable parallel execution and follow-up handoff.
- Impact files: `core/project_task_board.py`, `core/project_pipeline.py`, `core/dynamic_orchestrator.py`, `tests/test_project_pipeline.py`, `tests/test_dynamic_orchestrator_workspace_scope.py`, `docs/work-items/modular_task_planning_workflow.md`, `docs/architecture.md`, `docs/change_history.md`
- Follow-up: Improve planner prompts so `modules/tasks` are produced more reliably and connect mailbox-based handoff to board threads.

### 2026-03-21 14:00:00
- Summary: Expanded planner prompts so `planning_steps`, `modules`, and `tasks` are generated directly, and added normalization tests for planner output.
- Reason: The first planning pass needed to emit module decomposition directly instead of depending only on board fallback.
- Impact files: `core/bootstrap_roles.py`, `tests/test_bootstrap_roles.py`, `docs/architecture.md`, `docs/change_history.md`
- Follow-up: Add prompt templates that guide better module decomposition by project type.

### 2026-03-21 14:30:00
- Summary: Added structured agent mailbox handoff for task-id based messages, blockers, review requests, decisions, and results.
- Reason: Parallel agent work needed a stable structured channel for handoff and state transfer.
- Impact files: `core/project_mailbox.py`, `core/agent_runner.py`, `core/dynamic_orchestrator.py`, `tests/*mailbox*`, `docs/architecture.md`, `docs/change_history.md`
- Follow-up: Refine mailbox digest summaries in planner/runtime paths and keep mailbox notes aligned with board notes.
