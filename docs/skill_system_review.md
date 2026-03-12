# 스킬 시스템 심층 분석 및 보완 계획
(Agent Factory Skill System vs Claude Code Skills 2.0)

## 1. 구조 파악 및 심층 분석 (Structure Analysis)

### 1) Agent Factory 스킬 시스템 (기존 Skill Creator 모델)
* **핵심 철학**: **데이터 기반의 객관적 품질 검증(Automated QC)**과 '기획 우위(Planning-First)' 원칙.
* **아키텍처 구조**:
  * **목적별 스킬 이분화**: 능력 향상 스킬(임시 보완점)과 규칙 강제 스킬(코어 룰셋)로 엄격히 구분.
  * **5단계 QC 파이프라인**: `(1) TDD 포지 가동` ➡ `(2) 블라인드 아레나(A/B 테스트)` ➡ `(3) 하이브리드 지능 이원화 평가` ➡ `(4) 매니저 라우터 튜닝` ➡ `(5) 자가 진화/자연 은퇴 프로토콜`.
* **한계점**: 에러 복구 및 검증 파이프라인(`FSALoop`)이 코어 레벨에 하드코딩된 정적(Static) 클래스에 의존하고 있어, 최신 동적 검증 루프와 오케스트레이션이 통합되지 못하고 고립(Isolation)되어 있습니다.

### 2) Claude Code Skills 2.0 (LangChain 연동 기반 모델)
* **핵심 철학**: **"Agent-Improving-Agent"**, 즉 에이전트 간의 상호 작용과 터미널 로깅을 통한 **완전 자율 자가 발전 루프(Self-Improvement Loop)**.
* **아키텍처 구조**:
  * **Dynamic Skill Loading (12-Cap)**: 라우팅 차원의 차원의 저주(Curse of Dimensionality)를 방지하기 위해 각 턴마다 상황별로 가장 적합한 최대 12개의 스킬만 LLM 컨텍스트에 동적 주입.
  * **명시적 가이드라인 강제 맵핑**: 스킬을 던져주기만 하는 것이 아니라, 프롬프트(`AGENTS.md`) 내부에 명확한 **Decision Tree (언제 어떤 스킬을 사용할 것인가)** 규격화.
  * **독립된 Evaluator 에이전트 운용**: 실무 코딩 에이전트와 철저히 분리되어, 오직 `trace_execution`, `summarize_failure`, `generate_eval_dataset` (LangSmith 스킬)만을 장착한 에이전트를 터미널에 상주시켜 런타임 대응 체계를 갖춤.

---

## 2. 두 시스템 간의 차이점 (Differences)

> [!NOTE]
> Agent Factory는 스킬이 **'배포되기 전(사전)' 단계의 품질 검증(TDD, 블라인드 테스트)과 은퇴 관리**에 극도로 최적화되어 있다면, Claude Code Skills 2.0은 런타임 **'터미널 실행(사후)' 동안 연속적으로 발생하는 에러 트레이싱과 실시간 자가 발전**에 최적화되어 있습니다.

1. **평가(Evaluation) 주체의 동적/정적 차이**:
   * **Agent Factory**: `core/fsa_loop.py` 내부의 정적 클래스(`StrategyEvaluator`)가 하드코딩된 로직에 따라 에러 재시도 로직을 제어합니다.
   * **Claude 2.0**: 완전히 독립된 AI 에이전트(`Evaluator Agent`)가 LangSmith 도구를 들고 직접 로그를 스캔하고 결론을 생성합니다.
2. **컨텍스트 최적화 (라우팅 관리)**:
   * **Agent Factory**: 매니저가 스킬 설명을 읽고 임의 판단. 스킬 갯수의 명시적 제한이나 강제 필터링 체계가 미비. (오작동 확률 큼)
   * **Claude 2.0**: **@skill_metadata**를 활용해 주입되는 스킬 수를 **Max 12개**로 캡(Cap)을 씌워 오작동(False Positive/Negative)을 수학적으로 원천 차단.
3. **피드백 루프의 형태**:
   * **Agent Factory**: 성공률 벤치마크를 바탕으로 승률을 측정하고 스킬을 수정합니다.
   * **Claude 2.0**: 실패 원인을 짧은 `.json` 로 요약하고, 그 원인에 대한 **pytest 검증 코드(방어 코드)를 자율적으로 생성**하여 다시는 같은 논리적 오류를 반복하지 않게 만듭니다.

---

## 3. Agent Factory 스킬 시스템 보완점 (Areas for Improvement)

Claude Code Skills 2.0의 사상을 흡수하여 Agent Factory의 코어 엔진 파이프라인을 완전체로 만들려면 다음 네 가지 핵심 타겟팅 보완이 필요합니다.

1. **에러 복구 오케스트레이션 재조립 (정적 -> 동적)**: 하드코딩된 에러 복구망(`FSALoop`)을 들어내고, Evaluator 에이전트를 실시간으로 호출하는 동적 배관 연결 체제로 리팩토링 및 오케스트레이션 충돌 해소.
2. **Dynamic Skill Loader(동적 스킬 로더) 구축**: 코어 레벨에서 컨텍스트 토큰을 절약하고 환각을 줄이기 위해 스킬을 카테고리별로 캡슐화하고 12-Cap 정책 강제 도입.
3. **메타데이터 기반 프롬프트/가이드라인 연결성 롹보**: 에이전트에 스킬을 주입할 때 의사결정 트리(Decision Tree)를 자동으로 병합하는 시스템 구축.
4. **HitL(Human-in-the-Loop) 제어 체계**: 무한 반복되는 자가 발전 루프가 폭주(Token Burn)하지 않도록, 영속성(Persistence) 체크포인트를 두고 인간의 승인이 필요한 로직 보강.

---

# Agent Factory 스킬 시스템 심층 코드 분석 및 통합 마스터 플랜
(Integration Plan for Claude Code Skills 2.0 & Agent Factory)

## 4. 코드베이스 기반 심층 분석 결과 (Code-Level Findings)

### 4.1 `core/fsa_loop.py` (정적 매커니즘의 한계)
- **현재 상태**: `FSALoop.run_mission()`은 실패(fail)를 감지하면, 1차적으로 `self.agent_mgr.get_or_create("evaluator")`를 통해 동적 에이전트 호출을 시도하나, 실패 시 낡은 하드코딩된 `StrategyEvaluator`로 Fallback 합니다.
- **분석 결과**: `agent_mgr`를 통한 Evaluator 호츌 로직이 도입은 되었으나, LangSmith 스킬(`trace_execution` 등)이 제대로 파이프라인(Event Bus)과 연동되지 않아, 반쪽짜리 동적 호출에 머물러 있습니다. 이로 인해 에러 복구 시나리오가 여전히 하드코딩된 프롬프트 파싱(`parse_evaluator_response`)에 의존하고 있습니다.

### 4.2 `core/manager.py` (스킬 로딩의 한계)
- **현재 상태**: `AgentManager.get_or_create()`와 `install_skills()`가 단순히 YAML 파일의 `skills` 배열을 읽고 병합(Merge)하는 역할만 수행합니다.
- **분석 결과**: 스킬이 몇 개이든 상관없이 등록된 모든 스킬을 에이전트 컨텍스트에 밀어 넣습니다. Claude 2.0의 핵심인 **Dynamic Skill Loader (12-Cap)** 및 상황 인지 필터링 로직이 전무하여, 12개가 넘어가는 스킬이 장착될 시 "라우팅 차원의 저주(Curse of Dimensionality)" 즉, 오작동(False Positive)이 발생할 코드 구조입니다.

### 4.3 `agent_launcher.py` 및 라우팅 체계
- **현재 상태**: 단일 실행(`run`)과 정적/동적 워크플로우를 관장하지만, 스킬의 메타데이터(카테고리, max_tokens, when_to_use)를 바탕으로 에이전트의 프롬프트를 동적으로 조작하는 레이어가 부족합니다.
- **분석 결과**: 터미널 기반의 Self-Improvement Loop를 돌기 위해서는, `run()` 메서 실행 전후로 이벤트 훅(`LangSmithTracingHook`)을 삽입하여 출력을 캡처하고 Evaluator로 넘기는 배관(Pipe)이 `agent_launcher.py` 단에 추가되어야 합니다.

### 4.4 `agents/evaluator.yaml` (단일 스킬 매니페스트 파편화)
- **현재 상태**: 이 에이전트에는 `trace_execution`, `summarize_failure`, `generate_eval_dataset` 스킬이 YAML 배열로만 선언되어 있고, "When to use"에 대한 Decision Tree가 `system_prompt` 내부에 일부 텍스트로 박혀 있습니다.
- **분석 결과**: 스킬들이 파이썬 단위(`@skill_metadata`)로 구조화되어 있지 않고 텍스트로 하드코딩되어 있어, 팩토리 코어 엔진이 이들을 논리적으로 필터링하거나 제한할 수 없습니다.

---

## 5. 두 시스템 간의 차이점 요약 (Differences)

1. **오케스트레이션 결합도**: Agent Factory(As-Is)는 에러가 터지면 `fsa_loop.py`의 Python Exception Handling 로직이 개입하여 정적 프롬프트를 날립니다. 반면 Claude Code Skills 2.0(To-Be) 모델에서는 에러 발생 자체가 '트레이스(Trace) 스킬'의 인풋 스트림으로 들어가 순수하게 에이전트(Evaluator)가 터미널 런타임에서 자율 복구 루프를 돕니다.
2. **컨텍스트 로딩 방식**: Factory는 YAML에 선언된 스킬을 전부 주입(All-in)하는 반면, Claude 2.0은 턴마다 현재 Task의 벡터/키워드를 분석해 12개 이하로 동적 주입(Context-Aware 12-Cap)합니다.

---

## 6. 아키텍처 통합 6단계 구현 가이드 (`implementation_plan.md`)

다음은 코드 단위의 구체적이고 명시적인 적용 6단계입니다.

### [Phase 1: 메타데이터 기반 스킬 데코레이터 전면 도입]
1. `core/skills/metadata.py` 생성: `@skill_metadata(description, category, max_tokens, logic_type)` 데코레이터 클래스 구현.
2. 기존 `skills/` 하위의 모든 `.py` 스킬 파일(예: 파일 조작, 웹 검색 등)에 이 데코레이터를 부착하여, 각 스킬의 `when_to_use` 조건을 구조화된 딕셔너리로 반환하게 리팩토링.

### [Phase 2: 12-Cap Dynamic Skill Loader 구현]
1. `core/manager.py` (또는 신규 `core/skill_loader.py`)에 **`DynamicSkillLoader`** 클래스 작성.
2. 해당 클래스는 태스크 인풋(`task_input`)을 입력받아, 등록된 스킬들의 데코레이터 메타데이터 풀과 매칭 스코어를 계산.
3. 스코어가 가장 높은 **상위 12개 스킬만 필터링**하여 에이전트의 구동 컨텍스트(Tool array)에 주입하는 로직 완성.

### [Phase 3: LangSmith Tracing Hook 이식 (Event Bus 연동)]
1. `core/hooks/langsmith_tracing.py` 작성.
2. `HookEventBus` (현재 `agent_launcher.py`에서 import 중)에 등록하여, `AgentRunner.run()` 실행 중 발생하는 모든 커맨드 라인 출력 및 에러(`stdout/stderr`)를 낚아챔.
3. 낚아챈 데이터를 `.system_generated/logs/trace_<run_id>.log` 형식으로 실시간 덤프.

### [Phase 4: Evaluator 에이전트 및 검증 시스템 스킬 이식]
1. `agents/evaluator.yaml` 업데이트: `system_prompt`에 명시적 Decision Tree (트레이스 수집 $\rightarrow$ 에러 요약 $\rightarrow$ 테스트셋 구축) 주입.
2. `skills/eval/` 디렉토리에 **`trace_execution.py`, `summarize_failure.py`, `generate_eval_dataset.py`** 3대장 스킬 코드 구현 (LangSmith CLI 명령어 Wrap).

### [Phase 5: FSALoop 아키텍처 해체 및 "자가 최적화 루프" 융합]
1. `core/fsa_loop.py` 개조: `_run_evaluator` 메서드에서 `StrategyEvaluator` 부분 삭제(Deprecate).
2. 에러가 발생하면, 앞선 Phase 3에서 생성된 트레이스 로그의 Path만을 인자로 삼아 **`Evaluator` 에이전트를 독립 호출**.
3. Evaluator가 생성한 `feedback.json`과 신규 방어용 `pytest` 코드를 리턴받아, 원래 목적이었던 `Developer Agent`의 다음 재시도(Cycle) 프롬프트 최상단에 강제 병합(`[EVALUATOR ADVICE]` 섹션).

### [Phase 6: Human-in-the-Loop (HitL) 영속성(Persistence) 훅 설정]
1. `fsa_loop.py` 재시도 횟수 한계(Limit)를 설정하고, `max_cycles` (예: 3회) 내에 복구 실패 시 무한 루프 폭주를 막기 위해 `Suspend` 트리거 발동.
2. 터미널 프롬프트 상에서 "Evaluator가 3회 시도했으나 복구 불가. 현재까지의 `feedback.json` 분석 결과 요약본 출력" 후, 인간 엔지니어의 개입(Rollback, Override, Approve)을 기다리는 HitL 인터럽트 함수 작성.

---

## 7. 변경 대상 및 검증 계획 (Verification Plan)

### 변경 예정 파일
- `core/manager.py` 혹은 신규 `core/skill_loader.py` [NEW / MODIFY]
- `core/fsa_loop.py` [MODIFY]
- `core/hooks/langsmith_tracing.py` [NEW]
- `skills/eval/*.py` (트레이스 및 요약 툴 3종) [NEW]
- `agents/evaluator.yaml` [MODIFY]

### 자동화 및 수동 검증 계획 (Verification)
1. **Dynamic Loader 검증**: 수십 개의 모의 스킬이 존재하는 샌드박스에서 요청 프롬프트를 날렸을 때, 반환되는 스킬의 개수가 정확히 12개를 넘지 않는지(12-Cap) 단위 테스트(`pytest test_skill_loader.py`).
2. **FSALoop Self-Improvement 검증**:
   - 의도적으로 에러를 뱉는 잘못된 파이썬 스크립트 작성 태스크를 부여함.
   - `FSALoop`이 즉각 에러를 감지하는지 관찰.
   - 시스템 훅이 `.log` 파일을 정상 덤프하는지 확인.
   - `Evaluator` 에이전트가 깨어나서 해당 로그를 읽고 `feedback.json`을 남긴 뒤 복구된 코드를 제시하는지(Terminal 자동화 복구 루프) **수동 End-to-End** 관찰.
