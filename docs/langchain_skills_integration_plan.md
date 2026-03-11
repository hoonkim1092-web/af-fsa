# LangChain & LangSmith 스킬 아키텍처 분석 및 Agent Factory 적용 방안

최근 LangChain 커뮤니티에서 공개된 "에이전트 스킬(Skills) 아키텍처"에 따르면, 단순 프롬프트 지시보다 **명확하게 설계된 스킬(도구) 세트**를 장착했을 때 LLM 에이전트의 타겟 태스크 성공률이 25% → 95%로 극적으로 상승함이 확인되었습니다.
특히 LangSmith CLI와 연동된 트레이싱(Tracing), 평가(Eval), 데이터셋 구축 스킬은 "에이전트가 스스로 실행 로그를 분석하고, 요약하며, 테스트셋을 만들어 자신을 개선하는" 완전 자율 자가 발전 루프를 가능하게 합니다.

본 문서는 이 구조를 파악하고, Agent Factory의 기존 `Skill Creator` 파이프라인(Phase 1~5)에 어떻게 구체적/단계적으로 이식할 것인지 명시합니다.

---

## 1. 평가 및 교훈 (핵심 인사이트 분석)

1. **적정 스킬 탑재의 법칙 (Curse of Dimensionality in Routing):**
    * 에이전트에게 20개 이상의 스킬을 주면 오작동(False Positive: 엉뚱한 스킬 호출, False Negative: 필요할 때 미호출) 비율이 급증합니다.
    * **적정 수준은 12개 내외**이며, Agent Factory의 라우터(Factory Manager) 역시 컨텍스트 당 최대 12개의 스킬만 동적으로 주입(Context-Aware Skill Loading)되도록 구조를 변경해야 합니다.
2. **명시적 가이드라인 (AGENTS.md / CLAUDE.md 최적화):**
    * 단순히 도구를 던져주는 것이 아니라, 각 에이전트의 지배적 프롬프트(`AGENTS.md` 등) 내부에 **"언제 어느 스킬을 써야 하는지(When to use Which skill)"**에 대한 명확한 룰과 페르소나 지시가 명시되어야 스킬의 효과가 극대화됩니다.
3. **에이전트 주도형 피드백 루프 (Terminal-based Self-Improvement):**
    * 에이전트가 터미널 환경에서 실행 결과를 트레이스하고(LangSmith), 에러를 요약하며, 평가 데이터셋을 자동으로 생성/수정하는 능력을 갖게 됩니다.
    * Agent Factory에서는 기존 `Skill Creator` 파이프라인 중 **Phase 4 & 5**에 이 엔진을 직접 이식하여 구현합니다.

---

## 2. Agent Factory 적용 설계 (Architecture Mapping)

기존 Agent Factory는 `Skill Creator`를 통해 품질 검증을 계획 중이었습니다. 여기에 LangChain/LangSmith의 교훈을 더해 **"Terminal-based Agent Auto-Tuning Loop"** 체계를 확립합니다.

### A. 동적 스킬 로딩 시스템 (Dynamic Skill Loader)

* 모든 스킬 파일(파이썬 스크립트 등)에 `@skill_metadata(max_tokens=.., category=..)` 데코레이터를 붙여 분류.
* 에이전트가 특정 Task(예: 빌드/테스트/DB설계)에 진입할 때 **관련된 상위 10개의 스킬만 선별하여 LLM 컨텍스트에 주입**합니다. (지나친 스킬 오작동 방지)

### B. "AGENTS.md" 가이드라인 강제 맵핑

* `AGENTS.md`에 단지 에이전트의 역할만 적는 것이 아니라, 각 에이전트 챕터별로 **[Available Core Skills Array]**와 **[Decision Tree for Skill Usage]** 규칙을 명문화합니다.

### C. 에이전트 자가 최적화 도구 (Agent-Improving-Agent Skills) 구축

* LangSmith CLI의 핵심 기능을 Agent Factory 내장 스킬로 모방/구현합니다.
  * `trace_execution` 스킬: 직전 에이전트의 터미널/실행 로그(Crash, Exception 등 포함) 수집.
  * `summarize_failure` 스킬: 트레이스 데이터를 바탕으로 "이 스킬/코드에서 왜 실패했는지" 원인을 요약 추출(`feedback.json` 파일 생성).
  * `generate_eval_dataset` 스킬: 요약된 실패 원인을 바탕으로, 절대 실패해서는 안 되는 테스트 케이스(가상 쿼리)를 `pytest`용 코드로 자동 생성.

---

## 3. 단계별 상세 이식 방안 (Explicit Implementation Steps)

다음은 코어 엔진 및 폴더 구조에 이를 실제로 구현하고 편입시키는 상세 가이드입니다.

### Step 1: 디렉토리 및 메타데이터 정비

1. 기존 `skills/` 디렉토리 하위에 카테고리를 분리합니다.
    * `skills/core/`: 파일 읽기/쓰기, 검색 등 (상시 로드)
    * `skills/eval/`: 트레이싱, 테스트 생성, 평가 전용 터미널 스킬 (검증 태스크 시 로드)
    * `skills/domain/`: 게임 개발, 웹 프레임워크 전용 등 (해당 도메 작업 시 로드)
2. 각 도구의 docstring 첫 줄에 **"언제 사용할 것인가(When to use)"**를 강제 기입하는 린트(Lint) 규칙을 만듭니다.

### Step 2: 평가 전용 에이전트 (QA/Eval Agent) 신설

* 직접 코딩을 수행하는 `Developer Agent`와 철저히 분리된 **`Evaluator Agent`**를 `agent_launcher.py`에 추가 정의합니다.
* 이 에이전트는 **`skills/eval/`** 툴셋만 100% 장착합니다.

### Step 3: 자가 피드백 루프 트랜잭션 구현 (The Loop)

1. **실행 (Execute):** `Developer Agent`가 코드를 짜고 터미널에서 스크립트를 실행합니다. (예: `test_fallback.py` 실행)
2. **트레이스 (Trace):** 성공/실패와 무관하게 모든 stdout/stderr가 `.system_generated/logs/trace_{id}.log`로 저장됩니다.
3. **검증 (Eval):** 즉시 `Evaluator Agent`가 깨어나 `trace_execution` 스킬로 해당 로그를 스캔합니다.
4. **피드백 (Summarize target):** 에러가 감지되면, `summarize_failure` 스킬을 통해 "무엇이 문제인지" 짧은 리전(reasoning)을 뽑아내어 `.system_generated/feedback_loop/` 경로에 JSON으로 덤프합니다.
5. **훈련 데이터 확충 (Datasets):** 패치된 코드가 다시 짜여지기 전, `generate_eval_dataset` 스킬이 이 피드백을 활용하여 새로운 방어적 Assert Test Case를 기존 `tests/` 폴더 내 TDD 코드에 쑤셔넣습니다. (이로써 다시는 같은 논리적 오류를 반복하지 않습니다.)
6. **재귀 (Reflect):** `Developer Agent`는 이렇게 업데이트된 실패 피드백과 테스트 케이스를 읽고 다시 코드를 짠 후 Step 1으로 돌아갑니다.

### Step 4: AGENTS.md 룰북 업데이트

* `AGENTS.md` 문서를 열어, 각 에이전트의 행동 지침에 아래와 같은 명시적 규칙을 추가합니다.

> **Skill Routing Rule:** 당신이 지닌 스킬 중 한 턴에 1~2개만 사용하라. 스킬 목록이 12개가 넘어가면, 작업과 무관한 스킬의 사용을 우선적으로 배제하라. 명시된 "When to use" 조건에 100% 부합할 때만 트리거하라.

이러한 구조적 접근은 그저 "AI야 더 잘 코딩해봐"라는 모호한 요구나 단일 거대 프롬프트 구조를, **LangChain이 증명한 "도구 중심, 데이터/평가 기반 루프"** 체계(Skill based AI-loop)로 진화시키는 결정적 마일스톤이 될 것입니다.
