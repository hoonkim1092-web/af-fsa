# 설계 문서 자동 교차 검증 — af.exe 런타임 통합

> 작성일: 2026-04-06
> 상태: 설계 (v2 — Codex 피드백 반영)
> 관련: `scripts/design_review_trigger.py`, `scripts/design_review_watcher.py`

---

## 1. 목적

설계 문서 교차 검증 시스템이 현재 **Claude Code PostToolUse hook에만 연결**되어 있어, af.exe 런타임에서 에이전트가 설계 문서를 작성/수정해도 자동 트리거가 되지 않는다. af.exe 내부에서 ContinuationHook을 통해 동일하게 동작하도록 통합한다.

**범위**: af.exe 런타임 내부 parity. Codex/Gemini CLI를 직접 사용하는 경우는 af.exe의 hook bus가 실행되지 않으므로 이 설계의 범위 밖이다 (기존 수동 트리거 또는 각 CLI의 지시사항으로 대응).

## 2. 현재 상태

| 환경 | 트리거 방식 | 동작 여부 |
|------|-----------|----------|
| Claude Code (직접 사용) | `.claude/settings.local.json` PostToolUse hook | O |
| af.exe (CLI 프로바이더 경유) | 없음 | X ← 이 설계의 대상 |
| af.exe (네이티브 API) | 없음 | X ← 이 설계의 대상 |
| Codex/Gemini CLI (직접) | 수동 `python scripts/design_review_trigger.py` | 범위 밖 |

## 3. 목표 상태

| 환경 | 트리거 방식 | 비고 |
|------|-----------|------|
| Claude Code | PostToolUse hook (기존 유지) | 변경 없음 |
| af.exe (CLI 프로바이더) | `pre_execute` baseline → `post_execute` delta 비교 | ContinuationHook |
| af.exe (네이티브 API) | `post_tool_call` → 파일 쓰기 즉시 감지 | ContinuationHook |

## 4. 설계

### 4.1 shared 로직 추출: `core/design_review_utils.py` (신규)

현재 `scripts/design_review_trigger.py`에 있는 핵심 함수들을 `core/`로 이동하여 frozen 빌드에서도 정상 동작하도록 한다.

**이동 대상 함수**:
- `INCLUDE_PATTERNS`, `EXCLUDE_PATTERNS` — 트리거 대상 패턴
- `_normalize_path(filepath, workspace)` — 상대경로 변환
- `_matches_glob(path, pattern)` — glob 매칭
- `is_design_doc(filepath, workspace)` — 설계 문서 판정
- `_pathhash(rel_path)` — deterministic hash
- `enqueue(filepath, workspace, source)` — pending 큐 등록
- `ensure_watcher(workspace)` — watcher 생존 확인 + 시작

**이유**: `af.spec`의 `datas`에 `scripts/`가 포함되지 않는다 (`af.spec:14-18`). `hiddenimport`는 Python 모듈만 보장하고 외부 스크립트 파일 존재를 보장하지 못한다. `core/`에 두면 `hiddenimport`로 확실히 번들링된다.

### 4.2 `scripts/design_review_trigger.py` (수정 → CLI wrapper)

shared 로직 이동 후 CLI wrapper로 축소:

```python
#!/usr/bin/env python3
"""CLI wrapper — core/design_review_utils.py의 진입점."""
from core.design_review_utils import is_design_doc, enqueue, ensure_watcher, show_status
# argparse + main() 유지, 내부 로직은 core 모듈 호출
```

기존 Claude Code PostToolUse hook (`python scripts/design_review_trigger.py "$fp"`)과 수동 CLI 호출 모두 호환 유지.

### 4.3 신규 Hook: `core/hooks/design_review_hook.py`

`ContinuationHook`을 상속하는 `DesignReviewHook` 생성.

**PRIORITY**: 87 (CodeReviewDocHook=85 이후, CheckpointHook=90 이전)

#### 경로 1 — `post_tool_call` (네이티브 API 경로)

- `agent_runner.py:1375`에서 발생하는 이벤트
- `tool_name in _WRITE_TOOLS` 감지 (`LSPCheckHook` 패턴 재사용)
- 파일 경로 추출 → `is_design_doc()` 체크 → `enqueue()` + `ensure_watcher()`
- CLI 프로바이더 경유 시에는 이 이벤트가 발생하지 않음 (CLI가 내부적으로 tool 처리)

#### 경로 2 — `pre_execute` baseline + `post_execute` delta (CLI 프로바이더 catch-all)

**v1의 문제**: `post_execute`에서 `git diff HEAD`를 그대로 쓰면, 이전부터 dirty 상태인 파일도 매 실행마다 재리뷰된다. watcher가 처리 후 큐 파일을 삭제하므로(`design_review_watcher.py:399`) dedupe가 안 된다.

**v2 해결 — baseline-delta 비교**:

```python
def pre_execute(self, agent_state):
    workspace = agent_state.get("workspace", "")
    if workspace:
        # 실행 전 dirty 파일 스냅샷 저장
        agent_state["_dr_baseline"] = set(_git_changed_files(workspace))
    return True  # 차단하지 않음

def post_execute(self, agent_state, result):
    workspace = agent_state.get("workspace", "")
    if not workspace:
        return result
    
    baseline = agent_state.get("_dr_baseline", set())
    current = set(_git_changed_files(workspace))
    # 이번 실행에서 새로 변경된 파일만 추출
    new_changes = current - baseline
    
    for rel_path in new_changes:
        abs_path = os.path.join(workspace, rel_path)
        if is_design_doc(abs_path, workspace):
            enqueue(abs_path, workspace, source="af_hook")
    
    if any_enqueued:
        ensure_watcher(workspace)
    
    # 알림 전달
    self._deliver_notifications(workspace)
    return result
```

**실패/차단 경로**: `agent_runner.py:1021` (blocked), `1141` (failed)에서도 `post_execute`가 호출되지만, baseline == current이므로 delta가 비어 있어 enqueue되지 않는다.

### 4.4 중복 방지 (3중)

1. **baseline-delta**: 이전부터 dirty인 파일 제외
2. **pathhash dedupe**: `enqueue()`가 같은 파일명 덮어쓰기
3. **watcher quiet period**: 8초 이내 중복 요청 병합

### 4.5 알림 전달

| 환경 | 알림 경로 |
|------|----------|
| Claude Code | PostToolUse hook → `cat *.txt && rm` (기존) |
| af.exe | `post_execute` → 파일 읽기 → stdout 출력 → 삭제 |

**제약**: af.exe에서는 watcher 완료가 다음 에이전트 실행의 `post_execute` 시점에 전달된다. 동일 run 내에서 즉시 알림은 불가.

## 5. 동작 흐름

```
[af.exe 에이전트 실행]
    ↓
[pre_execute] → baseline = set(git diff --name-only HEAD) 저장
    ↓
[에이전트가 docs/features/xxx.md 작성/수정]
    ↓
┌─ 네이티브 API 경로 ────────────────────────────────┐
│ post_tool_call → is_design_doc() → enqueue()       │
│ → ensure_watcher() → 즉시 큐 등록                  │
└────────────────────────────────────────────────────┘
    ↓
[post_execute] → current - baseline = delta
┌─ CLI 프로바이더 경로 ─────────────────────────────────┐
│ delta에 설계 문서 있으면 → enqueue() + ensure_watcher()│
└───────────────────────────────────────────────────────┘
    ↓
[알림 파일 읽기 → 출력 → 삭제]
    ↓
[watcher 10s 폴링 → 8s quiet period]
    ↓
[프로바이더 탐지 → critic / critic+cross+judge]
    ↓
[결과 → docs/reviews/ + notifications/]
```

## 6. 수정/생성 파일

| 파일 | 변경 | 설명 |
|------|------|------|
| `core/design_review_utils.py` | 신규 (~100줄) | shared 로직 (패턴 매칭, 큐, watcher) |
| `core/hooks/design_review_hook.py` | 신규 (~100줄) | DesignReviewHook 클래스 |
| `scripts/design_review_trigger.py` | 수정 (축소) | CLI wrapper → core 모듈 import |
| `core/agent_runner.py` | 5줄 추가 (L942~) | hook 등록 블록 |
| `af.spec` | 2줄 추가 | hiddenimport: `core.design_review_utils`, `core.hooks.design_review_hook` |

기존 파일 변경 없음: `design_review_watcher.py`, `.claude/settings.local.json`

## 7. 영향 범위

- `core/` — 신규 파일 2개 (`design_review_utils.py`, `hooks/design_review_hook.py`)
- `scripts/design_review_trigger.py` — 내부 로직 → core import로 교체 (외부 인터페이스 동일)
- `core/agent_runner.py` — hook bus 등록만, 실행 로직 변경 없음
- `af.spec` — hiddenimport 2줄
- 기존 Claude Code 동작에 영향 없음 (독립 경로)
- 기존 watcher 동작에 영향 없음 (큐 형식 동일)

## 8. 검증 체크리스트

- [ ] `python -m py_compile core/design_review_utils.py`
- [ ] `python -m py_compile core/hooks/design_review_hook.py`
- [ ] `python -m py_compile scripts/design_review_trigger.py` (CLI wrapper)
- [ ] `is_design_doc()` 패턴 매칭 테스트 (INCLUDE/EXCLUDE)
- [ ] baseline-delta 동작 확인: dirty 파일이 재리뷰 안 되는지
- [ ] `post_tool_call` 경로: 네이티브 API에서 설계 문서 쓰기 감지
- [ ] `post_execute` 경로: CLI 프로바이더에서 delta 기반 enqueue
- [ ] 실패/차단 경로: `result.ok=False`일 때 delta 비어있는지
- [ ] Claude Code PostToolUse hook 기존 동작 유지
- [ ] frozen 빌드: `core.design_review_utils` import 정상 동작

## 9. 참고: 기존 Hook 시스템 구조

```
ContinuationHook (base.py)
├── pre_execute(agent_state) → bool
├── post_execute(agent_state, result) → Any
├── pre_tool_call(agent_state, tool_name, tool_args) → ToolCallDecision
└── post_tool_call(agent_state, tool_name, result) → Any

등록: agent_runner.py L912~ → bus.register(Hook())
트리거: agent_runner.py L1375 (post_tool_call), L1019/1119/1141/1404/1416 (post_execute)
```

**참조 구현**: `LSPCheckHook` (`core/hooks/lsp_check.py`)
- `_WRITE_TOOLS` frozenset으로 파일 쓰기 tool 감지
- `pre_tool_call`에서 `agent_state["_last_tool_args"]` 저장
- `post_tool_call`에서 파일 경로 추출 후 분석
