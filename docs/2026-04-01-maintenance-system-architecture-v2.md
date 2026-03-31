# Agent Factory 유지보수/업데이트 운영 시스템 아키텍처 v2

작성일: 2026-04-01
기반: `2026-03-31-maintenance-update-system-design.md` (v1) 심층 분석 결과
상태: design
범위: v1 설계의 검증 결과, 누락 영역 보완, 개선된 아키텍처 상세 설계

---

## 1. v1 설계 검증 요약

### 1.1 코드 정확성 검증 결과

v1 문서가 참조한 10개 모듈의 현재 코드를 전수 대조했다.

| 모듈 | v1 기술 정확도 | 비고 |
|------|--------------|------|
| `core/request_router.py` | 정확 | `single/project` 2값, score 기반 분류 |
| `core/intent.py` | 정확 | 5개 카테고리 (`trivial/question/refactoring/greenfield/debugging`) |
| `core/project_pipeline.py` | 정확 | `prepare→approval→execute`, PipelineStageGuard 적용 |
| `core/project_task_board.py` | 거의 정확 | module status에 `at_risk` 존재 (v1 미언급, 사소) |
| `core/dynamic_orchestrator.py` | 정확 | max_cycles=15, max_concurrent=5, manifest 기반 복구 |
| `core/providers/session_adapter.py` | 정확 | provider별 hook 이름 차이 확인 |
| `core/continuity/resume_brief.py` | 정확 | `resume_brief.md`, 1600자 excerpt 제한 |
| `core/continuity/manifest_store.py` | 정확 | `.af_manifest.json`, atomic write, 0.25s debounce |
| `core/approval_gate.py` | 정확 | SHA256[:16] 해시, 5개 문서 고정 |
| `core/memory_system/issue_tracker.py` | 정확 | GitHub 어댑터 구현, Jira 스켈레톤, pipeline 비결합 |

**결론: v1 코드 분석 정확도 98%+.** 설계 기반이 신뢰할 수 있다.

### 1.2 네이밍/경로 충돌 검증

- `core/control/` 디렉토리: 존재하지 않음 → 생성 안전
- 제안된 9개 파일명: 코드베이스에 없음 → 충돌 없음
- `work_kind`, `issue_kind`, `control_status` 문자열: 설계 문서에서만 참조 → 충돌 없음
- `.af_runtime/control/` 경로: 존재하지 않음 → 충돌 없음
- **주의:** `RunLedger` vs 기존 `StrategyLedger` (`core/ise_strategy_ledger.py`) — 목적 상이하나 이름 혼동 가능

### 1.3 v1 설계 원칙 평가

v1의 6대 원칙은 모두 건전하다:

1. 교체가 아니라 사이드카 → **동의.** `ProjectPipeline`에 콜백/훅 시스템이 없어 서브클래싱 위험 높음. 컴포지션 래퍼만 안전.
2. 1차/2차 분류 분리 → **동의.** `IntentGate` 5개 값을 건드리면 fallback 로직 전파 위험.
3. source of truth 보존 → **동의.** 4개 파일(manifest/resume/board/sessions) writer 동시 교체 시 상태 분열 위험.
4. status 확장은 sidecar → **동의하되 보완 필요.** board `status="pending"` + `control_status="waiting_user"` 일 때 오케스트레이터가 여전히 dispatch 시도 → filter layer 필요.
5. provider hook 이름 bridge 정규화 → **동의.**
6. approval/work-item 재사용 → **동의.** `_DOC_FILES` 5개 고정은 초기 단계에서 건드리면 안 됨.

---

## 2. v1 대비 개선 사항

### 2.1 v1에서 누락된 설계 영역

| # | 누락 영역 | 영향도 | 설명 |
|---|----------|--------|------|
| G1 | Change Impact Profiler 상세 설계 | 높음 | 변경 대상 file→module→role 역추적 메커니즘 미정의 |
| G2 | Rollback 전략 | 높음 | 유지보수 부분 실패 시 롤백 방법 없음 |
| G3 | ContinuitySnapshot merge 정책 | 중간 | 4개 소스 간 상태 불일치 해소 규칙 없음 |
| G4 | ExecutionPolicy → 스테이지 매핑 | 중간 | `quick_fix`가 어떤 단계를 스킵하는지 불명확 |
| G5 | 동시 유지보수 충돌 방지 | 중간 | 같은 파일을 건드리는 2개 op 간 충돌 감지 없음 |
| G6 | Supervisor heartbeat/recovery | 중간 | 간격, 실패 감지, 복구 액션 미정의 |
| G7 | Regression Safety Gate | 중간 | 변경 후 회귀 테스트 실행 조건/범위 미정의 |
| G8 | Observability/Metrics | 낮음 | 유지보수 성공률, MTTR 추적 미언급 |

### 2.2 v1 대비 구조적 개선 4가지

**개선 A: 선형 12단계 → State Machine 생명주기**

v1의 12단계 선형 흐름은 재개/분기/롤백을 암시적으로만 처리한다.
명시적 상태 머신으로 전환하면 어떤 상태에서 어떤 전이가 허용되는지 코드로 강제할 수 있다.

**개선 B: Change Impact Profiler 추가**

유지보수의 핵심은 "무엇이 영향받는가"이다.
변경 대상 파일에서 module→role→task 역추적을 자동화하면:
- evidence 수집 범위를 targeted로 좁힐 수 있다
- 관련 없는 role/task를 execution에서 제외할 수 있다
- 회귀 테스트 범위를 결정할 수 있다

**개선 C: ExecutionPolicy의 구체적 스테이지 매핑**

v1은 policy 이름만 정의하고 실제 파이프라인 동작 차이를 명시하지 않았다.
각 policy가 정확히 어떤 단계를 실행/스킵하는지 테이블로 정의해야 한다.

**개선 D: Board + Ledger 원자적 업데이트**

board 상태와 ledger 기록이 비동기면 "ledger에는 실행됨 + board에는 pending" 불일치가 생길 수 있다.
Two-phase 패턴으로 원자성을 보장한다.

---

## 3. 아키텍처 상세 설계

### 3.1 전체 아키텍처

```text
User / Trigger / Issue Event / Hook Event
                |
                v
+=========================================================+
| Control Plane (core/control/)                           |
|                                                         |
|  ControlPlaneIntake                                     |
|    → WorkKindClassifier                                 |
|    → IssueContextManager                                |
|    → ContinuitySnapshotBuilder                          |
|    → ExecutionPolicyResolver                             |
|    → MaintenanceStateMachine (NEW)                      |
|    → ChangeImpactProfiler (NEW)                         |
|    → RuntimeSupervisor                                  |
|    → RunLedger                                          |
|    → CanonicalLifecycleBridge                           |
+=========================================================+
                |
                v
+=========================================================+
| Quality Plane (기존 재사용)                                |
|                                                         |
|  MaintenancePipeline (래퍼)                               |
|    → ProjectPipeline.prepare() / execute()              |
|    → ResearchVerifier.verify_with_retry()               |
|    → RubricCompiler.evaluate()                          |
|    → ParallelCritiqueEngine.critique()                  |
|    → AggregatedVerdict.evaluate()                       |
|    → PipelineStageGuard.run()                           |
|    → RegressionSafetyGate (NEW)                         |
+=========================================================+
                |
                v
+=========================================================+
| Memory Plane (기존 재사용 + 집계기)                         |
|                                                         |
|  ContinuitySnapshotBuilder (read model)                 |
|    → .af_manifest.json (기존 writer 유지)                  |
|    → resume_brief.md (기존 writer 유지)                    |
|    → project_board_state.json (기존 writer 유지)           |
|    → .af_runtime/cli_sessions/*.json (기존 유지)           |
|  + IssueHistoryConsolidator                             |
|  + RunLedger (append-only journal)                      |
+=========================================================+
```

### 3.2 State Machine 설계

```text
          ┌──────────┐
          │ INTAKE   │  ControlPlaneIntake.normalize()
          └────┬─────┘
               v
          ┌──────────┐
          │CLASSIFIED│  WorkKindClassifier.classify()
          └────┬─────┘
               v
          ┌──────────────┐
          │CONTEXT_BOUND │  IssueContextManager.bind()
          └────┬─────────┘
               v
          ┌───────────────┐
          │POLICY_RESOLVED│  ExecutionPolicyResolver.resolve()
          └────┬──────────┘   + ChangeImpactProfiler.profile()
               v
          ┌──────────────┐
          │ PREPARING    │  MaintenancePipeline.prepare()
          └────┬─────────┘
               v
      ┌────────────────────┐
      │AWAITING_APPROVAL   │  ApprovalGate (기존 재사용)
      └────────┬───────────┘
               v
          ┌──────────┐
          │APPROVED  │  gate.approve()
          └────┬─────┘
               v
          ┌──────────┐     실패 시
          │EXECUTING │  ───────────┐
          └────┬─────┘             v
               v           ┌────────────┐
          ┌──────────┐     │ RETRYING   │ (max 2회)
          │VERIFYING │     └──────┬─────┘
          └────┬─────┘            │ 재실패 시
               v                  v
          ┌──────────┐     ┌───────────┐
          │ CLOSING  │     │ ROLLBACK  │
          └────┬─────┘     └─────┬─────┘
               v                 v
          ┌──────────┐     ┌──────────────┐
          │ CLOSED   │     │CLOSED_FAILED │
          └──────────┘     └──────────────┘
```

**상태 전이 규칙:**

```python
VALID_TRANSITIONS = {
    "intake":             ["classified"],
    "classified":         ["context_bound"],
    "context_bound":      ["policy_resolved"],
    "policy_resolved":    ["preparing"],
    "preparing":          ["awaiting_approval", "approved"],  # quick_fix는 auto-approve
    "awaiting_approval":  ["approved", "closed_failed"],      # 사용자 거부 가능
    "approved":           ["executing"],
    "executing":          ["verifying", "retrying", "rollback"],
    "retrying":           ["executing", "rollback"],           # max 2회
    "verifying":          ["closing", "rollback"],
    "closing":            ["closed"],
    "rollback":           ["closed_failed"],
    "closed":             [],
    "closed_failed":      [],
}
```

### 3.3 신규 모듈 상세 설계

#### 3.3.1 `core/control/intake.py` — ControlPlaneIntake

```python
@dataclass
class NormalizedRequest:
    raw_input: str
    route: dict               # RequestRouter.route() 결과 (pipeline, intent, confidence)
    work_kind: str            # "new_project" | "maintenance" | "bugfix" | "feature_update" | "refactor"
    issue_kind: str           # "incident" | "planned_update" | "regression" | "backlog_item" | "research_task"
    issue_context: dict       # IssueContextManager 출력
    execution_policy: str     # "quick_fix" | "standard_update" | "deep_update" | "full_bootstrap"
    continuity_snapshot: dict # ContinuitySnapshotBuilder 출력
    change_impact: dict       # ChangeImpactProfiler 출력 (maintenance only)
    state: str                # 현재 상태 머신 단계

class ControlPlaneIntake:
    """유지보수 요청의 정규화 입구. 기존 RequestRouter를 감싸되 대체하지 않음."""

    def normalize(self, task_input: str, workspace: str, route: dict) -> NormalizedRequest:
        """
        1. RequestRouter.route() 결과를 받음 (기존 흐름 유지)
        2. WorkKindClassifier로 2차 분류
        3. IssueContextManager로 issue binding
        4. ContinuitySnapshotBuilder로 이전 상태 집계
        5. ExecutionPolicyResolver로 실행 정책 결정
        6. ChangeImpactProfiler로 영향 범위 분석 (maintenance일 때만)
        """
```

**설계 원칙:**
- `RequestRouter.route()` 결과를 입력으로 받음 (호출하지 않음 — 기존 호출 흐름 보존)
- 반환값은 기존 route dict를 포함하는 상위 구조체

#### 3.3.2 `core/control/work_kind.py` — WorkKindClassifier

```python
# 2차 분류 규칙 (IntentGate 결과 + 추가 신호 조합)
WORK_KIND_RULES = {
    # (intent, 키워드 존재, 기존 board 존재) → work_kind
    ("debugging", True,  True):  "bugfix",
    ("debugging", True,  False): "bugfix",
    ("refactoring", True, True): "refactor",
    ("greenfield", True, False): "new_project",
    ("greenfield", True, True):  "feature_update",
    # 기존 board가 있으면 기본적으로 maintenance
    ("*", "*", True):            "maintenance",
}

ISSUE_KIND_RULES = {
    "bugfix":         "incident",
    "refactor":       "planned_update",
    "feature_update": "planned_update",
    "maintenance":    "planned_update",
    "new_project":    "backlog_item",
}

class WorkKindClassifier:
    def classify(self, route: dict, workspace: str) -> tuple[str, str]:
        """
        Returns: (work_kind, issue_kind)

        판단 신호:
          1. IntentGate intent 결과 (route["intent"])
          2. workspace에 project_board_state.json 존재 여부 (기존 프로젝트?)
          3. git diff 존재 여부 (진행 중인 변경?)
          4. task_input 키워드 ("수정", "업데이트", "버그", "추가" 등)
        """
```

**충돌 회피:** `IntentGate.INTENT_CATEGORIES`를 수정하지 않음. intent 값을 읽기만 함.

#### 3.3.3 `core/control/issue_context.py` — IssueContextManager

```python
@dataclass
class IssueContext:
    issue_id: str              # "local-{date}-{seq}" 또는 "gh-{number}"
    work_kind: str
    issue_kind: str
    title: str
    workspace: str
    risk_level: str            # "low" | "normal" | "high" | "critical"
    source: str                # "user_request" | "github_issue" | "hook_event" | "supervisor_retry"
    parent_issue_id: str       # 상위 이슈 (optional, NEW in v2)
    affected_files: list[str]  # ChangeImpactProfiler 결과 (NEW in v2)
    affected_modules: list[str]# 영향받는 module_id 목록 (NEW in v2)
    created_at: str
    metadata: dict             # 확장 가능한 추가 정보

class IssueContextManager:
    """file-backed issue context 관리. 외부 tracker는 adapter로 붙임."""

    CONTEXT_DIR = ".af_runtime/control"

    def bind(self, normalized: NormalizedRequest) -> IssueContext:
        """issue_context.json 생성 또는 기존 이슈에 연결."""

    def link_external(self, issue_id: str, external_url: str) -> None:
        """GitHub/Jira 이슈와 양방향 링크."""

    def get_active(self, workspace: str) -> list[IssueContext]:
        """현재 workspace의 미해결 이슈 목록."""
```

**v1 대비 개선:**
- `parent_issue_id`: 이슈 간 상하위 관계 (regression → 원본 bugfix 연결)
- `affected_files/modules`: Change Impact Profiler 결과 인라인 저장

#### 3.3.4 `core/control/change_impact.py` — ChangeImpactProfiler (NEW)

```python
@dataclass
class ImpactProfile:
    affected_files: list[str]        # git diff 또는 사용자 명시
    affected_modules: list[str]      # board modules 중 매칭되는 것
    affected_roles: list[str]        # module→role 역추적
    affected_task_ids: list[str]     # module→task 역추적
    evidence_scope: list[str]        # targeted evidence 수집 범위 (디렉토리)
    regression_test_scope: list[str] # 회귀 테스트 대상 (파일 패턴)
    blast_radius: str                # "isolated" | "module" | "cross_module" | "system_wide"

class ChangeImpactProfiler:
    """변경 요청의 영향 범위를 분석한다."""

    def profile(self, task_input: str, workspace: str, board: dict | None = None) -> ImpactProfile:
        """
        영향 분석 흐름:
          1. git diff (staged + unstaged)에서 affected_files 추출
          2. task_input에서 파일/모듈 명시 추출 (키워드 매칭)
          3. board의 modules[].task_ids → 파일 경로 매핑 (artifacts 필드 활용)
          4. affected_files → module 매칭 → role 매칭
          5. blast_radius 판정:
             - 1개 module 내 → "isolated"
             - 2-3개 module → "module"
             - 4+ module 또는 cross-dependency → "cross_module"
             - core/ 변경 또는 10+ files → "system_wide"
        """
```

**file→module 매핑 전략:**

```python
def _file_to_modules(self, filepath: str, board: dict) -> list[str]:
    """board.tasks[].artifacts와 filepath를 매칭한다."""
    matched = []
    for task in (board.get("tasks") or []):
        for artifact in (task.get("artifacts") or []):
            if self._path_matches(filepath, artifact):
                mid = task.get("module_id", "")
                if mid and mid not in matched:
                    matched.append(mid)
    return matched
```

#### 3.3.5 `core/control/continuity_snapshot.py` — ContinuitySnapshotBuilder

```python
@dataclass
class ContinuitySnapshot:
    timestamp: str
    workspace: str
    # manifest 소스
    orchestrator_status: str           # manifest.state_board.current_status
    completed_tasks: int
    failed_tasks: int
    interrupted_tasks: int
    # board 소스
    board_pending: int
    board_in_progress: int
    board_completed: int
    board_failed: int
    board_blocked: int
    # resume brief 소스
    resume_brief_excerpt: str          # 1600자 제한
    # session 소스
    latest_session_provider: str
    latest_session_run_id: str
    # 집계 판정
    overall_health: str                # "healthy" | "degraded" | "interrupted" | "unknown"
    conflict_notes: list[str]          # 소스 간 불일치 설명

class ContinuitySnapshotBuilder:
    """4개 소스를 읽기 전용으로 집계한다. 기존 writer를 대체하지 않음."""

    # merge 정책: 소스 간 충돌 시 우선순위
    _PRIORITY = {
        "status": ["board", "manifest", "session", "resume_brief"],
        "task_count": ["board", "manifest"],
    }

    def build(self, workspace: str) -> ContinuitySnapshot:
        """
        merge 정책:
          1. task 수: board 우선 (board가 task-level source of truth)
          2. 전체 상태: board completion 기반 판정, manifest는 보조
          3. manifest가 "crashed"이면 override (비정상 종료 신호)
          4. 불일치 시 conflict_notes에 기록 (silent하게 무시하지 않음)
        """

    def _resolve_health(self, manifest_status: str, board_summary: dict) -> str:
        """overall_health 판정 규칙."""
        if manifest_status == "crashed":
            return "interrupted"
        if board_summary.get("failed_tasks", 0) > 0:
            return "degraded"
        if board_summary.get("in_progress_tasks", 0) > 0:
            return "degraded"  # 비정상 재개 (in_progress는 reset되어야 함)
        if manifest_status in ("completed", ""):
            return "healthy"
        return "unknown"
```

**v1 대비 개선:**
- merge 정책 명시 (board 우선, manifest "crashed" override)
- `conflict_notes`로 불일치를 기록 (디버깅 지원)
- `overall_health` 집계 판정 추가

#### 3.3.6 `core/control/execution_policy.py` — ExecutionPolicyResolver

```python
@dataclass
class ExecutionPolicy:
    policy_id: str
    execution_policy: str           # "quick_fix" | "standard_update" | "deep_update" | "full_bootstrap"
    requires_approval: bool
    requires_work_item_docs: bool
    requires_agent_qa: bool
    requires_parallel_critique: bool # NEW in v2
    requires_regression_test: bool   # NEW in v2
    skippable_stages: list[str]      # NEW in v2: 스킵 가능한 파이프라인 단계 목록
    max_retries: int                 # NEW in v2

# ExecutionPolicy → 파이프라인 스테이지 매핑 (v1에서 누락, v2에서 추가)
POLICY_STAGE_MAP = {
    "quick_fix": {
        "requires_approval":          False,
        "requires_work_item_docs":    False,
        "requires_agent_qa":          False,
        "requires_parallel_critique": False,
        "requires_regression_test":   True,
        "skippable_stages":           ["evidence_retry", "critique", "agent_qa", "convergence_loop"],
        "max_retries":                1,
    },
    "standard_update": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          "auto",  # iMAD gate로 자동 결정
        "requires_parallel_critique": False,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                2,
    },
    "deep_update": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          True,
        "requires_parallel_critique": True,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                2,
    },
    "full_bootstrap": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          True,
        "requires_parallel_critique": True,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                3,
    },
}

class ExecutionPolicyResolver:
    def resolve(self, work_kind: str, risk_level: str, blast_radius: str,
                continuity_health: str) -> ExecutionPolicy:
        """
        판정 규칙:
          1. work_kind == "bugfix" AND risk_level == "low" AND blast_radius == "isolated"
             → quick_fix
          2. work_kind in ("maintenance", "feature_update") AND risk_level != "critical"
             → standard_update
          3. risk_level == "critical" OR blast_radius == "system_wide"
             → deep_update
          4. work_kind == "new_project"
             → full_bootstrap (기존 ProjectPipeline.prepare() 전체)
        """
```

**v1 대비 개선:**
- `POLICY_STAGE_MAP`으로 각 policy의 정확한 파이프라인 동작 차이를 명시
- `blast_radius`를 판정 입력으로 추가 (ChangeImpactProfiler 연동)
- `requires_regression_test` 필드 추가

#### 3.3.7 `core/control/run_ledger.py` — RunLedger

```python
@dataclass
class LedgerEntry:
    run_id: str
    issue_id: str
    workspace: str
    pipeline: str              # "project" | "single" (기존 값 그대로)
    work_kind: str
    execution_policy: str
    work_item_slug: str
    state: str                 # 상태 머신 현재 상태
    board_summary: dict        # {"pending": N, "in_progress": N, ...}
    change_impact_summary: str # blast_radius
    started_at: str
    updated_at: str
    closed_at: str             # 종료 시점 (빈 문자열이면 미종료)
    outcome: str               # "success" | "partial" | "failed" | "rolled_back" | ""

class RunLedger:
    """append-only JSONL journal. 기존 manifest를 대체하지 않음."""

    LEDGER_PATH = ".af_runtime/control/run_ledger.jsonl"

    def append(self, entry: LedgerEntry) -> None:
        """원자적 append (file lock + flush)."""

    def get_active_runs(self, workspace: str) -> list[LedgerEntry]:
        """closed_at이 빈 문자열인 항목 반환."""

    def close_run(self, run_id: str, outcome: str) -> None:
        """마지막 엔트리에 closed_at + outcome 기록."""

    def conflict_check(self, affected_files: list[str]) -> list[LedgerEntry]:
        """진행 중인 다른 run이 같은 파일을 건드리는지 확인. (G5 해결)"""
```

**v1 대비 개선:**
- `conflict_check()`: 동시 유지보수 충돌 감지 (v1 누락 G5 해결)
- `change_impact_summary`: blast radius 기록으로 이력 추적
- `outcome` 필드: 종료 유형 분류

#### 3.3.8 `core/control/supervisor.py` — RuntimeSupervisor

```python
class RuntimeSupervisor:
    """오케스트레이터 바깥의 실행 감독자. DynamicOrchestrator를 대체하지 않고 감싸는 역할."""

    # heartbeat 설정 (v1 누락 G6 해결)
    HEARTBEAT_INTERVAL_SEC = 30
    HEARTBEAT_TIMEOUT_SEC = 120     # 이 시간 동안 board 변경 없으면 stall 판정
    MAX_RETRIES = 2

    def supervise(self, normalized: NormalizedRequest, prepared: PreparedProject) -> dict:
        """
        실행 감독 흐름:
          1. RunLedger에 시작 기록
          2. DynamicOrchestrator.run_project() 호출 (기존 그대로)
          3. heartbeat 모니터링 (board_state 주기적 확인)
          4. stall 감지 시: 실패한 task를 retry 또는 skip
          5. 최종 결과를 verification 단계로 전달
          6. RunLedger에 종료 기록
        """

    def _check_heartbeat(self, workspace: str, last_change_at: str) -> str:
        """
        Returns: "alive" | "stalled" | "completed"

        판정:
          - board에 변경 있음 (updated_at > last_check) → "alive"
          - HEARTBEAT_TIMEOUT_SEC 초과 무변경 → "stalled"
          - board_is_complete() == True → "completed"
        """

    def _handle_stall(self, workspace: str, stalled_tasks: list[dict]) -> str:
        """
        Returns: "retried" | "skipped" | "aborted"

        stall 대응:
          1. in_progress 태스크를 pending으로 리셋
          2. retry_count < MAX_RETRIES → 재시도
          3. retry_count >= MAX_RETRIES → skip 후 다음 태스크 진행
          4. 전체 stall (모든 태스크 멈춤) → abort
        """
```

#### 3.3.9 `core/control/lifecycle_bridge.py` — CanonicalLifecycleBridge

```python
# Canonical Event Name 매핑
CANONICAL_EVENTS = {
    # Provider native name → canonical name
    "SessionStart":       "session_start",
    "BeforeAgent":        "session_start",      # Gemini equivalent
    "UserPromptSubmit":   "prompt_submit",
    "PreCompact":         "pre_compact",
    "PreCompress":        "pre_compact",         # Gemini equivalent
    "Stop":               "session_end",
    "SessionEnd":         "session_end",
    "AfterAgent":         "after_agent",         # Gemini only
}

@dataclass
class CanonicalEvent:
    canonical_name: str     # "session_start" | "prompt_submit" | "pre_compact" | "session_end" | "after_agent"
    provider_id: str        # 원본 프로바이더
    native_name: str        # 원본 hook 이름 (보존)
    timestamp: str
    workspace: str
    run_id: str
    payload: dict           # provider-specific 데이터

class CanonicalLifecycleBridge:
    """provider-specific hook을 canonical event로 변환. provider native 이름은 보존."""

    def normalize_event(self, provider_id: str, native_name: str,
                        payload: dict, workspace: str, run_id: str) -> CanonicalEvent:
        """native hook 이름을 canonical로 변환. 미등록 이름은 그대로 pass-through."""

    def on_canonical_event(self, event: CanonicalEvent) -> None:
        """
        canonical event 처리:
          - session_start: ContinuitySnapshot 갱신
          - pre_compact: 현재 진행 상태 checkpoint 저장
          - session_end: RunLedger 갱신, resume_brief 갱신 트리거
        """
```

**충돌 회피:** `session_adapter.handle_hook_event()`를 교체하지 않음. bridge는 session_adapter 호출 후 추가 처리만 수행.

#### 3.3.10 `core/control/maintenance_pipeline.py` — MaintenancePipeline

```python
class MaintenancePipeline:
    """유지보수 요청을 ProjectPipeline.prepare()로 위임하는 컴포지션 래퍼.

    ProjectPipeline을 서브클래싱하지 않음 (prepare() 468줄 복제 위험).
    대신 prepare() 전후에 control logic을 추가한다.
    """

    def __init__(self, project_pipeline: ProjectPipeline, control_intake: ControlPlaneIntake):
        self._pipeline = project_pipeline    # 기존 파이프라인 인스턴스
        self._control = control_intake

    def prepare(self, normalized: NormalizedRequest) -> PreparedProject:
        """
        흐름:
          1. ExecutionPolicy에 따라 스킵할 단계 결정
          2. quick_fix인 경우 minimal prepare (brief만 생성, work-item docs 스킵)
          3. standard_update/deep_update인 경우 ProjectPipeline.prepare() 위임
          4. checkpoint 저장
          5. ImpactProfile 기반 evidence 수집 범위 제한
        """

    def execute(self, prepared: PreparedProject, normalized: NormalizedRequest) -> dict:
        """
        흐름:
          1. RunLedger conflict check (동시 작업 충돌 확인)
          2. ExecutionPolicy에 따라 approval 스킵 여부 결정
          3. Supervisor.supervise() 호출 (DynamicOrchestrator 감싸기)
          4. RegressionSafetyGate 실행 (policy에 따라)
          5. Verification + closeout
        """

    def _minimal_prepare(self, normalized: NormalizedRequest) -> PreparedProject:
        """quick_fix 전용: brief만 생성, work-item docs 및 full board 스킵."""
```

**핵심 설계 결정:**
- **컴포지션** (Has-A), 상속 아님 (Is-A) — `ProjectPipeline.prepare()`의 468줄 메서드를 복제하지 않음
- `quick_fix` 경로는 `_minimal_prepare()`로 경량화
- 나머지 policy는 `ProjectPipeline.prepare()`를 그대로 호출

### 3.4 Regression Safety Gate (NEW)

```python
# core/control/ 또는 core/ 에 배치 (Quality Plane 영역)
class RegressionSafetyGate:
    """유지보수 후 회귀 안전성을 검증하는 gate."""

    def check(self, workspace: str, impact_profile: ImpactProfile,
              execution_policy: ExecutionPolicy) -> dict:
        """
        Returns: {"pass": bool, "test_results": dict, "warnings": list}

        검증 흐름:
          1. impact_profile.regression_test_scope에 해당하는 테스트 실행
             - pytest discovery: 영향받는 파일에 대응하는 test_*.py 매칭
          2. blast_radius == "system_wide"면 전체 테스트 실행
          3. 테스트 실패 시 pass=False + 실패 목록
          4. 테스트 없으면 warning 태그 + pass=True (테스트 부재가 차단 사유는 아님)
        """

    def _discover_tests(self, affected_files: list[str], workspace: str) -> list[str]:
        """affected_files에 대응하는 테스트 파일을 찾는다.

        매칭 규칙:
          core/foo.py → tests/test_foo.py
          core/bar/baz.py → tests/test_bar_baz.py OR tests/bar/test_baz.py
        """
```

### 3.5 Rollback 전략 (NEW, v1 누락 G2 해결)

```python
@dataclass
class RollbackPlan:
    strategy: str              # "git_revert" | "checkpoint_restore" | "manual"
    checkpoint_path: str       # .checkpoint/{stage}.json
    git_ref_before: str        # 실행 시작 전 git HEAD
    affected_files: list[str]
    rollback_instructions: list[str]

class RollbackManager:
    """유지보수 실패 시 안전한 롤백을 수행한다."""

    def create_rollback_point(self, workspace: str) -> RollbackPlan:
        """실행 시작 전 git ref + checkpoint를 기록."""

    def execute_rollback(self, plan: RollbackPlan) -> dict:
        """
        전략별 롤백:
          - git_revert: git revert --no-commit {commits_since_ref}
          - checkpoint_restore: .checkpoint 파일에서 이전 상태 복원
          - manual: rollback_instructions를 사용자에게 제시
        """
```

---

## 4. 데이터 저장 구조

### 4.1 파일 레이아웃

```text
{workspace}/
├── .af_manifest.json                      # 기존 유지 (DynamicOrchestrator writer)
├── .af_runtime/
│   ├── cli_sessions/                      # 기존 유지 (session_adapter writer)
│   │   ├── claude_run_xxx.json
│   │   └── claude_run_xxx_events.jsonl
│   └── control/                           # 신규 (Control Plane sidecar)
│       ├── issue_context.json             # 현재 이슈 정보
│       ├── execution_policy.json          # 실행 정책
│       ├── run_ledger.jsonl               # append-only 실행 이력
│       ├── continuity_snapshot.json       # 집계된 읽기 모델
│       ├── change_impact.json             # 영향 분석 결과
│       ├── rollback_plan.json             # 롤백 지점
│       ├── maintenance_state.json         # 상태 머신 현재 상태
│       └── checkpoints/
│           └── {run_id}.json
├── .checkpoint/                           # 기존 유지 (PipelineStageGuard checkpoint)
├── planning/
│   ├── research_evidence.json             # 기존 유지
│   ├── project_brief.json                 # 기존 유지
│   └── role_plan.json                     # 기존 유지
├── project_board_state.json               # 기존 유지 (task board writer)
├── resume_brief.md                        # 기존 유지 (resume_brief writer)
└── docs/work-items/{slug}/
    ├── feature-plan.md                    # 기존 유지
    ├── feature-spec.md                    # 기존 유지
    ├── implementation-design.md           # 기존 유지
    ├── implementation-tasks.md            # 기존 유지
    ├── approval-gate.md                   # 기존 유지
    └── maintenance-runbook.md             # 신규 optional sidecar
```

### 4.2 코드 레이아웃

```text
core/
├── control/                               # 신규 Control Plane 계층
│   ├── __init__.py
│   ├── intake.py                          # ControlPlaneIntake, NormalizedRequest
│   ├── work_kind.py                       # WorkKindClassifier
│   ├── issue_context.py                   # IssueContextManager, IssueContext
│   ├── change_impact.py                   # ChangeImpactProfiler, ImpactProfile (NEW)
│   ├── continuity_snapshot.py             # ContinuitySnapshotBuilder, ContinuitySnapshot
│   ├── execution_policy.py                # ExecutionPolicyResolver, ExecutionPolicy, POLICY_STAGE_MAP
│   ├── run_ledger.py                      # RunLedger, LedgerEntry
│   ├── supervisor.py                      # RuntimeSupervisor
│   ├── lifecycle_bridge.py                # CanonicalLifecycleBridge, CanonicalEvent
│   ├── maintenance_pipeline.py            # MaintenancePipeline
│   ├── maintenance_state.py               # MaintenanceStateMachine, VALID_TRANSITIONS (NEW)
│   ├── rollback.py                        # RollbackManager, RollbackPlan (NEW)
│   └── regression_gate.py                 # RegressionSafetyGate (NEW)
├── project_pipeline.py                    # 수정 없음 (래퍼가 호출만 함)
├── request_router.py                      # 수정 없음 (결과를 intake가 읽음)
├── intent.py                              # 수정 없음
├── dynamic_orchestrator.py                # 수정 없음 (supervisor가 감싸기만 함)
├── approval_gate.py                       # 수정 없음
└── ...
```

---

## 5. 구현 순서 (수정된 Phase 계획)

### Phase 1: 무충돌 메타데이터 기반 (v1과 동일 + 보강)

**범위:**
- `core/control/__init__.py`
- `core/control/work_kind.py` — WorkKindClassifier
- `core/control/issue_context.py` — IssueContextManager
- `core/control/execution_policy.py` — ExecutionPolicyResolver + POLICY_STAGE_MAP
- `core/control/run_ledger.py` — RunLedger
- `core/control/maintenance_state.py` — MaintenanceStateMachine + VALID_TRANSITIONS
- `core/control/change_impact.py` — ChangeImpactProfiler

**원칙:**
- 기존 모듈 import 없음 (기존 코드 무수정)
- 입출력은 모두 `.af_runtime/control/` 파일
- 테스트는 독립적으로 가능 (기존 코드 의존성 없음)

**검증 기준:**
- WorkKindClassifier가 기존 IntentGate 결과를 올바르게 2차 분류하는가
- ExecutionPolicy의 POLICY_STAGE_MAP이 모든 policy에 대해 정의되어 있는가
- RunLedger의 conflict_check가 동시 작업 파일 중복을 감지하는가
- MaintenanceStateMachine이 허용되지 않은 전이를 거부하는가

### Phase 2: Continuity Snapshot 집계기 (v1과 동일 + merge 정책 추가)

**범위:**
- `core/control/continuity_snapshot.py`
- `core/control/lifecycle_bridge.py`

**원칙:**
- 기존 writer(manifest_store, resume_brief, session_adapter)를 교체하지 않음
- 읽기 전용 집계만 수행
- merge 정책은 "board 우선, manifest crashed override" 원칙

**검증 기준:**
- 4개 소스가 모두 있을 때 정상 집계되는가
- 소스 간 불일치 시 conflict_notes에 기록되는가
- manifest "crashed" 상태가 board 정상보다 우선하는가

### Phase 3: MaintenancePipeline 래퍼 + Rollback (v1 + 신규)

**범위:**
- `core/control/maintenance_pipeline.py`
- `core/control/intake.py` — ControlPlaneIntake (Phase 1 모듈 통합)
- `core/control/rollback.py` — RollbackManager

**원칙:**
- `ProjectPipeline`을 서브클래싱하지 않고 컴포지션으로 감쌈
- `quick_fix`는 `_minimal_prepare()`로 경량 경로 제공
- 실행 시작 전 rollback point 기록

**검증 기준:**
- MaintenancePipeline.prepare()가 ProjectPipeline.prepare()를 정상 위임하는가
- quick_fix 경로가 work-item 문서 생성을 스킵하는가
- rollback point가 git ref + checkpoint를 올바르게 기록하는가

### Phase 4: Supervisor + Regression Gate (v1 + 신규)

**범위:**
- `core/control/supervisor.py` — RuntimeSupervisor
- `core/control/regression_gate.py` — RegressionSafetyGate

**원칙:**
- Supervisor는 DynamicOrchestrator.run_project()를 감싸기만 함
- heartbeat 기반 stall 감지 (30초 간격, 120초 무변경 시 stall)
- Regression gate는 변경 영향 범위에 해당하는 테스트만 실행

**검증 기준:**
- Supervisor가 DynamicOrchestrator 정상 완료 시 간섭하지 않는가
- stall 감지 후 retry→skip→abort 시퀀스가 작동하는가
- RegressionSafetyGate가 영향받는 파일에 대응하는 테스트를 정확히 발견하는가

### Phase 5: Issue Tracker / Memory Consolidation (v1과 동일)

**범위:** 외부 통합 확장 (optional)

---

## 6. 절대 건드리면 안 되는 것 (v1 + 추가)

v1 목록을 그대로 유지하되 2개 추가:

1. `RequestRouter.route()` 의 `pipeline` 반환 집합
2. `IntentGate.INTENT_CATEGORIES`
3. `project_board_state.json` 의 기존 `status` 의미
4. `DynamicOrchestrator.run_project()` 의 핵심 책임 범위
5. `resume_brief.md` 기본 포맷
6. `session_adapter` 가 provider native hook 이름을 쓰는 방식
7. `ApprovalGate` 의 필수 문서 해시 검증 방식
8. **`ProjectPipeline.prepare()` 의 메서드 시그니처** (v2 추가)
9. **`next_board_tasks()` 의 status 필터링 로직 (`pending/blocked` 만 dispatch)** (v2 추가)

---

## 7. v1 → v2 변경 비교 요약

| 영역 | v1 | v2 | 변경 이유 |
|------|----|----|----------|
| 생명주기 | 선형 12단계 | 상태 머신 (14 states) | 재개/분기/롤백 명시적 관리 |
| Change Impact | "profiler 필요" 언급만 | `ChangeImpactProfiler` 상세 설계 | 영향 범위 자동 분석이 유지보수 핵심 |
| ExecutionPolicy | 4 policy 이름만 | 이름 + `POLICY_STAGE_MAP` 상세 | 파이프라인 동작 차이 불명확 → 명시 |
| Continuity merge | "집계기 도입" | merge 정책 + conflict_notes | 소스 간 충돌 해소 규칙 부재 → 명시 |
| Rollback | 미언급 | `RollbackManager` + `RollbackPlan` | 부분 실패 대응 전략 부재 → 추가 |
| 동시 충돌 | 미언급 | `RunLedger.conflict_check()` | 같은 파일 동시 작업 충돌 위험 → 감지 |
| Supervisor | "outer supervisor" 언급 | heartbeat + stall 감지 + retry/skip/abort | 구체적 동작 미정의 → 상세 설계 |
| Regression | "change impact checklist" 언급 | `RegressionSafetyGate` + test discovery | 회귀 안전성 보장 메커니즘 미정의 → 추가 |
| 파이프라인 확장 | "래퍼 또는 확장" | 컴포지션 래퍼만 허용 (서브클래싱 금지) | prepare() 468줄 복제 위험 → 명시적 금지 |
| 파일 구조 | 9 files | 13 files (+4 신규) | 누락 영역 모듈화 |
