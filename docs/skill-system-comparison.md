# 스킬 시스템 비교: Claude Code vs OpenAI Codex vs agent-factory

날짜: 2026-03-18  
범위: 스킬 시스템 설계만 비교  
비교 대상: 이 문서에서 `OpenAI`는 회사 전체가 아니라 `Codex`를 의미함

## 이 문서의 목적

이 문서는 세 시스템의 스킬 시스템을 같은 기준으로 비교한다.

1. 스킬이 어떤 단위로 패키징되는가
2. 스킬이 언제, 어떻게 호출되는가
3. 스킬이 무엇을 통제할 수 있는가
4. 스킬이 에이전트와 서브에이전트에 어떻게 연결되는가
5. 스킬이 어떻게 공유되고 운영되는가
6. `agent-factory`가 무엇을 가져와야 하는가

이 문서의 목적은 셋 중 하나를 단순히 1등으로 고르는 것이 아니다. 세 시스템은 서로 다른 최적화 목표를 가진다.

- `Claude Code`: 깔끔한 스킬 명세와 로컬 에이전트 워크플로
- `OpenAI Codex`: 제품화된 팀 워크플로와 다중 표면 재사용
- `agent-factory`: 프로그래머블한 멀티에이전트 오케스트레이션과 벤더 비종속 주입

## 1단계. 각 시스템에서 "스킬"이 의미하는 것

### Claude Code

`Claude Code`는 스킬을 `SKILL.md` 중심의 가벼운 표준 역량 단위로 다룬다.  
공식 설계는 문서 우선이다. frontmatter와 markdown 본문이 중심이고, 필요하면 스크립트나 보조 자산이 붙는다.

해석:

- 스킬은 기본적으로 선언형 행동 패키지다
- diff, 리뷰, 버전 관리가 쉽다
- 무거운 실행 번들보다 에이전트 행동 조정에 최적화돼 있다

### OpenAI Codex

`Codex`는 스킬을 재사용 가능한 작업 패키지로 다룬다.  
공식 문서상 스킬은 instructions, resources, scripts를 묶어 app, CLI, IDE 전반에서 재사용하는 단위다.

해석:

- 스킬은 운영 가능한 워크플로 번들에 가깝다
- 단일 markdown 파일보다 더 무겁다
- 단순 프롬프트 재사용보다 반복 가능한 팀 작업에 최적화돼 있다

### agent-factory

`agent-factory`는 Claude나 Codex처럼 스킬을 제품 UI의 1급 개념으로 노출하지 않는다.  
이 저장소에서 스킬은 runtime composition 계층의 일부다. loader, registry, role plan, provider runner가 각 에이전트에 어떤 역량 세트를 줄지 결정한다.

코드 근거:

- [`core/agent_runner.py`](../core/agent_runner.py)
- [`core/skill_loader.py`](../core/skill_loader.py)
- [`core/skill_registry.py`](../core/skill_registry.py)

해석:

- 스킬은 내부 역량 모듈이다
- 시스템은 사용자 발견성보다 주입과 오케스트레이션에 최적화돼 있다
- 아키텍처 자유도는 크지만, 표준화는 Claude/Codex보다 약하다

## 2단계. 스킬은 언제, 어떻게 호출되는가

### Claude Code

Claude Code는 자동 호출과 명시 호출을 모두 지원한다.  
공식 스킬 시스템은 관련성이 있으면 Claude가 자동으로 스킬을 불러올 수 있고, 이름으로 직접 호출하는 방식도 지원한다.

해석:

- 대화형 사용에 강하다
- 수동과 자동이 섞인 워크플로에 강하다
- 사용자 레이어에서 발견성이 높다

### OpenAI Codex

Codex도 명시 사용과 자동 사용을 모두 지원하지만, 더 운영 관점에 가깝다.  
제품 방향은 "이 작업에 맞는 패키지된 워크플로를 쓰고, 이를 팀 전체에서 재사용한다"에 가깝다.

해석:

- 반복 팀 작업에 강하다
- app, CLI, IDE 연속성에 강하다
- 채팅형 UX보다 워크플로형 UX에 더 가깝다

### agent-factory

`agent-factory`에서 스킬 호출은 주로 사용자 주도보다 런타임 주도다.  
role, model, pipeline, provider 실행 경로를 따라 시스템이 어떤 스킬을 로드할지 정한다.

코드 근거:

- [`core/agent_runner.py`](../core/agent_runner.py)
- [`core/project_pipeline.py`](../core/project_pipeline.py)

해석:

- 사람이 직접 "스킬을 호출"하는 구조가 중심은 아니다
- orchestrator와 runner가 역량 주입을 결정한다
- 자동화에는 강하지만, 인간 중심 명시 UX는 약하다

## 3단계. 스킬이 통제할 수 있는 범위

### Claude Code

이 부분은 Claude Code가 특히 강하다.  
공식 문서에는 누가 스킬을 호출할 수 있는지, 모델이 자동 호출해도 되는지, 어떤 도구를 허용할지 같은 제어 요소가 나온다.

해석:

- 스킬은 단순 지침이 아니다
- 호출 경계와 도구 경계까지 함께 정의한다
- 선언 모델이 명확해서 추론하기 쉽다

### OpenAI Codex

Codex는 운영 통제를 스킬 단독보다 주변 제품 환경에 더 많이 둔다.  
플랫폼이 sandbox, project 설정, 공유 워크플로를 제공하고, 스킬은 그 안에서 재사용 가능한 작업 논리를 담는다.

해석:

- 스킬 통제는 더 큰 제품 제어면 안에 들어간다
- 운영 안전성은 markdown spec보다 플랫폼 쪽 무게가 더 크다

### agent-factory

`agent-factory`는 통제가 스킬 포맷 안에 집중되기보다 런타임 전반에 분산돼 있다.  
hook, provider wrapper, destructive guard, orchestration policy가 실행 제어와 안전성의 상당 부분을 맡는다.

코드 근거:

- [`core/agent_runner.py`](../core/agent_runner.py)
- [`docs/architecture.md`](./architecture.md)

해석:

- 스킬은 역량 선택을 담당한다
- hook과 provider가 행동을 강제한다
- 유연성은 높지만, 스킬 spec 자체의 선언성은 Claude보다 약하다

## 4단계. 스킬은 에이전트와 서브에이전트에 어떻게 연결되는가

### Claude Code

Claude Code는 이 부분의 공개 모델이 가장 정제돼 있다.  
공식 문서 기준으로 양방향 관계가 있다.

- skill은 forked context로 실행될 수 있다
- subagent는 자신만의 skill을 preload할 수 있다

해석:

- `skill -> subagent` 구성이 명시적이다
- `subagent -> skills` 구성도 명시적이다
- 설명하기 쉽고, 학습하기 쉬운 모델이다

### OpenAI Codex

Codex는 최소 명세 모델을 예쁘게 보여주기보다 제품형 멀티에이전트 운영에 더 강하다.  
multiple agents, background tasks, isolated work environment를 제공하고, 스킬은 그 위에서 재사용 워크플로로 동작한다.

해석:

- 제품 차원의 병렬 작업에 강하다
- 팀 위임 실행에 강하다
- 간결한 선언 모델 자체는 Claude보다 덜 우아하다

### agent-factory

이 축은 `agent-factory`가 아키텍처적으로 가장 강한 지점이다.  
저장소 자체가 role planning과 병렬 agent dispatch를 중심으로 설계돼 있다.

코드 근거:

- [`core/project_pipeline.py`](../core/project_pipeline.py)
- [`core/dynamic_orchestrator.py`](../core/dynamic_orchestrator.py)

핵심 흐름:

1. `ProjectPipeline`이 research와 role plan을 만든다
2. role artifact를 materialize한다
3. `DynamicOrchestrator`가 agent를 병렬 dispatch한다
4. `AgentRunner`가 provider/runtime 동작을 주입한다

해석:

- `agent-factory`는 "서브에이전트가 있는 코딩 에이전트"가 아니다
- 멀티에이전트 운영 레이어에 가깝다
- 스킬 주입은 이 구조에 매우 자연스럽게 들어맞는다

대신 tradeoff도 분명하다.

- 조합 모델이 안정된 skill spec으로 드러나기보다 코드 안에 더 많이 숨어 있다

## 5단계. 스킬은 어떻게 공유되고 운영되는가

### Claude Code

Claude Code는 로컬, 프로젝트, 더 넓은 조직 범위의 사용 패턴을 지원한다.  
가장 큰 강점은 단순성과 가독성이다.

해석:

- 사람이 읽는 스킬 라이브러리에 강하다
- 감사와 진화가 쉽다
- 명시적 지침 자산을 중시하는 팀에 잘 맞는다

### OpenAI Codex

Codex는 팀이 스킬을 운영 플랫폼의 일부로 만들고 싶을 때 가장 강하다.  
스킬은 Codex의 여러 표면에서 공유될 수 있고, 반복 워크플로와 연결된다.

해석:

- 반복 엔지니어링 작업의 표준화에 가장 강하다
- 스킬을 더 넓은 제품 워크플로와 통합하기 좋다
- 팀 운영 관점의 제품 스토리가 가장 강하다

### agent-factory

`agent-factory`는 가장 유연하지만 동시에 가장 자가 관리형이다.  
스킬은 저장소 아키텍처 일부로 존재하고, local loader, registry, orchestrator 모델에 의존한다.

해석:

- 벤더 락인은 가장 낮다
- 커스터마이즈 자유도는 가장 높다
- 일관성, 툴링, 거버넌스 비용도 가장 높다

## 6단계. 설계 품질을 항목별로 평가하면

### 가장 좋은 선언형 스킬 설계

`Claude Code`

이유:

- 깔끔한 skill format
- 명시적인 invocation control
- 명시적인 tool control
- 정돈된 subagent integration model

### 가장 좋은 운영형 팀 스킬 모델

`OpenAI Codex`

이유:

- 스킬이 제품 워크플로와 직접 연결된다
- cross-surface reuse가 1급 이야기다
- 팀 운영과 반복 실행에 가장 잘 맞는다

### 가장 좋은 오케스트레이션 친화 내부 스킬 모델

`agent-factory`

이유:

- role, model, provider별로 스킬을 주입할 수 있다
- 시스템 자체가 멀티에이전트 중심이다
- runtime이 vendor-agnostic하다

주의점도 분명하다.

- `agent-factory`는 플랫폼 아키텍처로는 가장 강하다
- 공개된 skill specification의 다듬어진 정도는 Claude/Codex보다 약하다

## 7단계. agent-factory가 가져와야 할 것

### Claude Code에서 가져올 것

`agent-factory`는 더 강한 선언형 skill metadata를 가져와야 한다.

추천 항목:

- invocation policy
- allowed tools
- execution context mode
- skill과 subagent role의 관계 표현

이유:

- 시스템을 더 추론 가능하게 만든다
- loader/runtime 코드의 숨은 결합을 줄인다
- 리뷰 가능성을 높인다

### OpenAI Codex에서 가져올 것

`agent-factory`는 더 강한 lifecycle과 sharing mechanic을 가져와야 한다.

추천 항목:

- 팀 배포형 skill bundle
- local에서 shared로의 promotion 경로
- 반복 워크플로용 automation hook
- skill 주변의 운영 패키징 강화

이유:

- 스킬을 엔지니어링 내부 부품에서 팀 자산으로 끌어올린다
- 스킬 품질과 채택을 더 쉽게 확장할 수 있다

### agent-factory가 유지해야 할 것

`agent-factory`는 자신의 가장 강한 차별점을 유지해야 한다.

유지할 것:

- role-based orchestration
- multi-agent parallel dispatch
- provider-agnostic runtime
- planning과 execution에 연결된 runtime skill injection

이유:

- 이 부분은 Claude Code나 Codex가 같은 방식으로 제공하지 않는다
- 이 저장소의 진짜 아키텍처 강점이다

## 결론

질문이 "가장 깔끔한 스킬 설계 기준점이 무엇인가"라면 답은 `Claude Code`다.

질문이 "팀 운영용으로 가장 강한 제품형 스킬 시스템이 무엇인가"라면 답은 `OpenAI Codex`다.

질문이 "커스텀 멀티에이전트 플랫폼용으로 가장 확장성 있는 구조가 무엇인가"라면 답은 `agent-factory`다.

실무적으로는 이렇게 가져가면 된다.

- 명세 기준은 `Claude Code`
- 운영 패키징 기준은 `Codex`
- 오케스트레이션 기준은 `agent-factory`

## 출처

### 공식 제품 문서

- [Claude Code Skills](https://code.claude.com/docs/en/skills)
- [Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [OpenAI Codex](https://openai.com/codex/)
- [Introducing the Codex app](https://openai.com/index/introducing-the-codex-app/)
- [Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan/)

### 저장소 근거

- [`README.md`](../README.md)
- [`docs/architecture.md`](./architecture.md)
- [`core/project_pipeline.py`](../core/project_pipeline.py)
- [`core/dynamic_orchestrator.py`](../core/dynamic_orchestrator.py)
- [`core/agent_runner.py`](../core/agent_runner.py)
- [`core/skill_loader.py`](../core/skill_loader.py)
- [`core/skill_registry.py`](../core/skill_registry.py)
