# [Goal] OmO 시너지 비동기 병렬 아키텍처 V2 (Async Parallel Synergy)

`synergy_omo_fsa`의 `subprocess.run`(동기) 블로킹 호출을 **Fire-and-Forget → Poll → Collect** 비동기 패턴으로 개편합니다.

## AS-IS → TO-BE

```
AS-IS: Himari → synergy_omo_fsa() → subprocess.run() → ⏳ 120초 블로킹
TO-BE: Himari → dispatch() → job_id 즉시 반환 → 본인 작업 계속
                status(job_id) → 상태 확인
                collect(job_id) → 결과 수거
                cancel(job_id) → 강제 회수
```

---

## 1. API 명세 (4개 고정)

| 도구 | 입력 | 반환 | 설명 |
|---|---|---|---|
| `synergy_omo_dispatch` | `task, timeout_sec=300` | `{ok, job_id, status:"queued"}` | 작업 발사, 즉시 반환 |
| `synergy_omo_status` | `job_id` | `{status, elapsed_ms, pid, ...}` | 상태 확인 |
| `synergy_omo_collect` | `job_id` | `{ok, stdout, stderr, returncode}` | **종결 상태에서만** 결과 수거 |
| `synergy_omo_cancel` | `job_id` | `{ok, prev_status, new_status}` | 강제 회수 (2단계 종료) |

> [!IMPORTANT]
> 기존 `synergy_omo_fsa`(동기식)은 레거시 하위 호환용으로 보존합니다.

---

## 2. 상태머신 (State Machine)

```mermaid
stateDiagram-v2
    [*] --> queued: dispatch()
    queued --> running: 프로세스 시작
    running --> succeeded: returncode == 0
    running --> failed: returncode != 0
    running --> timeout: soft_terminate → grace → hard_kill
    running --> cancelled: cancel()
    queued --> cancelled: cancel()
```

- `collect()`는 **종결 상태(succeeded|failed|timeout|cancelled)**에서만 성공
- 미종결 상태에서 `collect()` 호출 시: `{"ok": false, "error": "job_not_finished", "status": "running"}`

```python
class JobStatus(str, Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    SUCCEEDED = "succeeded"
    FAILED    = "failed"
    TIMEOUT   = "timeout"
    CANCELLED = "cancelled"

TERMINAL_STATES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED}
```

---

## 3. 선반영 필수 리스크 2건

### 🔴 리스크 1: Pipe 데드락 방지

> [!CAUTION]
> `subprocess.PIPE`는 OS 버퍼(64KB)가 차면 자식 프로세스가 write-block되어 **데드락** 발생.

**해법: 파일 리다이렉트**

```python
# ❌ 위험 (데드락 가능)
proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# ✅ 안전 (파일로 직접 저장)
job_dir = Path(BASE_DIR) / "jobs" / job_id
job_dir.mkdir(parents=True, exist_ok=True)
stdout_f = open(job_dir / "stdout.log", "w", encoding="utf-8")
stderr_f = open(job_dir / "stderr.log", "w", encoding="utf-8")
proc = subprocess.Popen(argv, stdout=stdout_f, stderr=stderr_f, cwd=...)
```

### 🔴 리스크 2: Windows 자식 프로세스 트리 정리

> [!CAUTION]
> Windows에서 `Popen.terminate()/kill()`은 **자식 프로세스만** 죽이고, 손자 프로세스(npm → node 등)는 고아로 남음.

**해법: `taskkill /F /T /PID` + `CREATE_NEW_PROCESS_GROUP`**

```python
import platform

def _kill_tree(pid: int):
    """프로세스 트리 전체 종료 (Windows/Unix 호환)."""
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, check=False)
    else:
        import signal
        os.killpg(os.getpgid(pid), signal.SIGKILL)

# Popen 생성 시 새 프로세스 그룹
kwargs = {}
if platform.system() == "Windows":
    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
else:
    kwargs["start_new_session"] = True

proc = subprocess.Popen(argv, **kwargs, ...)
```

---

## 4. 타임아웃 정책 (2단계)

```
[경과 시간]
 0s ────── timeout_sec ────── +grace_sec ──────
 │          soft terminate     hard kill (트리)  │
 │              ↓                   ↓            │
 │         SIGTERM/taskkill    _kill_tree(pid)   │
 │         status="timeout"                      │
```

```python
def _watchdog(self, job_id: str, heartbeat_cb=None):
    job = self._jobs[job_id]
    grace_sec = 10  # soft → hard 유예 시간

    while job.process.poll() is None:
        elapsed = time.time() - job.started_at
        if elapsed > job.timeout_sec:
            # 1단계: Soft Terminate
            job.process.terminate()
            try:
                job.process.wait(timeout=grace_sec)
            except subprocess.TimeoutExpired:
                # 2단계: Hard Kill (트리 전체)
                _kill_tree(job.process.pid)
            job.status = JobStatus.TIMEOUT
            self._finalize_job(job)
            return
        if heartbeat_cb:
            heartbeat_cb()
        time.sleep(2)

    # 정상 종료
    job.returncode = job.process.returncode
    job.status = JobStatus.SUCCEEDED if job.returncode == 0 else JobStatus.FAILED
    self._finalize_job(job)
```

---

## 5. 출력/결과 정책

```
jobs/
└── omo_a1b2c3d4/           # job_id별 디렉토리
    ├── stdout.log           # 전체 stdout (파일)
    ├── stderr.log           # 전체 stderr (파일)
    └── meta.json            # job 메타데이터
```

- `collect()` 시: `stdout.log`/`stderr.log`를 읽어 `_truncate(2400)`/`_truncate(1200)` 적용 후 반환
- `collect()` 성공 후: 디렉토리 자동 삭제 (디스크 누수 방지)

---

## 6. 관측성 (Structured Logging)

모든 상태 전이 시 아래 필드를 구조화 로깅:

```python
logger.info(json.dumps({
    "event": "omo_job_state_change",
    "job_id": job.job_id,
    "pid": job.process.pid,
    "group_id": "omo_synergy",
    "status": job.status.value,
    "prev_status": prev_status,
    "started_at": job.started_at,
    "elapsed_ms": int((time.time() - job.started_at) * 1000),
    "attempt": job.attempt,
    "task_preview": job.task[:80]
}))
```

---

## Proposed Changes

### [MODIFY] [synergy_runner.py](file:///d:/agent-factory/core/synergy_runner.py)

1. `JobStatus` Enum + `OmoJob` dataclass 추가
2. `SynergyBridge`에 `dispatch`, `check_status`, `collect_result`, `cancel` 4개 메서드 추가
3. `_watchdog()` (2단계 타임아웃), `_kill_tree()` (Windows 트리 종료), `_finalize_job()` (출력 파일 닫기) 내부 메서드 추가
4. `build_synergy_tools()`에 도구 4종 등록
5. `get_synergy_context()` 브리핑에 병렬 패턴 안내 추가

### [MODIFY] [concurrency.py](file:///d:/agent-factory/core/concurrency.py)

- `abort_task()`에 `_popen_ref` → `_kill_tree()` 연동 추가

### [MODIFY] [schema.py](file:///d:/agent-factory/config/schema.py)

- `BackgroundTasksConfig`에 `omo_dispatch_timeout_sec`, `omo_max_concurrent_jobs`, `omo_grace_period_sec` 추가

---

## 6대 가드레일 매핑 (V2)

| 가드레일 | 병렬 OmO 적용 |
|---|---|
| **1. 2단계 종료** | `terminate()` → grace 10s → `_kill_tree(pid)` |
| **2. 하트비트** | watchdog 2초 간격 heartbeat |
| **3. 동시성 제한** | `omo_max_concurrent_jobs` (Semaphore) |
| **4. 회로 차단** | `omo_synergy` 그룹 CircuitBreaker |
| **5. 종료 훅** | `_finalize_job()` → stdout/stderr 파일 close + 디렉토리 삭제 |
| **6. 관측성** | 모든 상태 전이 structured JSON logging |

---

## Verification Plan

### Automated Tests (8종)

1. `test_dispatch_returns_immediately` — 1초 이내 job_id 반환
2. `test_state_transitions` — `queued→running→succeeded/failed/timeout/cancelled` Enum 전이
3. `test_collect_only_on_terminal` — 미종결 시 에러, 종결 시 성공
4. `test_cancel_kills_tree` — cancel 시 `_kill_tree()` 호출 (mock)
5. `test_timeout_2stage` — soft terminate → grace → hard kill 순서 확인
6. `test_pipe_deadlock_safe` — stdout 파일 리다이렉트 대용량(1MB+) 출력 테스트
7. `test_concurrent_limit` — `omo_max_concurrent_jobs` 초과 시 queued 대기
8. `test_legacy_fsa_compat` — 기존 `synergy_omo_fsa` 동기 호출 정상 동작

### Manual Verification

- Himari에게 복잡 명령 → `dispatch` → 본인 작업 → `status` → `collect` 로그 확인
