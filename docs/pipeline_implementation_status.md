# 🔍 Agent Factory 파이프라인 구현 상태 보고서
**(Tavily ➡️ NotebookLM ➡️ Lilith Forge)**

이전 버전에서 기획되었던 "외부 지식 탐색 -> 팩트 소화 -> 동적 에이전트 생성" 파이프라인은 현재 `agent-factory` 코드베이스 내에 성공적으로 이식되어 가동 대기 중입니다. 아래는 각 파이프라인 단계별 실제 코드 구현 위치와 작동 방식입니다.

---

## 1. Tavily (외부 실시간 브루트포스 리서치)
**상태: 구현 완료 🟢**
보스님의 낯선 도메인/신기술 요구에 대비해, 실시간 웹 검색 엔진이 통합되어 있습니다.

- **핵심 모듈:** [core/web_search.py](file:///d:/hoonProJect/worktrees/agent-factory/core/web_search.py)
  - `tavily_search()` 함수를 통해 API 통신 및 데이터 스크랩핑 수행.
- **실행 스킬:** [skills/hound_librarian/skill.py](file:///d:/hoonProJect/worktrees/agent-factory/skills/hound_librarian/skill.py) (사냥개 명칭)
  - `Step 1 (🐕 사냥개)`: Tavily를 가동하여 최신 문서와 URL을 수집하는 로직이 명시적으로 분리되어 있습니다.
  - 검색된 결과는 정제되지 않은 방대한 초기 팩트로 활용됩니다.

## 2. NotebookLM (도메인 흡수 및 지식 필터링)
**상태: 구현 완료 🟢**
Tavily가 가져온 방대한 리서치 소스들을 우리 사내 규정([GEMINI.md](file:///d:/hoonProJect/worktrees/agent-factory/GEMINI.md) 등)에 맞게 씹어서 소화시키는 RAG 파이프라인이 연결되어 있습니다.

- **핵심 모듈:** [core/researcher.py](file:///d:/hoonProJect/worktrees/agent-factory/core/researcher.py) 및 [test_hound_librarian.py](file:///d:/hoonProJect/worktrees/agent-factory/test_hound_librarian.py)
  - `query_notebooklm()` 함수가 내장되어 있어, NotebookLM(리버스 엔지니어링 API 연동)에 직접 쿼리를 던지고 답변을 받아옵니다.
- **실행 스킬:** [skills/hound_librarian/skill.py](file:///d:/hoonProJect/worktrees/agent-factory/skills/hound_librarian/skill.py)
  - `Step 2 (📥 소스 주입)`: 수집된 URL/소스들을 NotebookLM에 자동 업로드합니다.
  - `Step 3 (📚 사서)`: `query_notebooklm`을 호출하여, 우리 프로젝트 세계관(보스님의 요구사항)에 맞춰 내용들을 심층 분석(Deep Analysis) 및 필터링하여 **통합 인사이트**를 반환합니다.

## 3. Lilith (Senior PM의 지휘 및 요원 스폰 - Forge)
**상태: 구현 완료 🟢**
단순 챗봇이 아닌, 리서치 결과를 바탕으로 필요한 에이전트를 스스로 '찍어내는(Forge)' 기능이 오케스트레이터의 심장부에 있습니다.

- **핵심 모듈:** [project_orchestrator.py](file:///d:/hoonProJect/worktrees/agent-factory/project_orchestrator.py)
  - `You are Lilith, a PM orchestrator for a multi-agent factory.` 라는 명확한 페르소나와 함께 전체 흐름을 주관합니다.
  - **Forge 로직:** [forge_roles()](file:///d:/hoonProJect/worktrees/agent-factory/project_orchestrator.py#84-167) 함수가 구현되어 있어, 새로운 능력이 필요할 경우 `skills/forge/` 디렉토리에 **동적으로 새로운 커스텀 에이전트/스킬(py 파일)을 생성**합니다.
- **연결 구조:** 
  - NotebookLM이 정리해 준 심층 인사이트를 Lilith가 전달받아, 현재 보유한 기본 에이전트들(`agents/` 폴더)로 부족할 경우 즉석에서 용병을 스폰하여 임무를 완수하게 합니다.

## 4. Planning-First Gate (기획/설계 검증 및 승인)
**상태: 구현 완료 🟢**
에이전트들이 제멋대로 코드를 짜지 못하도록, 실제 파일 작성 전 반드시 기획 파일(`plan.md` 등)이 존재하는지 검증하는 방어막 로직입니다.

- **핵심 모듈:** `core/builder.py` 및 `test_complex_skill_build.py`
  - `SandboxedBuilder` 내에 **Planning-First Gate** 로직이 명시적으로 구현되어 있습니다.
  - 에이전트(LLM)에게 코드를 짜라고 시키기 전, 충분한 기획 데이터(`evidence_pack`)가 제안되지 않으면 즉시 예외(`reason='planning_first_violated'`)를 발생시키고 코드 생성을 원천 차단합니다.
- **오케스트레이터 연동:** `project_orchestrator.py` 및 관련 테스트(`test_project_pipeline.py`)
  - 릴리트가 이끄는 파이프라인에서 작업을 시작할 때 가장 먼저 `planning/project_brief.json` 및 `planning/role_plan.json` 같은 기획(Plan) 산출물을 먼저 기록하도록 강제되어 있습니다.
  - 이를 통해 보스님의 요구사항이 '무작정 코딩'으로 이어지지 않고, '명확한 기획 단계'를 거친 후 승인을 거쳐 코드로 변환됩니다.

## 5. 작업 강제 이정표: `.todo.md` (Todo Continuation Enforcer)
**상태: 구현 완료 🟢**
보스님의 요청이 복잡하거나 길어질 경우, 에이전트가 중간에 길을 잃지 않고 남은 작업을 추적하도록 강제하는 '행동 나침반' 역할입니다.

- **생성 시점 및 트리거:** `project_orchestrator.py` 및 `core/intent.py`
  - 릴리트가 이끄는 파이프라인에서 작업을 시작할 때, `IntentGate`라는 전용 분류기(Classifier)가 먼저 개입합니다.
  - **판단 로직:** LLM(`LLMEngine`)에게 5가지 카테고리(`trivial`, `question`, `refactoring`, `greenfield`, `debugging`) 중 하나로 사용자 입력(Project Description)을 분류하라고 시킵니다.
    - 이때 LLM의 응답 결과가 **"새로운 기능 추가(`greenfield`)"**이거나 **"구조적 변경(`refactoring`)"**일 경우, 이를 **"복잡한 의도(Complex Intent)"**로 확정 짓습니다.
    - 만약 LLM API 호출이 실패하더라도 "create", "new", "refactor" 같은 단순 키워드 폴백(Fallback) 방식을 통해 이중 방어망으로 분류를 이뤄냅니다.
  - 이렇게 '복잡한 의도'로 판명되면, 초기 기획(Plan) 직후 프로젝트 작업 공간(Workspace) 최상단에 `.todo.md` 파일로 자동 생성됩니다.
- **핵심 역할:** `TodoContinuationEnforcer` 
  - 코딩 에이전트들이 작업을 시작하기 전, `.todo.md` 파일 내에 아직 체크되지 않은 `[ ]` (해야 할 업무) 항목이 남아있는지 검사합니다.
  - 에이전트들은 이 파일에 적힌 순서대로만 작업을 수행하게 되며, 만약 파일이 없거나 내용이 부실하면 "작업이 Todo Enforcer에 의해 차단되었습니다. 기획부터 먼저 하세요." 라며 실행을 거부합니다.
  - 작업이 끝날 때마다 `[x]`로 상태를 업데이트하여 다음 에이전트에게 **명확한 인수인계(Context Handoff)**를 보장합니다.

---

### 💡 결론
과거의 야심찼던 **"Tavily(수집) -> NotebookLM(정제) -> Lilith(창조/분배)"** 아키텍처는 유실되지 않고 현재 코드베이스에 깊숙이 내장되어 있습니다. 이 엔진(사냥개 스킬 + 릴리트 오케스트레이션)을 사용하여 새로운 지뢰찾기 프로젝트나 미지의 웹앱 프로젝트를 돌려볼 준비가 완벽히 되어 있습니다.
