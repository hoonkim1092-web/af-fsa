# Paperclip 비교 기능 설계서 v2 — Supervisor+Worker 아키텍처

> ⚠️ **Superseded by v2.1** — 이 문서는 초안입니다. spawn 경로, Windows graceful restart, 비용 crash 복구, step checkpoint 경계가 미흡합니다. 구현 시 `2026-04-03-design-paperclip-comparison-features-v2.1.md`를 따르세요.

> 작성일: 2026-04-03
> 상태: **초안 / v2.1로 대체됨**
> 선행 문서: `docs/design_paperclip_comparison_features.md` (v1, 2026-04-02)
> 변경 사유: v1의 3가지 한계(핫 업데이트 불가, 에이전트 체크포인트 부재, 다중 인스턴스 충돌) 해소
> 설계 원칙: **흡수형 리팩터링** — 기존 시스템을 확장하되 새 인프라를 만들지 않는다

## v1 대비 변경 요약

| 항목 | v1 설계 | v2 설계 | 변경 사유 |
|------|---------|---------|-----------|
| 비용 추적 | `core/cost_ledger.py` 신규 파일 | `RunLedger.metadata`로 흡수 | 동일 JSONL 패턴 이중 구현 방지 |
| 프로젝트 격리 | `core/project_context.py` (thread-local stack) | Worker 프로세스 격리 | 프로세스 분리가 근본적 해결 |
| 데몬 실행 | 단일 프로세스에서 직접 실행 | Supervisor+Worker 2-layer | 핫 업데이트, 격리, 체크포인트 |
| 체크포인트 | 미설계 | `CheckpointHook` 확장 | 에이전트 스텝 단위 resume |

### 신규 파일 비교

| v1 (4개 신규) | v2 (2개 신규) |
|---|---|
| `core/cost_ledger.py` | ~~삭제~~ → `run_ledger.py` 확장 |
| `core/project_context.py` | ~~삭제~~ → Worker 프로세스 격리 |
| `core/daemon.py` | `core/daemon_supervisor.py` |
| `core/daemon_executor.py` | `core/daemon_worker.py` |

---

## 전체 아키텍처

```
                    ┌──────────────────────────────────┐
                    │  af serve (DaemonSupervisor)      │  ← 상주 프로세스 (< 10MB)
                    │                                   │
                    │  역할:                             │
                    │   1. 프로젝트별 WorkDetector.scan() │
                    │   2. Worker subprocess spawn/kill  │
                    │   3. 코드 변경 감시 (mtime)         │
                    │   4. graceful restart 관리          │
                    │   5. daemon_status.json 갱신       │
                    │                                   │
                    │  보유하지 않는 것:                    │
                    │   - ModelRouter, AgentRunner       │
                    │   - UnifiedMemoryFacade            │
                    │   - config_paths 상수              │
                    └──────────┬────────────────────────┘
                               │ subprocess.Popen()
                               │ (task.json → result.json)
                ┌──────────────┴──────────────────┐
                ▼                                  ▼
    ┌─────────────────────┐          ┌─────────────────────┐
    │ Worker (프로젝트 A)   │          │ Worker (프로젝트 B)   │
    │                      │          │                      │
    │ 독립 프로세스:         │          │ 독립 프로세스:         │
    │  - 자체 Python import │          │  - 자체 Python import │
    │  - 자체 RunBudget     │          │  - 자체 RunBudget     │
    │  - 자체 RunLedger     │          │  - 자체 RunLedger     │
    │  - 자체 MemoryFacade  │          │  - 자체 MemoryFacade  │
    │                      │          │                      │
    │ 기존 파이프라인 호출:   │          │ 기존 파이프라인 호출:   │
    │  MaintenancePipeline │          │  MaintenancePipeline │
    │  → RuntimeSupervisor │          │  → RuntimeSupervisor │
    │  → DynamicOrchestrator│         │  → DynamicOrchestrator│
    │  → AgentRunner       │          │  → AgentRunner       │
    └─────────────────────┘          └─────────────────────┘
```

### 핵심 원리: 기존 `agent_worker.py` 패턴의 확장

현재 `agent_worker.py`는 이미 이 패턴을 사용한다:
```
DynamicOrchestrator → task.json 작성 → subprocess.Popen(agent_worker.py) → result.json 수집
```

v2는 이를 **한 단계 위**로 올린다:
```
DaemonSupervisor → wake_task.json 작성 → subprocess.Popen(daemon_worker.py) → wake_result.json 수집
```

**daemon_worker.py는 agent_worker.py의 상위 버전** — agent가 아닌 전체 파이프라인을 실행한다.

---

## Feature 1: 에이전트별 비용 추적 (RunLedger 흡수)

### v1과의 차이

```
v1: AgentRunner → CostLedger.record()           → cost_ledger.jsonl (별도 파일)
v2: AgentRunner → result["tokens_estimated"]     → RunLedger.metadata["agent_costs"] (동일 파일)
```

### 설계

#### 수정: `core/control/run_ledger.py`

기존 `LedgerEntry.metadata`에 비용 데이터를 넣는다. 별도 스키마 변경 없음 — metadata는 이미 `dict[str, Any]`이다.

```python
class RunLedger:
    # ── 기존 API 유지 ──
    # append(), open_run(), update_run(), close_run() 변경 없음

    # ── 신규 메서드 (3개) ──

    def record_agent_cost(
        self,
        run_id: str,
        agent_name: str,
        agent_role: str,
        task_id: str,
        tokens_estimated: int,
        latency_ms: int,
        ok: bool,
    ) -> None:
        """에이전트 비용을 in-memory 버퍼에 누적한다.

        close_run() 시 metadata에 flush된다.
        실행 중에는 JSONL에 매번 쓰지 않는다 (v1의 비대화 문제 해결).
        """
        if run_id not in self._cost_buffer:
            self._cost_buffer[run_id] = []
        self._cost_buffer[run_id].append({
            "agent_name": agent_name,
            "agent_role": agent_role,
            "task_id": task_id,
            "tokens": tokens_estimated,
            "latency_ms": latency_ms,
            "ok": ok,
            "ts": _now_iso(),
        })

    def flush_costs(self, run_id: str) -> dict:
        """in-memory 비용 버퍼를 dict로 반환하고 비운다.
        close_run()이 내부적으로 호출.

        Returns:
            {"agent_costs": [...], "tokens_total": N, "top_consumers": [...]}
        """
        costs = self._cost_buffer.pop(run_id, [])
        tokens_total = sum(c["tokens"] for c in costs)
        # role별 합산 → 상위 10
        role_totals: dict[str, int] = {}
        for c in costs:
            role_totals[c["agent_role"]] = role_totals.get(c["agent_role"], 0) + c["tokens"]
        top = sorted(role_totals.items(), key=lambda x: -x[1])[:10]
        return {
            "agent_costs": costs,
            "tokens_total": tokens_total,
            "top_consumers": [{"role": r, "tokens": t} for r, t in top],
        }

    def top_consumers(self, run_id: str, limit: int = 10) -> list[dict]:
        """완료된 run의 에이전트별 비용 순위를 조회한다."""
        latest = self._get_latest(run_id)
        if not latest:
            return []
        costs = latest.metadata.get("agent_costs", [])
        role_totals: dict[str, int] = {}
        for c in costs:
            role_totals[c.get("agent_role", "?")] = (
                role_totals.get(c.get("agent_role", "?"), 0) + c.get("tokens", 0)
            )
        top = sorted(role_totals.items(), key=lambda x: -x[1])[:limit]
        return [{"role": r, "tokens": t} for r, t in top]
```

#### 수정: `close_run()` 확장

```python
def close_run(self, run_id: str, outcome: str, state: str = "closed") -> LedgerEntry | None:
    latest = self._get_latest(run_id)
    if latest is None:
        return None

    # ★ 비용 flush → metadata에 병합
    cost_data = self.flush_costs(run_id)
    merged_metadata = {**latest.metadata, **cost_data}

    closed = LedgerEntry(
        # ... 기존 필드 동일 ...
        metadata=merged_metadata,
    )
    self.append(closed)
    return closed
```

#### 데이터 구조 (RunLedger.metadata 예시)

```json
{
  "agent_costs": [
    {"agent_name": "agent_1", "agent_role": "backend_dev", "task_id": "task_001",
     "tokens": 1250, "latency_ms": 3400, "ok": true, "ts": "2026-04-03T09:15:30Z"},
    {"agent_name": "agent_2", "agent_role": "tester", "task_id": "task_002",
     "tokens": 800, "latency_ms": 2100, "ok": true, "ts": "2026-04-03T09:16:45Z"}
  ],
  "tokens_total": 2050,
  "top_consumers": [
    {"role": "backend_dev", "tokens": 1250},
    {"role": "tester", "tokens": 800}
  ]
}
```

#### 수정: `core/agent_runner.py` — `_flush_trace()`

```python
# 기존 RunBudget 기록 직후:
_tokens = max(1, len(_text) // 4)
result["tokens_estimated"] = _tokens

# RunLedger에 비용 기록 (CostLedger 대신)
try:
    from core.control.run_ledger import RunLedger
    RunLedger(target_workspace).record_agent_cost(
        run_id=run_id,
        agent_name=str(agent.get("name", "")),
        agent_role=str(agent.get("role", "")),
        task_id=safe_id(task_id),
        tokens_estimated=_tokens,
        latency_ms=int(result.get("latency_ms", 0)),
        ok=bool(result.get("ok")),
    )
except Exception:
    pass
```

#### 충돌 해결: metadata 비대화 문제

v1에서 식별한 "update_run마다 agent_costs 전체 복사" 문제를 **in-memory 버퍼 + close 시 flush** 패턴으로 해결:

```
실행 중:  agent 완료 → _cost_buffer[run_id].append(cost)  ← 메모리만, JSONL 안 씀
종료 시:  close_run() → flush_costs() → metadata에 한 번만 기록 ← JSONL 1줄
```

**트레이드오프**: Worker 프로세스가 crash하면 in-memory 버퍼가 소실된다. 이를 보완하기 위해 CheckpointHook이 agent_state에 누적 비용을 포함시킨다 (Feature 4 참조).

### 수정 파일 목록

| 파일 | 변경 |
|------|------|
| `core/control/run_ledger.py` | `_cost_buffer` dict 추가, `record_agent_cost()`, `flush_costs()`, `top_consumers()` 메서드 추가, `close_run()` 확장 |
| `core/agent_runner.py` | `_flush_trace()`에서 `RunLedger.record_agent_cost()` 호출 |
| `core/dynamic_orchestrator.py` | state_board entries에 `tokens_estimated` 필드 추가 |

**삭제된 파일**: ~~`core/cost_ledger.py`~~ (불필요)

---

## Feature 2: `af serve` 데몬 — Supervisor + Worker 2-Layer

### v1과의 차이

```
v1: AfDaemon.run_forever() → 같은 프로세스에서 MaintenancePipeline 직접 호출
v2: DaemonSupervisor.run_forever() → Worker subprocess spawn → Worker가 파이프라인 실행
```

### 신규 파일: `core/daemon_supervisor.py`

Supervisor는 **극도로 가벼운 프로세스 관리자**이다. AF의 핵심 모듈을 import하지 않는다.

```python
"""
core/daemon_supervisor.py
==========================
DaemonSupervisor — 프로세스 관리 전용 데몬.

이 모듈은 AF 핵심 코드(ModelRouter, AgentRunner 등)를 import하지 않는다.
Worker subprocess가 실제 작업을 수행한다.

이유:
  1. 핫 업데이트: Worker가 새 프로세스로 spawn되므로 최신 코드 사용
  2. 프로젝트 격리: Worker가 독립 프로세스이므로 싱글톤 충돌 없음
  3. 안정성: Supervisor crash 확률 최소화 (import 최소)

의존성: os, sys, json, time, signal, subprocess, threading (표준 라이브러리만)
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
    projects: list[str]               # 감시 대상 프로젝트 ID 목록
    projects_root: str                # 프로젝트 루트 디렉토리
    factory_root: str                 # agent-factory 소스 루트 (Worker 실행 경로)
    check_interval_sec: int = 300     # 스캔 간격 (idle 시)
    budget_per_wake: int = 0          # Worker당 토큰 예산 (0=unlimited)
    log_path: str = ""                # 로그 파일 경로
    code_watch: bool = True           # 코드 변경 감시 활성화


class WorkDetector:
    """
    프로젝트별 작업 감지기.

    AF 핵심 코드를 import하지 않고 파일 시스템만 읽는다.
    Worker가 실행할 작업이 있는지 판단하는 것이 목적.
    """

    def scan(self, workspace: str) -> list[dict]:
        """
        workspace를 스캔하여 작업 목록을 반환한다.

        감지 소스 4개:
          1. project_board_state.json에서 pending 태스크
          2. .af_runtime/mailbox/에서 미처리 메시지
          3. .todo.md 변경 (mtime 비교)
          4. run_ledger.jsonl에서 stale run

        Returns:
            list of {"source": str, "detail": str, "data": dict}
        """
        items = []
        items.extend(self._scan_board(workspace))
        items.extend(self._scan_mailbox(workspace))
        items.extend(self._scan_todo(workspace))
        items.extend(self._scan_stale_runs(workspace))
        return items

    def _scan_board(self, workspace: str) -> list[dict]:
        board_path = os.path.join(workspace, "project_board_state.json")
        if not os.path.isfile(board_path):
            return []
        try:
            with open(board_path, encoding="utf-8") as f:
                board = json.load(f)
            pending = [t for t in (board.get("tasks") or [])
                       if t.get("status") == "pending"]
            if pending:
                return [{"source": "board", "detail": f"{len(pending)} pending tasks",
                         "data": {"tasks": pending}}]
        except Exception:
            pass
        return []

    def _scan_mailbox(self, workspace: str) -> list[dict]:
        mailbox_dir = os.path.join(workspace, ".af_runtime", "mailbox")
        if not os.path.isdir(mailbox_dir):
            return []
        try:
            msgs = [f for f in os.listdir(mailbox_dir)
                    if f.endswith(".json") and not f.startswith("_processed_")]
            if msgs:
                return [{"source": "mailbox", "detail": f"{len(msgs)} unread messages",
                         "data": {"files": msgs}}]
        except Exception:
            pass
        return []

    def _scan_todo(self, workspace: str) -> list[dict]:
        todo_path = os.path.join(workspace, ".todo.md")
        marker_path = os.path.join(workspace, ".af_runtime", ".todo_mtime")
        if not os.path.isfile(todo_path):
            return []
        try:
            current_mtime = os.path.getmtime(todo_path)
            last_mtime = 0.0
            if os.path.isfile(marker_path):
                with open(marker_path) as f:
                    last_mtime = float(f.read().strip())
            if current_mtime > last_mtime:
                return [{"source": "todo", "detail": "todo.md changed",
                         "data": {"mtime": current_mtime}}]
        except Exception:
            pass
        return []

    def _scan_stale_runs(self, workspace: str) -> list[dict]:
        ledger_path = os.path.join(workspace, ".af_runtime", "control", "run_ledger.jsonl")
        if not os.path.isfile(ledger_path):
            return []
        try:
            from datetime import datetime, timezone
            stale = []
            with open(ledger_path, encoding="utf-8") as f:
                # run_id별 마지막 항목
                latest: dict[str, dict] = {}
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    latest[entry.get("run_id", "")] = entry

            now = datetime.now(timezone.utc)
            for run_id, entry in latest.items():
                if entry.get("closed_at"):
                    continue
                started = entry.get("started_at", "")
                if not started:
                    stale.append(entry)
                    continue
                try:
                    dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    if (now - dt).total_seconds() > 1800:
                        stale.append(entry)
                except Exception:
                    pass

            if stale:
                return [{"source": "stale_run", "detail": f"{len(stale)} stale runs",
                         "data": {"run_ids": [s.get("run_id") for s in stale]}}]
        except Exception:
            pass
        return []


class CodeWatcher:
    """
    core/*.py 파일의 mtime을 추적하여 코드 변경을 감지한다.

    변경 감지 시 Supervisor는 현재 Worker의 graceful stop을 요청하고
    새 Worker를 spawn한다 (최신 코드로 fresh import).
    """

    def __init__(self, factory_root: str):
        self._root = factory_root
        self._snapshot: dict[str, float] = {}
        self._take_snapshot()

    def _take_snapshot(self) -> None:
        """core/ 디렉토리의 모든 .py 파일 mtime을 기록한다."""
        self._snapshot = {}
        core_dir = os.path.join(self._root, "core")
        if not os.path.isdir(core_dir):
            return
        for root, _dirs, files in os.walk(core_dir):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    try:
                        self._snapshot[path] = os.path.getmtime(path)
                    except OSError:
                        pass

    def check_changed(self) -> list[str]:
        """
        마지막 snapshot 이후 변경된 .py 파일 목록을 반환한다.
        변경이 있으면 snapshot을 갱신한다.

        Returns:
            변경된 파일 경로 목록 (빈 리스트면 변경 없음)
        """
        changed = []
        new_snapshot: dict[str, float] = {}
        core_dir = os.path.join(self._root, "core")
        if not os.path.isdir(core_dir):
            return []
        for root, _dirs, files in os.walk(core_dir):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(path)
                        new_snapshot[path] = mtime
                        old_mtime = self._snapshot.get(path)
                        if old_mtime is None or mtime > old_mtime:
                            changed.append(path)
                    except OSError:
                        pass

        if changed:
            self._snapshot = new_snapshot
        return changed


class DaemonSupervisor:
    """
    Supervisor 프로세스. Worker subprocess를 관리한다.

    생명주기:
      1. run_forever() 진입
      2. 프로젝트별 WorkDetector.scan()
      3. 작업 있으면 Worker spawn (daemon_worker.py)
      4. Worker 완료 대기 (result.json 수집)
      5. 코드 변경 감지 시 Worker graceful restart
      6. SIGINT/SIGTERM → graceful shutdown
    """

    def __init__(self, config: DaemonConfig):
        self._config = config
        self._detector = WorkDetector()
        self._watcher = CodeWatcher(config.factory_root) if config.code_watch else None
        self._shutdown = False
        self._sleep_event = threading.Event()
        self._active_workers: dict[str, subprocess.Popen] = {}  # project_id → Popen
        self._status = {
            "state": "initializing",
            "pid": os.getpid(),
            "started_at": "",
            "last_check_at": "",
            "last_work_at": "",
            "current_project": "",
            "total_wakes": 0,
            "projects": config.projects,
        }

    def run_forever(self) -> None:
        """메인 루프."""
        self._install_signal_handlers()
        self._status["started_at"] = _now_iso()
        self._status["state"] = "running"
        self._write_status()

        print(f"[DaemonSupervisor] started — watching {self._config.projects}")

        while not self._shutdown:
            self._status["state"] = "scanning"
            self._status["last_check_at"] = _now_iso()

            # 1. 코드 변경 확인 → 실행 중인 Worker graceful restart
            if self._watcher:
                changed = self._watcher.check_changed()
                if changed:
                    print(f"[DaemonSupervisor] code changed: {[os.path.basename(c) for c in changed[:5]]}")
                    self._graceful_restart_all()

            # 2. 프로젝트별 스캔 → Worker spawn
            for project_id in self._config.projects:
                if self._shutdown:
                    break
                workspace = os.path.join(self._config.projects_root, project_id)
                if not os.path.isdir(workspace):
                    continue

                # 이미 Worker가 실행 중이면 스킵
                if project_id in self._active_workers:
                    worker = self._active_workers[project_id]
                    if worker.poll() is None:  # 아직 실행 중
                        continue
                    else:
                        self._collect_result(project_id)

                items = self._detector.scan(workspace)
                if items:
                    self._spawn_worker(project_id, workspace, items)

            # 3. 완료된 Worker 수거
            self._collect_finished_workers()

            self._write_status()

            # 4. sleep
            self._status["state"] = "sleeping"
            self._sleep_event.wait(timeout=self._config.check_interval_sec)
            self._sleep_event.clear()

        # shutdown
        self._graceful_shutdown()

    def _spawn_worker(self, project_id: str, workspace: str, items: list[dict]) -> None:
        """Worker subprocess를 spawn한다."""
        self._status["total_wakes"] = self._status.get("total_wakes", 0) + 1
        self._status["last_work_at"] = _now_iso()
        self._status["current_project"] = project_id

        # wake_task.json 작성 (agent_worker.py 패턴과 동일)
        runtime_dir = os.path.join(workspace, ".af_runtime", "daemon")
        os.makedirs(runtime_dir, exist_ok=True)
        task_file = os.path.join(runtime_dir, "wake_task.json")
        result_file = os.path.join(runtime_dir, "wake_result.json")

        task_data = {
            "project_id": project_id,
            "workspace": workspace,
            "factory_root": self._config.factory_root,
            "items": items,
            "budget": self._config.budget_per_wake,
            "spawned_at": _now_iso(),
        }

        with open(task_file, "w", encoding="utf-8") as f:
            json.dump(task_data, f, ensure_ascii=False, indent=2)

        # Worker subprocess 실행
        worker_script = os.path.join(self._config.factory_root, "core", "daemon_worker.py")
        cmd = [
            sys.executable, worker_script,
            "--task-file", task_file,
            "--result-file", result_file,
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self._config.factory_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self._active_workers[project_id] = proc
            print(
                f"[DaemonSupervisor] Worker spawned for {project_id} "
                f"(pid={proc.pid}, items={len(items)})"
            )
        except Exception as exc:
            print(f"[DaemonSupervisor] Worker spawn failed for {project_id}: {exc}")

    def _collect_result(self, project_id: str) -> dict | None:
        """Worker의 result.json을 수집한다."""
        workspace = os.path.join(self._config.projects_root, project_id)
        result_file = os.path.join(workspace, ".af_runtime", "daemon", "wake_result.json")
        if not os.path.isfile(result_file):
            return None
        try:
            with open(result_file, encoding="utf-8") as f:
                result = json.load(f)
            os.remove(result_file)  # 수거 후 삭제
            print(
                f"[DaemonSupervisor] Worker result for {project_id}: "
                f"success={result.get('success')}, tokens={result.get('tokens_total', 0)}"
            )
            return result
        except Exception:
            return None

    def _collect_finished_workers(self) -> None:
        """완료된 Worker 프로세스를 정리한다."""
        finished = []
        for project_id, proc in self._active_workers.items():
            if proc.poll() is not None:
                finished.append(project_id)
        for project_id in finished:
            self._collect_result(project_id)
            del self._active_workers[project_id]

    def _graceful_restart_all(self) -> None:
        """모든 활성 Worker에 graceful stop을 요청한다.

        Worker는 현재 task를 완료한 후 종료된다.
        다음 scan 시 새 Worker가 최신 코드로 spawn된다.
        """
        for project_id, proc in list(self._active_workers.items()):
            if proc.poll() is None:
                print(f"[DaemonSupervisor] requesting graceful stop for Worker:{project_id}")
                # Worker에 SIGTERM 전송 → Worker가 현재 task 완료 후 종료
                try:
                    proc.terminate()  # SIGTERM on Unix, TerminateProcess on Windows
                except Exception:
                    pass

    def _graceful_shutdown(self) -> None:
        """모든 Worker 종료 대기 후 Supervisor 종료."""
        print("[DaemonSupervisor] shutting down...")
        for project_id, proc in self._active_workers.items():
            if proc.poll() is None:
                print(f"[DaemonSupervisor] waiting for Worker:{project_id} (pid={proc.pid})...")
                try:
                    proc.terminate()
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                self._collect_result(project_id)
        self._status["state"] = "stopped"
        self._write_status()
        print("[DaemonSupervisor] stopped.")

    def _install_signal_handlers(self) -> None:
        """SIGINT/SIGTERM → graceful shutdown."""
        def _handler(signum, frame):
            print(f"\n[DaemonSupervisor] received signal {signum}, initiating shutdown...")
            self._shutdown = True
            self._sleep_event.set()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    def _write_status(self) -> None:
        """daemon_status.json 갱신."""
        status_dir = os.path.join(self._config.projects_root, ".af_runtime")
        os.makedirs(status_dir, exist_ok=True)
        status_path = os.path.join(status_dir, "daemon_status.json")
        try:
            tmp = status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._status, f, ensure_ascii=False, indent=2)
            os.replace(tmp, status_path)
        except Exception:
            pass


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

### 신규 파일: `core/daemon_worker.py`

Worker는 **`agent_worker.py`의 상위 버전**이다. 동일한 file-based IPC 패턴을 사용한다.

```python
"""
core/daemon_worker.py
======================
데몬 Worker 프로세스.

DaemonSupervisor에 의해 subprocess로 spawn된다.
기존 AF 파이프라인(MaintenancePipeline, FSALoop 등)을 실행한다.

사용법:
  python core/daemon_worker.py --task-file <wake_task.json> --result-file <wake_result.json>

wake_task.json 스키마:
  {
    "project_id": "minesweeper",
    "workspace": "C:/Projects/minesweeper",
    "factory_root": "C:/Project/agent-factory",
    "items": [{"source": "board", "detail": "3 pending tasks", "data": {...}}],
    "budget": 50000,
    "spawned_at": "2026-04-03T..."
  }

wake_result.json 스키마:
  {
    "success": true,
    "project_id": "minesweeper",
    "items_processed": 3,
    "tokens_total": 12500,
    "duration_ms": 45000,
    "errors": []
  }
"""
from __future__ import annotations

import argparse
import json
import os
import signal
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
        _write_result(args.result_file, {"success": False, "error": f"task load failed: {exc}"})
        sys.exit(1)

    factory_root = task.get("factory_root", "")
    workspace = task.get("workspace", "")
    project_id = task.get("project_id", "")
    items = task.get("items", [])
    budget = task.get("budget", 0)

    if factory_root and factory_root not in sys.path:
        sys.path.insert(0, factory_root)

    # ── 2. graceful stop 핸들러 ──
    _stop_requested = False

    def _sigterm_handler(signum, frame):
        nonlocal _stop_requested
        _stop_requested = True
        print(f"[DaemonWorker:{project_id}] graceful stop requested, finishing current task...")

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # ── 3. 예산 설정 ──
    if budget:
        try:
            from core.run_budget import set_run_budget
            set_run_budget(budget)
        except Exception:
            pass

    # ── 4. 항목별 실행 ──
    start = time.time()
    result = {
        "success": True,
        "project_id": project_id,
        "items_processed": 0,
        "tokens_total": 0,
        "duration_ms": 0,
        "errors": [],
    }

    for item in items:
        if _stop_requested:
            print(f"[DaemonWorker:{project_id}] stop requested, exiting after {result['items_processed']} items")
            break

        source = item.get("source", "")
        try:
            item_result = _execute_item(source, item, workspace, project_id)
            result["items_processed"] += 1
            result["tokens_total"] += item_result.get("tokens", 0)
            if not item_result.get("ok", False):
                result["errors"].append(item_result.get("error", "unknown"))
        except Exception as exc:
            result["errors"].append(f"{source}: {exc}")
            traceback.print_exc()

    result["duration_ms"] = int((time.time() - start) * 1000)
    result["success"] = len(result["errors"]) == 0

    # ── 5. todo mtime 마커 갱신 ──
    _update_todo_marker(workspace)

    # ── 6. 결과 기록 ──
    _write_result(args.result_file, result)
    print(f"[DaemonWorker:{project_id}] done — {result['items_processed']} items, {result['tokens_total']} tokens")


def _execute_item(source: str, item: dict, workspace: str, project_id: str) -> dict:
    """
    WorkItem source에 따라 적절한 파이프라인으로 라우팅한다.

    기존 AF 모듈을 여기서 import한다 (Worker 프로세스 시작 시 fresh import).
    """
    if source == "board":
        return _execute_board_tasks(item, workspace)
    elif source == "mailbox":
        return _execute_mailbox(item, workspace)
    elif source == "todo":
        return _execute_todo(item, workspace)
    elif source == "stale_run":
        return _execute_stale_resume(item, workspace)
    else:
        return {"ok": False, "error": f"unknown source: {source}"}


def _execute_board_tasks(item: dict, workspace: str) -> dict:
    """pending board tasks → MaintenancePipeline 또는 DynamicOrchestrator."""
    try:
        from core.control.intake import ControlPlaneIntake
        from core.control.maintenance_pipeline import MaintenancePipeline
        from core.project_pipeline import ProjectPipeline
        from core.model_router import ModelRouter

        mr = ModelRouter()
        intake = ControlPlaneIntake(workspace)
        pipeline_obj = ProjectPipeline(mr, None, None, None)
        mp = MaintenancePipeline(pipeline_obj, workspace)

        # pending tasks를 하나의 maintenance request로 통합
        tasks = item.get("data", {}).get("tasks", [])
        task_summary = "; ".join(t.get("description", "")[:60] for t in tasks[:5])
        normalized = intake.normalize({"raw_input": f"[daemon] process pending: {task_summary}"})

        prepared = mp.prepare(normalized)
        result = mp.execute(prepared, normalized)
        return {"ok": result.get("success", False), "tokens": _get_run_tokens(workspace)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _execute_mailbox(item: dict, workspace: str) -> dict:
    """미처리 mailbox 메시지 → ControlPlaneIntake → MaintenancePipeline."""
    try:
        from core.control.intake import ControlPlaneIntake
        from core.control.maintenance_pipeline import MaintenancePipeline
        from core.project_pipeline import ProjectPipeline
        from core.model_router import ModelRouter

        mr = ModelRouter()
        intake = ControlPlaneIntake(workspace)
        pipeline_obj = ProjectPipeline(mr, None, None, None)
        mp = MaintenancePipeline(pipeline_obj, workspace)

        files = item.get("data", {}).get("files", [])
        mailbox_dir = os.path.join(workspace, ".af_runtime", "mailbox")
        processed = 0

        for fname in files:
            msg_path = os.path.join(mailbox_dir, fname)
            try:
                with open(msg_path, encoding="utf-8") as f:
                    msg = json.load(f)
                normalized = intake.normalize(msg)
                prepared = mp.prepare(normalized)
                mp.execute(prepared, normalized)
                # 처리 완료 → 파일 이름 변경
                os.rename(msg_path, os.path.join(mailbox_dir, f"_processed_{fname}"))
                processed += 1
            except Exception as exc:
                print(f"[DaemonWorker] mailbox {fname} failed: {exc}")

        return {"ok": processed > 0, "tokens": _get_run_tokens(workspace)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _execute_todo(item: dict, workspace: str) -> dict:
    """todo.md 변경 → FSALoop.run_mission()."""
    try:
        from core.fsa_loop import FSALoop
        from core.model_router import ModelRouter
        from core.agent_runner import AgentRunner

        mr = ModelRouter()
        runner = AgentRunner(mr)
        fsa = FSALoop(mr, runner, workspace=workspace)

        todo_path = os.path.join(workspace, ".todo.md")
        with open(todo_path, encoding="utf-8") as f:
            todo_content = f.read()

        result = fsa.run_mission(
            task_input=f"[daemon] process todo changes:\n{todo_content[:2000]}",
            workspace=workspace,
        )
        return {"ok": result.get("ok", False), "tokens": _get_run_tokens(workspace)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _execute_stale_resume(item: dict, workspace: str) -> dict:
    """stale run → OrchestratorManifestStore.load_resume_state() → resume."""
    try:
        from core.continuity.manifest_store import OrchestratorManifestStore
        from core.dynamic_orchestrator import DynamicOrchestrator
        from core.model_router import ModelRouter

        store = OrchestratorManifestStore
        resume_state = store.load_resume_state()
        if not resume_state:
            return {"ok": True, "tokens": 0}  # nothing to resume

        mr = ModelRouter()
        orch = DynamicOrchestrator(mr, workspace=workspace)
        result = orch.run_project(
            f"[daemon] resume stale run",
            workspace,
            resume_state=resume_state,
        )
        return {"ok": bool(result), "tokens": _get_run_tokens(workspace)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _get_run_tokens(workspace: str) -> int:
    """현재 프로세스의 RunBudget consumed 값을 반환한다."""
    try:
        from core.run_budget import get_run_budget
        return get_run_budget().consumed
    except Exception:
        return 0


def _update_todo_marker(workspace: str) -> None:
    """todo.md mtime 마커를 갱신하여 다음 스캔에서 중복 감지를 방지한다."""
    todo_path = os.path.join(workspace, ".todo.md")
    marker_path = os.path.join(workspace, ".af_runtime", ".todo_mtime")
    if os.path.isfile(todo_path):
        try:
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, "w") as f:
                f.write(str(os.path.getmtime(todo_path)))
        except Exception:
            pass


def _write_result(path: str, result: dict) -> None:
    """결과 파일 작성 (agent_worker.py 패턴 동일)."""
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

### 수정: `run_factory_cli.py` — `serve` 서브커맨드

```python
# 기존 early return 패턴과 동일하게 추가:
if effective_argv and effective_argv[0] == "serve":
    _run_serve(effective_argv[1:])
    return

def _run_serve(argv):
    """af serve — Supervisor+Worker 데몬 모드."""
    import argparse
    parser = argparse.ArgumentParser(description="Agent Factory Daemon (Supervisor+Worker)")
    parser.add_argument("--projects", type=str, required=True,
                        help="Comma-separated project IDs")
    parser.add_argument("--projects-root", type=str, default="",
                        help="Projects root directory (default: ./projects)")
    parser.add_argument("--interval", type=int, default=300,
                        help="Scan interval in seconds (default: 300)")
    parser.add_argument("--budget", type=int, default=0,
                        help="Token budget per Worker wake (0=unlimited)")
    parser.add_argument("--no-code-watch", action="store_true",
                        help="Disable code change detection")
    parser.add_argument("--log", type=str, default="",
                        help="Log file path")
    args = parser.parse_args(argv)

    from core.daemon_supervisor import DaemonSupervisor, DaemonConfig

    factory_root = os.path.dirname(os.path.abspath(__file__))
    projects_root = args.projects_root or os.path.join(factory_root, "projects")

    config = DaemonConfig(
        projects=[p.strip() for p in args.projects.split(",")],
        projects_root=projects_root,
        factory_root=factory_root,
        check_interval_sec=args.interval,
        budget_per_wake=args.budget,
        log_path=args.log,
        code_watch=not args.no_code_watch,
    )
    daemon = DaemonSupervisor(config)
    daemon.run_forever()
```

---

## Feature 3: 프로젝트 격리 — 프로세스 분리로 대체

### v1과의 차이

```
v1: thread-local stack + project_scope() + UnifiedMemoryFacade.for_project()
v2: Worker subprocess = 프로세스 자체가 격리 단위
```

### 왜 별도 코드가 필요 없는가

Worker는 `subprocess.Popen()`으로 spawn되는 **독립 Python 프로세스**이다:

| 격리 대상 | v1 (thread-local) | v2 (프로세스 분리) |
|---|---|---|
| `config_paths` 모듈 상수 | `project_scope()` 내에서 임시 교체 | Worker가 `sys.path`에 factory_root만 넣음 → import 시점에 고정 |
| `UnifiedMemoryFacade` singleton | `for_project()` 분기 | 프로세스 별도 → 싱글톤 충돌 원천 불가 |
| `RunBudget` 모듈 싱글톤 | reset 타이밍 관리 필요 | 프로세스별 독립 인스턴스 → 충돌 없음 |
| `RunLedger` | workspace 파라미터로 이미 분리 | 동일 (변경 없음) |
| `SkillFeedback` | `DATA_DIR` import 시점 고정 문제 | 프로세스별 독립 import → 문제 없음 |

**결과: `core/project_context.py` 신규 파일 불필요. `config_paths.py` 수정 불필요. `facade.py` 수정 불필요.**

### 단일 프로젝트 모드 (기존 `af run`)에서는?

기존 `af run`은 변경 없이 그대로 작동한다. 단일 프로세스, 단일 프로젝트이므로 격리 문제 자체가 없다.

### 격리 증명

```
af serve --projects A,B

Supervisor (pid 1000):
  │  import: os, sys, json, subprocess만
  │  AF 핵심 코드 import 없음 → 싱글톤 오염 불가
  │
  ├─ Worker (pid 1001, 프로젝트 A):
  │    import: core.* (fresh)
  │    RunBudget._budget → A 전용
  │    UnifiedMemoryFacade._shared_instance → A 전용
  │    RunLedger(A/workspace) → A/.af_runtime/control/run_ledger.jsonl
  │
  └─ Worker (pid 1002, 프로젝트 B):
       import: core.* (fresh)
       RunBudget._budget → B 전용 (pid 1001과 별개 메모리)
       UnifiedMemoryFacade._shared_instance → B 전용
       RunLedger(B/workspace) → B/.af_runtime/control/run_ledger.jsonl
```

---

## Feature 4 (신규): 에이전트 스텝 체크포인트 — CheckpointHook 확장

### 왜 필요한가

v1에서 미설계였던 "에이전트 수준의 세밀한 체크포인트" 문제를 해결한다. Worker가 crash해도 에이전트가 처음부터 다시 시작하지 않고 마지막 스텝부터 이어간다.

### 현재 CheckpointHook 분석

`core/hooks/checkpoint.py`는 이미 다음을 수행한다:
- `pre_execute()`: checkpoint.json이 있으면 `cycle`, `current_task`, `eval_history`, `metadata`를 복구
- `post_execute()`: JSON-직렬화 가능한 agent_state 필드를 checkpoint.json에 저장

**부족한 점**: 현재 checkpoint는 **사이클 단위**이다. 한 사이클 안에서 에이전트가 파일 3개를 수정해야 하는데 2개째에서 crash하면, 다음 복구 시 1개째부터 다시 시작한다.

### 설계: 스텝 단위 체크포인트

기존 `CheckpointHook`을 확장하여 **사이클 내 스텝 추적**을 추가한다.

```python
# core/hooks/checkpoint.py — 확장

class CheckpointHook(ContinuationHook):
    PRIORITY = 90

    def __init__(self, runs_dir: str | None = None):
        self._runs_dir = runs_dir

    # ── 기존 API 유지 ──
    # pre_execute(), post_execute() 변경 없음

    # ── 신규: 스텝 단위 체크포인트 ──

    def save_step(self, agent_state: dict, step_id: str, step_data: dict) -> None:
        """에이전트 실행 중 특정 스텝 완료 시 호출.

        Args:
            agent_state: 현재 에이전트 상태 (run_id 포함)
            step_id: 스텝 식별자 (예: "file_edit_2_of_3")
            step_data: 스텝별 저장 데이터 (예: {"files_completed": ["a.py", "b.py"]})
        """
        path = self._checkpoint_path(agent_state)
        if not path:
            return
        try:
            # 기존 checkpoint 로드
            existing = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)

            # steps 섹션에 누적
            steps = existing.get("_steps", {})
            steps[step_id] = {**step_data, "_saved_at": _now_iso()}
            existing["_steps"] = steps
            existing["_last_step"] = step_id

            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[Checkpoint] step save failed: {e}")

    def get_completed_steps(self, agent_state: dict) -> dict:
        """복구 시 완료된 스텝 목록을 반환한다.

        Returns:
            {"step_id": step_data, ...} 또는 {}
        """
        path = self._checkpoint_path(agent_state)
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("_steps", {})
        except Exception:
            return {}

    def get_last_step(self, agent_state: dict) -> str | None:
        """마지막으로 완료된 스텝 ID를 반환한다."""
        path = self._checkpoint_path(agent_state)
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("_last_step")
        except Exception:
            return None
```

### 비용 데이터 체크포인트 연동

Worker crash 시 in-memory 비용 버퍼(`RunLedger._cost_buffer`) 소실을 방지:

```python
# CheckpointHook.post_execute() 확장:
def post_execute(self, agent_state: dict, result: Any) -> Any:
    # ... 기존 serializable 필드 저장 ...

    # ★ 비용 누적값도 checkpoint에 포함
    try:
        from core.run_budget import get_run_budget
        serializable["_budget_consumed"] = get_run_budget().consumed
    except Exception:
        pass

    # ... 파일 저장 ...
```

Worker 재시작 시 `pre_execute()`에서 `_budget_consumed`를 복구하면, 이전 Worker의 비용이 이어진다.

---

## 수정: `run_factory_cli.py`

`serve` 서브커맨드를 기존 early return 패턴에 추가한다.

```python
# 기존 패턴:
# if effective_argv and effective_argv[0] == "worker": ...
# if effective_argv and effective_argv[0] == "skill-create": ...

# 추가:
if effective_argv and effective_argv[0] == "serve":
    _run_serve(effective_argv[1:])
    return
```

---

## 수정: `af.spec`

```python
hiddenimports=[
    # ... 기존 ...
    'core.daemon_supervisor',
    'core.daemon_worker',
    # core.cost_ledger 불필요 (삭제)
    # core.project_context 불필요 (삭제)
]
```

---

## 동작 흐름: 전체 시나리오

### 시나리오 1: 일상적인 데몬 운영

```
$ af serve --projects minesweeper,tictactoe --interval 300 --budget 50000

[09:00] Supervisor started (pid=1000)
[09:00] Scanning minesweeper... 0 items
[09:00] Scanning tictactoe... 0 items
[09:00] Sleeping 300s...

[09:05] Scanning minesweeper... 2 pending tasks detected
[09:05] Worker spawned for minesweeper (pid=1001)
[09:05] Scanning tictactoe... 0 items
        Worker:minesweeper executing MaintenancePipeline...
        Worker:minesweeper → AgentRunner → tokens_estimated=12500
        Worker:minesweeper → RunLedger.record_agent_cost()
[09:08] Worker result for minesweeper: success=true, tokens=12500
[09:08] Sleeping 300s...
```

### 시나리오 2: 코드 업데이트 중 실행

```
[10:00] Worker:minesweeper 실행 중 (pid=1001)...
[10:02] 개발자가 core/agent_runner.py 수정
[10:05] Supervisor: code changed: ['agent_runner.py']
[10:05] Supervisor: requesting graceful stop for Worker:minesweeper
        Worker:minesweeper → SIGTERM 수신 → _stop_requested=True
        Worker:minesweeper → 현재 task 완료 → checkpoint 저장 → 종료
[10:06] Worker result for minesweeper: success=true (partial)
[10:06] Scanning minesweeper... 1 remaining task + checkpoint
[10:06] Worker spawned for minesweeper (pid=1002)  ← 새 프로세스, 새 코드
        Worker:minesweeper → checkpoint 복구 → 이전 스텝부터 이어감
```

### 시나리오 3: 긴급 중단 (Ctrl+C)

```
[11:00] Worker:minesweeper 실행 중, Worker:tictactoe 실행 중
[11:01] Ctrl+C → Supervisor receives SIGINT
[11:01] Supervisor: shutting down...
[11:01] Supervisor: waiting for Worker:minesweeper (pid=1001)...
        Worker:minesweeper → SIGTERM → 현재 task 완료 → checkpoint 저장
[11:02] Supervisor: waiting for Worker:tictactoe (pid=1002)...
        Worker:tictactoe → SIGTERM → 현재 task 완료 → checkpoint 저장
[11:03] Supervisor: stopped.

# 재시작 시:
$ af serve --projects minesweeper,tictactoe
[11:10] Scanning minesweeper... stale run detected → resume
[11:10] Worker spawned → checkpoint에서 이어감
```

### 시나리오 4: 프로젝트 간 격리 증명

```
Worker:minesweeper (pid=1001):
  RunBudget._budget.consumed = 25000
  RunLedger("projects/minesweeper") → minesweeper/run_ledger.jsonl

Worker:tictactoe (pid=1002):
  RunBudget._budget.consumed = 8000   ← pid 1001과 독립
  RunLedger("projects/tictactoe") → tictactoe/run_ledger.jsonl

# 프로세스 메모리 완전 분리 → 상호 영향 0
```

---

## 구현 순서 및 의존성

```
Phase 1: Feature 1 (비용 추적 — RunLedger 흡수)
  ├─ run_ledger.py: _cost_buffer, record_agent_cost(), flush_costs(), top_consumers()
  ├─ agent_runner.py: _flush_trace()에서 RunLedger.record_agent_cost() 호출
  └─ dynamic_orchestrator.py: state_board에 tokens_estimated 추가
  의존: 없음
  크기: 소(小) — 기존 파일 3개 수정, 신규 파일 0개

Phase 2: Feature 4 (체크포인트 확장)
  ├─ hooks/checkpoint.py: save_step(), get_completed_steps(), get_last_step()
  └─ hooks/checkpoint.py: post_execute()에 budget_consumed 저장
  의존: Phase 1 (budget_consumed)
  크기: 소(小) — 기존 파일 1개 수정

Phase 3: Feature 2+3 (Supervisor+Worker 데몬 = 격리 포함)
  ├─ core/daemon_supervisor.py: 신규 (DaemonSupervisor, WorkDetector, CodeWatcher)
  ├─ core/daemon_worker.py: 신규 (Worker 엔트리포인트)
  ├─ run_factory_cli.py: serve 서브커맨드 추가
  └─ af.spec: hiddenimports 2개 추가
  의존: Phase 1 (비용 기록), Phase 2 (체크포인트)
  크기: 중(中) — 신규 파일 2개, 기존 파일 2개 수정
```

---

## 영향 범위

| 파일 | 변경 | Phase | 신규/수정 |
|------|------|-------|-----------|
| `core/control/run_ledger.py` | `_cost_buffer`, `record_agent_cost()`, `flush_costs()`, `top_consumers()`, `close_run()` 확장 | 1 | 수정 |
| `core/agent_runner.py` | `_flush_trace()`에서 `RunLedger.record_agent_cost()` 호출 | 1 | 수정 |
| `core/dynamic_orchestrator.py` | state_board에 `tokens_estimated` 추가 | 1 | 수정 |
| `core/hooks/checkpoint.py` | `save_step()`, `get_completed_steps()`, `get_last_step()`, `post_execute()` 확장 | 2 | 수정 |
| `core/daemon_supervisor.py` | **신규** — DaemonSupervisor, WorkDetector, CodeWatcher | 3 | 신규 |
| `core/daemon_worker.py` | **신규** — Worker 엔트리포인트 | 3 | 신규 |
| `run_factory_cli.py` | `serve` 서브커맨드 + `_run_serve()` | 3 | 수정 |
| `af.spec` | hiddenimports 2개 추가 | 3 | 수정 |
| `Master_Blueprint.md` | §0, §3, §12 | 1-3 | 수정 |

**v1 대비**: 신규 파일 4개 → 2개, 수정 파일 동일

---

## v1 대비 해소된 한계

| 한계 | v1 상태 | v2 해결 방법 |
|------|---------|-------------|
| 핫 업데이트 불가 | 미해결 | CodeWatcher → graceful restart → 새 Worker spawn |
| 에이전트 체크포인트 부재 | 미해결 | CheckpointHook.save_step() → 스텝 단위 resume |
| 다중 인스턴스 충돌 | thread-local로 우회 | Worker 프로세스 분리 → 원천 해소 |
| RunLedger metadata 비대화 | 미인식 | in-memory buffer + close 시 flush |
| Supervisor 이중 감시 | 미인식 | Worker 내부 RuntimeSupervisor 동작, Supervisor는 프로세스 레벨만 |
| facade 메모리 누수 | 미인식 | Worker 프로세스 종료 시 GC로 자동 해소 |

---

## 검증 방법

1. **Feature 1**: `af run` 후 `run_ledger.jsonl` 마지막 줄의 `metadata.agent_costs` 확인
2. **Feature 4**: `af run` 중 kill → 재실행 → `checkpoint.json`의 `_steps` 복구 확인
3. **Feature 2+3**:
   - `af serve --projects test_a,test_b --interval 10`
   - test_a에 pending task 추가 → Worker spawn 확인
   - `core/` 파일 수정 → graceful restart 확인
   - test_a, test_b의 `run_ledger.jsonl`이 별도 파일인지 확인
   - `Ctrl+C` → checkpoint 저장 → 재시작 → resume 확인
4. **회귀**: `pytest tests/` — 기존 테스트 전부 통과 (Feature 1-2 수정은 하위호환)
