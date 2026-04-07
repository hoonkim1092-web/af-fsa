# Planning 문서 LLM 생성 파이프라인

> 날짜: 2026-04-07
> 상태: 설계 v4 (교차 검증 반영)
> 대상 파일: `core/work_item_generator.py`
> 연관 파일: `core/project_pipeline.py`, `core/researcher.py`, `core/requirement_llm.py`, `core/work_item_parser.py`, `core/interactive_chat.py`, `agent_launcher.py`

## 1. 문제 정의

### 현재 상태

사용자가 "로또 프로그램 만들어줘"를 요청하면 Planning 파이프라인이 5단계를 거쳐 문서를 생성한다:

```
Step 1: Evidence 수집    → LLM 0회 (로컬/웹 검색)       ✅ 정상
Step 2: Brief 생성       → LLM 1회 (Evidence 입력)       ✅ 정상
Step 3: Role Plan 생성   → LLM 1회 (Brief 입력)          ✅ 정상
Step 4: Task Board 조립  → LLM 0회 (코드 구조화)         ✅ 정상
Step 5: 문서 4종 생성    → LLM 0회 (f-string 템플릿)     ❌ 병목
```

### 핵심 문제: Step 5에서 정보 손실

| 함수 | 생성 문서 | 방식 | 문제 |
|------|----------|------|------|
| `_generate_feature_plan()`:133 | feature-plan.md | f-string 조립 | Brief 복사 수준 |
| `_generate_feature_spec()`:208 | feature-spec.md | f-string 조립 | NFR/예외 = `(edit required)` |
| `_generate_implementation_design()`:312 | implementation-design.md | f-string 조립 | 인터페이스/대안 = `(edit required)` |
| `_generate_implementation_tasks()`:430 | implementation-tasks.md | f-string 조립 | 체크리스트 복사, 의존성/순서 불충분 |

Brief→RolePlan까지 LLM이 정보를 **정제·확장**하는데, 문서 생성에서 LLM 없이 **복사·재배열**만 하므로 `(edit required)` 빈칸이 다수 발생한다.

### 비교: Kiro AI의 접근

Kiro는 3단계 **Requirements → Design → Tasks** 각각에서 LLM을 호출하며, 이전 단계 산출물을 다음 단계 LLM의 입력으로 전달한다. 마지막 단계까지 LLM이 정보를 **재해석·구체화**하므로 빈칸 없는 실행 가능 문서가 생성된다.

### 추가 문제: Evidence 활용 부족

> **교차 검증 수정**: `_merge_project_brief_evidence()`가 evidence를 `project_brief`에 직접 병합하므로,
> `project_brief`에는 `evidence_summary`, `local_references`, `web_references`, `notebook_summary` 등이
> 이미 포함되어 있다. "Evidence 원본이 Brief에서 소실된다"는 기존 전제는 **부분적으로만 유효**하다.

실제 문제는 전파 단절이 아니라 **활용 부족**이다:
- `_research_bullets()` (work_item_generator.py:36) — `evidence_summary`의 **처음 8개만** 사용
- `_reference_bullets()` (work_item_generator.py:49) — URL/제목의 **축약 버전만** 사용
- NFR, 예외 사항, 대안 분석 등에는 **Evidence가 전혀 반영되지 않음**

즉 `project_brief` 안에 Evidence는 있지만, 문서 생성 함수가 그것을 **충분히 활용하지 않는다**. 별도 `evidence` 파라미터 추가가 아니라, **LLM 프롬프트에서 `project_brief` 내 evidence 필드를 적극 참조**하는 것이 해결책이다.

---

## 2. 설계 목표

1. **4개 문서 생성 함수를 LLM 기반으로 전환** — `(edit required)` 제거, 실행 가능 문서 생성
2. **`project_brief` 내 Evidence 필드를 LLM 프롬프트에서 적극 활용** — 별도 파라미터 추가 없이, 이미 병합된 evidence 데이터를 문서 생성에 반영
3. **기존 fallback 철학 유지** — LLM 실패 시 현재 f-string 방식으로 graceful degradation
4. **LLM 호출 예산 관리** — 추가 5회 이내 (기존 Brief 1회 + RolePlan 1회에 더해)
5. **연쇄 정제 패턴 적용** — 각 문서가 이전 문서를 입력으로 받아 품질 누적 + 실패 전파 방지
6. **Clarification 단계 도입** — Brief 생성 직후 LLM이 모호한 부분을 질문, 사용자 답변으로 Brief 보강 후 문서 생성
7. **Markdown 생성 전용 LLM 인터페이스** — 기존 JSON-only `execute_requirement_prompt()`와 분리
8. **기존 파서 호환성 보장** — FR+AC 일체형 구조에서도 `work_item_parser.py`가 정상 동작

---

## 3. 아키텍처 설계

### 3.1 개선 후 파이프라인

```
Step 1:  Evidence 수집           → LLM 0회                    (변경 없음)
Step 2:  Brief 생성              → LLM 1회                    (변경 없음)
Step 2.5: Clarification 질의     → LLM 1회 + 사용자 답변       ★ 신규
Step 3:  Role Plan 생성          → LLM 1회 (보강된 Brief 입력)  (변경: 입력 보강)
Step 4:  Task Board 조립         → LLM 0회                    (변경 없음)
Step 5a: feature-plan.md         → LLM 1회 (Evidence+Brief)    ★ 신규
Step 5b: feature-spec.md         → LLM 1회 (Evidence+Brief+RolePlan+5a)  ★ 신규
Step 5c: impl-design.md          → LLM 1회 (Evidence+Brief+RolePlan+5b)  ★ 신규
Step 5d: impl-tasks.md           → LLM 1회 (RolePlan+TaskBoard+5c)       ★ 신규
```

총 LLM 호출: 기존 2회 → **7회** (5회 추가, Clarification 1회 포함)

### 3.2 Clarification 단계 (사용자 질의를 통한 요구사항 구체화)

#### 3.2.1 문제: 사용자 입력의 모호성

사용자가 "로또 프로그램 만들어줘"라고 입력하면 시스템은 모든 것을 **추측**해야 한다:
- GUI인가 CLI인가?
- 데이터를 크롤링할 건가 직접 입력할 건가?
- 통계 분석 기능이 필요한가?
- 배포 형태는? (exe, 웹, 스크립트)

현재 시스템은 이 모호성을 **LLM 추측 + 키워드 heuristic**으로 해결한다. Kiro AI는 이 지점에서 **사용자에게 직접 질문**하여 모호성을 제거한다.

#### 3.2.2 파이프라인 삽입 위치

```
Step 2: Brief 생성 (LLM)
  → Brief에 goal, constraints, tech_stack, user_flows 등이 채워짐
  → 하지만 LLM의 추측이 섞여 있음
         ↓
★ Step 2.5: Clarification
  → LLM이 Brief를 분석해서 "모호하거나 누락된 부분" 질문 생성
  → 사용자에게 질문 표시, 답변 수집
  → 답변을 Brief에 병합 (enriched_brief)
         ↓
Step 3: Role Plan 생성 (enriched_brief 입력)
  → 사용자 의도가 반영된 정확한 역할/모듈 설계
```

**Brief 이후, RolePlan 이전**에 넣는 이유:
- Brief가 있어야 "뭘 모르는지"를 파악할 수 있다 (빈 상태에서 질문 생성 불가)
- RolePlan부터는 Brief의 정보에 의존하므로, 보강된 Brief가 전체 하류에 전파됨
- Evidence도 이미 수집된 상태이므로, "Evidence로 답할 수 없는 것"만 질문 가능

#### 3.2.3 Clarification LLM 프롬프트

```
You are a requirements analyst. Analyze the project brief below and generate
targeted clarification questions for ambiguous or missing requirements.

## Project Brief
{json.dumps(project_brief, ensure_ascii=False)}

## Evidence Available
{_format_evidence_context(evidence)}

## Rules
- Generate 3-5 questions that would MOST impact the quality of implementation
- Do NOT ask about things already answered in the brief or evidence
- Each question must target a specific gap: UI preference, data source, deployment,
  performance requirement, user flow, scope boundary, etc.
- Provide 2-3 suggested options per question to reduce user effort
- Questions must be in Korean

## Output Format (JSON)
{
  "questions": [
    {
      "id": "Q1",
      "category": "ui|data|deployment|scope|performance|integration",
      "question": "질문 텍스트",
      "why": "이 질문이 왜 중요한지 한줄 설명",
      "options": ["옵션A", "옵션B", "옵션C"],
      "default": "옵션A"
    }
  ]
}
```

예시 출력 ("로또 프로그램 만들어줘" 입력 시):
```
Q1 [ui]         UI 형태를 어떤 걸로 할까요?
                 → A) 데스크톱 GUI (tkinter)  B) 웹 앱  C) CLI
                 (기본값: A)

Q2 [data]       과거 당첨 번호를 어디서 가져올까요?
                 → A) 동행복권 크롤링  B) 사용자가 직접 입력  C) 내장 데이터
                 (기본값: A)

Q3 [scope]      통계 분석 기능이 필요한가요?
                 → A) 번호별 출현 빈도  B) 고급 통계 (연속번호, 핫/콜드)  C) 불필요
                 (기본값: A)

Q4 [deployment] 배포 형태는?
                 → A) exe 단독 실행  B) Python 스크립트  C) 웹 배포
                 (기본값: A)
```

#### 3.2.4 통합 패턴: prepare() 3분할 (교차 검증 Hold 수정)

> **문제**: 현재 `prepare()`는 순수 데이터 생성 함수이고 I/O를 하지 않는다.
> Clarification은 중간에 사용자 입력이 필요하므로, `prepare()` 내부에서 직접 처리 불가.
> 또한 `agent_launcher.py`의 `_run_project_with_approval()`이 `prepare()` → `execute()` 
> 2-Phase 구조이므로, Clarification은 이 사이에 삽입되어야 한다.

**해결**: `prepare()`를 3단계로 분할:

```python
# project_pipeline.py

def prepare_brief(self, task_input, workspace, ...) -> PreparedBrief:
    """Phase 1a: Evidence 수집 + Brief 생성까지만. I/O 없음."""
    # ... Evidence 수집, Brief 생성
    return PreparedBrief(
        project_brief=project_brief,
        research_evidence=research_evidence,
        workspace=target_workspace,
        run_id=run_id,
    )

def prepare_documents(self, prepared_brief: PreparedBrief, ...) -> PreparedProject:
    """Phase 1b: Brief(enriched) → RolePlan → TaskBoard → Documents. I/O 없음."""
    # ... RolePlan 생성, TaskBoard, 문서 생성
    return PreparedProject(...)
```

**기존 `prepare()` 유지 (하위 호환)**:
```python
def prepare(self, task_input, workspace, ...) -> PreparedProject:
    """기존 API 유지 — 내부적으로 3단계 호출."""
    brief_result = self.prepare_brief(task_input, workspace, ...)
    # Clarification 없이 바로 진행 (기존 동작)
    return self.prepare_documents(brief_result, ...)
```

**호출자 패턴** (`agent_launcher.py` / `interactive_chat.py`):
```python
# Clarification이 필요한 경우 (interactive 모드)
brief_result = pipeline.prepare_brief(task_input, workspace, ...)
questions = generate_clarification_questions(brief_result.project_brief)
if questions and not fsa_mode:
    answers = collect_user_answers(questions)  # UI에서 수집
    enriched_brief = merge_clarification(brief_result.project_brief, questions, answers)
    brief_result.project_brief = enriched_brief
prepared = pipeline.prepare_documents(brief_result, ...)

# Clarification이 불필요한 경우 (FSA 모드 / 기존 호환)
prepared = pipeline.prepare(task_input, workspace, ...)  # 기존과 동일
```

이 패턴의 장점:
- `prepare()`의 기존 동작 100% 유지 (하위 호환)
- Clarification I/O는 **호출자(interactive_chat/agent_launcher)**가 담당
- `prepare_brief()`와 `prepare_documents()`는 순수 함수 유지 (I/O 없음)
- FSA 모드는 기존 `prepare()` 그대로 사용

**CLI 표시 형태:**
```
[Himari] 프로젝트를 더 정확하게 설계하기 위해 몇 가지 확인이 필요합니다.

  Q1. UI 형태를 어떤 걸로 할까요?
      → 이 선택에 따라 기술 스택과 모듈 구조가 달라집니다.
      1) 데스크톱 GUI (tkinter)  [기본값]
      2) 웹 앱
      3) CLI

  Q2. 과거 당첨 번호를 어디서 가져올까요?
      → 데이터 수집 모듈의 복잡도와 오프라인 지원 여부가 결정됩니다.
      1) 동행복권 크롤링  [기본값]
      2) 사용자가 직접 입력
      3) 내장 데이터

  Q3. 통계 분석 기능이 필요한가요?
      → 분석 깊이에 따라 추가 모듈과 라이브러리가 필요합니다.
      1) 번호별 출현 빈도  [기본값]
      2) 고급 통계 (연속번호, 핫/콜드)
      3) 불필요

  Q4. 배포 형태는?
      → 빌드 파이프라인과 의존성 패키징 전략이 결정됩니다.
      1) exe 단독 실행  [기본값]
      2) Python 스크립트
      3) 웹 배포

  번호로 답변하세요 (예: 1 1 2 1). Enter만 누르면 기본값 적용:
```

**사용자 답변 형태:**
- `1 1 2 1` → 각 질문에 옵션 번호로 답변
- Enter (빈 입력) → 모든 질문에 기본값 적용
- `1 _ 2 _` → 일부만 답변, `_`는 기본값

#### 3.2.5 Brief 병합

사용자 답변을 Brief에 병합하는 로직:

```python
def _merge_clarification(project_brief: dict, questions: list[dict], answers: list[str]) -> dict:
    """사용자 답변을 Brief에 병합하여 enriched_brief 반환."""
    enriched = dict(project_brief)
    clarification_log = []

    for q, answer in zip(questions, answers):
        selected = answer if answer else q["default"]
        clarification_log.append({
            "question": q["question"],
            "answer": selected,
            "category": q["category"],
        })

        # 카테고리별 Brief 필드 업데이트
        if q["category"] == "ui":
            enriched["architecture_style"] = _map_ui_answer(selected)
        elif q["category"] == "deployment":
            enriched.setdefault("constraints", []).append(f"배포: {selected}")
        elif q["category"] == "data":
            enriched.setdefault("constraints", []).append(f"데이터 소스: {selected}")
        elif q["category"] == "scope":
            if "불필요" in selected or "없" in selected:
                enriched.setdefault("non_goals", []).append(selected)
            else:
                enriched.setdefault("deliverables", []).append(selected)
        # ... 기타 카테고리

    enriched["clarification_log"] = clarification_log
    return enriched
```

#### 3.2.6 FSA 모드 (자동 승인) 처리

`execution_mode == "fsa"` (Full Self Automation)일 때는 사용자에게 질문하지 않고 **기본값 자동 적용**:

```python
if execution_mode == "fsa":
    # 질문 생성은 하되, 모든 답변을 default로 자동 채움
    answers = [q["default"] for q in questions]
    enriched_brief = _merge_clarification(brief, questions, answers)
```

이렇게 하면 FSA에서도 Clarification의 이점(LLM이 gap을 분석해서 default로 채움)은 유지하면서 사용자 개입 없이 진행된다.

#### 3.2.7 Clarification 스킵 조건

모든 프로젝트에 질문을 던지면 오버헤드. 다음 경우 스킵:

| 조건 | 이유 |
|------|------|
| `pipeline == "single"` | 단순 태스크는 질문 불필요 |
| Brief의 빈 필드가 2개 이하 | 충분히 구체적인 입력 |
| `execution_mode == "fsa"` | 기본값 자동 적용 (질문 생성은 하되 표시 안 함) |
| 사용자가 `/skip` 입력 | 명시적 스킵 |

### 3.3 연쇄 정제 (Chained Refinement)

```
Evidence ──────────────────────────────────────────────┐
Brief ─────────────────────────────────────────────────┤
RolePlan ──────────────────────────────────────────────┤
TaskBoard ─────────────────────────────────────────────┤
                                                       ↓
Step 5a: feature-plan.md  ← Evidence + Brief
         │
         ↓
Step 5b: feature-spec.md  ← Evidence + Brief + RolePlan + feature-plan 결과
         │
         ↓
Step 5c: impl-design.md   ← Evidence + Brief + RolePlan + feature-spec 결과
         │
         ↓
Step 5d: impl-tasks.md    ← RolePlan + TaskBoard + impl-design 결과
```

핵심: 각 문서는 **이전 문서의 LLM 생성 결과**를 입력으로 받는다. 정보가 단계별로 구체화되며 누적된다.

#### 연쇄 실패 전파 방지 (교차 검증 High 수정)

> **문제**: Step 5a LLM 실패 → fallback(`(edit required)` 포함) → Step 5b LLM 입력 → placeholder 연쇄 전파

**해결**: 각 단계에서 `(문서 내용, fallback 여부)` 튜플 반환. 이전 단계가 fallback이면 다음 단계에 알림:

```python
plan_content, plan_is_fallback = _generate_doc_with_llm(plan_prompt, _fallback_feature_plan)
spec_content, spec_is_fallback = _generate_doc_with_llm(spec_prompt, _fallback_feature_spec)

# 이전 단계가 fallback이면 → 다음 단계 LLM 프롬프트에 경고 주입
if plan_is_fallback:
    spec_prompt += "\n\nWARNING: The Feature Plan above was generated by fallback (not LLM). "
    "It may contain placeholder text like '(edit required)'. "
    "Ignore placeholders and generate complete content from Brief and Evidence directly."
```

**총 fallback 카운트 > 2이면 연쇄 중단**:
```python
fallback_count = sum([plan_is_fallback, spec_is_fallback, design_is_fallback])
if fallback_count >= 2:
    # 남은 문서는 LLM 시도 없이 즉시 fallback — 시간 낭비 방지
    tasks_content = _fallback_impl_tasks(...)
```

**시간 제한**: 문서 생성 전체에 총 제한 시간 300초. 초과 시 남은 문서를 fallback으로 즉시 생성:
```python
doc_gen_deadline = time.time() + 300  # 5분
if time.time() > doc_gen_deadline:
    # 남은 문서 fallback
```

### 3.3 Evidence 활용 방식 (교차 검증 수정)

> **수정 전**: 별도 `evidence` 파라미터를 추가하여 Evidence 원본 전달
> **수정 후**: `project_brief`에 이미 병합된 evidence 필드를 LLM 프롬프트에서 적극 참조

`_merge_project_brief_evidence()` (researcher.py:571)가 Brief 생성 시점에 다음 필드를 `project_brief`에 병합한다:
- `evidence_summary` — Evidence 요약 리스트
- `local_references` — 로컬 파일 참조
- `web_references` — 웹 검색 결과
- `notebook_summary` — NotebookLM 심층 분석
- `llm_prior_references` — LLM 사전지식

따라서 `generate_work_items()`의 시그니처는 변경하지 않는다. 대신 LLM 프롬프트에서 `project_brief`의 evidence 필드를 직접 참조한다.

**`project_pipeline.py` 변경 없음** — 기존 호출 그대로 유지:
```python
work_item_files = generate_work_items(
    workspace=target_workspace,
    slug=slug,
    project_brief=project_brief,   # evidence가 이미 포함되어 있음
    role_plan=role_plan,
    task_board=task_board,
)
```

### 3.4 함수 시그니처 변경

```python
# work_item_generator.py — evidence 파라미터 불필요 (project_brief에 포함)

def _generate_feature_plan(work_item, project_brief, role_plan, prev_plan="") -> str:
def _generate_feature_spec(work_item, project_brief, role_plan, task_board, prev_plan="") -> str:
def _generate_implementation_design(work_item, project_brief, role_plan, prev_spec="") -> str:
def _generate_implementation_tasks(work_item, role_plan, task_board, project_brief=None, prev_design="") -> str:
```

각 함수 내부에서 `_format_evidence_from_brief(project_brief)`로 evidence 필드를 추출하여 프롬프트에 포함한다.

---

## 4. 각 문서별 LLM 프롬프트 설계

### 4.1 공통 구조

#### 4.1.1 Markdown 전용 LLM 함수 (교차 검증 Critical 수정)

> **문제**: `execute_requirement_prompt()`는 시스템 프롬프트에 `"Return JSON only"`가 하드코딩.
> 기존 모든 호출부가 `safe_json_load()` 전제. 이 함수로 markdown을 생성하면 **계약 위반**.

**해결**: `core/requirement_llm.py`에 markdown 생성 전용 함수를 추가한다:

```python
# requirement_llm.py — 신규

_DOCUMENT_SYSTEM_PROMPT = (
    "You are a technical document generator. "
    "Return well-structured markdown only. Do not return JSON. "
    "Do not inspect files, call tools, or modify the workspace."
)

def execute_document_prompt(
    prompt: str,
    *,
    workspace: str | None = None,
    run_id: str = "",
    timeout_sec: int = 120,
) -> dict:
    """Markdown 문서 생성 전용. execute_requirement_prompt()와 동일한 provider 순회,
    단 시스템 프롬프트만 다름."""
    # 구현: execute_requirement_prompt()와 동일 로직,
    # _REQUIREMENT_SYSTEM_PROMPT 대신 _DOCUMENT_SYSTEM_PROMPT 사용
```

기존 `execute_requirement_prompt()`는 **일절 수정하지 않는다** — JSON 계약 유지.

#### 4.1.2 문서 생성 공통 패턴

```python
from core.requirement_llm import execute_document_prompt  # ★ 새 함수

def _generate_doc_with_llm(prompt: str, fallback_fn: Callable, prev_was_fallback: bool = False) -> tuple[str, bool]:
    """LLM 호출 시도, 실패 시 기존 f-string fallback.
    Returns: (문서 내용, fallback 사용 여부)"""
    try:
        result = execute_document_prompt(prompt)
        if result.get("ok") and result.get("text", "").strip():
            return result["text"].strip(), False
    except Exception:
        pass
    return fallback_fn(), True
```

- LLM 호출: `execute_document_prompt()` (markdown 전용, JSON-only 계약과 분리)
- 반환: `(markdown 문자열, fallback 사용 여부)` — 연쇄 실패 전파 방지용
- Fallback: 현재 f-string 함수를 `_fallback_feature_plan()` 등으로 리네임하여 보존

### 4.2 Evidence 컨텍스트 포맷

`project_brief`에 이미 병합된 evidence 필드를 추출하여 프롬프트용 텍스트로 변환:

```python
def _format_evidence_from_brief(project_brief: dict) -> str:
    """project_brief 내 evidence 필드 → LLM 프롬프트용 텍스트.
    _merge_project_brief_evidence()가 Brief에 병합한 필드를 활용."""
    parts = []
    # evidence_summary (가장 중요 — 전체 사용, 8개 제한 해제)
    summary = project_brief.get("evidence_summary") or []
    if summary:
        parts.append("### Evidence Summary")
        for item in summary:  # ★ 제한 없이 전체 사용
            parts.append(f"- {item}")

    # web_references (구체적 기술 자료 — excerpt 전체 활용)
    web_refs = project_brief.get("web_references") or []
    if web_refs:
        parts.append("### Web Research")
        for ref in web_refs[:8]:
            title = ref.get("title", "")
            excerpt = ref.get("excerpt", "")[:400]  # ★ 200→400 확장
            url = ref.get("url", "")
            parts.append(f"- {title}: {excerpt}")
            if url:
                parts.append(f"  source: {url}")

    # notebook_summary (심층 분석)
    notebook = project_brief.get("notebook_summary") or ""
    if notebook:
        parts.append(f"### Deep Analysis\n{notebook[:800]}")  # ★ 500→800 확장

    # local_references
    local_refs = project_brief.get("local_references") or []
    if local_refs:
        parts.append("### Local References")
        for ref in local_refs[:5]:
            path = ref.get("path", "")
            heading = ref.get("heading", "")
            parts.append(f"- {path}: {heading}")

    return "\n".join(parts) if parts else "(no evidence available)"
```

기존 `_research_bullets()`/`_reference_bullets()`와 달리 **제한 없이 전체 evidence를 활용**한다.

### 4.3 feature-plan.md 프롬프트

```
You are a project planning specialist.

## Input
- Task: {task_input (= project_brief.goal)}
- Evidence: {_format_evidence_context(evidence)}
- Brief: {json.dumps(project_brief, ensure_ascii=False)}

## Output Format
Return a complete markdown document with the following structure:

# Feature Plan

## Metadata
- work_item: {work_item}
- status: draft
- last_updated: {timestamp}

## Project Overview
(2-3 paragraphs: 배경, 동기, 핵심 가치 제안. Brief의 goal/background_context/problem_statement를 통합하되 반복하지 말 것)

## Scope
(각 모듈의 이름과 역할을 1-2줄로. Evidence에서 도출된 기술적 제약 포함)

## Deliverables
(구체적 산출물 목록. "working implementation" 같은 추상 표현 금지)

## Stakeholders
(역할별 담당 영역)

## Risks and Mitigations
(각 리스크에 대한 구체적 완화 방안 포함. Evidence 기반)

## Research Findings
(Evidence에서 핵심 발견 사항 요약)

## References
(로컬/웹 출처)

## Approval Request
- Review this scope and confirm approval-gate.md when ready.

Rules:
- 한국어로 작성
- Evidence가 있으면 반드시 근거로 활용
- "(edit required)" 사용 금지 — 정보가 부족하면 합리적 추론으로 채울 것
- Brief의 데이터를 그대로 복사하지 말고, 프로젝트 기획자 관점에서 재구성
```

### 4.4 feature-spec.md 프롬프트

**핵심 설계 결정: 요구사항+AC 일체형 구조 (Kiro 방식 채택)**

기존 설계에서는 Functional Requirements와 Acceptance Criteria가 별도 섹션이었으나,
Kiro AI의 접근법을 채택하여 **각 요구사항 블록 안에 AC를 내장**한다.

이점:
- 각 요구사항이 **자기 완결적** — 요구사항만 보고 바로 테스트 코드 작성 가능
- AC ↔ 요구사항 **매핑이 명확** — 별도 섹션이면 어떤 AC가 어떤 FR인지 불분명
- **누락 검증 용이** — AC 없는 FR이 있으면 바로 식별 가능

```
You are a requirements engineer using EARS notation and structured acceptance criteria.

## Input
- Task: {goal}
- Evidence: {_format_evidence_context(evidence)}
- Brief: {json.dumps(project_brief)}
- Role Plan: {json.dumps(role_plan)}
- Feature Plan: {prev_plan_content}

## Output Format
Return a complete markdown document:

# Feature Spec

## Metadata
(work_item, source_plan, status, last_updated)

## Feature Overview
(Feature Plan의 Project Overview를 기능 명세 관점으로 재해석)

## User Scenarios
(구체적 사용자 시나리오. "사용자가 X하면 시스템이 Y한다" 형식)

## Functional Requirements

### FR-1: (요구사항 제목)
- [모듈명] WHEN [조건] THE SYSTEM SHALL [동작]
- Acceptance Criteria:
  - AC-1.1: (검증 가능한 기준)
  - AC-1.2: (검증 가능한 기준)

### FR-2: (요구사항 제목)
- [모듈명] WHEN [조건] THE SYSTEM SHALL [동작]
- Acceptance Criteria:
  - AC-2.1: (검증 가능한 기준)
  - AC-2.2: (검증 가능한 기준)

(각 요구사항 블록 안에 EARS 표기 + Acceptance Criteria를 일체형으로 포함.
FR-N 번호는 순차 부여. AC-N.M 형식으로 FR과 매핑.)

## Non-Functional Requirements

### NFR-1: (제목)
- (측정 가능한 기준)
- Acceptance Criteria:
  - AC-NFR-1.1: (검증 방법 포함)

(NFR도 동일하게 개별 AC 포함)

## Inputs and Outputs
(data_model 기반. 엔티티, 필드, 저장소 명시)

## Exceptions and Failure Scenarios

### EX-1: (실패 시나리오 제목)
- WHEN [실패 조건] THE SYSTEM SHALL [fallback 동작]
- Acceptance Criteria:
  - AC-EX-1.1: (검증 가능한 기준)

(예외 시나리오도 EARS + AC 일체형)

## Existing Behavior To Preserve
(기존 시스템 영향 분석. 신규 프로젝트면 "해당 없음 (신규 프로젝트)")

## Evidence
(Research findings 요약)

## References
(출처)

## Out Of Scope
(non_goals 기반. 경계 명확화)

## Acceptance Criteria Summary
(★ 하위 호환 필수 — work_item_parser.py가 이 섹션을 파싱)
(모든 FR/NFR/EX의 AC를 여기에도 모아서 나열:
- AC-1.1: ...
- AC-1.2: ...
- AC-2.1: ...
- AC-NFR-1.1: ...
- AC-EX-1.1: ...)

Rules:
- 한국어로 작성 (EARS 키워드 WHEN/THE SYSTEM SHALL은 영문 유지)
- "(edit required)" 사용 금지
- 모든 Functional Requirement는 EARS 형식 필수
- **모든 FR/NFR/EX에 Acceptance Criteria 필수** — AC 없는 요구사항은 불완전
- AC는 검증 가능해야 함 (주관적 표현 금지: "적절한", "빠른" → 구체적 수치/조건)
- Feature Plan의 Scope를 기능 단위로 분해
- Evidence의 기술 세부사항(API 형식, 라이브러리 등)을 요구사항에 반영
- FR, NFR, EX 번호는 각각 독립 순차 부여
- **Acceptance Criteria Summary 섹션 필수** — 모든 AC를 요약 나열 (파서 호환용)
```

> **교차 검증 High 수정**: `work_item_parser.py`의 `parse_feature_spec()`은
> `_extract_section(text, "수용 기준")`으로 별도 `## 수용 기준` 섹션을 찾는다.
> FR+AC 일체형 구조에서 이 섹션이 없으면 acceptance가 빈 리스트로 반환되어
> `sync_board_from_work_items()`의 acceptance 보완이 무효화된다.
>
> **해결**: LLM 프롬프트에 `## Acceptance Criteria Summary` 섹션을 추가로 생성하도록 지시.
> 이 섹션은 FR+AC 일체형의 모든 AC를 모아 나열하여 **파서 하위 호환성**을 보장한다.
> 추후 파서를 FR+AC 일체형 구조로 확장하면 이 섹션은 제거 가능.

### 4.5 implementation-design.md 프롬프트

```
You are a software architect.

## Input
- Task: {goal}
- Evidence: {_format_evidence_context(evidence)}
- Brief: {json.dumps(project_brief)}
- Role Plan: {json.dumps(role_plan)}
- Feature Spec: {prev_spec_content}

## Output Format
Return a complete markdown document:

# Implementation Design

## Metadata
(work_item, spec_type, source_spec, status, last_updated)

## Design Summary
(아키텍처 스타일, 기술 스택, 실행 전략을 2-3 문단으로. Feature Spec의 요구사항을 어떻게 충족하는지 설명)

## Planned Modules
(각 모듈에 대해:
### 모듈명
- owner: 담당 역할
- objective: 기술적 목적 (1-2줄)
- interface: 주요 public API/함수 시그니처
- depends_on: 의존 모듈
- deliverables: 산출물
- feature_slices: 구현 단위)

## Data Flow
(모듈 간 데이터 흐름. 시퀀스 다이어그램 형태:
1. 사용자 → ModuleA: 요청
2. ModuleA → ModuleB: 데이터 전달
3. ModuleB → Storage: 저장)

## Interface Impact
(외부 시스템/API 연동 사항. 없으면 "외부 인터페이스 없음")

## State And Data Model
(엔티티 정의, 필드, 저장소, 관계)

## Compatibility Considerations
(OS, 런타임, 의존성 호환 사항)

## Migration Requirement
(마이그레이션 필요 여부와 계획)

## Risks
(기술적 리스크와 완화 방안)

## Alternatives Considered
(검토했지만 선택하지 않은 대안과 그 이유. 최소 2개)

## Design Evidence
(Evidence에서 설계 결정을 뒷받침하는 근거)

## References
(출처)

## Test Strategy
(단위 테스트, 통합 테스트, E2E 테스트 전략)

Rules:
- 한국어로 작성
- "(edit required)" 사용 금지
- Feature Spec의 EARS 요구사항을 모듈 설계로 매핑
- Alternatives Considered는 반드시 2개 이상 (실제 대안 분석)
- interface 섹션에 함수 시그니처 포함
```

### 4.6 implementation-tasks.md 프롬프트

```
You are a technical project manager creating an implementation task breakdown.

## Input
- Role Plan: {json.dumps(role_plan)}
- Task Board: {json.dumps(task_board)}
- Implementation Design: {prev_design_content}
- Brief: {json.dumps(project_brief)}

## Output Format
Return a complete markdown document:

# Implementation Tasks

## Metadata
(work_item, source_design, status, last_updated)

## Preconditions
(구현 시작 전 충족해야 할 조건)

## Task Evidence
(Evidence 기반 기술 참고 사항)

## Task List
(각 태스크를 다음 형식으로:
- [ ] 태스크 제목
  - task_id: T-001
  - owner_role: 담당_역할
  - phase: scope|build|integrate|verify
  - depends_on: 선행 태스크 ID
  - acceptance: 완료 기준 (검증 가능)
  - artifacts: 산출물
  - estimated_complexity: low|medium|high
  - implementation_hint: 구현 힌트 (Design의 모듈/함수 참조)

태스크 순서 규칙:
1. scope 태스크 먼저 (환경 설정, 프로젝트 초기화)
2. build 태스크 (핵심 로직 → UI → 통합 순서)
3. integrate 태스크 (모듈 연결)
4. verify 태스크 (테스트, 빌드 검증))

## Blockers
(의존성으로 인한 잠재적 블로커)

## Rollback Sign-Off
(각 마일스톤별 커밋 포인트)

## Definition Of Done
(프로젝트 전체 완료 기준)

Rules:
- 한국어로 작성
- "(edit required)" 사용 금지
- 모든 태스크에 depends_on 포함 (없으면 빈 리스트)
- acceptance는 검증 가능한 문장 (WHEN/THEN 또는 구체적 조건)
- Implementation Design의 모듈을 태스크로 분해
- 태스크 ID는 T-001부터 순차 부여
- estimated_complexity 필수 포함
```

---

## 5. 구현 계획

### 5.1 변경 파일 목록

| 파일 | 변경 내용 |
|------|----------|
| `core/work_item_generator.py` | 4개 생성 함수 LLM화, fallback 분리, `_format_evidence_from_brief()`, `_generate_doc_with_llm()` 추가 |
| `core/requirement_llm.py` | `execute_document_prompt()` 신규 함수 추가 (markdown 전용, 기존 함수 미수정) |
| `core/project_pipeline.py` | `prepare()` 3분할: `prepare_brief()` + `prepare_documents()` + 기존 `prepare()` 하위 호환 |
| `core/clarification.py` | **신규** — `generate_clarification_questions()`, `merge_clarification()` |
| `core/interactive_chat.py` | Clarification 질문 표시 + 답변 수집 + `prepare_brief()`/`prepare_documents()` 호출 패턴 |
| `agent_launcher.py` | `_run_project_with_approval()`에서 Clarification 호출 (선택적) |
| `core/work_item_parser.py` | 추후 확장 대상 (현재는 Summary 섹션으로 하위 호환) |

### 5.2 구현 순서

```
Phase 1: LLM 인터페이스 (선행 조건)
  └─ requirement_llm.py: execute_document_prompt() 신규 함수 추가

Phase 2: prepare() 3분할
  ├─ project_pipeline.py: prepare_brief() + prepare_documents() 추출
  └─ 기존 prepare() 하위 호환 유지

Phase 3: Clarification 단계
  ├─ core/clarification.py 신규 생성 (질문 생성 + Brief 병합)
  ├─ interactive_chat.py: 질문 표시 + 답변 수집 UI
  └─ agent_launcher.py: Clarification 호출 (선택적)

Phase 4: 공통 인프라
  ├─ _format_evidence_from_brief() — project_brief에서 evidence 추출
  └─ _generate_doc_with_llm() — LLM 호출 + fallback + 실패 전파 방지

Phase 5: 문서별 LLM 전환 (순서 중요 — 연쇄 정제)
  ├─ Step 1: _generate_feature_plan() → LLM화
  ├─ Step 2: _generate_feature_spec() → LLM화 + AC Summary 섹션 (파서 호환)
  ├─ Step 3: _generate_implementation_design() → LLM화
  └─ Step 4: _generate_implementation_tasks() → LLM화

Phase 6: 검증
  ├─ 기존 테스트 통과 확인 (prepare() 하위 호환)
  ├─ LLM 실패 시 fallback 동작 + 연쇄 중단 확인
  ├─ work_item_parser.py가 AC Summary 섹션 정상 파싱 확인
  ├─ Clarification 스킵/기본값 동작 확인
  └─ 로또 프로그램 예시로 전후 비교
```

### 5.3 Fallback 전략

```python
# 기존 함수를 _fallback_ 접두사로 리네임
_fallback_feature_plan()      # 현재 _generate_feature_plan() 코드 그대로
_fallback_feature_spec()      # 현재 _generate_feature_spec() 코드 그대로
_fallback_impl_design()       # 현재 _generate_implementation_design() 코드 그대로
_fallback_impl_tasks()        # 현재 _generate_implementation_tasks() 코드 그대로

# 새 함수가 LLM 실패 시 fallback 호출
def _generate_feature_plan(...) -> str:
    llm_result = _try_llm_generation(prompt, ...)
    if llm_result:
        return llm_result
    return _fallback_feature_plan(...)  # 기존 f-string 방식
```

### 5.4 LLM 호출 예산

| 단계 | LLM 호출 | 함수 | 용도 |
|------|----------|------|------|
| Brief 생성 | 1회 | `execute_requirement_prompt()` (JSON) | 기존 유지 |
| Clarification 질문 생성 | 1회 | `execute_requirement_prompt()` (JSON) | **신규** — 질문 목록 반환 |
| Role Plan 생성 | 1회 | `execute_requirement_prompt()` (JSON) | 기존 유지 (enriched_brief 입력) |
| feature-plan.md | 1회 | `execute_document_prompt()` (markdown) | **신규** |
| feature-spec.md | 1회 | `execute_document_prompt()` (markdown) | **신규** |
| impl-design.md | 1회 | `execute_document_prompt()` (markdown) | **신규** |
| impl-tasks.md | 1회 | `execute_document_prompt()` (markdown) | **신규** |
| **합계** | **7회** | JSON 3회 + markdown 4회 | 기존 2 + 신규 5 |

> Clarification은 JSON 반환(질문 목록)이므로 기존 `execute_requirement_prompt()` 사용.
> 문서 4종은 markdown 반환이므로 신규 `execute_document_prompt()` 사용.

---

## 6. 영향 범위 (Blast Radius)

| 영향 대상 | 수준 | 설명 |
|----------|------|------|
| `work_item_generator.py` | **High** | 4개 함수 재구조화 (기존 코드는 fallback으로 보존) |
| `requirement_llm.py` | **Medium** | `execute_document_prompt()` 신규 함수 추가 (기존 함수 수정 없음) |
| `project_pipeline.py` | **Medium** | `prepare()` → `prepare_brief()` + `prepare_documents()` 3분할 (기존 `prepare()` 하위 호환 유지) |
| `core/clarification.py` | **신규** | 질문 생성 + Brief 병합 모듈 |
| `interactive_chat.py` | **Medium** | Clarification 질문/답변 UI + `prepare_brief()`/`prepare_documents()` 호출 패턴 |
| `agent_launcher.py` | **Low** | `_run_project_with_approval()`에서 Clarification 호출 (선택적) |
| `work_item_parser.py` | **Medium** | FR+AC 일체형 구조의 파서 호환. 당장은 `## Acceptance Criteria Summary` 섹션으로 하위 호환 유지, 추후 파서 확장 |
| 기존 문서 포맷 | **Low** | FR+AC 일체형 + Summary 섹션 추가. 파서 호환용 |
| LLM 비용 | **Medium** | 프로젝트당 5회 추가 (Clarification 1 + 문서 4) |
| 실행 시간 | **Medium** | LLM 5회 직렬 + 사용자 답변 대기. 총 제한 300초, fallback 2회 시 연쇄 중단 |
| UX 흐름 | **Medium** | 사용자에게 질문 단계 추가 (FSA 모드는 자동 처리) |
| 테스트 | **Low** | 기존 테스트에 영향 없음 (fallback = 현재 동작, `prepare()` 하위 호환) |

---

## 7. 기대 효과

### Before (현재)

```markdown
## Non-Functional Requirements
- (edit required)

## Exceptions and Failure Scenarios
- (edit required)

## Alternatives Considered
- (edit required)
```

### After (개선 후)

```markdown
## Functional Requirements

### FR-1: 당첨 번호 자동 수집
- [데이터 수집기] WHEN 앱 실행 THE SYSTEM SHALL 동행복권에서 최신 회차 당첨 번호를 크롤링한다
- Acceptance Criteria:
  - AC-1.1: 최근 100회차 데이터가 SQLite에 저장된다
  - AC-1.2: 이미 수집된 회차는 스킵한다 (중복 INSERT 없음)
  - AC-1.3: 크롤링 완료 후 "N회차 갱신 완료" 메시지 표시

### FR-2: 번호 생성
- [번호 생성기] WHEN 사용자가 생성 버튼 클릭 THE SYSTEM SHALL 1~45 중 중복 없는 6개 번호를 생성한다
- Acceptance Criteria:
  - AC-2.1: 생성된 번호 6개 모두 1~45 범위 내
  - AC-2.2: 6개 번호 간 중복 없음
  - AC-2.3: 오름차순 정렬 상태로 UI에 표시

## Non-Functional Requirements

### NFR-1: 오프라인 동작
- 인터넷 연결 없이도 캐시된 통계 데이터로 번호 생성 가능
- Acceptance Criteria:
  - AC-NFR-1.1: 네트워크 차단 상태에서 앱 실행 → 캐시 기반 정상 동작

## Exceptions and Failure Scenarios

### EX-1: 크롤링 실패
- WHEN 동행복권 API 응답 실패 THE SYSTEM SHALL 캐시된 최신 데이터로 fallback하고 "오프라인 모드" 알림 표시
- Acceptance Criteria:
  - AC-EX-1.1: API 타임아웃(5초) 후 캐시 fallback 동작 확인
  - AC-EX-1.2: 사용자에게 오프라인 모드 전환 알림 표시

## Alternatives Considered
1. **웹 앱 (Flask/React)**: 크로스 플랫폼 접근성 높지만, 사용자 요구사항이 "단독 실행 파일"이므로 부적합
2. **PyQt 대신 tkinter**: PyQt가 더 풍부한 위젯 제공하지만, 배포 크기와 라이선스(GPL) 고려하여 tkinter 선택
```

### 정량적 기대

| 지표 | Before | After |
|------|--------|-------|
| `(edit required)` 수 | 8~12개 | **0개** |
| EARS 표기법 요구사항 | 0개 | **10~20개** |
| 문서만으로 코딩 착수 가능 여부 | ❌ | ✅ |
| Evidence 반영률 | Brief 요약만 | **원본 직접 반영** |

---

## 8. 체크리스트

- [ ] 설계 승인
- [ ] Phase 1: `execute_document_prompt()` 신규 함수 (`requirement_llm.py`)
- [ ] Phase 2: `prepare()` 3분할 (`project_pipeline.py` — 하위 호환 유지)
- [ ] Phase 3-1: `core/clarification.py` 생성 (질문 생성 + Brief 병합)
- [ ] Phase 3-2: `interactive_chat.py` 질문/답변 UI
- [ ] Phase 3-3: `agent_launcher.py` Clarification 호출
- [ ] Phase 4: 공통 인프라 (`_format_evidence_from_brief`, `_generate_doc_with_llm` + 연쇄 실패 방지)
- [ ] Phase 5-1: `_generate_feature_plan()` LLM화 + fallback 분리
- [ ] Phase 5-2: `_generate_feature_spec()` LLM화 + FR+AC 일체형 + AC Summary 섹션
- [ ] Phase 5-3: `_generate_implementation_design()` LLM화 + fallback 분리
- [ ] Phase 5-4: `_generate_implementation_tasks()` LLM화 + fallback 분리
- [ ] Phase 6: 검증 (기존 테스트 + prepare() 하위 호환 + 파서 AC 호환 + fallback 연쇄 + Clarification)
- [ ] Master_Blueprint.md 업데이트
- [ ] code-review.md 업데이트
