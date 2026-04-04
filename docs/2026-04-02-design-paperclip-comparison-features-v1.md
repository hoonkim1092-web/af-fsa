# Paperclip 비교 3대 기능 설계서

> 작성일: 2026-04-02
> 상태: **설계 완료 / 미구현** (3건 모두 코드 미작성)
> 배경: https://paperclip.ing/ 와 agent-factory 비교 분석 후 도출된 미구현 기능 3건

## Context

Paperclip.ing은 "AI 회사 운영 플랫폼"으로, 오케스트레이션을 DB/코드 로직으로 처리하여 LLM 토큰을 실제 작업에만 사용한다. agent-factory와 비교하여 부족한 3가지 기능을 설계한다:

1. **에이전트별 비용 추적** — 누가 토큰 얼마 썼는지 영속 기록
2. **`af serve` 데몬 모드** — 할 일 있으면 자동 기상, 없으면 수면 (이벤트 기반)
3. **다중 프로젝트 격리** — 프로젝트 간 메모리/상태 누수 방지

### Paperclip vs Agent Factory 비교

| | Paperclip | Agent Factory |
|---|---|---|
| **오케스트레이션** | DB 쿼리 (토큰 0) | ~~LLM 매 사이클~~ → rule-based 디스패치 (v1.0.3에서 개선) |
| **토큰 예산** | 월별 예산 + 자동 일시정지 | 글로벌 RunBudget (v1.0.3에서 추가) |
| **에이전트별 비용** | 있음 | **없음** ← Feature 1 |
| **실행 방식** | 항상 돌아가는 서버 (heartbeat) | CLI 1회 실행 |
| **자동 기상** | heartbeat 스케줄 | **없음** ← Feature 2 |
| **다중 프로젝트** | 완전 격리 (DB) | 파일만 격리, 메모리 누수 | ← Feature 3 |
| **감사 로그** | 불변 로그 | RunLedger (JSONL) |
| **상태 저장** | PostgreSQL | .af_manifest.json + autosave (v1.0.3에서 추가) |

### 이미 구현된 것 (v1.0.3)

| Paperclip 기능 | agent-factory 대응 | 상태 |
|---|---|---|
| 토큰 예산 관리 | `RunBudget` (core/run_budget.py) | ✅ 구현 완료 |
| 규칙 기반 태스크 분배 | sparse governor `_dispatch_from_board()` | ✅ 구현 완료 |
| 상태 저장/재개 | autosave + `--resume-chat` | ✅ 구현 완료 |
| 자동 재시도 | retry count + evaluator | ✅ 기존 존재 |
| 실패 분석 | StrategyEvaluator + FailureClassifier | ✅ 기존 존재 |
| 스킬 진화 | SkillSelfEvolutionHook + ISELoop | ✅ 기존 존재 |
| Heartbeat 감시 | RuntimeSupervisor 30초 주기 | ✅ 기존 존재 |

구현 순서: **1 → 3 → 2** (데몬이 1과 3에 의존)

---

## Feature 1: 에이전트별 비용 추적

### 현재 상태

- `RunBudget` (core/run_budget.py): 글로벌 in-memory 토큰 카운터 — 에이전트 구분 없음
- `AgentRunner.run()` 결과: `{ok, reason, latency_ms, approval_rejects}` — 토큰 필드 없음
- `RunLedger` (core/control/run_ledger.py): append-only JSONL — 토큰 필드 없음
- `state_board` (dynamic_orchestrator.py): completed/failed 리스트 — 비용 없음
- `SkillFeedback` (core/skill_feedback.py): `data/skill-usage.jsonl` — 스킬별, 에이전트별 아님

### 신규 파일: `core/cost_ledger.py`

RunLedger와 동일한 JSONL append 패턴. 저장: `{workspace}/.af_runtime/control/cost_ledger.jsonl`

```python
@dataclasses.dataclass
class AgentCostEntry:
    run_id: str
    agent_name: str
    agent_role: str
    task_id: str
    project_id: str
    workspace: str
    tokens_estimated: int    # 4-char≈1-token 휴리스틱
    latency_ms: int
    ok: bool
    ts: str                  # ISO timestamp

class CostLedger:
    def __init__(self, workspace: str): ...
    def record(self, entry: AgentCostEntry) -> None: ...     # JSONL append (thread-safe)
    def query_by_run(self, run_id: str) -> list[AgentCostEntry]: ...
    def top_consumers(self, limit: int = 10) -> list[tuple[str, int]]: ...  # (role, total_tokens)
    def total_for_run(self, run_id: str) -> int: ...
```

### 수정 파일

**`core/agent_runner.py` — `_flush_trace()` (line 863)**
```python
# 기존 RunBudget 기록 직후에 추가:
try:
    from core.cost_ledger import CostLedger, AgentCostEntry
    _tokens = max(1, len(_text) // 4)
    CostLedger(target_workspace).record(AgentCostEntry(
        run_id=run_id, agent_name=str(agent.get("name", "")),
        agent_role=str(agent.get("role", "")), task_id=safe_id(task_id),
        project_id=project_id, workspace=target_workspace,
        tokens_estimated=_tokens, latency_ms=int(result.get("latency_ms", 0)),
        ok=bool(result.get("ok")), ts=now_iso(),
    ))
except Exception:
    pass
```

또한 result dict에 `tokens_estimated` 필드 추가 (모든 return 경로).

**`core/dynamic_orchestrator.py` — state_board entries**
- `completed_entry`와 `failed_entry`에 `tokens_estimated` 필드 추가
- `result.get("tokens_estimated", 0)` 로 가져옴

**`core/control/run_ledger.py` — `close_run()`**
- `metadata["tokens_total"]` 에 CostLedger 합산값 기록 (선택적)

**`core/dashboard.py` — `append_dashboard_run()`**
- 엔트리에 `tokens_total` 필드 추가 (호출자가 전달)

**`af.spec`** — `'core.cost_ledger'` hiddenimport 추가

### 데이터 흐름

```
AgentRunner.run() 완료
    → _flush_trace()
        → RunBudget.record(text)          # 글로벌 합산 (in-memory)
        → CostLedger.record(entry)        # 에이전트별 JSONL 영속
    → result["tokens_estimated"] = N
        → state_board에 기록
        → RunLedger.metadata에 기록
```

---

## Feature 2: `af serve` 데몬 모드

### 현재 상태

- `RuntimeSupervisor` (core/control/supervisor.py): heartbeat 30초, stall 120초 — **실행 중 감시만**
- `MaintenancePipeline` (core/control/maintenance_pipeline.py): prepare→execute→verify 파이프라인
- `ControlPlaneIntake` (core/control/intake.py): work_kind 분류 (bugfix/feature/maintenance)
- `FSALoop` (core/fsa_loop.py): one-shot 5사이클
- `ISELoop` (core/ise_loop.py): infinite meta-loop (사용자 중단 가능)
- `OrchestratorManifestStore` (core/continuity/manifest_store.py): .af_manifest.json 기반 resume
- `BackgroundTaskManager` (core/concurrency.py): 범용 태스크 스케줄러
- **없는 것**: 외부 트리거, cron, 데몬 프로세스, 이벤트 기반 기상

### 핵심 개념: Paperclip과의 차이

```
Paperclip:  에이전트가 4시간마다 자동 기상 → 할 일 확인 → 실행 → 보고 → 잠듦
AF (현재):  사용자가 af run 실행 → 완료 → 종료 (수동)
AF (목표):  af serve → 할 일 감지 → 자동 기상 → 실행 → 잠듦 (자동)
```

단, Paperclip처럼 시간 기반이 아닌 **이벤트 기반**: 할 일 없으면 토큰 0.

### 신규 파일: `core/daemon.py`

```python
@dataclasses.dataclass
class DaemonConfig:
    projects: list[str]               # 감시 대상 프로젝트 ID 목록
    projects_root: str                # 프로젝트 루트 디렉토리
    check_interval_sec: int = 300     # 5분 (idle 시)
    active_interval_sec: int = 30     # 30초 (작업 감지 후)
    budget_per_wake: int = 0          # 0 = unlimited
    log_path: str = ""                # 로그 파일 경로

class WorkItem:
    source: str         # "board" | "mailbox" | "todo" | "stale_run"
    project_id: str
    workspace: str
    detail: str         # 요약

class WorkDetector:
    """프로젝트별 새 작업 감지."""
    def scan(self, workspace: str) -> list[WorkItem]:
        # 1. project_board에서 pending 태스크 확인 (next_board_tasks)
        # 2. mailbox에서 미처리 메시지 확인 (load_mailbox_messages)
        # 3. .todo.md mtime 변경 감지
        # 4. RunLedger에서 stale run 확인 (is_stale)

class AfDaemon:
    """이벤트 기반 데몬. 할 일 있으면 기상, 없으면 수면."""

    def __init__(self, config: DaemonConfig): ...

    def run_forever(self) -> None:
        """메인 루프: sleep → scan → execute → repeat."""
        # signal.signal(SIGINT/SIGTERM) → graceful shutdown
        while not self._shutdown:
            for project_id in config.projects:
                workspace = os.path.join(config.projects_root, project_id)
                items = self._detector.scan(workspace)
                if items:
                    self._wake(project_id, workspace, items)
            self._sleep_event.wait(timeout=self._current_interval)

    def _wake(self, project_id, workspace, items):
        """기상: 예산 설정 → 실행 → 비용 기록 → 상태 갱신."""
        # project_scope(workspace) 컨텍스트 (Feature 3)
        # set_run_budget(config.budget_per_wake) (Feature 1 연동)
        # MaintenancePipeline 또는 DynamicOrchestrator로 실행
        # CostLedger로 비용 기록

    def _write_status(self) -> None:
        """상태 파일 갱신: .af_runtime/daemon_status.json"""
```

### 신규 파일: `core/daemon_executor.py`

WorkItem → 적절한 파이프라인 라우팅:

```python
class DaemonExecutor:
    def execute(self, items: list[WorkItem], workspace: str) -> dict:
        # board tasks → MaintenancePipeline.execute()
        # mailbox messages → ControlPlaneIntake.normalize() → MaintenancePipeline
        # todo changes → FSALoop.run_mission()
        # stale runs → OrchestratorManifestStore.load_resume_state() → resume
```

### 수정: `run_factory_cli.py`

`serve` 서브커맨드 추가 (기존 subcommand 패턴 동일):

```python
if effective_argv and effective_argv[0] == "serve":
    _run_serve(effective_argv[1:])
    return

def _run_serve(argv):
    parser = argparse.ArgumentParser(description="Agent Factory Daemon")
    parser.add_argument("--projects", type=str, required=True, help="Comma-separated project IDs")
    parser.add_argument("--projects-root", type=str, help="Projects root override")
    parser.add_argument("--interval", type=int, default=300, help="Check interval seconds")
    parser.add_argument("--budget", type=int, default=0, help="Token budget per wake cycle")
    parser.add_argument("--log", type=str, default="", help="Log file path")
    args = parser.parse_args(argv)
    ...
    daemon = AfDaemon(config)
    daemon.run_forever()
```

### 상태 파일: `.af_runtime/daemon_status.json`

```json
{
  "state": "sleeping | scanning | executing",
  "pid": 12345,
  "started_at": "2026-04-02T...",
  "last_check_at": "2026-04-02T...",
  "last_work_at": "2026-04-02T...",
  "current_project": "minesweeper",
  "total_wakes": 42,
  "total_tokens_consumed": 150000,
  "projects": ["minesweeper", "tictactoe"]
}
```

### 동작 흐름

```
af serve --projects minesweeper,tictactoe --interval 300 --budget 50000
    │
    ▼
[Sleep 5분] → [Scan: minesweeper] → 할 일 있음?
    │                                    │
    │                              No → [Scan: tictactoe] → ...
    │                              Yes ↓
    │                         [Wake]
    │                           ├─ project_scope(workspace) 진입
    │                           ├─ set_run_budget(50000)
    │                           ├─ WorkItem 분류 → Pipeline 실행
    │                           ├─ CostLedger 기록
    │                           ├─ daemon_status.json 갱신
    │                           └─ project_scope 퇴장
    │                              ↓
    ▼                         [Sleep → 다음 스캔]
```

### 엣지 케이스

1. **실행 중 SIGINT**: `_shutdown = True` → 현재 태스크 완료 후 종료 (manifest 저장)
2. **budget 소진**: `is_exhausted()` → 현재 wake 즉시 중단, 다음 wake에서 reset
3. **모든 프로젝트 할 일 없음**: interval_sec 대기 후 재스캔 (토큰 0)
4. **프로젝트 디렉토리 없음**: 경고 출력, 스킵

---

## Feature 3: 다중 프로젝트 격리

### 현재 상태 (문제점)

| 컴포넌트 | 격리 상태 | 문제 |
|----------|----------|------|
| 파일 시스템 | OK | `projects/<id>/`로 물리 분리 |
| workspace 파라미터 | OK | 대부분 함수가 workspace 명시적 수신 |
| DynamicOrchestrator | OK | workspace-aware |
| Worker subprocess | OK | task.json으로 workspace 전달 |
| **config_paths.py** | **BAD** | 모듈 레벨 상수 → import 시점 고정 |
| **UnifiedMemoryFacade** | **BAD** | 프로세스 전역 singleton |
| **SkillFeedback** | **BAD** | `from config_paths import DATA_DIR` 모듈 레벨 |

### 신규 파일: `core/project_context.py`

```python
import threading
from dataclasses import dataclass
from contextlib import contextmanager

@dataclass
class ProjectContext:
    project_id: str
    workspace: str
    agents_dir: str
    runs_dir: str
    data_dir: str
    artifacts_dir: str
    skills_dir: str

    @classmethod
    def from_workspace(cls, workspace: str) -> "ProjectContext":
        """workspace 경로에서 모든 하위 경로를 파생."""
        project_id = os.path.basename(os.path.abspath(workspace))
        return cls(
            project_id=project_id, workspace=workspace,
            agents_dir=os.path.join(workspace, "agents"),
            runs_dir=os.path.join(workspace, "runs"),
            data_dir=os.path.join(workspace, "data"),
            artifacts_dir=os.path.join(workspace, "artifacts"),
            skills_dir=os.path.join(workspace, "skills"),
        )

    @classmethod
    def from_config_paths(cls) -> "ProjectContext":
        """기존 config_paths 모듈 레벨 상수에서 생성 (하위호환)."""
        from core import config_paths as cp
        return cls(
            project_id=cp.PROJECT_ID, workspace=cp.PROJECT_ROOT,
            agents_dir=cp.AGENTS_DIR, runs_dir=cp.RUNS_DIR,
            data_dir=cp.DATA_DIR, artifacts_dir=cp.ARTIFACTS_DIR,
            skills_dir=cp.PROJECT_SKILLS_DIR,
        )

# ── Thread-local 컨텍스트 스택 ──
_local = threading.local()

def push_project_context(ctx: ProjectContext) -> None:
    if not hasattr(_local, "stack"):
        _local.stack = []
    _local.stack.append(ctx)

def pop_project_context() -> ProjectContext | None:
    if hasattr(_local, "stack") and _local.stack:
        return _local.stack.pop()
    return None

def current_project_context() -> ProjectContext:
    """현재 스레드의 프로젝트 컨텍스트. 없으면 config_paths 폴백."""
    if hasattr(_local, "stack") and _local.stack:
        return _local.stack[-1]
    return ProjectContext.from_config_paths()

@contextmanager
def project_scope(workspace: str):
    """프로젝트 스코프 컨텍스트 매니저."""
    ctx = ProjectContext.from_workspace(workspace)
    push_project_context(ctx)
    try:
        yield ctx
    finally:
        pop_project_context()
```

### 수정 파일

**`core/config_paths.py`** — 추가 (기존 모듈 레벨 상수 유지):

```python
def get_project_paths(workspace: str | None = None) -> dict:
    """workspace 기준 경로 dict 반환. None이면 현재 모듈 상수 사용."""
    if workspace is None:
        return {"project_id": PROJECT_ID, "workspace": PROJECT_ROOT, ...}
    ws = os.path.abspath(workspace)
    return {
        "project_id": os.path.basename(ws),
        "workspace": ws,
        "agents_dir": os.path.join(ws, "agents"),
        "runs_dir": os.path.join(ws, "runs"),
        "data_dir": os.path.join(ws, "data"),
        "artifacts_dir": os.path.join(ws, "artifacts"),
        "skills_dir": os.path.join(ws, "skills"),
    }
```

**`core/memory_system/facade.py`** — 추가 (기존 singleton 유지):

```python
class UnifiedMemoryFacade:
    _shared_instance = None              # 기존 유지
    _project_instances: dict[str, "UnifiedMemoryFacade"] = {}  # 추가

    @classmethod
    def for_project(cls, project_id: str) -> "UnifiedMemoryFacade":
        """프로젝트별 인스턴스 반환. 없으면 생성."""
        if project_id not in cls._project_instances:
            cls._project_instances[project_id] = cls(project_id=project_id)
        return cls._project_instances[project_id]

    @classmethod
    def clear_project(cls, project_id: str) -> None:
        """프로젝트 메모리 인스턴스 해제."""
        cls._project_instances.pop(project_id, None)
```

**`core/agent_runner.py` (line ~944)** — 조건부 분기:

```python
# 기존: _mem_facade = UnifiedMemoryFacade(project_id=...); set_instance(...)
# 변경: workspace가 명시되면 for_project() 사용 (singleton 오염 방지)
if workspace:
    _mem_facade = UnifiedMemoryFacade.for_project(project_id)
else:
    _mem_facade = UnifiedMemoryFacade(project_id=project_id)
    UnifiedMemoryFacade.set_instance(_mem_facade)
```

**`af.spec`** — `'core.project_context'` hiddenimport 추가

### 격리 메커니즘

```
af serve (다중 프로젝트)
    │
    ├─ project_scope("projects/minesweeper")
    │   └─ current_project_context() → minesweeper 경로들
    │   └─ UnifiedMemoryFacade.for_project("minesweeper") → 전용 인스턴스
    │
    ├─ project_scope("projects/tictactoe")
    │   └─ current_project_context() → tictactoe 경로들
    │   └─ UnifiedMemoryFacade.for_project("tictactoe") → 전용 인스턴스
    │
    └─ 각 프로젝트 스코프 퇴장 시 원상복구
```

---

## 구현 순서 및 의존성

```
Phase 1: Feature 1 (에이전트별 비용 추적)
  └─ 의존 없음. core/cost_ledger.py 신규 + agent_runner.py 수정

Phase 2: Feature 3 (다중 프로젝트 격리)
  └─ 의존 없음. core/project_context.py 신규 + facade.py 수정

Phase 3: Feature 2 (af serve 데몬)
  └─ Feature 1의 CostLedger 사용 (비용 기록)
  └─ Feature 3의 project_scope() 사용 (프로젝트 전환)
  └─ core/daemon.py + core/daemon_executor.py 신규
```

각 Phase는 독립 배포 가능 (Phase 2 없이 Phase 3 구현 시 단일 프로젝트 데몬).

---

## 영향 범위

| 파일 | 변경 | Phase |
|------|------|-------|
| `core/cost_ledger.py` | **신규** | 1 |
| `core/agent_runner.py` | _flush_trace에 CostLedger 기록 추가 | 1 |
| `core/dynamic_orchestrator.py` | state_board에 tokens_estimated 추가 | 1 |
| `core/project_context.py` | **신규** | 2 |
| `core/config_paths.py` | `get_project_paths()` 함수 추가 | 2 |
| `core/memory_system/facade.py` | `for_project()` 클래스메서드 추가 | 2 |
| `core/daemon.py` | **신규** | 3 |
| `core/daemon_executor.py` | **신규** | 3 |
| `run_factory_cli.py` | `serve` 서브커맨드 추가 | 3 |
| `af.spec` | hiddenimports 4개 추가 | 1-3 |
| `Master_Blueprint.md` | §0, §3, §12 업데이트 | 1-3 |

---

## 검증 방법

1. **Feature 1**: `af run` 후 `.af_runtime/control/cost_ledger.jsonl` 확인 → 에이전트별 토큰 기록 존재
2. **Feature 3**: 두 프로젝트 순차 실행 → 메모리 facade 인스턴스가 다른지 확인
3. **Feature 2**: `af serve --projects test_project --interval 10` → 할 일 없으면 sleeping, 할 일 있으면 wake 확인
4. **회귀**: `pytest tests/test_dynamic_orchestrator_workspace_scope.py` 통과
