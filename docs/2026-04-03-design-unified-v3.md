# Agent Factory 통합 설계서 v3

> 작성일: 2026-04-03
> 최종 수정: 2026-04-03 (리뷰 6개 권고 반영, 코드 리뷰 기반 사실관계 보정)
> 상태: **설계 완료 / 미구현**
>
> ## 문서 체계
>
> | 문서 | 역할 | 상태 |
> |------|------|------|
> | **이 문서 (v3)** | 최종 구현 명세 | 기준 문서 |
> | `2026-04-03-design-paperclip-comparison-features-v2.1.md` | 런타임 기준 설계 (v3의 근거) | 참조용 |
> | `features/claude-code-insights-synergy.md` | 상위 전략 (컨텍스트/메모리/UX만) | 전략 문서 |
> | `2026-04-03-design-paperclip-comparison-features-v2.md` | 초안 | **Superseded by v2.1** |
> | `2026-04-02-design-paperclip-comparison-features-v1.md` | 원본 | **Superseded by v2** |
> | `2026-04-03-code-review.md` | 전체 코드 리뷰 (174파일, 46K줄) | 스냅샷 |
>
> ## 설계 원칙
>
> 1. **흡수형 리팩터링** — 기존 시스템 확장, 새 인프라 최소화
> 2. **Provider-Agnostic** — serve 레이어는 Claude/Gemini/Codex를 모른다
> 3. **AF-owned 경계** — checkpoint, cost, restart는 provider hook에 의존하지 않는다
> 4. **런타임 축은 v2.1, 컨텍스트 축은 insights-synergy** — 역할 분리, 중복 제거
>
> ## 통합 원칙 (v2 → v3 변경)
>
> | v2/v2.1 항목 | v3 결정 | 사유 |
> |-------------|---------|------|
> | `build_worker_cmd()` 이중 구현 | 의도적 허용 (`core/utils.py` + `_build_worker_cmd()` in supervisor) | Supervisor는 core import 금지 (provider-agnostic) |
> | `AF_CONTROL_PLANE_PROVIDERS` 환경변수 | `control_plane_llm.py`에서 읽도록 수정 | v2.1에서 제안만, 코드 연동 누락 |
> | Checkpoint 3중 (manifest, agent, wake) | 우선순위 명시: wake > manifest > agent | v2.1에서 미정의 |
> | 비용 상세 canonical source | 상세=trace_*.jsonl, 요약=RunLedger.metadata | v2.1에서 모호 |
> | `provider_bridge.py` 신규 (insights-synergy) | 신규 파일 없음, 기존 `event_bus.py` + `session_adapter.py` 확장 | `lifecycle_bridge.py`와 중복 방지 |
> | insights-synergy daemon 설계 | v2.1 daemon으로 통합, insights-synergy에서 런타임 제거 | 역할 중복 제거 |
> | `PROJECT_CONTEXT.md` | 채택, `ingestion_pipeline.py`에서도 읽도록 | insights-synergy 핵심 제안 |

---

## 현재 상태 진단 (코드 리뷰 기반)

### 프로젝트 규모
- core/ 174파일, 46,030줄, 서브디렉토리 6개
- 상세: `2026-04-03-code-review.md` 참조

### 구현되어 있지만 연결 안 된 것 (dead code)

| 모듈 | 기능 | 줄 수 | import 수 | 문제 |
|------|------|-------|----------|------|
| `ise_loop.py` | Level 1~5 에스컬레이션, 태스크 분해+실행 | 469 | **0** | 완전 dead code |
| `ise_redesigner.py` | 3~7개 서브태스크 생성, 의존성 정렬 | 264 | ISELoop만 | ISELoop이 dead이므로 역시 dead |
| `claim_tracer.py` | 클레임 추적 | 214 | **0** | 삭제 검토 |
| `onboarding_wizard.py` | 온보딩 마법사 | 191 | **0** | 삭제 검토 |
| `policy.yaml` granularity: "small" | LLM 프롬프트 힌트 | - | - | 런타임 분해 없음, 프롬프트 힌트만 |

### 구현 안 된 것

| 항목 | 상태 |
|------|------|
| PROJECT_CONTEXT.md | 파일 없음 (CLAUDE.md, GEMINI.md, AGENTS.md는 존재) |
| SharedContextBuilder | 미구현 (knowledge_injection이 에이전트마다 개별 검색) |
| Provider-Agnostic 훅 브릿지 | event_bus.py에 provider 이벤트 매핑 없음 |
| 컨텍스트 압축 (compaction) | context_window_manager.py에 should_compact()/compact() 없음 |
| AF-owned wake checkpoint | checkpoint.py에 wake 관련 함수 없음 |
| 에이전트별 비용 추적 | run_budget.py는 글로벌 싱글톤, run_ledger.py에 비용 API 없음 |
| af serve 데몬 | 없음. one-shot 실행만 가능 |
| Provider Policy | control_plane_llm.py가 AF_CONTROL_PLANE_PROVIDERS를 읽지 않음 |

### Checkpoint 진실의 원천 (현재: 무질서)

| 메커니즘 | 파일 | 저장 위치 | 기록 내용 |
|----------|------|-----------|-----------|
| OrchestratorManifestStore | `continuity/manifest_store.py` (105줄) | `.af_manifest.json` | 오케스트레이터 전체 상태 |
| CheckpointHook | `hooks/checkpoint.py` (68줄) | `runs/{run_id}/checkpoint.json` | 에이전트 사이클 상태 |
| wake_checkpoint (미구현) | 설계 중 | `.af_runtime/daemon/wake_checkpoint.json` | 데몬 wake 상태 |

**v3 우선순위 규칙:**
1. `wake_checkpoint` — 데몬 crash 복구. 가장 구체적, 가장 최근. 있으면 무조건 사용.
2. `manifest_store` — 오케스트레이션 중간 상태. wake_checkpoint 없을 때 fallback.
3. `CheckpointHook` — 개별 에이전트 사이클. 위 두 개 없을 때 fallback.

복원 시 상위가 하위를 포함하므로 충돌 없음.

### 비용 추적 진실의 원천 (현재: 분산)

| 데이터 | canonical source | 비고 |
|--------|-----------------|------|
| 에이전트별 상세 비용 | `trace_*.jsonl` (기존) | 변경 없음 |
| 런별 요약 비용 | `run_ledger.jsonl` → `metadata` | v3에서 추가 |
| 글로벌 토큰 카운터 | `run_budget.py` 싱글톤 | 기존 유지, 에이전트별 분해 안 함 |
| 스킬별 사용량 | `data/skill-usage.jsonl` | 기존 유지 |

---

## 통합 구현 순서 (8 Phase)

```
Phase 1: Subprocess 계약 + 태스크 분해 활성화           ← 기반
Phase 2: RunLedger 비용 추적                           ← 관측성
Phase 3: Provider별 Context 파일 + PROJECT_CONTEXT.md  ← 즉시 효과
Phase 4: SharedContextBuilder                          ← 비용 절감
Phase 5: AF-Owned Checkpoint + 세션 연속성              ← 안정성
Phase 6: 단일 프로젝트 af serve                         ← 자율 운영
Phase 7: Provider Policy + Failover                    ← 멀티 provider
Phase 8: 멀티 프로젝트 + 훅 브릿지                      ← 확장
```

### 의존성 그래프

```
Phase 1 (subprocess + 태스크 분해) ──────────────────────┐
    │                                                     │
Phase 2 (비용 추적) ──────┐                               │
    │                     │                               │
Phase 3 (context 파일)    │ (독립 — 병렬 가능)             │
    │                     │                               │
Phase 4 (SharedContext)   │ (독립 — 병렬 가능)             │
    │                     │                               │
Phase 5 (checkpoint + compaction) ←── Phase 2 (비용 복구) │
    │                                                     │
Phase 6 (단일 serve) ←── Phase 1 + Phase 2 + Phase 5     │
    │                                                     │
Phase 7 (provider policy) ←── Phase 6                     │
    │                                                     │
Phase 8 (멀티프로젝트 + 훅 브릿지) ←── Phase 6 + Phase 7  │
```

Phase 3, 4는 Phase 1/2와 **독립** — 병렬 진행 가능.

---

## Phase 1: Subprocess 계약 + 태스크 분해 활성화

### 1A. Spawn Helper — `build_worker_cmd()`

**설계 결정: 이중 구현 허용**

v2.1 리뷰에서 `build_worker_cmd()`가 `core/utils.py`(공용)와 supervisor 내부 `_build_worker_cmd()`로 이중 구현되는 문제가 지적됨. 이는 **의도적**:

- `core/utils.py`의 `build_worker_cmd()` → `dynamic_orchestrator.py`에서 사용 (AF 코드 import 가능)
- supervisor 내부의 `_build_worker_cmd()` → `daemon_supervisor.py`에서 사용 (stdlib만 import, provider-agnostic 원칙)

두 함수의 로직은 동일하지만, supervisor가 core를 import하면 provider-agnostic 원칙이 깨짐.

**수정: `core/utils.py`** — 추가

```python
import os, sys
from pathlib import Path

_WORKER_SCRIPTS = {
    "worker": "core/agent_worker.py",
    "daemon-worker": "core/daemon_worker.py",
}

def build_worker_cmd(worker_type: str, *extra_args: str) -> list[str]:
    """frozen/source 공통 worker subprocess 명령어.

    frozen:  ["C:/.../af.exe", "worker_type", *extra_args]
    source:  ["python", "C:/.../core/xxx.py", *extra_args]
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, worker_type, *extra_args]
    factory_root = str(Path(__file__).parent.parent)
    script = _WORKER_SCRIPTS.get(worker_type)
    if not script:
        raise ValueError(f"unknown worker_type: {worker_type}")
    return [sys.executable, os.path.join(factory_root, script), *extra_args]
```

**수정: `core/dynamic_orchestrator.py:567-571`** — inline 분기 → `build_worker_cmd()` 교체

```python
# 기존 (L567-571):
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
# 기존 worker 디스패치 바로 아래:
if effective_argv and effective_argv[0] == "daemon-worker":
    from core.daemon_worker import main as daemon_worker_main
    sys.argv = ["af-daemon-worker"] + effective_argv[1:]
    daemon_worker_main()
    return
```

### 1B. 태스크 분해 활성화 — ISELoop 연결

**문제**: `ISELoop`(469줄)이 존재하지만 **0개 파일**에서 import. `ISERedesigner.decompose_task()`도 도달 불가.
`DynamicOrchestrator`는 board 태스크를 **분해 없이 그대로** 에이전트에 넘긴다.

**해결**: 두 지점에서 태스크 분해를 활성화.

#### 1B-1. DynamicOrchestrator에 사전 분해 도입

**수정: `core/dynamic_orchestrator.py`**

```python
# __init__에 추가:
from core.ise_redesigner import ISERedesigner
self._redesigner = ISERedesigner(self.llm)
self._task_retry_count: dict[str, int] = {}  # task_id → 실패 횟수

def _should_decompose(self, subtask: str, task_meta: dict) -> bool:
    """태스크가 분해 대상인지 판단.

    기준:
    1. subtask 길이 > 500자 (복합 지시)
    2. 복수 작업 키워드 포함 ("and", "그리고", "및", ";")
    3. 이전 실행에서 같은 task가 1회 이상 실패
    4. _sub suffix가 있으면 재분해 금지 (무한 분해 방지)
    """
    task_id = task_meta.get("task_id", "")
    if "_sub" in task_id:
        return False  # 이미 분해된 서브태스크
    if len(subtask) > 500:
        return True
    lowered = subtask.lower()
    if any(kw in lowered for kw in (" and ", " 그리고 ", " 및 ", "; ")):
        return True
    if task_id and self._task_retry_count.get(task_id, 0) >= 1:
        return True
    return False

def _decompose_task(self, subtask: str, role: str, task_meta: dict) -> list[dict]:
    """ISERedesigner.decompose_task()를 호출하여 서브태스크로 분해.

    Returns:
        [{"subtask": str, "role": str, "dependencies": list, "priority": int}, ...]
        분해 실패 시 원본 1개짜리 리스트 반환.
    """
    try:
        from core.ise_redesigner import ISEAnalysis, StrategyLedger
        analysis = ISEAnalysis(
            error_pattern="complex_task",
            root_cause="task_too_large",
            suggested_action="decompose",
        )
        ledger = StrategyLedger()
        subs = self._redesigner.decompose_task(subtask, analysis, ledger)
        if subs and len(subs) >= 2:
            print_agent_msg("Orchestrator", f"태스크 분해: {len(subs)}개 서브태스크", "")
            return subs
    except Exception as exc:
        print_agent_msg("Orchestrator", f"태스크 분해 실패: {exc}", "")
    return [{"subtask": subtask, "role": role, "dependencies": [], "priority": 1}]
```

**수정: `_execute_agent_task()` 시작부**

```python
async def _execute_agent_task(self, role, subtask, agents, run_id, workspace, ...):
    task_meta = {"task_id": task_id, "role": role}

    # ★ 사전 분해 체크
    if self._should_decompose(subtask, task_meta):
        subs = self._decompose_task(subtask, role, task_meta)
        if len(subs) > 1:
            for s in subs:
                new_task_id = f"{task_id}_sub{s['priority']}"
                update_project_board_task(
                    workspace, new_task_id, "pending",
                    note=f"[decomposed from {task_id}]",
                    extra={"description": s["subtask"], "owner_role": s["role"],
                           "dependencies": s.get("dependencies", [])},
                )
            update_project_board_task(workspace, task_id, "completed",
                                      note=f"decomposed into {len(subs)} subtasks")
            return  # 다음 사이클에서 서브태스크들이 board에서 pick됨

    # 기존 실행 로직 계속...
```

#### 1B-2. RuntimeSupervisor에서 deep_update → ISELoop 분기

**수정: `core/control/supervisor.py` — `_run_orchestrator()`**

```python
def _run_orchestrator(self, pipeline, prepared, normalized):
    task_input = self._get_task_input(normalized)
    policy = getattr(normalized, "execution_policy", {}) or {}
    execution_policy = policy.get("execution_policy", "standard_update")

    # ★ deep_update 이상이면 ISELoop 사용 (태스크 분해 포함)
    if execution_policy in ("deep_update", "full_bootstrap"):
        try:
            from core.ise_loop import ISELoop
            from core.model_router import ModelRouter
            from core.agent_runner import AgentRunner
            mr = ModelRouter()
            runner = AgentRunner(mr)
            ise = ISELoop(mr, runner, workspace=self._workspace)
            return ise.run(
                agent={},
                task_input=task_input,
                run_id=self._get_run_id(normalized),
                workspace=self._workspace,
            )
        except Exception as exc:
            print(f"[RuntimeSupervisor] ISELoop failed, falling back: {exc}")

    # 기존 fallback
    if hasattr(pipeline, "run_project"):
        return pipeline.run_project(task_input, self._workspace)
    if hasattr(pipeline, "execute"):
        return pipeline.execute(task_input, self._workspace)
    return {"success": False, "error": "no executable method"}
```

### 분해 안전장치

| 안전장치 | 구현 |
|---------|------|
| 분해 실패 fallback | ISERedesigner 실패 시 원본 1개 리스트 → 기존 동작 |
| 무한 분해 방지 | `_sub` suffix가 있는 task는 `_should_decompose()=False` |
| 의존성 보존 | 서브태스크 dependencies → board에 기록 → `next_board_tasks()`가 순서 보장 |
| 비용 제한 | 분해 자체가 LLM 1회 호출 → RunBudget에 반영 |

### Phase 1 수정 파일

| 파일 | 변경 |
|------|------|
| `core/utils.py` | `build_worker_cmd()` 추가 |
| `core/dynamic_orchestrator.py` | spawn helper 교체 + `_should_decompose()`, `_decompose_task()` 추가 |
| `core/control/supervisor.py` | `_run_orchestrator()`에서 deep_update → ISELoop 분기 |
| `run_factory_cli.py` | `daemon-worker` 서브커맨드 |
| `af.spec` | hiddenimports: `core.ise_loop`, `core.ise_redesigner` 추가 |

### Phase 1 검증

1. `af run --task "A 하고 B도 하고 C도 해줘"` → board에 서브태스크 3개 생성 확인
2. `af run --mode fsa --task "복잡한 리팩터링"` 실패 1회 후 → 재시도 시 분해 확인
3. frozen 빌드: `af.exe daemon-worker --help` 동작 확인

---

## Phase 2: RunLedger 비용 추적 (요약 모델)

### 원칙

```
상세 비용 → trace_*.jsonl (기존, 변경 없음)
런별 요약 → RunLedger.metadata (v3에서 추가)
글로벌 카운터 → run_budget.py (기존 유지)
```

새 JSONL 파일(cost_ledger.jsonl 등) **만들지 않는다**. RunLedger.metadata에 요약만 추가.

### 수정: `core/control/run_ledger.py` (현재 310줄)

```python
# 인스턴스 변수 추가 (__init__):
self._cost_buffer: dict[str, list[dict]] = {}  # run_id → [{agent_role, provider_id, tokens, ...}]

# 새 메서드:
def record_agent_cost(self, run_id: str, agent_role: str, provider_id: str,
                      tokens_estimated: int, latency_ms: int, ok: bool,
                      plane: str = "worker") -> None:
    """에이전트 실행 비용을 in-memory 버퍼에 기록."""
    self._cost_buffer.setdefault(run_id, []).append({
        "agent_role": agent_role, "provider_id": provider_id,
        "tokens": tokens_estimated, "latency_ms": latency_ms,
        "ok": ok, "plane": plane,
    })

def flush_cost_summary(self, run_id: str) -> dict:
    """in-memory 버퍼를 요약 dict로 변환. close_run()에서 자동 호출."""
    entries = self._cost_buffer.pop(run_id, [])
    if not entries:
        return {}
    tokens_total = sum(e["tokens"] for e in entries)
    by_provider: dict[str, int] = {}
    by_plane: dict[str, int] = {}
    infra_failures: dict[str, int] = {}
    role_tokens: dict[str, int] = {}
    for e in entries:
        by_provider[e["provider_id"]] = by_provider.get(e["provider_id"], 0) + e["tokens"]
        by_plane[e["plane"]] = by_plane.get(e["plane"], 0) + e["tokens"]
        role_tokens[e["agent_role"]] = role_tokens.get(e["agent_role"], 0) + e["tokens"]
        if not e["ok"]:
            infra_failures[e["provider_id"]] = infra_failures.get(e["provider_id"], 0) + 1
    top_roles = sorted(role_tokens.items(), key=lambda x: -x[1])[:5]
    return {
        "tokens_total": tokens_total,
        "tokens_by_provider": by_provider,
        "tokens_by_plane": by_plane,
        "infra_failures_by_provider": infra_failures,
        "top_roles": [{"role": r, "tokens": t} for r, t in top_roles],
    }

def get_pending_costs(self, run_id: str) -> list[dict]:
    """checkpoint 저장용: 아직 flush 안 된 비용 데이터."""
    return list(self._cost_buffer.get(run_id, []))

def restore_pending_costs(self, run_id: str, costs: list[dict]) -> None:
    """checkpoint 복구용: 비용 데이터 복원."""
    self._cost_buffer[run_id] = costs
```

**수정: `close_run()` 확장**

```python
def close_run(self, run_id, ..., metadata=None):
    # 기존 close 로직...
    metadata = metadata or {}
    # ★ 비용 요약 자동 병합
    cost_summary = self.flush_cost_summary(run_id)
    if cost_summary:
        metadata.update(cost_summary)
    # 기존 metadata 저장 로직...
```

### 수정: `core/agent_runner.py` — `_flush_trace()` (L863)

```python
# 기존 RunBudget.record(text) 직후에 추가:
try:
    _tokens = max(1, len(_text) // 4)
    from core.control.run_ledger import RunLedger
    RunLedger(target_workspace).record_agent_cost(
        run_id=run_id, agent_role=str(agent.get("role", "")),
        provider_id=provider_id, tokens_estimated=_tokens,
        latency_ms=int(result.get("latency_ms", 0)),
        ok=bool(result.get("ok")), plane="worker",
    )
    result["tokens_estimated"] = _tokens
except Exception:
    pass
```

### 수정: `core/dynamic_orchestrator.py` — state_board entries

`completed_entry`와 `failed_entry`에 `tokens_estimated` 필드 추가:

```python
entry["tokens_estimated"] = result.get("tokens_estimated", 0)
```

### Phase 2 metadata 구조

```json
{
  "tokens_total": 45000,
  "tokens_by_provider": {"claude_cli": 30000, "gemini_cli": 15000},
  "tokens_by_plane": {"worker": 40000, "control": 5000},
  "infra_failures_by_provider": {"codex_cli": 1},
  "top_roles": [{"role": "backend_dev", "tokens": 25000}]
}
```

### Phase 2 검증

`af run` 후 `run_ledger.jsonl` 마지막 줄의 `metadata.tokens_total` 존재 확인.

---

## Phase 3: Provider별 Context 파일 + PROJECT_CONTEXT.md

### 현재 상태

- `CLAUDE.md` ✅ 존재 (프로젝트 지시사항)
- `GEMINI.md` ✅ 존재
- `AGENTS.md` ✅ 존재 (`scripts/generate_agents_md.py` 이미 존재)
- `docs/PROJECT_CONTEXT.md` ❌ 없음 — 3개 파일의 공통 소스가 될 문서

### 신규: `docs/PROJECT_CONTEXT.md`

```markdown
# Agent Factory — Project Context

## Architecture
- Sidecar control plane: ControlPlaneIntake → WorkKind → ExecutionPolicy → MaintenancePipeline
- 2-phase pipeline: prepare (planning) → execute (DynamicOrchestrator)
- Sparse Governor: rule-based dispatch first, LLM only on events
- Multi-provider: claude_cli, gemini_cli, codex_cli (registry.py)
- Worker subprocess: file-based IPC (task.json → result.json)

## Entry Points
- `run_factory_cli.py` → argparse → route to pipeline/chat/serve
- `core/dynamic_orchestrator.py` → asyncio 5-concurrent agent execution
- `core/agent_runner.py` → provider failover loop (L1076)
- `core/fsa_loop.py` → 5-cycle EXECUTE→TRACE→EVAL→SUMMARIZE→REFLECT

## Key Rules
- workspace 밖 git 조작 금지
- 12-skill 컨텍스트 캡
- policy.yaml의 granularity: "small" 준수
- af.spec hiddenimports에 새 core/*.py 반드시 추가
- Master_Blueprint.md 동시 업데이트

## Build
- `python build_exe.py` → `dist/af-{version}.zip`
- 버전: `version.py` (__version__)

## Testing
- `pytest tests/` — 전체 테스트
- `py_compile` — 구문 검증 우선
```

### 수정: `core/ingestion_pipeline.py` (현재 144줄)

`PROJECT_CONTEXT.md`를 ingestion 대상에 포함:

```python
# _discover_documents() 또는 유사 함수에서:
context_path = os.path.join(workspace, "docs", "PROJECT_CONTEXT.md")
if os.path.isfile(context_path):
    documents.append(context_path)
```

### Phase 3 검증

1. `claude` CLI에서 프로젝트 열기 → CLAUDE.md 인식 확인
2. 3개 context 파일(CLAUDE.md, GEMINI.md, AGENTS.md)이 PROJECT_CONTEXT.md를 참조하는지 확인

---

## Phase 4: SharedContextBuilder

### 문제

`KnowledgeInjectionHook.pre_execute()` (156줄)는 에이전트마다 **개별 검색**. 에이전트 5개가 같은 프로젝트에서 돌면 동일한 검색을 5번 반복.

### 설계

**수정: `core/dynamic_orchestrator.py`**

```python
def run_project(self, task_input, workspace, ...):
    # ★ SharedContext: 1회 빌드 → 전체 에이전트 공유
    shared_context = self._build_shared_context(task_input, workspace)
    # ... 기존 로직에 shared_context 전달 ...

def _build_shared_context(self, task_input: str, workspace: str) -> str:
    """메모리 시스템에서 프로젝트 관련 지식을 1회 검색하여 공유 prefix 생성.

    결정론적 텍스트 (정렬 순서 고정) → provider별 캐시 자동 활성화.
    """
    parts = []

    # 1. PROJECT_CONTEXT.md 로드
    ctx_path = os.path.join(workspace, "docs", "PROJECT_CONTEXT.md")
    if os.path.isfile(ctx_path):
        with open(ctx_path, encoding="utf-8") as f:
            parts.append(f"[Project Context]\n{f.read()[:3000]}")

    # 2. KnowledgeInjection에서 관련 지식 검색 (1회)
    try:
        from core.memory_system.knowledge_injection import KnowledgeInjectionHook
        hook = KnowledgeInjectionHook()
        triples = hook._search_relevant(task_input, workspace)
        if triples:
            formatted = hook._format_injection(triples)
            parts.append(f"[Knowledge Base]\n{formatted[:3000]}")
    except Exception:
        pass

    # 3. Cross-project recall
    try:
        from core.memory_system.cross_project import CrossProjectRecall
        recall = CrossProjectRecall()
        cross = recall.recall_global(task_input, limit=5)
        if cross:
            parts.append(f"[Cross-Project Knowledge]\n{cross[:2000]}")
    except Exception:
        pass

    # 4. 정렬 → 바이트 동일성 보장 (캐시 최적화)
    return "\n\n---\n\n".join(parts)
```

**수정: `_execute_agent_task()`** — shared_context를 system prompt에 prepend

```python
async def _execute_agent_task(self, role, subtask, agents, run_id, workspace, shared_context="", ...):
    if shared_context:
        full_prompt = f"{shared_context}\n\n---\n\n{subtask}"
    else:
        full_prompt = subtask
    # full_prompt를 AgentRunner에 전달
```

### Phase 4 검증

1. 에이전트 3개 병렬 → `KnowledgeInjectionHook._search_relevant()` 호출 3→1회로 감소
2. 각 에이전트 prompt 앞부분이 바이트 동일

---

## Phase 5: AF-Owned Checkpoint + 세션 연속성

### 5A. Wake Checkpoint (5단계 경계)

**수정: `core/hooks/checkpoint.py`** (현재 68줄) — 함수 3개 추가

```python
_WAKE_CHECKPOINT_FILENAME = "wake_checkpoint.json"

def save_wake_checkpoint(workspace: str, project_id: str, stage: str, data: dict) -> None:
    """AF-owned 5단계 경계에서 checkpoint 저장.

    5단계:
    1. wake_detected — daemon_worker 진입
    2. request_normalized — ControlPlaneIntake 완료
    3. prepare_completed — MaintenancePipeline.prepare() 완료
    4. subtask_committed:<task_id> — 개별 task 완료
    5. run_closed — 전체 완료

    저장 내용: stage, project_id, pending_cost_summary, budget_consumed, timestamp
    """
    rt_dir = os.path.join(workspace, ".af_runtime", "daemon")
    os.makedirs(rt_dir, exist_ok=True)
    path = os.path.join(rt_dir, _WAKE_CHECKPOINT_FILENAME)
    payload = {
        "project_id": project_id,
        "stage": stage,
        "data": data,
        "ts": _now_iso(),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)

def load_wake_checkpoint(workspace: str) -> dict | None:
    """wake checkpoint 로드. 없으면 None."""
    path = os.path.join(workspace, ".af_runtime", "daemon", _WAKE_CHECKPOINT_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def clear_wake_checkpoint(workspace: str) -> None:
    """run_closed 후 checkpoint 제거."""
    path = os.path.join(workspace, ".af_runtime", "daemon", _WAKE_CHECKPOINT_FILENAME)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

### 5B. 컨텍스트 압축 (Compaction)

**수정: `core/context_window_manager.py`** (현재 633줄)

```python
# 기존 ContextBudget에 추가:
def should_compact(self) -> bool:
    """토큰 사용률 80% 초과 시 compaction 트리거."""
    if self.max_tokens <= 0:
        return False
    return self.used_tokens / self.max_tokens > 0.8

def compact(self, transcript: list[dict]) -> list[dict]:
    """핵심 메모리만 보존하는 compaction.

    1. episode_extractor로 핵심 학습 추출
    2. 오래된 턴 제거
    3. shared_context (Phase 4)는 보존
    """
    try:
        from core.memory_system.episode_extractor import EpisodeExtractor
        extractor = EpisodeExtractor()
        episodes = extractor.extract(transcript)
        return episodes + transcript[-5:]
    except Exception:
        return transcript[-10:]
```

**수정: `core/interactive_chat.py`** (현재 887줄) — 자동 compaction

```python
# send_message() 후:
if self.cwm and self.cwm.should_compact():
    self.transcript = self.cwm.compact(self.transcript)
    print("[Chat] context compacted — core memories preserved")
```

### Checkpoint 복원 순서 (v3 규칙)

```python
# daemon_worker.py 진입 시:
def _restore_state(workspace):
    # 1. wake_checkpoint 확인 (가장 구체적)
    from core.hooks.checkpoint import load_wake_checkpoint
    cp = load_wake_checkpoint(workspace)
    if cp:
        return "wake", cp

    # 2. manifest_store 확인 (오케스트레이션 중간 상태)
    from core.continuity.manifest_store import OrchestratorManifestStore
    manifest = OrchestratorManifestStore(workspace).load_resume_state()
    if manifest:
        return "manifest", manifest

    # 3. CheckpointHook은 에이전트 레벨 — 여기서는 사용 안 함
    return "fresh", None
```

### Phase 5 검증

1. daemon worker kill → 재시작 → wake_checkpoint에서 resume 확인
2. 긴 대화 → 80% 임계값 → 자동 compaction 확인
3. compaction 후 핵심 에피소드 보존 확인

---

## Phase 6: 단일 프로젝트 `af serve`

### 신규: `core/daemon_supervisor.py`

v2.1 설계 기반. **표준 라이브러리만 import** (provider-agnostic):

```python
import json, os, signal, subprocess, sys, threading, time
# ★ core import 없음 — provider 완전 무지

class WorkDetector:
    """파일 시스템만 읽어 작업 감지."""
    def scan(self, workspace: str) -> list[dict]:
        items = []
        # 1. project_board.json에서 pending 확인
        board_path = os.path.join(workspace, ".af_runtime", "project_board.json")
        if os.path.exists(board_path):
            with open(board_path, encoding="utf-8") as f:
                board = json.load(f)
            pending = [t for t in board.get("tasks", []) if t.get("status") == "pending"]
            if pending:
                items.append({"source": "board", "detail": f"{len(pending)} pending tasks"})
        # 2. mailbox 미처리 메시지
        # 3. .todo.md mtime 변경
        # 4. stale run (run_ledger.jsonl 마지막 줄 확인)
        # 5. wake_checkpoint 존재 (crash 복구)
        return items

class CodeWatcher:
    """core/*.py mtime 추적. 변경 감지 시 Worker restart 트리거."""
    def __init__(self, factory_root: str): ...
    def has_changed(self) -> bool: ...

class DaemonSupervisor:
    """이벤트 기반 데몬. 할 일 있으면 기상, 없으면 수면."""

    def __init__(self, config: dict):
        self._config = config
        self._shutdown = False
        self._sleep_event = threading.Event()
        self._detector = WorkDetector()
        self._watcher = CodeWatcher(config.get("factory_root", "."))
        self._status = {}

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        while not self._shutdown:
            for project_id in self._config["projects"]:
                workspace = os.path.join(self._config["projects_root"], project_id)
                items = self._detector.scan(workspace)
                if items:
                    self._wake(project_id, workspace, items)
            self._sleep_event.wait(timeout=self._config.get("interval", 300))
            self._sleep_event.clear()

    def _wake(self, project_id, workspace, items):
        """Worker subprocess 생성 → 실행 → 결과 수집."""
        task_file = os.path.join(workspace, ".af_runtime", "daemon", "wake_task.json")
        result_file = os.path.join(workspace, ".af_runtime", "daemon", "wake_result.json")
        os.makedirs(os.path.dirname(task_file), exist_ok=True)

        task = {
            "project_id": project_id,
            "workspace": workspace,
            "items": items,
            "budget": self._config.get("budget", 0),
            "provider_policy": self._config.get("provider_policy", {}),
        }
        with open(task_file, "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, indent=2)

        cmd = _build_worker_cmd(
            self._config.get("factory_root", "."),
            "daemon-worker", "--task-file", task_file, "--result-file", result_file,
        )
        proc = subprocess.Popen(cmd)

        # ★ stop-file 기반 협조적 종료 (Windows 안전)
        stop_file = os.path.join(workspace, ".af_runtime", "daemon", f"{project_id}.stop")
        while proc.poll() is None:
            if self._shutdown or self._watcher.has_changed():
                with open(stop_file, "w") as f:
                    f.write("stop")
                proc.wait(timeout=30)
                try:
                    os.remove(stop_file)
                except FileNotFoundError:
                    pass
                break
            time.sleep(1)

    def _handle_signal(self, signum, frame):
        self._shutdown = True
        self._sleep_event.set()

    def _write_status(self):
        """daemon_status.json 갱신."""
        # ... (v2.1과 동일)

# ── spawn helper (Supervisor 내부용, stdlib만) ──
def _build_worker_cmd(factory_root, worker_type, *extra_args):
    """frozen/source 공통 Worker 명령어.

    Note: core/utils.py의 build_worker_cmd()와 동일 로직이지만,
    Supervisor는 core를 import하지 않으므로 독립 구현.
    이것은 provider-agnostic 원칙을 위한 의도적 결정.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, worker_type, *extra_args]
    scripts = {"worker": "core/agent_worker.py", "daemon-worker": "core/daemon_worker.py"}
    script = scripts.get(worker_type)
    if not script:
        raise ValueError(f"unknown worker_type: {worker_type}")
    return [sys.executable, os.path.join(factory_root, script), *extra_args]
```

### 신규: `core/daemon_worker.py`

```python
"""daemon Worker subprocess.

진입 → Provider Policy 적용 → checkpoint 복구 → 작업 실행 → 비용 기록 → 종료.
"""
import argparse, json, os, sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--result-file", required=True)
    args = parser.parse_args()

    with open(args.task_file, encoding="utf-8") as f:
        task = json.load(f)

    workspace = task["workspace"]
    project_id = task["project_id"]

    # ★ Provider Policy 적용 (auto_configure 우회)
    policy = task.get("provider_policy", {})
    if policy.get("worker_providers"):
        from core.providers.registry import configure_providers
        configure_providers(policy["worker_providers"])
    if policy.get("control_plane_providers"):
        os.environ["AF_CONTROL_PLANE_PROVIDERS"] = ",".join(policy["control_plane_providers"])

    # ★ Checkpoint 복원 (우선순위: wake > manifest > fresh)
    from core.hooks.checkpoint import load_wake_checkpoint, save_wake_checkpoint, clear_wake_checkpoint
    cp = load_wake_checkpoint(workspace)
    if cp:
        # 비용 복구
        from core.control.run_ledger import RunLedger
        ledger = RunLedger(workspace)
        pending = cp.get("data", {}).get("pending_cost_summary", [])
        if pending:
            ledger.restore_pending_costs(cp.get("data", {}).get("run_id", ""), pending)

    # ★ 예산 설정
    budget = task.get("budget", 0)
    if budget > 0:
        from core.run_budget import set_run_budget
        set_run_budget(budget)

    # ★ stop-file 감시 함수
    stop_file = os.path.join(workspace, ".af_runtime", "daemon", f"{project_id}.stop")
    def _should_stop():
        return os.path.exists(stop_file)

    # ★ 작업 실행 (item 종류별 라우팅)
    result = {"ok": False, "items_processed": 0}
    try:
        for item in task.get("items", []):
            if _should_stop():
                save_wake_checkpoint(workspace, project_id, "interrupted", {})
                break
            source = item.get("source", "board")
            if source == "board":
                from core.control.maintenance_pipeline import MaintenancePipeline
                pipeline = MaintenancePipeline(workspace)
                pipeline.execute(item, workspace)
            # ... 다른 source 타입 라우팅 ...
            result["items_processed"] += 1
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        clear_wake_checkpoint(workspace)

    # ★ 결과 저장 (atomic write)
    tmp = args.result_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, args.result_file)

if __name__ == "__main__":
    main()
```

### 수정: `run_factory_cli.py`

`serve` 서브커맨드 추가:

```python
if effective_argv and effective_argv[0] == "serve":
    _run_serve(effective_argv[1:])
    return

def _run_serve(argv):
    parser = argparse.ArgumentParser(description="Agent Factory Daemon")
    parser.add_argument("--projects", type=str, required=True, help="Comma-separated project IDs")
    parser.add_argument("--projects-root", type=str, default="projects", help="Projects root")
    parser.add_argument("--interval", type=int, default=300, help="Check interval (seconds)")
    parser.add_argument("--budget", type=int, default=0, help="Token budget per wake")
    parser.add_argument("--worker-providers", type=str, default="", help="Comma-separated provider IDs")
    parser.add_argument("--control-providers", type=str, default="", help="Control plane providers")
    parser.add_argument("--log", type=str, default="", help="Log file path")
    args = parser.parse_args(argv)

    config = {
        "projects": [p.strip() for p in args.projects.split(",")],
        "projects_root": args.projects_root,
        "interval": args.interval,
        "budget": args.budget,
        "factory_root": os.path.dirname(os.path.abspath(__file__)),
        "provider_policy": {},
    }
    if args.worker_providers:
        config["provider_policy"]["worker_providers"] = [p.strip() for p in args.worker_providers.split(",")]
    if args.control_providers:
        config["provider_policy"]["control_plane_providers"] = [p.strip() for p in args.control_providers.split(",")]

    from core.daemon_supervisor import DaemonSupervisor
    daemon = DaemonSupervisor(config)
    daemon.run_forever()
```

### daemon_status.json 구조

```json
{
  "state": "sleeping",
  "pid": 12345,
  "started_at": "2026-04-03T...",
  "last_check_at": "2026-04-03T...",
  "last_work_at": "2026-04-03T...",
  "current_project": null,
  "total_wakes": 42,
  "total_tokens_consumed": 150000,
  "projects": ["minesweeper"]
}
```

### Phase 6 검증

1. `af serve --projects test --interval 10` → sleeping 상태 확인
2. board에 pending task 추가 → Worker spawn → 결과 확인
3. `core/` 파일 수정 → stop-file → Worker restart 확인
4. Ctrl+C → graceful shutdown (manifest 저장) 확인
5. Worker kill → 재시작 → wake checkpoint resume 확인

---

## Phase 7: Provider Policy + Failover

### 수정: `core/control_plane_llm.py` — AF_CONTROL_PLANE_PROVIDERS 지원

**현재 문제**: L50-56에서 `detect_available_cli_providers()`만 호출. daemon에서 `AF_CONTROL_PLANE_PROVIDERS` 환경변수를 설정해도 무시됨.

```python
def _init_providers(self) -> None:
    # ★ 환경변수 우선 (daemon에서 설정)
    env_providers = os.environ.get("AF_CONTROL_PLANE_PROVIDERS", "").strip()
    if env_providers:
        self._cli_providers = [p.strip() for p in env_providers.split(",") if p.strip()]
    else:
        # 기존: detect_available_cli_providers()
        try:
            from core.providers.registry import detect_available_cli_providers
            self._cli_providers = detect_available_cli_providers()
        except Exception:
            self._cli_providers = []

    # API key 존재 시 LLMEngine 생성 (기존 유지)
    # ...
```

### 수정: `core/agent_runner.py` — INFRA failover

```python
# L1076 provider loop 수정:
for provider_id in cli_providers:
    cli_result = self._run_with_cli_provider(provider_id, ...)
    if cli_result.get("ok"):
        break
    # ★ INFRA 실패 → 다음 provider 시도
    from core.failure_classifier import classify_failure, FailureCategory
    if classify_failure(cli_result.get("reason", "")) == FailureCategory.INFRA:
        # 비용 기록 (실패도 기록)
        try:
            from core.control.run_ledger import RunLedger
            RunLedger(workspace).record_agent_cost(
                run_id=run_id, agent_role=role, provider_id=provider_id,
                tokens_estimated=0, latency_ms=latency, ok=False, plane="worker",
            )
        except Exception:
            pass
        continue  # 다음 provider 시도
    break  # IMPLEMENTATION 실패는 provider 문제 아님 → 중단
```

### CLI

```bash
af serve --projects myapp \
         --worker-providers claude_cli,gemini_cli \
         --control-providers gemini_cli \
         --interval 60 --budget 50000
```

### Phase 7 검증

1. Claude만 로그인 → 정상 동작
2. Claude + Gemini → Claude quota 시 Gemini failover 확인
3. `run_ledger.jsonl`에 `infra_failures_by_provider` 기록 확인

---

## Phase 8: 멀티 프로젝트 + 훅 브릿지

### 8A. 멀티 프로젝트 격리

Worker 프로세스 분리로 **자동 격리**:

```
af serve --projects minesweeper,tictactoe
    │
    ├─ Worker (minesweeper) ← 독립 프로세스
    │   ├─ 독립 RunBudget
    │   ├─ 독립 RunLedger
    │   ├─ 독립 UnifiedMemoryFacade
    │   └─ config_paths 모듈 상수 충돌 없음 (별도 프로세스)
    │
    └─ Worker (tictactoe) ← 독립 프로세스
        ├─ 독립 RunBudget
        ├─ ... (위와 동일)
```

**v1/v2에서 제안한 `project_context.py` (thread-local 스택)는 불필요.**
프로세스 격리가 더 강력하고 단순.

### 8B. Provider-Agnostic 훅 브릿지

**설계 결정: `provider_bridge.py` 신규 파일 만들지 않음.**
기존 `event_bus.py` (155줄)과 `session_adapter.py` (642줄)을 확장.
이유: `lifecycle_bridge.py` (238줄)과 역할 중복 방지.

**수정: `core/hooks/event_bus.py`** — provider 이벤트 매핑 추가

```python
class HookEventBus:
    # 기존 API 유지: register_hook(), run_pre_execute(), run_post_execute()

    # ★ 추가: provider 이벤트 → AF 이벤트 매핑
    _PROVIDER_EVENT_MAP = {
        # Claude
        "UserPromptSubmit": "pre_execute",
        "PreToolUse": "pre_tool_call",
        "PostToolUse": "post_tool_call",
        "PreCompact": "pre_compact",
        # Gemini
        "BeforeAgent": "pre_execute",
        "AfterAgent": "post_execute",
        "PreCompress": "pre_compact",
    }

    def on_provider_event(self, provider_id: str, event_name: str, data: dict) -> None:
        """provider에서 발생한 이벤트를 AF 훅 체인으로 라우팅."""
        af_event = self._PROVIDER_EVENT_MAP.get(event_name)
        if not af_event:
            return
        if af_event == "pre_execute":
            self.run_pre_execute(data)
        elif af_event == "post_execute":
            self.run_post_execute(data, data.get("result"))
```

**수정: `core/providers/session_adapter.py`** — EventBus 연동

```python
# prepare_cli_session()에서 hook 등록 시 EventBus도 연결:
# frozen 빌드에서도 AF EventBus는 동작 (Python 코드이므로)
```

**핵심**: frozen 빌드에서 provider native hook이 건너뛰어져도 (`session_adapter.py:434`),
AF EventBus 기반 훅은 Python 코드이므로 항상 동작.
Codex(`wrapper_bridge`, `hook_events=()`)에서도 AF EventBus만으로 동일 동작.

### Phase 8 검증

1. `af serve --projects A,B` → 동시 Worker 2개 → 격리 확인
2. Claude 훅 이벤트 → EventBus → AF 훅 체인 실행 확인
3. Codex (wrapper_bridge) → AF EventBus만으로 동일 동작 확인

---

## 전체 영향 범위

| 파일 | 변경 | Phase | 종류 |
|------|------|-------|------|
| `core/utils.py` | `build_worker_cmd()` 추가 | 1A | 수정 |
| `core/dynamic_orchestrator.py` | spawn helper + `_should_decompose()` + `_decompose_task()` + `_build_shared_context()` + `tokens_estimated` | 1B, 2, 4 | 수정 |
| `core/control/supervisor.py` | `_run_orchestrator()` ISELoop 분기 | 1B | 수정 |
| `core/control/run_ledger.py` | `_cost_buffer` + 비용 API 5개 + `close_run()` 확장 | 2 | 수정 |
| `core/agent_runner.py` | `_flush_trace()` 비용 기록 + failover INFRA 분기 | 2, 7 | 수정 |
| `docs/PROJECT_CONTEXT.md` | **신규** — 공통 프로젝트 컨텍스트 | 3 | 신규 |
| `core/ingestion_pipeline.py` | PROJECT_CONTEXT.md 수집 대상 추가 | 3 | 수정 |
| `core/hooks/checkpoint.py` | wake checkpoint 함수 3개 | 5A | 수정 |
| `core/context_window_manager.py` | `should_compact()`, `compact()` | 5B | 수정 |
| `core/interactive_chat.py` | 자동 compaction 트리거 | 5B | 수정 |
| `core/daemon_supervisor.py` | **신규** — 데몬 감독자 | 6 | 신규 |
| `core/daemon_worker.py` | **신규** — 데몬 워커 | 6 | 신규 |
| `run_factory_cli.py` | `daemon-worker` + `serve` 서브커맨드 | 1A, 6 | 수정 |
| `core/control_plane_llm.py` | `AF_CONTROL_PLANE_PROVIDERS` 환경변수 지원 | 7 | 수정 |
| `core/hooks/event_bus.py` | `on_provider_event()` + provider 매핑 | 8B | 수정 |
| `core/providers/session_adapter.py` | EventBus 연동 | 8B | 수정 |
| `af.spec` | hiddenimports 추가 | 1A, 6 | 수정 |
| `Master_Blueprint.md` | §0, §3, §12 | 전체 | 수정 |

**신규 파일: 3개** (`daemon_supervisor.py`, `daemon_worker.py`, `PROJECT_CONTEXT.md`)
**수정 파일: 15개**

---

## 태스크 분해 상세 — 현재 dead code 활성화 계획

### 현재 실행 흐름 (분해 없음)

```
DynamicOrchestrator._orchestration_loop()
  → next_board_tasks() → [{task_id, subtask, role}]
  → _execute_agent_task(role, subtask, ...)
    → AgentRunner.run(agent, subtask, ...)        ← subtask 통째로 실행
```

### 개선 후 실행 흐름 (분해 있음)

```
DynamicOrchestrator._orchestration_loop()
  → next_board_tasks() → [{task_id, subtask, role}]
  → _execute_agent_task(role, subtask, ...)
    → _should_decompose(subtask)?
        ├─ No → AgentRunner.run(agent, subtask, ...)   ← 기존과 동일
        └─ Yes → _decompose_task(subtask, role)
                   → ISERedesigner.decompose_task()
                   → 서브태스크 3~7개 생성
                   → board에 pending으로 추가
                   → 원본 task를 "decomposed"로 완료
                   → 다음 사이클에서 서브태스크들이 자동 pick
```

### ISELoop 활성화 경로

```
현재:
  MaintenancePipeline.execute()
    → RuntimeSupervisor.supervise()
      → DynamicOrchestrator.run_project()  ← ISELoop 미사용

개선:
  MaintenancePipeline.execute()
    → RuntimeSupervisor.supervise()
      → execution_policy == "deep_update"?
          ├─ Yes → ISELoop.run()   ← Level 1~5 에스컬레이션 + 태스크 분해
          └─ No  → DynamicOrchestrator.run_project() + 사전 분해
```

### 분해 트리거 조건

| 조건 | 기준 | 이유 |
|------|------|------|
| 길이 초과 | subtask > 500자 | 복합 지시일 가능성 높음 |
| 복수 작업 키워드 | "and", "그리고", "및", ";" 포함 | 명시적 복수 요청 |
| 재시도 | 같은 task_id가 1회 이상 실패 | 복잡도 때문일 수 있음 |
| execution_policy | `deep_update`, `full_bootstrap` | 정책상 정밀 분해 필요 |
| _sub suffix | 재분해 금지 | 무한 분해 방지 |

---

## 검증 매트릭스

| Phase | 검증 항목 | 성공 기준 |
|-------|----------|-----------|
| 1A | `af.exe daemon-worker --help` | frozen에서 동작 |
| 1B | 복합 task 입력 ("A 하고 B도 하고 C도 해줘") | board에 서브태스크 생성됨 |
| 1B | task 1회 실패 후 재시도 | 분해 후 서브태스크로 실행 |
| 2 | `af run` 후 ledger 확인 | `metadata.tokens_total` 존재 |
| 3 | PROJECT_CONTEXT.md 생성 | 3개 context 파일이 참조 |
| 4 | 에이전트 5개 병렬 | knowledge 검색 1회만 |
| 5A | Worker kill → 재시작 | checkpoint resume 성공 |
| 5B | 긴 대화 80% 임계값 | 자동 compaction |
| 6 | `af serve --interval 10` | board pending → Worker spawn |
| 6 | stop-file 생성 | Worker graceful stop |
| 6 | core/ 파일 수정 | Worker restart |
| 7 | Claude quota | Gemini failover |
| 7 | AF_CONTROL_PLANE_PROVIDERS 설정 | control_plane_llm.py가 사용 |
| 8A | `--projects A,B` | 동시 Worker 2개, 격리 |
| 8B | Claude 훅 이벤트 | AF EventBus 실행 |
| 8B | Codex wrapper_bridge | AF EventBus만으로 동작 |
