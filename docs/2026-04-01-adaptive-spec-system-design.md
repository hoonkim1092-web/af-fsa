# Adaptive Spec System Design

> Kiro의 문서 선명도 + agent-factory의 운영 연결성을 결합한 적응형 spec 시스템 설계

- **작성일**: 2026-04-01
- **상태**: 설계 완료, 구현 대기
- **관련 문서**: `2026-04-01-maintenance-system-architecture-v2.md`, `2026-03-31-maintenance-update-system-design.md`
- **영향 범위**: `core/work_item_generator.py`, `core/approval_gate.py`, `core/work_item_parser.py`, `core/control/maintenance_pipeline.py`, `core/control/execution_policy.py`, `docs/work-items/_template/`

---

## 1. 목적

Kiro와 agent-factory는 각각 다른 영역에서 강하다. 이 설계의 목적은 **Kiro에서 부족한 부분을 agent-factory가 채우고, agent-factory에서 부족한 부분을 Kiro에서 가져와서 완성도 높은 산출물을 만드는 것**이다.

---

## 2. Kiro와 agent-factory 비교 분석

### 2.1 Kiro의 강점 — 문서 선명도

Kiro(AWS)의 3단 spec 구조:

| 단계 | 문서 | 역할 |
|------|------|------|
| What | Requirements Spec | 사용자 스토리 + 수용 기준 |
| How | Design Spec | 기술 설계 결정 |
| Do | Implementation Spec | 파일 수준 작업 목록 |

**Kiro가 잘하는 것:**
- 각 문서의 역할이 명확하고 한 눈에 파악됨
- 채울 수 없는 빈 섹션(`(edit required)`)이 없음 — 생성할 수 없는 내용은 포함하지 않음
- 복잡도와 무관하게 일관된 3단 구조

**Kiro에 부족한 것:**
- 문서 이후의 실행 운영 연결이 없음 (승인 게이트, 작업 보드 연동, 재개, 멀티에이전트 보드)
- 작업 복잡도와 무관하게 항상 3개 문서 — 단순 버그 수정에도 설계 문서 생성
- 장기 유지보수 상태 관리 (manifest, resume brief, session continuity) 없음

### 2.2 agent-factory의 강점 — 운영 연결성

현재 work-item 폴더 구조:

```
docs/work-items/{slug}/
├── feature-plan.md           ← 왜: 배경, 범위, 목표, 리스크, 증거
├── feature-spec.md           ← 무엇: 요구사항, 수용기준, 시나리오
├── implementation-design.md  ← 어떻게: 모듈, 데이터 흐름, 테스트 전략
├── implementation-tasks.md   ← 실행: 편집 가능한 체크리스트
├── approval-gate.md          ← 제어: 승인 게이트 (해시 무결성 검증)
├── verification-report.md    ← (실행 후 검증 결과 기록)
├── change-request.md         ← (범위 변경 추적)
└── bug-fix-spec.md           ← (버그 수정 전용 대체 spec)
```

**agent-factory가 잘하는 것:**
- 문서 → 승인 게이트 → 작업 보드 → 병렬 오케스트레이션 → manifest → resume brief로 이어지는 실행 운영 파이프라인
- 4개 문서가 각각 명확한 역할 분리 (왜/무엇/어떻게/실행)
- SHA256 해싱 기반 문서 무결성 검증
- 체크리스트 ↔ 작업 보드 양방향 동기화

**agent-factory에 부족한 것:**
- `(edit required)` 플레이스홀더 6개 — 채울 수 없는 빈 섹션
- 증거/참조 섹션이 feature-plan.md와 feature-spec.md에 중복
- 작업 복잡도와 무관하게 항상 4개 또는 0개 (quick_fix는 문서 0개)
- deep_update와 standard_update의 문서 차이 없음

### 2.3 결합 전략

| 부족한 점 | 해결 방법 | 출처 |
|-----------|----------|------|
| Kiro: 실행 운영 연결 없음 | 승인 게이트 + 보드 연동 + 상태 머신 유지 | agent-factory 기존 |
| Kiro: 복잡도 무관 고정 3파일 | `spec_depth` 적응형 생성 | 새 설계 |
| agent-factory: 빈 섹션 남발 | 자동 생성 불가 섹션 제거 | Kiro에서 차용 |
| agent-factory: 증거/참조 중복 | feature-spec.md에서 증거/참조 제거 (plan에만 배치) | Kiro에서 차용 |
| agent-factory: 0개 or 4개 이분법 | `ExecutionPolicy`와 `spec_depth` 연동 | 새 설계 |
| agent-factory: deep=standard 차이 없음 | design.md 유무로 정책 차별화 | 새 설계 |

---

## 3. 현재 구조의 구체적 문제

### 3.1 증거/참조 섹션 중복

`feature-plan.md`와 `feature-spec.md`에서 동일한 Evidence와 References가 반복된다:

```python
# work_item_generator.py — 두 함수에서 동일 호출
def _generate_feature_plan(...):
    research_lines = _research_bullets(project_brief)    # ← 동일 데이터
    reference_lines = _reference_bullets(project_brief)  # ← 동일 데이터

def _generate_feature_spec(...):
    research_lines = _research_bullets(project_brief)    # ← 동일 데이터
    reference_lines = _reference_bullets(project_brief)  # ← 동일 데이터
```

**해결:** feature-spec.md에서 Evidence/References 섹션을 제거한다. 증거와 참조는 feature-plan.md에만 둔다. spec은 plan을 참조하므로 중복이 불필요하다.

### 3.2 자동 생성 불가 플레이스홀더

`feature-spec.md`에 6개, `implementation-design.md`에 5개의 빈 섹션이 있다:

**feature-spec.md의 빈 섹션:**
```markdown
## Non-Functional Requirements    → (edit required)
## Inputs and Outputs             → (edit required)
## Exceptions and Failure Scenarios → (edit required)
## Existing Behavior To Preserve  → (edit required)
## Out Of Scope                   → (edit required)
```

**implementation-design.md의 빈 섹션:**
```markdown
## Interface Impact               → (edit required)
## State And Data Model           → (edit required)
## Compatibility Considerations   → (edit required)
## Migration Requirement          → none
## Alternatives Considered        → (edit required)
```

LLM 생성 단계에서 이 섹션들에 의미 있는 내용을 채울 수 없다. Kiro의 원칙을 차용한다: **생성할 수 없는 섹션은 포함하지 않는다.**

### 3.3 적응형 깊이 부재

| Policy | 현재 문서 수 | 문제 |
|--------|-------------|------|
| quick_fix | **0개** | 에이전트 실행 컨텍스트 전무 |
| standard_update | **4개** | 빈 섹션 포함한 design까지 생성 — 과도 |
| deep_update | **4개** | standard와 차이 없음 |
| full_bootstrap | **4개** | 역시 동일 |

```python
# maintenance_pipeline.py — 현재 이분법
def prepare(self, normalized):
    if execution_policy == "quick_fix":
        return self._minimal_prepare(normalized)   # → 문서 0개
    return self._full_prepare(normalized, policy)   # → 문서 4개 (항상)
```

---

## 4. 설계: 적응형 Spec 시스템

### 4.1 핵심 개념 — `spec_depth`

기존 4개 문서를 유지하되, `ExecutionPolicy`에 `spec_depth` 필드를 추가하여 복잡도에 따라 **필요한 문서만 생성**한다.

문서 계층 (역할 분리 유지):

```
feature-plan.md         ← 왜 (Why)   — 의사결정자용
feature-spec.md         ← 무엇 (What) — 실행자용
implementation-design.md ← 어떻게 (How) — 설계 검토용
implementation-tasks.md  ← 실행 (Do)   — 에이전트 실행 소스
```

### 4.2 Policy → spec_depth 매핑

`spec_depth`는 기본 문서 tier를 결정하고, `work_kind`가 bugfix인 경우 bug-fix-spec.md를 추가 생성한다.

```
┌─────────────────┬────────────┬───────────────────────────────────────────────────┬────────┐
│ Policy          │ spec_depth │ 생성 문서                                         │ 파일 수│
├─────────────────┼────────────┼───────────────────────────────────────────────────┼────────┤
│ quick_fix       │ 1          │ implementation-tasks.md                           │ 1      │
│ (generic)       │            │                                                   │        │
├─────────────────┼────────────┼───────────────────────────────────────────────────┼────────┤
│ quick_fix       │ 1          │ bug-fix-spec.md + implementation-tasks.md         │ 2      │
│ (bugfix)        │            │                                                   │        │
├─────────────────┼────────────┼───────────────────────────────────────────────────┼────────┤
│ standard_update │ 2          │ feature-plan.md + feature-spec.md                │ 3      │
│                 │            │ + implementation-tasks.md                         │        │
├─────────────────┼────────────┼───────────────────────────────────────────────────┼────────┤
│ deep_update     │ 3          │ feature-plan.md + feature-spec.md                │ 4      │
│                 │            │ + implementation-design.md                        │        │
│                 │            │ + implementation-tasks.md                         │        │
├─────────────────┼────────────┼───────────────────────────────────────────────────┼────────┤
│ full_bootstrap  │ 3          │ (deep_update와 동일)                              │ 4      │
└─────────────────┴────────────┴───────────────────────────────────────────────────┴────────┘
```

**설계 근거:**

- **depth 1 generic (tasks만):** 단순 오타/설정 변경. 체크리스트 1개면 충분. 현재 0개보다 개선.
- **depth 1 bugfix (bug-fix-spec + tasks):** Kiro가 bugfix에서 가장 강한 부분 — 현재 동작 / 기대 동작 / 유지 동작 분리. 이미 `docs/work-items/_template/bug-fix-spec.md`에 이 구조가 있다 (버그 요약, 현재 동작, 기대 동작, 재현 절차, 수정 후 수용 기준). 이것 없이 tasks만 주면 회귀 방지 명세가 없어진다.
- **depth 2 (plan + spec + tasks):** standard_update는 범위/요구사항 명시가 필요하지만 설계 문서까지는 불필요. "왜"와 "무엇"을 알면 "어떻게"는 에이전트가 판단 가능.
- **depth 3 (full):** deep_update/full_bootstrap은 고위험·광범위 변경. 모듈 분해와 의존성 순서를 명시하는 design이 필수.

**Kiro와의 차이:** Kiro는 항상 3개. 이 설계는 복잡도에 따라 1~4개를 생성한다. 단순한 작업에 불필요한 문서를 만들지 않고, 복잡한 작업에 필요한 문서를 빠뜨리지 않는다. 특히 bugfix 경로에서 Kiro의 핵심 강점(행동 분리 명세)을 그대로 가져온다.

### 4.3 문서별 개선 사항

#### 4.3.1 feature-plan.md — 증거/참조의 단일 소스

변경 없음. 기존 구조 그대로 유지.

이 문서가 프로젝트의 증거(Evidence)와 참조(References)의 **유일한 소스**가 된다. 다른 문서에서 증거가 필요하면 이 파일을 참조한다.

```markdown
# Feature Plan

## Metadata
## Background
## Problem Statement
## Goals
## Non-Goals
## Scope
## Stakeholders
## Success Metrics
## Risks and Assumptions
## Evidence                    ← 증거의 유일한 소스
## References                  ← 참조의 유일한 소스
## Approval Request
```

#### 4.3.2 feature-spec.md — 중복 제거 + 빈 섹션 제거

Evidence, References 섹션을 제거하고 (feature-plan.md 참조), 자동 생성 불가 섹션 5개를 제거한다.

현재 → 변경 후:

| 섹션 | 현재 | 변경 |
|------|------|------|
| Metadata | 유지 | 유지 |
| Feature Overview | 유지 | 유지 |
| User Scenarios | 유지 | 유지 |
| Functional Requirements | 유지 | 유지 |
| Non-Functional Requirements | `(edit required)` | **제거** |
| Inputs and Outputs | `(edit required)` | **제거** |
| Exceptions and Failure Scenarios | `(edit required)` | **제거** |
| Existing Behavior To Preserve | `(edit required)` | **제거** |
| Acceptance Criteria | 유지 | 유지 |
| Evidence | 중복 | **제거** (plan 참조) |
| References | 중복 | **제거** (plan 참조) |
| Out Of Scope | `(edit required)` | **제거** |

변경 후 구조:

```markdown
# Feature Spec

## Metadata
- work_item: {slug}
- source_plan: feature-plan.md
- status: draft
- last_updated: {timestamp}

## Feature Overview
{goal}

## User Scenarios
- {module_name}: {module_summary}

## Functional Requirements
- {feature_slice_1}
- {feature_slice_2}

## Acceptance Criteria
- {criteria_1}
- {criteria_2}
```

**제거 근거:** 5개 빈 섹션은 LLM이 자동으로 채울 수 없어 매번 `(edit required)`로 남았다. Kiro 원칙 — "생성할 수 없는 내용은 포함하지 않는다." 사용자가 필요하면 수동으로 추가할 수 있다.

#### 4.3.3 implementation-design.md — 빈 섹션 제거

자동 생성 가능한 섹션만 유지한다.

| 섹션 | 현재 | 변경 |
|------|------|------|
| Metadata | 유지 | 유지 |
| Design Summary | 유지 | 유지 |
| Planned Modules | 유지 | 유지 |
| Data Flow | 유지 | 유지 |
| Interface Impact | `(edit required)` | **제거** |
| State And Data Model | `(edit required)` | **제거** |
| Compatibility Considerations | `(edit required)` | **제거** |
| Migration Requirement | `none` (항상) | **제거** |
| Risks | `(edit required)` | **제거** (plan에 포함) |
| Alternatives Considered | `(edit required)` | **제거** |
| Design Evidence | 중복 | **제거** (plan 참조) — 아래 "증거 단일 소스 원칙" 참고 |
| References | 중복 | **제거** (plan 참조) |
| Test Strategy | 유지 | 유지 |

**증거 단일 소스 원칙:** feature-plan.md가 증거의 유일한 소스라면, implementation-design.md에 Design Evidence를 남기는 것은 모순이다. 동일한 `_research_bullets()`를 호출하므로 내용이 완전 중복된다. design에서도 제거하고, 증거가 필요한 맥락에서는 `> 상세 증거는 feature-plan.md ## Evidence 참조` 한 줄로 대체한다.

변경 후 구조:

```markdown
# Implementation Design

## Metadata
- work_item: {slug}
- source_spec: feature-spec.md
- status: draft
- last_updated: {timestamp}

## Design Summary
{goal}
execution_strategy: {parallel|sequential}

## Planned Modules

### {module_name}
- owner: {role_id}
- objective: {summary}
- depends_on: {module_ids}

## Data Flow
1. {module_1}
2. {dep} -> {module_2}

## Test Strategy
- Unit tests per module
- Integration tests for cross-module flows

> 설계 근거 및 증거는 feature-plan.md ## Evidence 참조
```

#### 4.3.4 implementation-tasks.md — 변경 없음

체크리스트 포맷은 현재와 완전히 동일. `work_item_parser.py`의 파싱 로직이 그대로 동작한다.

```markdown
# Implementation Tasks

## Metadata
## Preconditions
## Task Evidence
## Task List
- [ ] {task_title}
  - task_id: {task_id}
  - owner_role: {role_id}
  - phase: {phase}
  - depends_on: {ids}
  - acceptance: {criteria}
  - artifacts: {files}
## Blockers
## Rollback Sign-Off
## Definition Of Done
```

### 4.4 work-item 폴더 구조 비교

**현재:**
```
docs/work-items/{slug}/          ← 항상 4개 또는 0개
├── feature-plan.md
├── feature-spec.md
├── implementation-design.md
├── implementation-tasks.md
├── approval-gate.md
└── (템플릿 파일들)
```

**변경 후:**
```
docs/work-items/{slug}/          ← spec_depth + work_kind에 따라 1~4개
├── feature-plan.md              ← depth ≥ 2
├── feature-spec.md              ← depth ≥ 2
├── bug-fix-spec.md              ← depth 1 + work_kind=bugfix
├── implementation-design.md     ← depth ≥ 3
├── implementation-tasks.md      ← 항상 (depth ≥ 1)
├── approval-gate.md             ← requires_approval=true 일 때
├── verification-report.md       ← depth ≥ 2 (템플릿)
└── change-request.md            ← depth ≥ 2 (템플릿)
```

---

## 5. 영향받는 파일 상세

### 5.1 `core/control/execution_policy.py` — `spec_depth` 필드 추가

`POLICY_STAGE_MAP`에 `spec_depth` 추가:

```python
POLICY_STAGE_MAP = {
    "quick_fix": {
        "requires_approval":          False,
        "requires_work_item_docs":    True,       # ← False → True (tasks.md 1개 생성)
        "requires_agent_qa":          False,
        "requires_parallel_critique": False,
        "requires_regression_test":   True,
        "skippable_stages":           ["evidence_retry", "critique", "agent_qa", "convergence_loop"],
        "max_retries":                1,
        "spec_depth":                 1,           # ← 추가
    },
    "standard_update": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          "auto",
        "requires_parallel_critique": False,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                2,
        "spec_depth":                 2,           # ← 추가
    },
    "deep_update": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          True,
        "requires_parallel_critique": True,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                2,
        "spec_depth":                 3,           # ← 추가
    },
    "full_bootstrap": {
        "requires_approval":          True,
        "requires_work_item_docs":    True,
        "requires_agent_qa":          True,
        "requires_parallel_critique": True,
        "requires_regression_test":   True,
        "skippable_stages":           [],
        "max_retries":                3,
        "spec_depth":                 3,           # ← 추가
    },
}
```

`ExecutionPolicy` dataclass:

```python
@dataclass
class ExecutionPolicy:
    # ... 기존 필드 ...
    spec_depth: int = 3                            # ← 추가
```

**핵심 변경:**
- `quick_fix.requires_work_item_docs`를 `False` → `True`로 변경. `spec_depth=1`이면 tasks만 생성하므로 경량성 유지.
- bugfix 여부는 `work_kind`로 판별 (ExecutionPolicy가 아니라 NormalizedRequest에서 전달). `spec_depth=1 + work_kind=bugfix` → bug-fix-spec.md 추가 생성.

**위험도:** 낮음 — 필드 추가만. 기존 코드는 `spec_depth`를 읽지 않으므로 영향 없음.

### 5.2 `core/work_item_generator.py` — 조건부 생성 + 빈 섹션 제거

**함수 구조 — 유지 (리네임/병합 없음):**

```
_generate_feature_plan()          → 변경 없음
_generate_feature_spec()          → Evidence/References 제거, 빈 섹션 5개 제거
_generate_implementation_design() → 빈 섹션 5개 제거, References 제거
_generate_implementation_tasks()  → 변경 없음
generate_work_items()             → spec_depth 파라미터 추가, 조건부 생성
```

**`generate_work_items()` 시그니처:**

```python
# 변경 후
def generate_work_items(
    workspace: str,
    slug: str,
    project_brief: dict,
    role_plan: dict,
    task_board: dict,
    spec_depth: int = 3,           # ← 추가
) -> dict[str, str]:
```

**`generate_work_items()` 내부 — 조건부 생성:**

```python
def generate_work_items(
    ...,
    spec_depth: int = 3,
    work_kind: str = "",                # ← 추가: bugfix 분기용
    requires_approval: bool = True,     # ← 추가: gate 생성 제어
) -> dict[str, str]:
    work_dir = os.path.join(os.path.abspath(workspace), WORK_ITEMS_DIR_REL, slug)
    os.makedirs(work_dir, exist_ok=True)

    # 템플릿 복사도 spec_depth에 따라 제어
    _copy_extra_templates(template_dir, work_dir, spec_depth, work_kind)

    files = {}
    work_item_id = slug

    # depth ≥ 1: implementation-tasks.md (항상)
    tasks_content = _generate_implementation_tasks(work_item_id, role_plan, task_board, project_brief)
    tasks_path = os.path.join(work_dir, "implementation-tasks.md")
    write_text(tasks_path, tasks_content)
    files["implementation-tasks.md"] = tasks_path

    # depth 1 + bugfix: bug-fix-spec.md 자동 생성
    if spec_depth == 1 and work_kind == "bugfix":
        bugfix_path = os.path.join(work_dir, "bug-fix-spec.md")
        if not os.path.exists(bugfix_path):
            _copy_bugfix_template(template_dir, bugfix_path)
        files["bug-fix-spec.md"] = bugfix_path

    # depth ≥ 2: feature-plan.md + feature-spec.md
    if spec_depth >= 2:
        plan_content = _generate_feature_plan(work_item_id, project_brief, role_plan)
        plan_path = os.path.join(work_dir, "feature-plan.md")
        write_text(plan_path, plan_content)
        files["feature-plan.md"] = plan_path

        spec_content = _generate_feature_spec(work_item_id, project_brief, role_plan, task_board)
        spec_path = os.path.join(work_dir, "feature-spec.md")
        write_text(spec_path, spec_content)
        files["feature-spec.md"] = spec_path

    # depth ≥ 3: implementation-design.md
    if spec_depth >= 3:
        design_content = _generate_implementation_design(work_item_id, project_brief, role_plan)
        design_path = os.path.join(work_dir, "implementation-design.md")
        write_text(design_path, design_content)
        files["implementation-design.md"] = design_path

    # approval gate — requires_approval=true 일 때만 생성
    if requires_approval:
        gate = ApprovalGate(workspace, slug, spec_depth=spec_depth)
        gate.initialize(work_item_id)
        files["approval-gate.md"] = gate.gate_path

    return files
```

**`_copy_extra_templates()` — spec_depth 기반 제어:**

현재 `_copy_extra_templates()`는 항상 `verification-report.md`, `change-request.md`, `bug-fix-spec.md` 3개를 복사한다. spec_depth에 따라 필요한 템플릿만 복사하도록 변경한다:

```python
def _copy_extra_templates(template_dir, work_dir, spec_depth, work_kind):
    if not os.path.isdir(template_dir):
        return

    # verification-report.md — depth ≥ 2 (검증 보고서는 문서가 있는 경우에만 의미)
    # change-request.md — depth ≥ 2 (승인 후 범위 변경 추적)
    # bug-fix-spec.md — bugfix work_kind일 때만
    templates_to_copy = set()
    if spec_depth >= 2:
        templates_to_copy.update({"verification-report.md", "change-request.md"})
    if work_kind == "bugfix":
        templates_to_copy.add("bug-fix-spec.md")

    for filename in templates_to_copy:
        src = os.path.join(template_dir, filename)
        dst = os.path.join(work_dir, filename)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
```

**`_generate_feature_spec()` — 빈 섹션 제거 후:**

```python
def _generate_feature_spec(work_item, project_brief, role_plan, task_board):
    # ... 기존 데이터 추출 ...
    return (
        "# Feature Spec\n\n"
        "## Metadata\n\n"
        f"- work_item: {work_item}\n"
        "- source_plan: feature-plan.md\n"
        "- status: draft\n"
        f"- last_updated: {now_iso()}\n\n"
        "## Feature Overview\n\n"
        f"{goal or '(edit required)'}\n\n"
        "## User Scenarios\n\n"
        f"{scenario_text}\n\n"
        "## Functional Requirements\n\n"
        f"{req_text}\n\n"
        "## Acceptance Criteria\n\n"
        f"{acceptance_text}\n"
        # Evidence, References, 빈 섹션 5개 — 모두 제거
    )
```

**`_generate_implementation_design()` — 빈 섹션 + 증거 중복 제거 후:**

```python
def _generate_implementation_design(work_item, project_brief, role_plan):
    # ... 기존 데이터 추출 ...
    return (
        "# Implementation Design\n\n"
        "## Metadata\n\n"
        f"- work_item: {work_item}\n"
        "- source_spec: feature-spec.md\n"
        "- status: draft\n"
        f"- last_updated: {now_iso()}\n\n"
        "## Design Summary\n\n"
        f"{goal or '(edit required)'}\n"
        f"execution_strategy: {execution_strategy}\n\n"
        "## Planned Modules\n\n"
        f"{module_text}\n\n"
        "## Data Flow\n\n"
        f"{flow_text}\n\n"
        "## Test Strategy\n\n"
        "- Unit tests per module\n"
        "- Integration tests for cross-module flows\n\n"
        "> 설계 근거 및 증거는 feature-plan.md ## Evidence 참조\n"
        # Design Evidence, References — plan 단일 소스 원칙으로 제거
        # Interface Impact, State/Data Model, Compatibility,
        # Migration, Risks, Alternatives — 빈 섹션 제거
    )
```

**위험도:** 중간 — 빈 섹션 제거는 정보 손실 없음. `generate_work_items()` 분기 추가는 기존 호출부 확인 필요.

### 5.3 `core/approval_gate.py` — 적응형 해싱

`spec_depth`에 따라 해싱 대상 파일이 달라진다:

```python
# depth별 해싱 대상
_DOC_FILES_BY_DEPTH: dict[int, dict[str, str]] = {
    1: {
        "implementation_tasks": "implementation-tasks.md",
    },
    2: {
        "feature_plan": "feature-plan.md",
        "feature_spec": "feature-spec.md",
        "implementation_tasks": "implementation-tasks.md",
    },
    3: {
        "feature_plan": "feature-plan.md",
        "feature_spec": "feature-spec.md",
        "implementation_design": "implementation-design.md",
        "implementation_tasks": "implementation-tasks.md",
    },
}

# 구구조 하위호환 (기존 work-item에 bug_fix_spec 포함)
_LEGACY_DOC_FILES = {
    "feature_plan": "feature-plan.md",
    "feature_spec": "feature-spec.md",
    "bug_fix_spec": "bug-fix-spec.md",
    "implementation_design": "implementation-design.md",
    "implementation_tasks": "implementation-tasks.md",
}
```

`ApprovalGate` 변경:

```python
class ApprovalGate:
    def __init__(self, workspace: str, slug: str, spec_depth: int = 3):
        self.spec_depth = spec_depth
        # ... 기존 초기화 ...

    def initialize(self, work_item_id: str = "") -> None:
        """approval-gate.md 최초 생성. spec_depth를 metadata에 명시적으로 기록한다."""
        content = self._render(
            work_item=work_item_id or self.slug,
            spec_depth=self.spec_depth,    # ← metadata에 기록
            # ... 나머지 기존 동일 ...
        )
        write_text(self.gate_path, content)

    def _get_doc_files(self) -> dict[str, str]:
        """approval-gate.md metadata의 spec_depth를 읽어 해싱 대상 결정.
        파일 존재 여부 휴리스틱 대신 명시적 spec_depth 값을 사용한다.
        """
        # approval-gate.md에서 spec_depth 읽기
        data = self._parse()
        stored_depth = data.get("spec_depth")

        if stored_depth is not None:
            depth = int(stored_depth)
            return _DOC_FILES_BY_DEPTH.get(depth, _DOC_FILES_BY_DEPTH[3])

        # spec_depth 미기록 (구구조) → legacy 해싱
        return _LEGACY_DOC_FILES
```

**판별 방식 변경:** 파일 존재 여부 휴리스틱은 위험하다. `implementation-tasks.md`는 모든 depth에서 생성되므로 legacy 폴더에서도 신구조로 오판할 수 있다. 대신 `approval-gate.md` metadata에 `spec_depth` 값을 명시적으로 기록하고, 이 값으로 판별한다. `spec_depth`가 없으면 구구조로 간주한다.

```markdown
# approval-gate.md 예시 (metadata)
## Metadata
- work_item: {slug}
- spec_depth: 2              ← 명시적 기록
- approver:
- status: review_pending
- last_updated: {timestamp}
```

**위험도:** 중간 — 승인 게이트는 실행 안전성의 핵심. 구구조 fallback 테스트 필수.

### 5.4 `core/work_item_parser.py` — 최소 변경

기존 함수 모두 유지. 변경 없음.

- `parse_feature_plan()` — 유지
- `parse_feature_spec()` — 유지 (제거된 섹션은 파싱 대상이 아니었음)
- `parse_implementation_tasks()` — 유지
- `sync_board_from_work_items()` — 유지

**위험도:** 없음 — 변경 없음.

### 5.5 `core/control/maintenance_pipeline.py` — prepare 경로 통합

**현재 (이분법):**

```python
def prepare(self, normalized):
    policy = self._get_policy(normalized)
    execution_policy = policy.get("execution_policy", "standard_update")

    if execution_policy == "quick_fix":
        return self._minimal_prepare(normalized)       # → 문서 0개
    return self._full_prepare(normalized, policy)       # → 문서 4개
```

**변경 후 (spec_depth 기반):**

```python
def prepare(self, normalized):
    policy = self._get_policy(normalized)
    spec_depth = policy.get("spec_depth", 3)

    if spec_depth == 1:
        return self._lightweight_prepare(normalized, spec_depth)
    return self._full_prepare(normalized, policy, spec_depth)
```

`_minimal_prepare()` → `_lightweight_prepare()`:

```python
def _lightweight_prepare(self, normalized, spec_depth: int) -> dict:
    """
    spec_depth=1 전용 경량 prepare.
    ProjectPipeline.prepare()를 호출하지 않고 implementation-tasks.md만 생성.
    """
    task_input = self._get_task_input(normalized)
    work_kind = self._get_work_kind(normalized)

    brief = {
        "type": "lightweight_brief",
        "goal": task_input,
        "task_input": task_input,
        "work_kind": work_kind,
    }

    # owner_role은 기존 오케스트레이터 fallback 체계와 호환되는 역할명 사용.
    # bootstrap_roles._fallback_roles()에 정의된 역할:
    #   frontend_dev, backend_dev, game_logic_dev, designer, qa_engineer
    # 유지보수 단일 에이전트 실행 시 backend_dev가 가장 범용적.
    # "maintainer" 같은 미등록 역할은 에이전트 매칭 실패를 유발한다.
    minimal_task_board = {
        "tasks": [{
            "task_id": "task-001",
            "title": task_input[:120],
            "instruction": task_input,
            "owner_role": "backend_dev",
            "phase": "build",
            "depends_on": [],
            "acceptance": [],
            "artifacts": [],
        }]
    }
    minimal_role_plan = {"modules": [], "planning_steps": [], "roles": []}

    from core.work_item_generator import generate_work_items, slug_from_brief
    slug = slug_from_brief(brief)
    files = generate_work_items(
        self._workspace, slug, brief, minimal_role_plan, minimal_task_board,
        spec_depth=spec_depth,
    )

    return {
        "brief": brief,
        "board": minimal_task_board,
        "work_items": files,
        "skipped_stages": ["evidence_retry", "critique", "agent_qa",
                           "convergence_loop"],
    }
```

`_full_prepare()`에 `spec_depth` 전달:

```python
def _full_prepare(self, normalized, policy: dict, spec_depth: int = 3) -> dict:
    # ... 기존 ProjectPipeline.prepare() 위임 ...
    if isinstance(prepared, dict):
        prepared["spec_depth"] = spec_depth
    return prepared
```

**위험도:** 낮음 — MaintenancePipeline은 래퍼이므로 내부 변경이 외부에 전파되지 않음.

### 5.6 `core/project_pipeline.py` — `spec_depth` 전달

`prepare()` 내부 Stage 5에서 `spec_depth` 전달:

```python
spec_depth = kwargs.get("spec_depth", 3)
work_item_files = generate_work_items(
    workspace, slug, project_brief, role_plan, task_board,
    spec_depth=spec_depth,
)
```

기존 직접 호출 시 `spec_depth`를 전달하지 않으면 기본값 3이 적용되어 하위호환 유지.

**위험도:** 낮음.

### 5.7 `docs/work-items/_template/` — 변경 없음

기존 파일명을 유지하므로 템플릿 변경 불필요. 빈 섹션 제거는 `_generate_*()` 함수에서 처리.

---

## 6. 전후 비교

### 6.1 quick_fix generic — "fix typo in README.md"

**현재:**
```
→ ExecutionPolicy: quick_fix
→ _minimal_prepare()
→ 생성 문서: 0개
→ 에이전트 컨텍스트: brief dict만 (goal 60자)
→ 문제: 구조화된 실행 지시 없음
```

**변경 후:**
```
→ ExecutionPolicy: quick_fix, spec_depth=1, work_kind=maintenance
→ _lightweight_prepare()
→ 생성 문서: implementation-tasks.md 1개
→ 에이전트 컨텍스트: 체크리스트 (task_id, phase, acceptance)
→ 개선: 최소한의 구조화 + board 동기화 가능
```

### 6.2 quick_fix bugfix — "fix null pointer in user login"

**현재:**
```
→ ExecutionPolicy: quick_fix
→ _minimal_prepare()
→ 생성 문서: 0개
→ 문제: 현재 동작/기대 동작/유지 동작 분리 없음. 회귀 방지 명세 없음
```

**변경 후:**
```
→ ExecutionPolicy: quick_fix, spec_depth=1, work_kind=bugfix
→ _lightweight_prepare()
→ 생성 문서: bug-fix-spec.md + implementation-tasks.md (2개)
→ 개선: 현재 동작/기대 동작/재현 절차/수용 기준 명시. 회귀 방지 가능
```

### 6.3 standard_update — "add retry logic to API client"

**현재:**
```
→ ExecutionPolicy: standard_update
→ _full_prepare() → ProjectPipeline.prepare()
→ 생성 문서: 4개 (plan + spec + design + tasks)
→ 문제: design 불필요, spec에 빈 섹션 5개 + 중복 증거/참조
```

**변경 후:**
```
→ ExecutionPolicy: standard_update, spec_depth=2
→ _full_prepare(spec_depth=2)
→ 생성 문서: 3개 (plan + spec + tasks)
→ 개선: design 생략, spec 빈 섹션 제거, 증거/참조 중복 제거
```

### 6.4 deep_update — "refactor authentication middleware"

**현재:**
```
→ ExecutionPolicy: deep_update
→ _full_prepare() → ProjectPipeline.prepare()
→ 생성 문서: 4개 (standard_update와 동일)
→ 문제: standard와 차이 없음, design에 빈 섹션 5개
```

**변경 후:**
```
→ ExecutionPolicy: deep_update, spec_depth=3
→ _full_prepare(spec_depth=3)
→ 생성 문서: 4개 (plan + spec + design + tasks)
→ 개선: design 빈 섹션 제거, standard와 명확한 차이 (design 유무)
```

---

## 7. 하위 호환성

### 7.1 기존 work-item 폴더

이미 생성된 `docs/work-items/{slug}/`에는 기존 구조(빈 섹션 포함)의 파일이 존재한다.

**영향 없음.** 파일명이 동일하므로 기존 파일 읽기/해싱에 변경 없음. `ApprovalGate._get_doc_files()` fallback이 기존 스냅샷과 정상 비교.

### 7.2 ProjectPipeline 직접 호출

`MaintenancePipeline` 없이 직접 호출 시 `spec_depth` 기본값 3이 적용 → 풀 스펙 생성. 동작 변경 없음.

### 7.3 approval-gate.md 기존 스냅샷

기존 스냅샷의 키는 `feature_plan`, `feature_spec` 등으로 동일. 해싱 대상 파일명도 동일. 정상 동작.

---

## 8. 구현 단계

### 단계 1: `execution_policy.py` — spec_depth 추가

| 작업 | 변경 |
|------|------|
| `POLICY_STAGE_MAP`에 `spec_depth` 추가 | quick_fix=1, standard=2, deep=3, full=3 |
| `ExecutionPolicy` dataclass | `spec_depth: int = 3` 필드 추가 |
| `quick_fix.requires_work_item_docs` | `False` → `True` |

위험도: **낮음**

### 단계 2: `work_item_generator.py` — 빈 섹션 제거 + 조건부 생성

| 작업 | 변경 |
|------|------|
| `_generate_feature_spec()` | Evidence, References, 빈 섹션 5개 제거 |
| `_generate_implementation_design()` | Design Evidence, References, 빈 섹션 5개 제거 (plan 참조 한 줄로 대체) |
| `generate_work_items()` | `spec_depth`, `work_kind`, `requires_approval` 파라미터 추가 |
| `generate_work_items()` 분기 | depth 1 + bugfix → bug-fix-spec.md 추가 생성 |
| `_copy_extra_templates()` | spec_depth/work_kind 기반 조건부 복사 |

위험도: **중간** — 빈 섹션 제거는 정보 손실 없으나, `generate_work_items()` 시그니처 변경으로 호출부 확인 필요.

### 단계 3: `approval_gate.py` — 적응형 해싱

| 작업 | 변경 |
|------|------|
| `_DOC_FILES_BY_DEPTH` 추가 | depth별 해싱 대상 정의 |
| `_LEGACY_DOC_FILES` 추가 | 구구조 하위호환 |
| `ApprovalGate.__init__()` | `spec_depth` 파라미터 추가 |
| `initialize()` | approval-gate.md metadata에 `spec_depth` 기록 |
| `_get_doc_files()` 신규 | metadata의 `spec_depth` 값으로 판별 (파일 존재 휴리스틱 대신) |

위험도: **중간** — 구구조 fallback 테스트 필수.

### 단계 4: `maintenance_pipeline.py` — prepare 경로 통합

| 작업 | 변경 |
|------|------|
| `_minimal_prepare()` → `_lightweight_prepare()` | tasks.md 생성 (bugfix면 bug-fix-spec.md 추가) |
| `_lightweight_prepare()` owner_role | `"maintainer"` → `"backend_dev"` (기존 fallback 역할 체계 호환) |
| `_full_prepare()` | `spec_depth`, `work_kind`, `requires_approval` 전달 |
| `prepare()` 분기 | `spec_depth` 기반으로 변경 |

위험도: **낮음**

### 단계 5: `project_pipeline.py` — spec_depth 전달

| 작업 | 변경 |
|------|------|
| `prepare()` Stage 5 | `generate_work_items()` 호출에 `spec_depth` 전달 |

위험도: **낮음**

---

## 9. 비채택 대안

### 9.1 Kiro 완전 복제 (3파일로 병합)

feature-plan.md + feature-spec.md를 requirements.md 1개로 병합하는 방안.

**비채택 사유:** plan(왜)과 spec(무엇)은 역할이 다르다. 의사결정자용 문서와 실행자용 문서를 합치면 목적이 흐려진다. 증거/참조 중복은 섹션 제거만으로 해결 가능하므로 파일 병합이 불필요하다.

### 9.2 적응형 없이 빈 섹션만 제거

`spec_depth` 도입 없이 빈 섹션만 제거하는 방안.

**비채택 사유:** quick_fix 문서 0개 문제와 deep=standard 동일 문서 문제가 해결되지 않음. 빈 섹션 제거는 이 설계의 일부이지 전체가 아님.

### 9.3 단일 파일 통합

모든 내용을 spec.md 1개에 통합하는 방안.

**비채택 사유:** 역할 분리가 소멸. 체크리스트와 요구사항이 섞이면 `sync_board_from_work_items()` 파싱이 복잡해지고, 부분 편집이 불가능해짐.

---

## 10. 검증 기준

| 항목 | 검증 방법 |
|------|----------|
| quick_fix generic 시 tasks 1개만 생성 | `spec_depth=1, work_kind="maintenance"`로 호출 후 파일 목록 |
| quick_fix bugfix 시 bug-fix-spec + tasks 생성 | `spec_depth=1, work_kind="bugfix"`로 호출 후 파일 2개 확인 |
| standard_update 시 plan + spec + tasks 생성 | `spec_depth=2`로 호출 후 파일 3개 + 내용 확인 |
| deep_update 시 plan + spec + design + tasks 생성 | `spec_depth=3`로 호출 후 파일 4개 확인 |
| feature-spec.md에 Evidence/References 없음 | 생성 후 섹션 존재 여부 확인 |
| feature-spec.md에 빈 섹션 없음 | `(edit required)` 문자열 미포함 확인 |
| implementation-design.md에 Evidence/빈 섹션 없음 | `(edit required)` 미포함 + `## Design Evidence` 미존재 확인 |
| approval-gate.md에 spec_depth 기록됨 | `_parse()` 후 `spec_depth` 키 존재 확인 |
| 구구조 폴더에서 gate 정상 동작 (spec_depth 미기록) | `_get_doc_files()` → `_LEGACY_DOC_FILES` 반환 확인 |
| depth 1에서 템플릿 불필요 복사 없음 | verification-report.md, change-request.md 미생성 확인 |
| _lightweight_prepare() owner_role 매핑 | backend_dev가 오케스트레이터에서 에이전트 매칭 성공 |
| tasks 체크리스트 포맷 동일 | 기존 `parse_implementation_tasks()` 결과 비교 |
| board sync 정상 동작 | tasks 편집 후 `sync_board_from_work_items()` 반영 확인 |

---

## 11. 요약

| 측면 | 현재 | 변경 후 |
|------|------|---------|
| 문서 수 | 0 또는 4 (이분법) | 1~4 (적응형: depth + work_kind) |
| 파일 구조 | 4파일 (역할 분리) | 4파일 유지 (역할 분리 보존) |
| 빈 섹션 | spec 5개 + design 5개 | 0 (자동 생성 불가 섹션 제거) |
| 증거/참조 중복 | plan + spec + design 3곳 | plan에만 (단일 소스, design은 참조 한 줄) |
| quick_fix generic | 문서 0개 | tasks 1개 (최소 구조화) |
| quick_fix bugfix | 문서 0개 | bug-fix-spec + tasks 2개 (회귀 방지 명세 포함) |
| deep vs standard | 동일 4파일 | design 유무로 차별화 |
| 템플릿 복사 | 항상 3개 복사 | depth/work_kind에 따라 조건부 복사 |
| gate 판별 | - (고정 _DOC_FILES) | metadata spec_depth로 명시적 판별 |
| role 매핑 | - | 기존 fallback 역할 체계(backend_dev) 호환 |
| 정보 손실 | - | 0 (실제 데이터가 채워지는 섹션 전부 보존) |
| Kiro에서 차용 | - | 빈 섹션 제거, bugfix 행동 분리 명세, 문서 선명도 |
| agent-factory 유지 | - | 승인 게이트, 보드 연동, 역할 분리, 해시 무결성 |
