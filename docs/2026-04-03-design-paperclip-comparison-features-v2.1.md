# Provider-Agnostic Supervisor + Worker v2.1

> 작성일: 2026-04-03
> 상태: **설계 완료 / 미구현**
> 선행: v1 (2026-04-02), v2 (2026-04-03)
> 설계 원칙:
>   1. **흡수형 리팩터링** — 기존 시스템을 확장, 새 인프라 미생성
>   2. **Provider-Agnostic** — serve 레이어는 Claude/Gemini/Codex를 모른다
>   3. **AF-owned 경계** — checkpoint, cost, restart는 provider hook에 의존하지 않는다

## v2 → v2.1 변경 사항

| 항목 | v2 | v2.1 | 변경 사유 |
|------|-----|------|-----------|
| Worker spawn | `sys.executable + script path` | `build_worker_cmd()` — frozen/source 공통 | frozen exe에서 daemon 동작 불가 (v2 버그) |
| Graceful restart | `proc.terminate()` | stop-file 프로토콜 | Windows에서 terminate()는 강제 종료 (v2 버그) |
| Checkpoint 단위 | `save_step()` (호출 지점 미정) | AF-owned 5단계 경계 | provider hook 미의존, 구현 가능한 경계 |
| Cost 저장 | `agent_costs[]` 전체를 metadata | 요약만 metadata, 상세는 기존 로그 | 장기 wake 시 metadata 비대화 방지 |
| Provider 제어 | 미설계 (auto_configure 의존) | Provider Policy — 역할별 provider pool 명시 | 단일 provider 자동선택은 데몬에 부적합 |
| Crash 복구 | `_budget_consumed`만 checkpoint | `pending_cost_summary` + `budget_consumed` | 에이전트별 분해 정보 소실 방지 |

---

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────┐
│  af serve (DaemonSupervisor)                         │
│                                                      │
│  import: os, sys, json, subprocess, threading만      │
│  AF 핵심 코드 import 없음 → provider 완전 무지        │
│                                                      │
│  담당:                                               │
│   1. WorkDetector.scan() — 파일 시스템만 읽음          │
│   2. CodeWatcher — core/*.py mtime 추적              │
│   3. Worker spawn — build_worker_cmd() 사용           │
│   4. Stop-file 기반 협조적 종료                       │
│   5. daemon_status.json 갱신                         │
│   6. Provider Policy를 wake_task.json으로 전달        │
│                                                      │
│  모르는 것: ModelRouter, AgentRunner, RunBudget,       │
│            UnifiedMemoryFacade, configure_providers   │
└──────────┬──────────────────────────────────────────┘
           │ build_worker_cmd("daemon-worker", ...)
           │ wake_task.json → wake_result.json
           │
     ┌─────┴──────────────────────────────────────┐
     ▼                                             ▼
┌──────────────────────┐          ┌──────────────────────┐
│ Worker (프로젝트 A)    │          │ Worker (프로젝트 B)    │
│ (독립 프로세스)         │          │ (독립 프로세스)         │
│                       │          │                       │
│ 진입 시:               │          │ 진입 시:               │
│  configure_providers  │          │  configure_providers  │
│  (policy에서 받은 pool)│          │  (policy에서 받은 pool)│
│  set_run_budget()     │          │  set_run_budget()     │
│                       │          │                       │
│ 실행:                  │          │ 실행:                  │
│  ControlPlaneIntake   │          │  ControlPlaneIntake   │
│  → MaintenancePipeline│          │  → MaintenancePipeline│
│  → RuntimeSupervisor  │          │  → RuntimeSupervisor  │
│  → DynamicOrchestrator│          │  → DynamicOrchestrator│
│  → AgentRunner        │          │  → AgentRunner        │
│    (failover loop)    │          │    (failover loop)    │
│                       │          │                       │
│ 격리:                  │          │ 격리:                  │
│  자체 RunBudget       │          │  자체 RunBudget       │
│  자체 RunLedger       │          │  자체 RunLedger       │
│  자체 MemoryFacade    │          │  자체 MemoryFacade    │
└──────────────────────┘          └──────────────────────┘
```

---

## Part 0: Subprocess 계약 — spawn helper

### 문제

현재 `dynamic_orchestrator.py:567-571`:
```python
if getattr(sys, "frozen", False):
    cmd = [sys.executable, "worker", "--task-file", ...]    # af.exe worker
else:
    cmd = [sys.executable, worker_script, ...]              # python core/agent_worker.py
```

daemon-worker도 이 패턴을 따라야 한다. 두 곳에서 중복 구현하면 frozen/source 분기가 어긋날 수 있다.

### 설계

**수정: `core/utils.py`에 헬퍼 추가** (신규 파일 불필요)

```python
# core/utils.py — 추가

import os, sys
from pathlib import Path

_WORKER_SCRIPTS = {
    "worker": "core/agent_worker.py",
    "daemon-worker": "core/daemon_worker.py",
}

def build_worker_cmd(worker_type: str, *extra_args: str) -> list[str]:
    """frozen exe / source 공통 worker subprocess 명령어 생성.

    frozen:  ["C:/.../af.exe", "worker_type", *extra_args]
    source:  ["python", "C:/.../core/xxx.py", *extra_args]

    Args:
        worker_type: "worker" | "daemon-worker"
        *extra_args: 추가 CLI 인자 (e.g., "--task-file", path)
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, worker_type, *extra_args]
    else:
        factory_root = str(Path(__file__).parent.parent)
        script = _WORKER_SCRIPTS.get(worker_type)
        if not script:
            raise ValueError(f"unknown worker_type: {worker_type}")
        script_path = os.path.join(factory_root, script)
        return [sys.executable, script_path, *extra_args]
```

**수정: `core/dynamic_orchestrator.py:567-571`** — 기존 inline 분기를 헬퍼로 교체

```python
# 기존:
# if getattr(sys, "frozen", False):
#     cmd = [sys.executable, "worker", "--task-file", task_file, "--result-file", result_file]
# else:
#     worker_script = str(Path(__file__).parent / "agent_worker.py")
#     cmd = [sys.executable, worker_script, "--task-file", task_file, "--result-file", result_file]

# 변경:
from core.utils import build_worker_cmd
cmd = build_worker_cmd("worker", "--task-file", task_file, "--result-file", result_file)
```

**수정: `run_factory_cli.py`** — `daemon-worker` 서브커맨드 추가

```python
# 기존 worker 디스패치 (line 139-143) 바로 아래:
if effective_argv and effective_argv[0] == "daemon-worker":
    from core.daemon_worker import main as daemon_worker_main
    sys.argv = ["af-daemon-worker"] + effective_argv[1:]
    daemon_worker_main()
    return
```

### 영향

- `build_worker_cmd()`는 daemon_supervisor.py와 dynamic_orchestrator.py 양쪽에서 사용
- frozen 빌드에서 `af.exe daemon-worker --task-file ...`로 동작
- source 실행에서 `python core/daemon_worker.py --task-file ...`로 동작
- **테스트**: frozen 빌드 후 `af.exe daemon-worker --help`가 동작하는지 확인

---

## Part 1: 에이전트별 비용 추적 — RunLedger 흡수 (요약 모델)

### 원칙

```
RunLedger.metadata → 요약만 (5개 필드)
상세 시도 이력      → 기존 SkillFeedback (data/skill-usage.jsonl) + _flush_trace() 재사용
```

### 수정: `core/control/run_ledger.py`

```python
class RunLedger:

    def __init__(self, workspace: str):
        self._workspace = workspace
        self._control_dir = os.path.join(workspace, ".af_runtime", "control")
        self._cost_buffer: dict[str, list[dict]] = {}  # ★ 추가: run_id → agent cost entries

    # ── 기존 API 변경 없음: append, open_run, update_run, get_active_runs, ... ──

    # ── 신규: 비용 기록 API (3개) ──

    def record_agent_cost(
        self,
        run_id: str,
        agent_role: str,
        provider_id: str,
        tokens_estimated: int,
        latency_ms: int,
        ok: bool,
        plane: str = "worker",
    ) -> None:
        """에이전트 비용을 in-memory 버퍼에 누적한다.

        JSONL에 매번 쓰지 않는다 — close_run() 시 요약으로 flush.

        Args:
            run_id: 실행 ID
            agent_role: 에이전트 역할 (e.g., "backend_dev")
            provider_id: 사용된 provider (e.g., "claude_cli")
            tokens_estimated: 4-char≈1-token 추정치
            latency_ms: 실행 시간
            ok: 성공 여부
            plane: "worker" | "control" (오케스트레이션 vs 실행)
        """
        if run_id not in self._cost_buffer:
            self._cost_buffer[run_id] = []
        self._cost_buffer[run_id].append({
            "role": agent_role,
            "provider": provider_id,
            "tokens": tokens_estimated,
            "latency_ms": latency_ms,
            "ok": ok,
            "plane": plane,
        })

    def flush_cost_summary(self, run_id: str) -> dict:
        """in-memory 비용 버퍼를 요약 dict로 변환하고 비운다.

        close_run()이 내부 호출. metadata에는 이 요약만 기록된다.

        Returns:
            {
                "tokens_total": int,
                "tokens_by_provider": {"claude_cli": N, "gemini_cli": M, ...},
                "tokens_by_plane": {"worker": N, "control": M},
                "infra_failures_by_provider": {"codex_cli": N, ...},
                "top_roles": [{"role": str, "tokens": int}, ...]
            }
        """
        costs = self._cost_buffer.pop(run_id, [])
        if not costs:
            return {}

        tokens_total = sum(c["tokens"] for c in costs)

        by_provider: dict[str, int] = {}
        by_plane: dict[str, int] = {}
        by_role: dict[str, int] = {}
        infra_fail: dict[str, int] = {}

        for c in costs:
            by_provider[c["provider"]] = by_provider.get(c["provider"], 0) + c["tokens"]
            by_plane[c["plane"]] = by_plane.get(c["plane"], 0) + c["tokens"]
            by_role[c["role"]] = by_role.get(c["role"], 0) + c["tokens"]
            if not c["ok"]:
                infra_fail[c["provider"]] = infra_fail.get(c["provider"], 0) + 1

        top_roles = sorted(by_role.items(), key=lambda x: -x[1])[:10]

        return {
            "tokens_total": tokens_total,
            "tokens_by_provider": by_provider,
            "tokens_by_plane": by_plane,
            "infra_failures_by_provider": infra_fail,
            "top_roles": [{"role": r, "tokens": t} for r, t in top_roles],
        }

    def get_pending_costs(self, run_id: str) -> list[dict]:
        """checkpoint 저장용: 아직 flush되지 않은 비용 항목을 반환한다."""
        return list(self._cost_buffer.get(run_id, []))

    def restore_pending_costs(self, run_id: str, costs: list[dict]) -> None:
        """checkpoint 복구용: 비용 항목을 버퍼에 복원한다."""
        self._cost_buffer[run_id] = list(costs)

    # ── 수정: close_run() ──

    def close_run(self, run_id: str, outcome: str, state: str = "closed") -> LedgerEntry | None:
        """run 종료를 기록한다. 비용 요약을 metadata에 병합."""
        from core.utils import now_iso
        latest = self._get_latest(run_id)
        if latest is None:
            return None

        # ★ 비용 요약 flush → metadata에 병합
        cost_summary = self.flush_cost_summary(run_id)
        merged_metadata = {**latest.metadata, **cost_summary}

        closed = LedgerEntry(
            run_id=latest.run_id,
            issue_id=latest.issue_id,
            workspace=latest.workspace,
            pipeline=latest.pipeline,
            work_kind=latest.work_kind,
            execution_policy=latest.execution_policy,
            work_item_slug=latest.work_item_slug,
            state=state,
            board_summary=latest.board_summary,
            change_impact_summary=latest.change_impact_summary,
            started_at=latest.started_at,
            updated_at=now_iso(),
            closed_at=now_iso(),
            outcome=outcome,
            metadata=merged_metadata,
        )
        self.append(closed)
        return closed
```

### 수정: `core/agent_runner.py` — `_flush_trace()`

```python
def _flush_trace(result: dict):
    # 글로벌 토큰 예산 기록 (기존)
    try:
        from core.run_budget import get_run_budget
        _text = str(result.get("text", "") or "")
        if _text:
            get_run_budget().record(_text)
    except Exception:
        pass

    # ★ RunLedger 비용 기록 (신규)
    try:
        from core.control.run_ledger import RunLedger
        _text = str(result.get("text", "") or "")
        _tokens = max(1, len(_text) // 4) if _text else 0
        result["tokens_estimated"] = _tokens
        if _tokens and target_workspace:
            RunLedger(target_workspace).record_agent_cost(
                run_id=run_id,
                agent_role=str(agent.get("role", "")),
                provider_id=str(result.get("reason", "")),  # reason에 provider_id가 들어감
                tokens_estimated=_tokens,
                latency_ms=int(result.get("latency_ms", 0)),
                ok=bool(result.get("ok")),
                plane="worker",
            )
    except Exception:
        pass

    # ... 기존 trace 저장 로직 계속 ...
```

### 수정: `core/dynamic_orchestrator.py` — state_board

```python
# completed_entry / failed_entry에 추가:
entry["tokens_estimated"] = result.get("tokens_estimated", 0)
```

### 비용 데이터 흐름

```
AgentRunner.run() 완료
  → _flush_trace()
      → RunBudget.record(text)                    # 글로벌 in-memory 합산
      → RunLedger.record_agent_cost(...)           # run_id별 in-memory 버퍼 누적
      → SkillFeedback.record_runtime_event(...)    # 상세 기록 (기존, data/skill-usage.jsonl)
  → result["tokens_estimated"] = N
      → state_board에 기록

MaintenancePipeline.execute() 종료 시
  → RunLedger.close_run(outcome)
      → flush_cost_summary()
          → metadata에 요약 5필드 기록
          → in-memory 버퍼 삭제
```

### metadata 예시 (close_run 후 JSONL 한 줄)

```json
{
  "run_id": "run_20260403_001",
  "state": "closed",
  "outcome": "success",
  "metadata": {
    "tokens_total": 45000,
    "tokens_by_provider": {"claude_cli": 30000, "gemini_cli": 15000},
    "tokens_by_plane": {"worker": 40000, "control": 5000},
    "infra_failures_by_provider": {"codex_cli": 1},
    "top_roles": [
      {"role": "backend_dev", "tokens": 25000},
      {"role": "tester", "tokens": 12000},
      {"role": "reviewer", "tokens": 8000}
    ]
  }
}
```

---

## Part 2: AF-Owned Checkpoint — 5단계 경계

### 원칙

checkpoint는 **provider hook이 아닌 AF 코드 경계**에서 저장한다.
이유: frozen 빌드에서 Claude/Gemini native hook 등록이 건너뛰어짐 (`session_adapter.py:436`).
Codex는 `wrapper_bridge` 모드라 native hook 자체가 없음 (`session_adapter.py:110`).

### 5단계 경계 정의

```
1. wake_detected        ← daemon_worker.py 진입, items 수신
2. request_normalized   ← ControlPlaneIntake.normalize() 완료
3. prepare_completed    ← MaintenancePipeline.prepare() 완료
4. subtask_committed    ← DynamicOrchestrator에서 개별 task 완료 (state_board 갱신)
5. run_closed           ← MaintenancePipeline.execute() 종료 (=close_run)
```

### 수정: `core/hooks/checkpoint.py`

기존 `CheckpointHook`(사이클 단위)은 **변경하지 않는다** — 기존 FSALoop/AgentRunner와의 호환성 유지.
wake 단위 checkpoint는 **별도 함수**로 daemon_worker.py가 직접 호출한다.

```python
# core/hooks/checkpoint.py — 추가

_WAKE_CHECKPOINT_FILENAME = "wake_checkpoint.json"


def save_wake_checkpoint(
    workspace: str,
    project_id: str,
    stage: str,
    data: dict,
) -> None:
    """wake 단위 checkpoint 저장.

    daemon_worker.py가 AF-owned 경계를 통과할 때 호출한다.

    Args:
        workspace: 프로젝트 작업 디렉토리
        project_id: 프로젝트 ID
        stage: "wake_detected" | "request_normalized" | "prepare_completed"
               | "subtask_committed:<task_id>" | "run_closed"
        data: 단계별 저장 데이터
    """
    import json, os
    from core.utils import now_iso

    cp_dir = os.path.join(workspace, ".af_runtime", "daemon")
    os.makedirs(cp_dir, exist_ok=True)
    cp_path = os.path.join(cp_dir, _WAKE_CHECKPOINT_FILENAME)

    checkpoint = {
        "project_id": project_id,
        "stage": stage,
        "saved_at": now_iso(),
        "data": data,
    }
    tmp = cp_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cp_path)


def load_wake_checkpoint(workspace: str) -> dict | None:
    """wake checkpoint를 로드한다. 없으면 None."""
    import json, os
    cp_path = os.path.join(workspace, ".af_runtime", "daemon", _WAKE_CHECKPOINT_FILENAME)
    if not os.path.isfile(cp_path):
        return None
    try:
        with open(cp_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_wake_checkpoint(workspace: str) -> None:
    """wake 완료 후 checkpoint를 삭제한다."""
    import os
    cp_path = os.path.join(workspace, ".af_runtime", "daemon", _WAKE_CHECKPOINT_FILENAME)
    try:
        if os.path.isfile(cp_path):
            os.remove(cp_path)
    except Exception:
        pass
```

### checkpoint에 비용 복구 데이터 포함

```python
# daemon_worker.py에서 checkpoint 저장 시:
from core.control.run_ledger import RunLedger

save_wake_checkpoint(workspace, project_id, "subtask_committed:task_003", {
    "completed_items": items[:i+1],
    "remaining_items": items[i+1:],
    "pending_cost_summary": RunLedger(workspace).get_pending_costs(run_id),
    "budget_consumed": get_run_budget().consumed,
    "run_id": run_id,
    "normalized_request": normalized_dict,
    "prepared": prepared_dict,
})
```

Worker 재시작 시:
```python
cp = load_wake_checkpoint(workspace)
if cp:
    stage = cp["stage"]
    data = cp["data"]

    # 비용 복구
    if data.get("pending_cost_summary"):
        RunLedger(workspace).restore_pending_costs(data["run_id"], data["pending_cost_summary"])
    if data.get("budget_consumed"):
        budget = set_run_budget(task.get("budget", 0))
        budget.consumed = data["budget_consumed"]

    # 단계별 resume
    if stage == "wake_detected":
        pass  # 처음부터
    elif stage == "request_normalized":
        skip_normalize = True
        normalized = data["normalized_request"]
    elif stage == "prepare_completed":
        skip_normalize = True
        skip_prepare = True
        normalized = data["normalized_request"]
        prepared = data["prepared"]
    elif stage.startswith("subtask_committed:"):
        # 남은 items만 실행
        items = data.get("remaining_items", [])
    elif stage == "run_closed":
        # 이미 완료 — skip
        clear_wake_checkpoint(workspace)
```

---

## Part 3: Provider Policy

### 문제

`auto_configure_cli_provider()` (`engine_auth.py:85`)는 **설치된 CLI 중 하나를 자동 선택**한다.
우선순위: `gemini_cli → claude_cli → codex_cli` (line 124).

데몬에서는:
1. **전부 활성화**하고 싶다 (quota 시 failover)
2. **역할별로 다른 provider**를 쓰고 싶다 (control plane은 gemini, worker는 claude)
3. `auto_configure`가 gemini를 선택하면 claude가 idle 상태로 낭비된다

### 설계

**Provider Policy는 DaemonConfig의 일부**로, wake_task.json을 통해 Worker에 전달된다.

```python
@dataclasses.dataclass
class ProviderPolicy:
    """serve 전용 provider 설정.

    auto_configure_cli_provider()를 우회하고
    Worker가 configure_providers()로 명시적 설정.
    """
    # 기본 작업용 provider 목록 (순서 = 우선순위)
    worker_providers: list[str] = dataclasses.field(
        default_factory=lambda: ["claude_cli", "gemini_cli", "codex_cli"]
    )
    # control-plane용 (Lilith, StrategyEvaluator)
    control_plane_providers: list[str] = dataclasses.field(
        default_factory=lambda: ["gemini_cli", "claude_cli"]
    )
    # 교차검증용 (2개 이상 필요)
    cross_verify_providers: list[str] = dataclasses.field(
        default_factory=lambda: ["claude_cli", "gemini_cli"]
    )
    # 이 실패 유형이면 다음 provider로 failover
    failover_on: list[str] = dataclasses.field(
        default_factory=lambda: ["quota", "auth_required", "cli_timeout", "rate_limit", "worker_timeout"]
    )

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderPolicy":
        if not d:
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
```

### wake_task.json에 포함

```json
{
  "project_id": "minesweeper",
  "workspace": "C:/Projects/minesweeper",
  "factory_root": "C:/Project/agent-factory",
  "items": [...],
  "budget": 50000,
  "provider_policy": {
    "worker_providers": ["claude_cli", "gemini_cli"],
    "control_plane_providers": ["gemini_cli"],
    "cross_verify_providers": ["claude_cli", "gemini_cli"],
    "failover_on": ["quota", "auth_required", "cli_timeout", "rate_limit"]
  },
  "spawned_at": "2026-04-03T..."
}
```

### Worker에서 적용

```python
# daemon_worker.py — 진입부:

# ★ auto_configure 우회: Provider Policy에서 명시적 설정
policy = ProviderPolicy.from_dict(task.get("provider_policy", {}))
from core.providers.registry import configure_providers
configure_providers(policy.worker_providers)

# control-plane provider도 설정 (ControlPlaneLLM이 참조)
os.environ["AF_CONTROL_PLANE_PROVIDERS"] = ",".join(policy.control_plane_providers)
```

### AgentRunner failover와의 연동

`agent_runner.py:1076`의 기존 failover 루프:
```python
for provider_id in cli_providers:
    cli_result = self._run_with_cli_provider(provider_id, ...)
    if cli_result.get("ok"):
        break
```

이 루프는 **이미 configure_providers()로 설정된 목록**을 `get_requested_cli_providers()`에서 가져온다.
Worker가 `configure_providers(policy.worker_providers)`를 호출하면, **기존 failover 루프가 그대로 동작**한다.

추가로, `failure_classifier.py`의 INFRA 패턴 (`_INFRA_PATTERNS:19-37`)과 Provider Policy의 `failover_on`을 연결:

```python
# agent_runner.py — cli_result.get("ok")가 False일 때 분기 추가:
if not cli_result.get("ok"):
    from core.failure_classifier import classify_failure, FailureCategory
    reason = cli_result.get("reason", "")
    category = classify_failure(reason)
    if category == FailureCategory.INFRA:
        # INFRA 실패 → 다음 provider로 failover (기존 for loop 계속)
        RunLedger(workspace).record_agent_cost(
            run_id=run_id, agent_role=role, provider_id=provider_id,
            tokens_estimated=0, latency_ms=latency, ok=False, plane="worker",
        )
        continue
    else:
        # IMPLEMENTATION 실패 → provider 문제 아님, 중단
        break
```

### CLI 인자

```
af serve --projects A,B \
         --worker-providers claude_cli,gemini_cli \
         --control-providers gemini_cli \
         --cross-verify-providers claude_cli,gemini_cli \
         --failover-on quota,auth_required,cli_timeout
```

---

## Part 4: DaemonSupervisor

### 핵심 차이 (v2 대비)

1. **build_worker_cmd() 사용** — frozen/source 공통
2. **stop-file 프로토콜** — `proc.terminate()` 제거
3. **Provider Policy 전달** — wake_task.json에 포함
4. **표준 라이브러리만 import** — AF 핵심 코드 무지

### 신규 파일: `core/daemon_supervisor.py`

```python
"""
core/daemon_supervisor.py
==========================
DaemonSupervisor — Provider-Agnostic 프로세스 관리자.

이 모듈은 AF 핵심 코드를 import하지 않는다.
의존: os, sys, json, time, signal, subprocess, threading (표준 라이브러리만)

Worker subprocess가 실제 작업을 수행한다.
Supervisor는 scan, spawn, stop-file, status만 담당.
"""
import dataclasses
import json
import os
import signal
import subprocess
import sys
import threading
import time


@dataclasses.dataclass
class DaemonConfig:
    projects: list[str]
    projects_root: str
    factory_root: str
    check_interval_sec: int = 300
    budget_per_wake: int = 0
    code_watch: bool = True
    log_path: str = ""
    provider_policy: dict = dataclasses.field(default_factory=dict)


class WorkDetector:
    """프로젝트별 작업 감지기. AF 코드 import 없이 파일 시스템만 읽는다."""

    def scan(self, workspace: str) -> list[dict]:
        items = []
        items.extend(self._scan_board(workspace))
        items.extend(self._scan_mailbox(workspace))
        items.extend(self._scan_todo(workspace))
        items.extend(self._scan_stale_runs(workspace))
        # resume 대기 중인 checkpoint가 있으면 최우선
        items.extend(self._scan_pending_checkpoint(workspace))
        return items

    def _scan_board(self, workspace: str) -> list[dict]:
        path = os.path.join(workspace, "project_board_state.json")
        if not os.path.isfile(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                board = json.load(f)
            pending = [t for t in (board.get("tasks") or []) if t.get("status") == "pending"]
            if pending:
                return [{"source": "board", "detail": f"{len(pending)} pending tasks",
                         "data": {"tasks": pending}}]
        except Exception:
            pass
        return []

    def _scan_mailbox(self, workspace: str) -> list[dict]:
        mdir = os.path.join(workspace, ".af_runtime", "mailbox")
        if not os.path.isdir(mdir):
            return []
        try:
            msgs = [f for f in os.listdir(mdir) if f.endswith(".json") and not f.startswith("_processed_")]
            if msgs:
                return [{"source": "mailbox", "detail": f"{len(msgs)} messages", "data": {"files": msgs}}]
        except Exception:
            pass
        return []

    def _scan_todo(self, workspace: str) -> list[dict]:
        todo = os.path.join(workspace, ".todo.md")
        marker = os.path.join(workspace, ".af_runtime", ".todo_mtime")
        if not os.path.isfile(todo):
            return []
        try:
            cur = os.path.getmtime(todo)
            last = 0.0
            if os.path.isfile(marker):
                with open(marker) as f:
                    last = float(f.read().strip())
            if cur > last:
                return [{"source": "todo", "detail": "todo.md changed", "data": {"mtime": cur}}]
        except Exception:
            pass
        return []

    def _scan_stale_runs(self, workspace: str) -> list[dict]:
        ledger = os.path.join(workspace, ".af_runtime", "control", "run_ledger.jsonl")
        if not os.path.isfile(ledger):
            return []
        try:
            from datetime import datetime, timezone
            latest: dict[str, dict] = {}
            with open(ledger, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entry = json.loads(line)
                        latest[entry.get("run_id", "")] = entry

            now = datetime.now(timezone.utc)
            stale = []
            for rid, entry in latest.items():
                if entry.get("closed_at"):
                    continue
                started = entry.get("started_at", "")
                if not started:
                    stale.append(rid)
                    continue
                try:
                    dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    if (now - dt).total_seconds() > 1800:
                        stale.append(rid)
                except Exception:
                    pass
            if stale:
                return [{"source": "stale_run", "detail": f"{len(stale)} stale", "data": {"run_ids": stale}}]
        except Exception:
            pass
        return []

    def _scan_pending_checkpoint(self, workspace: str) -> list[dict]:
        cp = os.path.join(workspace, ".af_runtime", "daemon", "wake_checkpoint.json")
        if not os.path.isfile(cp):
            return []
        try:
            with open(cp, encoding="utf-8") as f:
                data = json.load(f)
            stage = data.get("stage", "")
            if stage and stage != "run_closed":
                return [{"source": "resume", "detail": f"resume from {stage}", "data": data}]
        except Exception:
            pass
        return []


class CodeWatcher:
    """core/*.py mtime 추적. 변경 감지 시 Supervisor가 Worker를 graceful restart."""

    def __init__(self, factory_root: str):
        self._root = factory_root
        self._snapshot: dict[str, float] = {}
        self._take_snapshot()

    def _take_snapshot(self) -> None:
        self._snapshot = {}
        core_dir = os.path.join(self._root, "core")
        if not os.path.isdir(core_dir):
            return
        for root, _dirs, files in os.walk(core_dir):
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(root, f)
                    try:
                        self._snapshot[p] = os.path.getmtime(p)
                    except OSError:
                        pass

    def check_changed(self) -> list[str]:
        changed = []
        new_snap: dict[str, float] = {}
        core_dir = os.path.join(self._root, "core")
        if not os.path.isdir(core_dir):
            return []
        for root, _dirs, files in os.walk(core_dir):
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(root, f)
                    try:
                        mt = os.path.getmtime(p)
                        new_snap[p] = mt
                        old = self._snapshot.get(p)
                        if old is None or mt > old:
                            changed.append(p)
                    except OSError:
                        pass
        if changed:
            self._snapshot = new_snap
        return changed


class DaemonSupervisor:
    """Provider-Agnostic 프로세스 관리자."""

    def __init__(self, config: DaemonConfig):
        self._config = config
        self._detector = WorkDetector()
        self._watcher = CodeWatcher(config.factory_root) if config.code_watch else None
        self._shutdown = False
        self._sleep_event = threading.Event()
        self._active_workers: dict[str, subprocess.Popen] = {}
        self._status = {
            "state": "initializing",
            "pid": os.getpid(),
            "started_at": "",
            "last_check_at": "",
            "last_work_at": "",
            "total_wakes": 0,
            "projects": config.projects,
        }

    # ── 메인 루프 ──

    def run_forever(self) -> None:
        self._install_signal_handlers()
        self._status["started_at"] = _now_iso()
        self._status["state"] = "running"
        self._write_status()
        print(f"[DaemonSupervisor] started — projects={self._config.projects}")

        while not self._shutdown:
            self._status["state"] = "scanning"
            self._status["last_check_at"] = _now_iso()

            # 1. 코드 변경 → 실행 중 Worker에 stop 요청
            if self._watcher:
                changed = self._watcher.check_changed()
                if changed:
                    names = [os.path.basename(c) for c in changed[:5]]
                    print(f"[DaemonSupervisor] code changed: {names}")
                    self._request_stop_all("code_change")

            # 2. 프로젝트별 scan → Worker spawn
            for pid in self._config.projects:
                if self._shutdown:
                    break
                ws = os.path.join(self._config.projects_root, pid)
                if not os.path.isdir(ws):
                    continue

                # Worker 실행 중이면 스킵 또는 결과 수거
                if pid in self._active_workers:
                    proc = self._active_workers[pid]
                    if proc.poll() is None:
                        continue
                    self._collect_result(pid)
                    del self._active_workers[pid]

                items = self._detector.scan(ws)
                if items:
                    self._spawn_worker(pid, ws, items)

            # 3. 완료된 Worker 수거
            self._collect_finished_workers()
            self._write_status()

            # 4. sleep
            self._status["state"] = "sleeping"
            self._sleep_event.wait(timeout=self._config.check_interval_sec)
            self._sleep_event.clear()

        self._graceful_shutdown()

    # ── Worker spawn ──

    def _spawn_worker(self, project_id: str, workspace: str, items: list[dict]) -> None:
        self._status["total_wakes"] = self._status.get("total_wakes", 0) + 1
        self._status["last_work_at"] = _now_iso()

        runtime_dir = os.path.join(workspace, ".af_runtime", "daemon")
        os.makedirs(runtime_dir, exist_ok=True)
        task_file = os.path.join(runtime_dir, "wake_task.json")
        result_file = os.path.join(runtime_dir, "wake_result.json")

        # stop-file 삭제 (이전 세션 잔여)
        stop_file = os.path.join(runtime_dir, f"{project_id}.stop")
        if os.path.isfile(stop_file):
            try:
                os.remove(stop_file)
            except Exception:
                pass

        task_data = {
            "project_id": project_id,
            "workspace": workspace,
            "factory_root": self._config.factory_root,
            "items": items,
            "budget": self._config.budget_per_wake,
            "provider_policy": self._config.provider_policy,
            "spawned_at": _now_iso(),
        }
        with open(task_file, "w", encoding="utf-8") as f:
            json.dump(task_data, f, ensure_ascii=False, indent=2)

        # ★ build_worker_cmd — frozen/source 공통
        cmd = _build_worker_cmd(
            self._config.factory_root,
            "daemon-worker",
            "--task-file", task_file,
            "--result-file", result_file,
        )

        try:
            proc = subprocess.Popen(cmd, cwd=self._config.factory_root)
            self._active_workers[project_id] = proc
            print(f"[DaemonSupervisor] Worker spawned: {project_id} (pid={proc.pid})")
        except Exception as exc:
            print(f"[DaemonSupervisor] spawn failed: {project_id}: {exc}")

    # ── Stop-file 프로토콜 (Windows 안전) ──

    def _request_stop(self, project_id: str, reason: str = "") -> None:
        """Worker에 협조적 종료를 요청한다. proc.terminate() 대신 사용."""
        ws = os.path.join(self._config.projects_root, project_id)
        stop_file = os.path.join(ws, ".af_runtime", "daemon", f"{project_id}.stop")
        try:
            os.makedirs(os.path.dirname(stop_file), exist_ok=True)
            with open(stop_file, "w") as f:
                f.write(json.dumps({"reason": reason, "at": _now_iso()}))
            print(f"[DaemonSupervisor] stop requested: {project_id} ({reason})")
        except Exception:
            pass

    def _request_stop_all(self, reason: str) -> None:
        for pid in list(self._active_workers.keys()):
            self._request_stop(pid, reason)

    # ── 결과 수거 ──

    def _collect_result(self, project_id: str) -> dict | None:
        ws = os.path.join(self._config.projects_root, project_id)
        result_file = os.path.join(ws, ".af_runtime", "daemon", "wake_result.json")
        if not os.path.isfile(result_file):
            return None
        try:
            with open(result_file, encoding="utf-8") as f:
                result = json.load(f)
            os.remove(result_file)
            print(
                f"[DaemonSupervisor] result: {project_id} "
                f"success={result.get('success')} tokens={result.get('tokens_total', 0)}"
            )
            return result
        except Exception:
            return None

    def _collect_finished_workers(self) -> None:
        finished = [pid for pid, proc in self._active_workers.items() if proc.poll() is not None]
        for pid in finished:
            self._collect_result(pid)
            del self._active_workers[pid]

    # ── Shutdown ──

    def _graceful_shutdown(self) -> None:
        print("[DaemonSupervisor] shutting down...")
        self._request_stop_all("supervisor_shutdown")

        for pid, proc in self._active_workers.items():
            if proc.poll() is None:
                print(f"[DaemonSupervisor] waiting: {pid} (pid={proc.pid})...")
                try:
                    proc.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    print(f"[DaemonSupervisor] force kill: {pid}")
                    proc.kill()
            self._collect_result(pid)

        self._status["state"] = "stopped"
        self._write_status()
        print("[DaemonSupervisor] stopped.")

    def _install_signal_handlers(self) -> None:
        def _handler(signum, frame):
            print(f"\n[DaemonSupervisor] signal {signum}, shutting down...")
            self._shutdown = True
            self._sleep_event.set()
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    def _write_status(self) -> None:
        sdir = os.path.join(self._config.projects_root, ".af_runtime")
        os.makedirs(sdir, exist_ok=True)
        spath = os.path.join(sdir, "daemon_status.json")
        try:
            tmp = spath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._status, f, ensure_ascii=False, indent=2)
            os.replace(tmp, spath)
        except Exception:
            pass


# ── spawn helper (Supervisor 내부용, 표준 라이브러리만) ──

def _build_worker_cmd(factory_root: str, worker_type: str, *extra_args: str) -> list[str]:
    """frozen/source 공통 Worker 명령어 생성.

    Note: core/utils.py의 build_worker_cmd()와 동일 로직이지만,
    Supervisor는 core/utils.py를 import하지 않으므로 독립 구현.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, worker_type, *extra_args]
    else:
        _SCRIPTS = {"worker": "core/agent_worker.py", "daemon-worker": "core/daemon_worker.py"}
        script = _SCRIPTS.get(worker_type)
        if not script:
            raise ValueError(f"unknown worker_type: {worker_type}")
        return [sys.executable, os.path.join(factory_root, script), *extra_args]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

---

## Part 5: DaemonWorker

### 신규 파일: `core/daemon_worker.py`

```python
"""
core/daemon_worker.py
======================
데몬 Worker 프로세스 — wake 1회 실행 단위.

DaemonSupervisor에 의해 subprocess로 spawn된다.
AF 핵심 파이프라인을 실행한다.

사용법:
  python core/daemon_worker.py --task-file <wake_task.json> --result-file <wake_result.json>
  af.exe daemon-worker --task-file <wake_task.json> --result-file <wake_result.json>

Stop 프로토콜:
  .af_runtime/daemon/{project_id}.stop 파일이 존재하면
  현재 item 완료 후 checkpoint 저장 → 정상 종료
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback


def main():
    parser = argparse.ArgumentParser(description="Agent Factory Daemon Worker")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--result-file", required=True)
    args = parser.parse_args()

    # ── 1. wake_task.json 로드 ──
    try:
        with open(args.task_file, encoding="utf-8") as f:
            task = json.load(f)
    except Exception as exc:
        _write_result(args.result_file, {"success": False, "error": f"task load: {exc}"})
        sys.exit(1)

    factory_root = task.get("factory_root", "")
    workspace = task.get("workspace", "")
    project_id = task.get("project_id", "")
    items = task.get("items", [])
    budget = task.get("budget", 0)
    provider_policy = task.get("provider_policy", {})

    if factory_root and factory_root not in sys.path:
        sys.path.insert(0, factory_root)

    print(f"[DaemonWorker:{project_id}] started — {len(items)} items")

    # ── 2. Provider 설정 (auto_configure 우회) ──
    try:
        from core.providers.registry import configure_providers
        worker_providers = provider_policy.get("worker_providers", [])
        if worker_providers:
            configure_providers(worker_providers)
            print(f"[DaemonWorker:{project_id}] providers: {worker_providers}")

        # control-plane providers 환경변수 설정
        cp_providers = provider_policy.get("control_plane_providers", [])
        if cp_providers:
            os.environ["AF_CONTROL_PLANE_PROVIDERS"] = ",".join(cp_providers)
    except Exception as exc:
        print(f"[DaemonWorker:{project_id}] provider config failed: {exc}")

    # ── 3. 예산 설정 ──
    if budget:
        try:
            from core.run_budget import set_run_budget
            set_run_budget(budget)
        except Exception:
            pass

    # ── 4. Checkpoint resume 확인 ──
    resume_data = None
    try:
        from core.hooks.checkpoint import load_wake_checkpoint
        cp = load_wake_checkpoint(workspace)
        if cp and cp.get("stage") and cp["stage"] != "run_closed":
            resume_data = cp
            print(f"[DaemonWorker:{project_id}] resuming from {cp['stage']}")

            # 비용 복구
            from core.control.run_ledger import RunLedger
            pending_costs = cp.get("data", {}).get("pending_cost_summary", [])
            if pending_costs:
                run_id = cp.get("data", {}).get("run_id", "")
                if run_id:
                    RunLedger(workspace).restore_pending_costs(run_id, pending_costs)

            consumed = cp.get("data", {}).get("budget_consumed", 0)
            if consumed and budget:
                from core.run_budget import get_run_budget
                get_run_budget().consumed = consumed

            # resume 대상 items 교체
            remaining = cp.get("data", {}).get("remaining_items")
            if remaining is not None:
                items = remaining
    except Exception as exc:
        print(f"[DaemonWorker:{project_id}] checkpoint load failed: {exc}")

    # ── 5. 항목별 실행 ──
    start = time.time()
    result = {
        "success": True,
        "project_id": project_id,
        "items_processed": 0,
        "tokens_total": 0,
        "duration_ms": 0,
        "errors": [],
    }

    for i, item in enumerate(items):
        # ★ Stop-file 확인 (Windows 안전 graceful stop)
        if _should_stop(workspace, project_id):
            print(f"[DaemonWorker:{project_id}] stop requested, checkpointing...")
            _save_checkpoint(workspace, project_id, f"stop_at_item_{i}", {
                "completed_items": items[:i],
                "remaining_items": items[i:],
                "budget_consumed": _get_budget_consumed(),
                "pending_cost_summary": _get_pending_costs(workspace),
            })
            break

        source = item.get("source", "")
        try:
            # ★ Checkpoint: subtask 시작 전
            _save_checkpoint(workspace, project_id, f"item_start:{i}:{source}", {
                "completed_items": items[:i],
                "remaining_items": items[i:],
                "budget_consumed": _get_budget_consumed(),
                "pending_cost_summary": _get_pending_costs(workspace),
            })

            item_result = _execute_item(source, item, workspace, project_id)
            result["items_processed"] += 1
            result["tokens_total"] += item_result.get("tokens", 0)
            if not item_result.get("ok", False):
                result["errors"].append(item_result.get("error", "unknown"))
        except Exception as exc:
            result["errors"].append(f"{source}: {exc}")
            traceback.print_exc()

    result["duration_ms"] = int((time.time() - start) * 1000)
    result["success"] = result["items_processed"] > 0 and len(result["errors"]) == 0

    # ── 6. 완료 처리 ──
    _update_todo_marker(workspace)

    # ★ Checkpoint: run_closed (다음 scan에서 resume 불필요)
    try:
        from core.hooks.checkpoint import clear_wake_checkpoint
        clear_wake_checkpoint(workspace)
    except Exception:
        pass

    # stop-file 정리
    _clear_stop_file(workspace, project_id)

    # ── 7. 결과 기록 ──
    _write_result(args.result_file, result)
    print(f"[DaemonWorker:{project_id}] done — {result['items_processed']} items, {result['tokens_total']} tokens")


# ── Item 실행 라우팅 ──

def _execute_item(source: str, item: dict, workspace: str, project_id: str) -> dict:
    if source == "board":
        return _exec_board(item, workspace)
    elif source == "mailbox":
        return _exec_mailbox(item, workspace)
    elif source == "todo":
        return _exec_todo(item, workspace)
    elif source == "stale_run":
        return _exec_stale(item, workspace)
    elif source == "resume":
        return _exec_resume(item, workspace)
    else:
        return {"ok": False, "error": f"unknown source: {source}"}


def _exec_board(item: dict, workspace: str) -> dict:
    try:
        from core.control.intake import ControlPlaneIntake
        from core.control.maintenance_pipeline import MaintenancePipeline
        from core.project_pipeline import ProjectPipeline
        from core.model_router import ModelRouter

        mr = ModelRouter()
        intake = ControlPlaneIntake(workspace)
        pp = ProjectPipeline(mr, None, None, None)
        mp = MaintenancePipeline(pp, workspace)

        tasks = item.get("data", {}).get("tasks", [])
        summary = "; ".join(t.get("description", "")[:60] for t in tasks[:5])
        normalized = intake.normalize({"raw_input": f"[daemon] pending: {summary}"})
        prepared = mp.prepare(normalized)
        exec_result = mp.execute(prepared, normalized)
        return {"ok": exec_result.get("success", False), "tokens": _get_budget_consumed()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _exec_mailbox(item: dict, workspace: str) -> dict:
    try:
        from core.control.intake import ControlPlaneIntake
        from core.control.maintenance_pipeline import MaintenancePipeline
        from core.project_pipeline import ProjectPipeline
        from core.model_router import ModelRouter

        mr = ModelRouter()
        intake = ControlPlaneIntake(workspace)
        pp = ProjectPipeline(mr, None, None, None)
        mp = MaintenancePipeline(pp, workspace)

        files = item.get("data", {}).get("files", [])
        mdir = os.path.join(workspace, ".af_runtime", "mailbox")
        processed = 0
        for fname in files:
            msg_path = os.path.join(mdir, fname)
            try:
                with open(msg_path, encoding="utf-8") as f:
                    msg = json.load(f)
                normalized = intake.normalize(msg)
                prepared = mp.prepare(normalized)
                mp.execute(prepared, normalized)
                os.rename(msg_path, os.path.join(mdir, f"_processed_{fname}"))
                processed += 1
            except Exception as exc:
                print(f"[DaemonWorker] mailbox {fname}: {exc}")
        return {"ok": processed > 0, "tokens": _get_budget_consumed()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _exec_todo(item: dict, workspace: str) -> dict:
    try:
        from core.fsa_loop import FSALoop
        from core.model_router import ModelRouter
        from core.agent_runner import AgentRunner

        mr = ModelRouter()
        runner = AgentRunner(mr)
        fsa = FSALoop(mr, runner, workspace=workspace)

        todo_path = os.path.join(workspace, ".todo.md")
        with open(todo_path, encoding="utf-8") as f:
            content = f.read()
        result = fsa.run_mission(
            task_input=f"[daemon] todo:\n{content[:2000]}",
            workspace=workspace,
        )
        return {"ok": result.get("ok", False), "tokens": _get_budget_consumed()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _exec_stale(item: dict, workspace: str) -> dict:
    try:
        from core.continuity.manifest_store import OrchestratorManifestStore
        from core.dynamic_orchestrator import DynamicOrchestrator
        from core.model_router import ModelRouter

        resume_state = OrchestratorManifestStore.load_resume_state()
        if not resume_state:
            return {"ok": True, "tokens": 0}
        mr = ModelRouter()
        orch = DynamicOrchestrator(mr, workspace=workspace)
        result = orch.run_project("[daemon] resume stale", workspace, resume_state=resume_state)
        return {"ok": bool(result), "tokens": _get_budget_consumed()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _exec_resume(item: dict, workspace: str) -> dict:
    """checkpoint에서 resume — 중단된 pipeline을 이어간다."""
    # resume item의 data는 checkpoint 전체
    # 실제 resume는 main()의 checkpoint resume 로직이 처리하므로
    # 여기서는 성공 반환 (items가 이미 remaining으로 교체됨)
    return {"ok": True, "tokens": 0}


# ── Stop-file 프로토콜 ──

def _should_stop(workspace: str, project_id: str) -> bool:
    stop_file = os.path.join(workspace, ".af_runtime", "daemon", f"{project_id}.stop")
    return os.path.isfile(stop_file)


def _clear_stop_file(workspace: str, project_id: str) -> None:
    stop_file = os.path.join(workspace, ".af_runtime", "daemon", f"{project_id}.stop")
    try:
        if os.path.isfile(stop_file):
            os.remove(stop_file)
    except Exception:
        pass


# ── Checkpoint 헬퍼 ──

def _save_checkpoint(workspace: str, project_id: str, stage: str, data: dict) -> None:
    try:
        from core.hooks.checkpoint import save_wake_checkpoint
        save_wake_checkpoint(workspace, project_id, stage, data)
    except Exception:
        pass


# ── 비용/예산 헬퍼 ──

def _get_budget_consumed() -> int:
    try:
        from core.run_budget import get_run_budget
        return get_run_budget().consumed
    except Exception:
        return 0


def _get_pending_costs(workspace: str) -> list[dict]:
    try:
        from core.control.run_ledger import RunLedger
        # 모든 run의 pending costs (daemon worker는 보통 1 run)
        ledger = RunLedger(workspace)
        all_costs = []
        for costs in ledger._cost_buffer.values():
            all_costs.extend(costs)
        return all_costs
    except Exception:
        return []


# ── 기타 헬퍼 ──

def _update_todo_marker(workspace: str) -> None:
    todo = os.path.join(workspace, ".todo.md")
    marker = os.path.join(workspace, ".af_runtime", ".todo_mtime")
    if os.path.isfile(todo):
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w") as f:
                f.write(str(os.path.getmtime(todo)))
        except Exception:
            pass


def _write_result(path: str, result: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        fallback = path + ".fallback.json"
        try:
            with open(fallback, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
        except Exception:
            pass


if __name__ == "__main__":
    main()
```

---

## Part 6: CLI 서브커맨드

### 수정: `run_factory_cli.py`

```python
# ── daemon-worker 서브커맨드 (line ~139 이후) ──
if effective_argv and effective_argv[0] == "daemon-worker":
    from core.daemon_worker import main as daemon_worker_main
    sys.argv = ["af-daemon-worker"] + effective_argv[1:]
    daemon_worker_main()
    return

# ── serve 서브커맨드 ──
if effective_argv and effective_argv[0] == "serve":
    _run_serve(effective_argv[1:])
    return


def _run_serve(argv):
    """af serve — Provider-Agnostic Supervisor+Worker 데몬."""
    import argparse
    parser = argparse.ArgumentParser(description="Agent Factory Daemon")
    parser.add_argument("--projects", type=str, required=True,
                        help="Comma-separated project IDs")
    parser.add_argument("--projects-root", type=str, default="",
                        help="Projects root directory")
    parser.add_argument("--interval", type=int, default=300,
                        help="Scan interval seconds (default: 300)")
    parser.add_argument("--budget", type=int, default=0,
                        help="Token budget per wake (0=unlimited)")
    parser.add_argument("--no-code-watch", action="store_true",
                        help="Disable code change detection")
    parser.add_argument("--worker-providers", type=str, default="",
                        help="Worker providers (comma-separated, e.g., claude_cli,gemini_cli)")
    parser.add_argument("--control-providers", type=str, default="",
                        help="Control-plane providers")
    parser.add_argument("--cross-verify-providers", type=str, default="",
                        help="Cross-verification providers")
    parser.add_argument("--log", type=str, default="")
    args = parser.parse_args(argv)

    from core.daemon_supervisor import DaemonSupervisor, DaemonConfig

    factory_root = os.path.dirname(os.path.abspath(__file__))
    projects_root = args.projects_root or os.path.join(factory_root, "projects")

    # Provider Policy 구성
    provider_policy = {}
    if args.worker_providers:
        provider_policy["worker_providers"] = [p.strip() for p in args.worker_providers.split(",")]
    if args.control_providers:
        provider_policy["control_plane_providers"] = [p.strip() for p in args.control_providers.split(",")]
    if args.cross_verify_providers:
        provider_policy["cross_verify_providers"] = [p.strip() for p in args.cross_verify_providers.split(",")]

    config = DaemonConfig(
        projects=[p.strip() for p in args.projects.split(",")],
        projects_root=projects_root,
        factory_root=factory_root,
        check_interval_sec=args.interval,
        budget_per_wake=args.budget,
        code_watch=not args.no_code_watch,
        log_path=args.log,
        provider_policy=provider_policy,
    )
    daemon = DaemonSupervisor(config)
    daemon.run_forever()
```

---

## 수정: `af.spec`

```python
hiddenimports=[
    # ... 기존 ...
    'core.daemon_supervisor',
    'core.daemon_worker',
]
```

---

## 구현 순서

```
Phase 1: Subprocess 계약
  ├─ core/utils.py: build_worker_cmd() 추가
  ├─ core/dynamic_orchestrator.py: inline spawn → build_worker_cmd() 교체
  ├─ run_factory_cli.py: daemon-worker 서브커맨드 추가
  └─ af.spec: hiddenimports 추가
  검증: frozen 빌드 후 af.exe daemon-worker --help 동작 확인

Phase 2: RunLedger 비용 추적
  ├─ core/control/run_ledger.py: _cost_buffer, record_agent_cost(), flush_cost_summary(),
  │   get_pending_costs(), restore_pending_costs(), close_run() 확장
  ├─ core/agent_runner.py: _flush_trace()에서 RunLedger.record_agent_cost() 호출
  └─ core/dynamic_orchestrator.py: state_board에 tokens_estimated 추가
  검증: af run 후 run_ledger.jsonl 마지막 줄의 metadata에 tokens_total 존재

Phase 3: AF-Owned Checkpoint
  ├─ core/hooks/checkpoint.py: save_wake_checkpoint(), load_wake_checkpoint(), clear_wake_checkpoint()
  └─ checkpoint에 pending_cost_summary + budget_consumed 포함
  검증: checkpoint 저장 → 프로세스 kill → 재시작 → checkpoint에서 비용 복구 확인

Phase 4: 단일 프로젝트 serve
  ├─ core/daemon_supervisor.py: DaemonSupervisor, WorkDetector, CodeWatcher
  ├─ core/daemon_worker.py: Worker 엔트리포인트
  ├─ run_factory_cli.py: serve 서브커맨드
  └─ stop-file 프로토콜
  검증: af serve --projects test_project --interval 10
        → board에 pending task 추가 → Worker spawn 확인
        → stop-file 생성 → Worker graceful stop 확인
        → core/ 파일 수정 → Worker restart 확인

Phase 5: Provider Policy + Failover
  ├─ daemon_worker.py: configure_providers(policy.worker_providers)
  ├─ agent_runner.py: INFRA 실패 시 다음 provider로 continue
  └─ CLI: --worker-providers, --control-providers 인자
  검증: --worker-providers claude_cli,gemini_cli
        → claude quota 시 gemini로 failover 확인

Phase 6: 멀티 프로젝트
  └─ Phase 4가 이미 멀티 프로젝트 구조이므로 검증만
  검증: af serve --projects A,B → 동시 Worker 2개 → 격리 확인
```

---

## 영향 범위

| 파일 | 변경 | Phase | 신규/수정 |
|------|------|-------|-----------|
| `core/utils.py` | `build_worker_cmd()` 추가 | 1 | 수정 |
| `core/dynamic_orchestrator.py` | spawn → `build_worker_cmd()` 교체 + `tokens_estimated` | 1,2 | 수정 |
| `core/control/run_ledger.py` | `_cost_buffer`, `record_agent_cost()`, `flush_cost_summary()`, `get_pending_costs()`, `restore_pending_costs()`, `close_run()` 확장 | 2 | 수정 |
| `core/agent_runner.py` | `_flush_trace()`에 `RunLedger.record_agent_cost()` 추가 | 2 | 수정 |
| `core/hooks/checkpoint.py` | `save_wake_checkpoint()`, `load_wake_checkpoint()`, `clear_wake_checkpoint()` | 3 | 수정 |
| `core/daemon_supervisor.py` | **신규** | 4 | 신규 |
| `core/daemon_worker.py` | **신규** | 4 | 신규 |
| `run_factory_cli.py` | `daemon-worker` + `serve` 서브커맨드 | 1,4 | 수정 |
| `af.spec` | hiddenimports 2개 | 1 | 수정 |
| `Master_Blueprint.md` | §0, §3, §12 | 1-6 | 수정 |

**신규 파일: 2개** (`daemon_supervisor.py`, `daemon_worker.py`)
**수정 파일: 7개**

---

## 시너지 매트릭스 (Provider-Agnostic 적용 후)

| 조건 | 동작 |
|------|------|
| Claude만 로그인 | `--worker-providers claude_cli` → 기존과 동일하되 무인 운영 |
| Codex만 로그인 | `--worker-providers codex_cli` → Claude hook 없이 같은 serve 경로 |
| Claude + Gemini | `--worker-providers claude_cli,gemini_cli` → quota 시 자동 failover |
| 전체 (3개) | 역할별 최적 provider + quota 우회 + 교차검증 |
| 멀티 프로젝트 | Worker 프로세스 분리 → 전역 상태 오염 없음 |
| 코드 업데이트 | CodeWatcher → stop-file → Worker restart → 최신 코드 반영 |
| crash 복구 | wake_checkpoint → pending costs + budget 복원 → resume |
