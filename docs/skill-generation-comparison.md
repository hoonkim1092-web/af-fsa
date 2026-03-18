# 스킬 생성 비교: Claude Code vs OpenAI Codex vs agent-factory

날짜: 2026-03-18  
범위: 스킬 "생성"과 "조달" 관점 비교  
비교 대상: 이 문서에서 `OpenAI`는 회사 전체가 아니라 `Codex`를 의미함

## 이 문서의 목적

이 문서는 세 시스템을 "스킬을 어떻게 만들어내는가"라는 관점에서 비교한다.

비교 축은 아래 여섯 단계다.

1. 어떤 스킬이 필요한지 판별하는가
2. 기존 스킬을 얼마나 정확하게 재사용하는가
3. 없으면 새 스킬을 어떻게 만드는가
4. 생성한 스킬을 어떻게 검증하는가
5. 운영 중 얼마나 안정적으로 재사용하는가
6. 정확도와 신뢰도를 어디까지 기대할 수 있는가

핵심 전제는 단순하다.

- `Claude Code`는 스킬을 가장 정확하게 정의하고 통제하는 쪽에 강하다
- `OpenAI Codex`는 스킬을 가장 잘 운영 가능한 팀 자산으로 만드는 쪽에 강하다
- `agent-factory`는 셋 중 가장 적극적으로 스킬을 조달하고, 없으면 생성하려는 쪽에 강하다

## 1단계. 어떤 스킬이 필요한지 판별하는가

### Claude Code

Claude Code는 공식 제품 관점에서 "없는 스킬을 먼저 탐지해 자동 생성한다"보다, 이미 존재하는 skill이나 subagent를 올바르게 선택하는 쪽이 중심이다.  
즉 판별의 핵심은 "현재 작업에 어떤 skill이 relevant한가"다.

장점:

- 사용자가 직접 skill을 호출할 수 있다
- description, tool policy, context 설정이 분명하다
- 잘 설계된 skill일수록 오동작 여지가 작다

한계:

- missing skill detection 자체가 제품의 핵심 파이프라인은 아니다

### OpenAI Codex

Codex도 중심은 "적절한 skill을 선택해 쓰는 것"이다.  
Codex app과 CLI/IDE는 task에 따라 skill을 자동 사용하거나 명시적으로 사용하게 할 수 있다.

장점:

- app, CLI, IDE를 가로지르는 일관된 skill 사용 경험
- task 단위로 skill을 붙이기 쉬움
- 운영형 workflow와 자연스럽게 연결됨

한계:

- 공식 제품 설명 기준으로 missing skill을 구조적으로 추론해 새 Python skill로 forge하는 흐름이 전면 기능은 아니다

### agent-factory

`agent-factory`는 셋 중 가장 명시적으로 missing skill 탐지를 수행한다.  
`RequirementAnalyzer.analyze()`가 `missing_skills`를 추론하고, `Researcher.research()`가 후보 스킬과 evidence를 만든다.

코드 근거:

- [`core/manager.py`](../core/manager.py)
- [`core/researcher.py`](../core/researcher.py)

장점:

- 스킬 필요성 자체를 파이프라인에서 다룸
- planner/research 단계와 연결되어 있음

한계:

- requirement 분석 실패 시 fallback이 얕은 키워드 규칙으로 떨어진다
- 이 단계의 정확도는 LLM availability와 prompt 품질에 크게 의존한다

## 2단계. 기존 스킬 재사용 정확도

### Claude Code

Claude Code의 강점은 재사용 대상이 잘 구조화되어 있다는 점이다.  
skill은 문서형 spec으로 존재하고, tool 제한과 invocation 정책이 함께 붙기 때문에 재사용 시 의미가 잘 보존된다.

강점:

- spec이 읽히기 때문에 재사용 정확도가 높다
- tool scope와 context mode가 명시적이다

약점:

- 팀 규모가 커질수록 search/discovery 자체는 별도 운영 체계에 의존할 수 있다

### OpenAI Codex

Codex는 재사용 운영이 가장 강하다.  
app에서 만든 skill을 CLI/IDE에서도 쓰고, repository에 체크인해 팀 전체에 배포할 수 있다.

강점:

- 여러 표면 간 재사용
- team config와 스킬 공유
- automation과 연결되는 운영 재사용성

약점:

- "왜 이 skill이 이 task에 맞는가"에 대한 선언형 설명은 Claude보다 덜 중심이다

### agent-factory

`agent-factory`는 재사용 정확도를 높이기 위해 여러 단계를 거친다.

1. exact local match 재사용
2. research가 뽑은 verified candidate 재사용
3. external source 설치 시도
4. 안 되면 builder fallback

코드 근거:

- [`core/skill_procurer.py`](../core/skill_procurer.py)
- [`core/registry_manager.py`](../core/registry_manager.py)
- [`core/external_skill_sources.py`](../core/external_skill_sources.py)

강점:

- project/global/user-wide/external source를 모두 본다
- `claude_repo -> codex_repo -> registry/external_cache` 우선순위를 둘 수 있다

약점:

- 외부 후보 매칭은 여전히 토큰 overlap 중심이라 의미적 정합성 판별이 깊지 않다
- verified candidate의 "검증"도 본질적으로 증거 품질에 좌우된다

## 3단계. 없으면 새 스킬을 어떻게 만드는가

### Claude Code

Claude Code는 스킬 생성보다 스킬 authoring과 실행 품질에 강하다.  
즉 사람이 `SKILL.md`와 관련 자산을 잘 쓰면, Claude가 그것을 정확히 사용하게 만드는 구조다.

강점:

- 사람이 설계한 skill을 깨끗하게 유지하기 좋다
- skill 자체를 하나의 계약으로 관리할 수 있다

한계:

- missing skill을 감지해 Python implementation까지 자동 생성하는 파이프라인은 제품 중심 기능이 아니다

### OpenAI Codex

Codex는 app에서 skills를 만들고 관리할 수 있고, instructions/resources/scripts를 묶어 workflow package로 만든다.  
즉 "제품 내부에서 운영 가능한 skill을 제작"하는 경험이 좋다.

강점:

- skill 생성 이후 운영으로 바로 이어진다
- UI, CLI, IDE, automation까지 연결된다

한계:

- 공식 문서 기준으로 생성 대상은 일반적으로 workflow package이지, `agent-factory`처럼 좁은 Python skill ABI를 자동 forge하는 구조는 아니다

### agent-factory

`agent-factory`는 셋 중 유일하게 자동 skill forge 파이프라인이 명확하다.  
`SandboxedBuilder.build_skill()`는 LLM으로부터 Python code를 받아 `propose(ctx)`, `apply(ctx)`, `test(ctx)`를 갖는 skill 모듈을 생성한다.

코드 근거:

- [`core/builder.py`](../core/builder.py)

강점:

- 없으면 실제 구현물을 만든다
- CLI provider 우선, 실패 시 SDK fallback 구조를 가진다
- planning-first gate가 있어 evidence 없이 무작정 생성하지 않는다

약점:

- 생성 형식이 매우 좁다
- 실제로는 범용 skill authoring보다 "제한된 계약을 만족하는 작은 Python 모듈 생성"에 가깝다

## 4단계. 생성한 스킬을 어떻게 검증하는가

### Claude Code

Claude Code의 검증 강점은 skill spec 자체의 명확성에 있다.  
allowed tools, invocation policy, forked context 같은 요소가 잘못된 실행을 줄인다.

장점:

- 명세 수준에서 실수를 줄인다
- 실행 전에 권한 경계를 분명히 만든다

한계:

- "생성한 code skill의 숨은 테스트 정확도" 같은 forge 품질 평가 파이프라인은 공식 중심 기능이 아니다

### OpenAI Codex

Codex는 제품 운영 차원의 검증이 강하다.  
isolated sandbox, review queue, multiple agents, automations, worktrees가 결합되어 결과를 점진적으로 검토하고 반복하기 좋다.

장점:

- 생성 결과를 운영 환경에서 검토하기 쉬움
- 리뷰와 재실행이 자연스럽다

한계:

- skill 자체의 숨은 benchmark 검증을 공식 제품 설명이 상세히 드러내진 않는다

### agent-factory

`agent-factory`는 생성물 검증을 꽤 보수적으로 한다.

1. `quick_guard()`로 금지 import/call을 AST 수준에서 차단
2. `run_isolated()`로 네트워크 차단, 파일 접근 제한, timeout 적용
3. `test(ctx)` 실행 결과의 `ok`를 본다

코드 근거:

- [`core/security_guard.py`](../core/security_guard.py)

강점:

- 안전성은 꽤 강하다
- 빌더가 만든 코드를 무방비로 실행하지 않는다

치명적 한계:

- `test(ctx)`도 같은 모델이 만든다
- 즉 구현과 테스트가 함께 잘못 생성되면 통과할 수 있다
- 숨은 oracle이나 외부 benchmark가 아니다

이 점 때문에 `agent-factory`의 생성 검증은 "안전성 검증"에는 강하지만, "정확성 검증"에는 아직 약하다.

## 5단계. 운영 안정성과 팀 재사용성

### Claude Code

운영 안정성은 높지만, 기본 철학은 로컬/프로젝트 단위의 정돈된 skill 사용에 가깝다.

잘하는 것:

- 명세 정합성
- tool/capability 통제
- subagent와의 깔끔한 결합

### OpenAI Codex

이 축에서는 가장 강하다.  
Codex app은 skills, automations, worktrees, review queue, multiple agents를 한 제품 표면으로 묶는다.

잘하는 것:

- 팀 배포
- 반복 작업 자동화
- 운영 자산화

### agent-factory

`agent-factory`는 운영성보다 조달/생성 쪽이 더 강하다.  
registry, workflow mapping, quality gate는 있지만 제품형 운영 UX는 약하다.

코드 근거:

- [`core/registry_manager.py`](../core/registry_manager.py)

강점:

- registry와 lock이 존재한다
- build 결과를 workflow mapping과 연결한다

약점:

- lifecycle이 아직 candidate/active 수준의 얕은 상태 관리에 머문다
- 팀 공유 경험은 Codex보다 약하다

## 6단계. 정확도를 어떻게 봐야 하는가

여기서 "정확도"는 하나가 아니다. 최소 네 종류로 봐야 한다.

### 6.1 명세 정확도

정의:

- skill이 무엇을 해야 하는지
- 어디까지 할 수 있는지
- 어떤 context에서 돌아야 하는지

평가:

- `Claude Code > OpenAI Codex > agent-factory`

이유:

- Claude는 skill spec이 가장 선언적이다
- Codex는 운영 패키징이 강하다
- agent-factory는 아직 metadata보다 코드 경로 의존이 크다

### 6.2 재사용 정확도

정의:

- 이미 있는 skill 중 정말 맞는 것을 골라오는 능력

평가:

- `OpenAI Codex >= Claude Code > agent-factory`

이유:

- Codex는 팀 단위 재사용과 배포가 강하다
- Claude는 skill 의미가 잘 보존된다
- agent-factory는 source는 많이 보지만 candidate matching이 아직 얕다

### 6.3 자동 생성 정확도

정의:

- 없는 skill을 만들었을 때 구현이 실제 요구를 얼마나 맞추는가

평가:

- `agent-factory > OpenAI Codex > Claude Code`

이유:

- 자동 skill forge 파이프라인을 가장 명시적으로 가진 건 agent-factory다
- 다만 이 평가는 "생성 시도 능력"에 대한 것이지 최종 신뢰도와는 다르다

### 6.4 생성 결과 신뢰도

정의:

- 만들어진 skill을 실제로 믿고 재사용할 수 있는가

평가:

- `OpenAI Codex ~= Claude Code > agent-factory`

이유:

- agent-factory는 self-test 의존이 크다
- Claude/Codex는 사람이 더 분명한 skill 계약을 만들고 플랫폼이 더 강하게 운영을 감싼다

## 7단계. 핵심 차이 요약

### Claude Code

- 스킬을 가장 정확하게 정의하게 해준다
- 스킬 단위 통제와 context 제어가 강하다
- 자동 생성보다는 정확한 spec 기반 재사용에 강하다

### OpenAI Codex

- 스킬을 가장 잘 운영 가능한 팀 자산으로 만든다
- 생성 이후 재사용, 자동화, 멀티에이전트 운영이 강하다
- 작업 패키지 관점이 강하다

### agent-factory

- 셋 중 가장 공격적으로 missing skill을 감지하고 조달/생성한다
- 외부 재사용 후 최후에 forge하는 메타 오케스트레이터다
- 그러나 생성 품질의 최종 정확성 검증은 아직 약하다

## 8단계. 결론

한 줄로 정리하면:

- `Claude Code`는 스킬을 가장 정확하게 "정의"한다
- `OpenAI Codex`는 스킬을 가장 정확하게 "운영"한다
- `agent-factory`는 스킬을 가장 적극적으로 "만들고 조달"한다

현재 `agent-factory`의 가장 큰 강점은 "없으면 만든다"는 점이다.  
현재 가장 큰 약점은 "만든 것이 정말 맞는지"를 검증하는 루프가 아직 충분히 강하지 않다는 점이다.

따라서 다음 세대 설계의 핵심은 더 많은 스킬을 만드는 것이 아니라, 아래를 강화하는 것이다.

1. 필요한 스킬 탐지 개선
2. 후보 재사용 순위 산정 개선
3. self-test가 아닌 외부 평가 기반 검증
4. 승격 단계와 상태 관리의 정교화

## 검증 참고

관련 테스트는 아래를 실행해 통과를 확인했다.

```bash
python -m pytest -q tests/test_builder_cli_fallback.py tests/test_skill_procurer_external_fallback.py tests/test_factory_evolution.py
```

결과:

- `8 passed`

주의:

- 이 테스트들은 CLI fallback, external miss 처리, registry/install 흐름은 본다
- 하지만 "생성된 skill이 실제로 유용하고 정확한가"까지는 충분히 검증하지 않는다

## 출처

### 공식 제품 문서

- [Claude Code Skills](https://code.claude.com/docs/en/skills)
- [Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)
- [Introducing the Codex app](https://openai.com/index/introducing-the-codex-app/)
- [Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan/)

### 저장소 근거

- [`core/manager.py`](../core/manager.py)
- [`core/researcher.py`](../core/researcher.py)
- [`core/skill_procurer.py`](../core/skill_procurer.py)
- [`core/builder.py`](../core/builder.py)
- [`core/registry_manager.py`](../core/registry_manager.py)
- [`core/external_skill_sources.py`](../core/external_skill_sources.py)
- [`core/security_guard.py`](../core/security_guard.py)
- [`tests/test_builder_cli_fallback.py`](../tests/test_builder_cli_fallback.py)
- [`tests/test_skill_procurer_external_fallback.py`](../tests/test_skill_procurer_external_fallback.py)
- [`tests/test_factory_evolution.py`](../tests/test_factory_evolution.py)

