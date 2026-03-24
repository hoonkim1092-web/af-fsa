# GSD 심층 구조 분석과 Agent Factory 비교

기준일: 2026-03-25

이 문서는 `get-shit-done`(이하 GSD)의 공식 저장소와 공식 문서를 소스 기반으로 정적 분석한 결과를 정리하고, 현재 이 저장소의 `Agent Factory` 구조와 단계별로 비교한 문서다. 실제 결론은 단순하다.

- GSD는 `문서 중심 spec-driven workflow OS`에 가깝다.
- Agent Factory는 `역할 기반 멀티에이전트 실행 플랫폼`에 가깝다.
- 둘 다 멀티에이전트를 쓰지만, GSD의 중심은 `phase와 planning artifact`, Agent Factory의 중심은 `materialized agent + orchestrator state`다.

## 1. 분석 범위

이번 분석은 아래 소스를 기준으로 했다.

1. GSD 공식 저장소 README와 공식 사이트
2. GSD 저장소의 실제 디렉터리 구조
3. GSD의 명령 프롬프트 파일
4. GSD의 workflow 파일
5. GSD의 역할별 agent 프롬프트
6. GSD의 installer 및 `gsd-tools` 유틸리티 구조
7. 현재 저장소의 `Agent Factory` 코드

Agent Factory 쪽 핵심 근거 파일은 아래다.

- `README.md:21`
- `README.md:61`
- `core/project_pipeline.py:75`
- `core/project_pipeline.py:124`
- `core/project_pipeline.py:132`
- `core/project_pipeline.py:216`
- `core/project_pipeline.py:308`
- `core/dynamic_orchestrator.py:31`
- `core/dynamic_orchestrator.py:56`
- `core/dynamic_orchestrator.py:76`
- `core/dynamic_orchestrator.py:87`
- `core/providers/session_adapter.py:156`

## 2. 한 줄 결론

같은 요청이 들어와도 두 시스템은 다르게 생각한다.

1. GSD는 먼저 `이 작업을 어떤 phase로 쪼개고 어떤 PLAN.md들로 나눌지`를 중심으로 움직인다.
2. Agent Factory는 먼저 `이 프로젝트를 어떤 역할 에이전트로 분해하고 어떤 상태 보드로 운영할지`를 중심으로 움직인다.

즉 GSD의 최소 단위는 `phase/plan`, Agent Factory의 최소 단위는 `role/assignment`다.

## 3. GSD 저장소 구조

공식 저장소 트리를 보면 GSD는 대략 아래 계층으로 나뉜다.

```text
repo root
|- bin/
|  \- install.js
|- commands/gsd/
|- agents/
|- get-shit-done/
|  |- bin/
|  |  |- gsd-tools.cjs
|  |  \- lib/*.cjs
|  |- commands/gsd/
|  |- references/
|  |- templates/
|  \- workflows/
|- hooks/
|- scripts/
\- tests/
```

구조상 핵심은 아래 다섯 층이다.

1. `bin/install.js`
설치기다. 여러 런타임에 맞는 명령/프롬프트/스킬을 각 환경 디렉터리에 배포한다.

2. `commands/gsd/*.md`
런타임에 붙는 얇은 엔트리 프롬프트다. 실제 workflow를 직접 다 구현하지 않고, 내부 workflow 파일을 호출하는 진입점 역할을 한다.

3. `get-shit-done/workflows/*.md`
실제 운영 프로토콜이다. `new-project`, `plan-phase`, `execute-phase`, `verify-work` 같은 단계 로직이 여기 있다.

4. `agents/*.md`
서브에이전트 역할 정의다. `gsd-planner`, `gsd-plan-checker`, `gsd-executor`, `gsd-verifier`, `gsd-codebase-mapper` 같은 역할이 분리되어 있다.

5. `get-shit-done/bin/gsd-tools.cjs` 와 `get-shit-done/bin/lib/*.cjs`
workflow가 상태를 읽고 쓰고, phase를 찾고, roadmap를 읽고, frontmatter를 다루고, 검증을 수행할 때 쓰는 도우미 CLI다.

이 조합을 보면 GSD는 대규모 애플리케이션 코드보다 `promptware + workflow spec + thin JS tooling` 쪽에 더 가깝다.

## 4. GSD의 핵심 설계 철학

README와 workflow, agent 프롬프트를 합쳐 보면 GSD의 철학은 아래처럼 압축된다.

1. 메인 컨텍스트는 얇게 유지한다.
2. 무거운 일은 subagent가 fresh context에서 수행한다.
3. 상태는 `.planning/` 문서들로 유지한다.
4. 모든 실행은 phase와 plan 단위로 분해한다.
5. 실행 전에 planner와 checker로 plan 품질을 끌어올린다.
6. 실행 후 verifier와 UAT로 다시 goal-backward 검증을 한다.

README는 이것을 `thin orchestrator + specialized agents + fresh context + state management`로 설명한다. 실제 workflow 파일도 이 설명과 거의 동일한 구조를 가진다.

## 5. GSD를 계층별로 뜯어보기

### 5.1 설치 계층

`package.json`은 배포물에 `bin`, `commands`, `get-shit-done`, `agents`, `hooks/dist`, `scripts`를 포함하고, 엔트리 바이너리로 `bin/install.js`를 노출한다. 즉 GSD는 "라이브러리"보다 "설치형 운영 레이어"에 가깝다.

중요한 포인트는 런타임별 적응이다.

1. Claude Code, OpenCode, Gemini CLI, Codex, Copilot, Cursor, Antigravity 등 여러 런타임에 설치된다.
2. Codex는 README 기준으로 custom prompt보다 `skills` 설치를 사용한다.
3. 같은 workflow라도 런타임에 따라 subagent spawn 방식이 달라진다.

즉 GSD는 특정 모델에 고정된 시스템이 아니라 `runtime adapter가 붙은 prompt operating system`이다.

### 5.2 명령 엔트리 계층

루트의 `commands/gsd/*.md`는 얇은 진입점이다.

예를 들어:

1. `commands/gsd/new-project.md`는 `PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`를 만든다고 선언하고, 실제 실행은 `workflows/new-project.md`로 위임한다.
2. `commands/gsd/plan-phase.md`는 연구, 계획, 검증 루프를 요약하고 실제 실행은 `workflows/plan-phase.md`로 위임한다.
3. `commands/gsd/execute-phase.md`는 wave-based parallel execution을 명시하고 실제 실행은 `workflows/execute-phase.md`로 위임한다.

즉 명령 파일은 CLI UX 레이어이고, 진짜 논리는 workflow 레이어에 있다.

### 5.3 Workflow 계층

`get-shit-done/workflows/*.md`는 GSD의 운영 프로토콜 본체다.

이 레이어의 특징:

1. 각 workflow는 맨 앞에서 `gsd-tools.cjs init ...`로 상태를 읽는다.
2. workflow는 필요한 컨텍스트를 JSON으로 초기화한 뒤 분기한다.
3. workflow는 스스로 heavy implementation을 하지 않고 역할 에이전트를 호출한다.
4. workflow는 생성 문서와 다음 명령을 명시한다.

이 구조 때문에 GSD는 "프롬프트 모음"이 아니라 `workflow state machine`에 더 가깝다.

### 5.4 Agent 계층

`agents/*.md`는 단순한 캐릭터 설정이 아니라 역할별 실행 규칙 세트다.

핵심 역할은 아래와 같다.

1. `gsd-phase-researcher`
phase 구현 방법을 조사한다.

2. `gsd-planner`
phase를 2-3 task짜리 작은 PLAN으로 쪼개고 dependency graph와 wave를 만든다.

3. `gsd-plan-checker`
계획이 phase goal을 실제로 달성하는지 backward로 검증한다.

4. `gsd-executor`
계획을 실행하고 task별 commit과 SUMMARY를 남긴다.

5. `gsd-verifier`
완료 여부가 아니라 goal 달성 여부를 검증한다.

6. `gsd-codebase-mapper`
brownfield 코드베이스를 영역별로 문서화한다.

여기서 중요한 점은 `orchestrator는 무거운 일을 안 하고, 에이전트가 직접 문서나 결과물을 쓴다`는 점이다.

### 5.5 Shared knowledge 계층

`get-shit-done/references/`와 `templates/`는 품질을 고정하는 규약 레이어다.

1. `references/`는 질문 방식, checkpoints, git 계획 규칙, verification 패턴, phase parsing 같은 운영 지침을 담는다.
2. `templates/`는 `project.md`, `requirements.md`, `roadmap.md`, `state.md`, `summary.md`, `UAT.md`, `verification-report.md` 같은 아티팩트 포맷을 고정한다.

즉 GSD는 문서를 그냥 많이 만드는 게 아니라 `문서 포맷을 시스템 계약`으로 쓴다.

### 5.6 Tooling 계층

`gsd-tools.cjs`와 `bin/lib/*.cjs`는 workflow가 상태를 다루기 위한 로컬 CLI다.

디렉터리 구조만 봐도 도메인이 나뉜다.

1. `init.cjs`
workflow 시작 시 필요한 컨텍스트를 JSON으로 계산한다.

2. `roadmap.cjs`
phase 조회와 계획 진행도 업데이트를 담당한다.

3. `state.cjs`
`STATE.md` 관련 갱신을 담당한다.

4. `verify.cjs`
artifact 검증과 health/consistency 계열 검증을 담당한다.

5. `frontmatter.cjs`
PLAN frontmatter를 구조적으로 다룬다.

6. `phase.cjs`, `milestone.cjs`, `commands.cjs`
phase, milestone, 진행률, 통계 같은 운영 명령을 다룬다.

즉 GSD는 문서 중심 시스템이지만, 문서를 맨손으로 다루지 않고 `작은 CLI 툴체인`으로 구조화한다.

## 6. GSD 단계별 실행 흐름

아래는 공식 workflow와 README를 합쳐 재구성한 실제 동작 흐름이다.

### 6.1 초기 설치 단계

1. 사용자가 `npx get-shit-done-cc@latest`를 실행한다.
2. 설치기가 런타임과 설치 위치를 묻는다.
3. 설치기는 해당 런타임 디렉터리에 command, workflow, agent, template, hook, skill 자산을 배포한다.
4. 이후 사용자는 런타임에서 `/gsd:*` 또는 Codex용 `$gsd-*`류 명령을 실행할 수 있다.

### 6.2 새 프로젝트 초기화 단계

1. 사용자가 `/gsd:new-project`를 실행한다.
2. workflow는 `gsd-tools init new-project`로 현재 디렉터리 상태를 읽는다.
3. 기존 코드가 있고 codebase map이 없으면 `/gsd:map-codebase`를 먼저 제안한다.
4. 질문 단계에서 목표, 제약, 선호, edge case를 수집한다.
5. `PROJECT.md`를 작성한다.
6. 설정을 수집해 `.planning/config.json`을 만든다.
7. 선택적으로 research 에이전트를 돌린다.
8. `REQUIREMENTS.md`를 만든다.
9. `ROADMAP.md`를 만든다.
10. `STATE.md`를 만든다.
11. 다음 단계는 `phase` 단위 planning으로 넘어간다.

### 6.3 Brownfield 분석 단계

1. 사용자가 `/gsd:map-codebase`를 실행한다.
2. workflow는 `.planning/codebase/` 생성 여부를 확인한다.
3. 가능하면 `gsd-codebase-mapper` 여러 개를 병렬로 띄운다.
4. 각 mapper는 기술 스택, 통합, 아키텍처, 구조, 컨벤션, 테스트, 우려사항을 문서화한다.
5. orchestrator는 확인과 요약만 하고, 문서 본문은 mapper들이 직접 쓴다.

### 6.4 Phase 논의 단계

1. 사용자가 `/gsd:discuss-phase N`을 실행한다.
2. 시스템은 phase 설명만으로 부족한 회색지대를 찾는다.
3. UI, API, 콘텐츠, 정리 규칙 등 도메인에 따라 질문 분기를 탄다.
4. 결과를 `N-CONTEXT.md`로 만든다.
5. 이후 planner와 researcher는 이 문서를 lock-in context로 사용한다.

### 6.5 Phase 계획 단계

1. 사용자가 `/gsd:plan-phase N`을 실행한다.
2. workflow는 `gsd-tools init plan-phase N`으로 phase 디렉터리와 상태를 읽는다.
3. 필요하면 `gsd-phase-researcher`를 돌려 `RESEARCH.md`를 만든다.
4. `gsd-planner`가 phase를 작은 PLAN들로 분해한다.
5. planner는 dependency graph와 execution wave를 같이 설계한다.
6. planner는 각 PLAN을 "문서"가 아니라 "실행 프롬프트"로 만든다.
7. `gsd-plan-checker`가 plan이 goal을 진짜 달성하는지 backward로 검증한다.
8. 실패하면 planner와 checker가 최대 3회 정도 revision loop를 돈다.
9. 성공하면 `N-RESEARCH.md`, `N-01-PLAN.md`, `N-02-PLAN.md` 같은 산출물이 남는다.

### 6.6 Phase 실행 단계

1. 사용자가 `/gsd:execute-phase N`을 실행한다.
2. workflow는 `gsd-tools init execute-phase N`으로 plans, incomplete plans, phase metadata를 읽는다.
3. orchestrator는 plan들의 dependency를 분석한다.
4. 독립 plan은 같은 wave로 묶는다.
5. wave 단위로 executor 서브에이전트를 띄운다.
6. 각 executor는 fresh context에서 자기 PLAN만 읽고 작업한다.
7. 각 task가 끝날 때마다 atomic commit을 만든다.
8. executor는 `SUMMARY.md`를 만들고 `STATE.md`를 갱신한다.
9. checkpoint가 있으면 그 지점에서 끊고 사람 확인이나 다음 fresh agent로 넘긴다.
10. 모든 wave가 끝나면 phase-level verification으로 넘어간다.

### 6.7 검증 단계

1. 자동 verifier가 phase goal을 backward로 검증한다.
2. `must_haves`, truth, artifact, wiring, data flow를 기준으로 평가한다.
3. 결과는 `VERIFICATION.md`에 남는다.
4. 이후 `/gsd:verify-work N`이 사람 중심 UAT를 진행한다.
5. `UAT.md`는 세션을 넘어가도 이어질 수 있게 유지된다.
6. 문제가 발견되면 gap plan이 생성되고 `/gsd:plan-phase N --gaps`로 되돌아간다.

### 6.8 ship / milestone 단계

1. 검증이 끝난 phase는 `/gsd:ship N`으로 PR을 만든다.
2. milestone이 끝나면 `/gsd:complete-milestone`이 archive와 tag를 남긴다.
3. 이후 `/gsd:new-milestone`로 다음 사이클을 시작한다.

## 7. GSD에서 특히 중요한 구조적 특징

### 7.1 명령은 얇고 workflow가 두껍다

GSD는 slash command 파일을 최대한 얇게 유지하고, 실제 절차를 workflow 파일에 몰아넣는다. 이건 유지보수에 유리하다.

1. UX를 바꾸고 싶으면 command wrapper를 수정한다.
2. 실제 운영 프로토콜을 바꾸고 싶으면 workflow를 수정한다.
3. 역할 세부 규칙을 바꾸고 싶으면 agent 프롬프트를 수정한다.

즉 책임 분리가 매우 선명하다.

### 7.2 Planner와 checker를 분리한다

GSD는 "계획을 만드는 모델"과 "그 계획이 진짜 목표를 달성하는지 따지는 모델"을 나눈다. 이게 품질의 핵심이다.

일반적인 에이전트 시스템은 `plan and do`에서 끝나는데, GSD는 `plan -> critique -> revise -> execute`를 강제한다.

### 7.3 문서가 메모리가 아니라 계약이다

`PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`, `PLAN.md`, `SUMMARY.md`, `UAT.md`는 단순 로그가 아니다.

1. planner가 읽는 입력이다.
2. executor가 따르는 계약이다.
3. verifier가 되돌아보는 검증 대상이다.
4. 다음 세션이 이어받는 상태다.

### 7.4 Fresh context 최적화가 구조에 박혀 있다

GSD는 컨텍스트 낭비를 문서와 구조로 막는다.

1. orchestrator는 파일 경로만 들고 다닌다.
2. heavy context는 subagent가 각자 읽는다.
3. executor와 verifier는 `AGENTS.md` 전체를 읽지 말라고 명시한다.
4. plan은 2-3 task로 제한해 context degradation을 줄인다.

### 7.5 GSD는 코드 생성기가 아니라 운영 체계다

이 점이 가장 중요하다. GSD는 특정 문제를 푸는 agent 하나가 아니다. 설치, 명령, workflow, planner, checker, executor, verifier, toolchain, template를 묶은 `operating model`이다.

## 8. Agent Factory 구조

Agent Factory는 GSD와 달리 문서 운영체계보다 `코드 오케스트레이션 플랫폼` 쪽 무게가 더 크다.

핵심 정체성은 README에 이미 드러난다.

1. `README.md:21`은 "프로젝트 단위로 에이전트를 실행/진화시키는 시스템"이라고 설명한다.
2. `README.md:61`은 "`초개인화된 에이전트 군단`"을 목표로 둔다.

즉 Agent Factory는 phase 중심이 아니라 `agent population` 중심이다.

## 9. Agent Factory 단계별 실행 흐름

`core/project_pipeline.py`와 `core/dynamic_orchestrator.py`, `core/providers/session_adapter.py`를 기준으로 정리하면 아래와 같다.

### 9.1 Prepare 단계

`ProjectPipeline`은 아예 2-phase 구조로 설계되어 있다.

1. `prepare()`는 문서를 생성하고 work-item을 채운다.
2. 이 단계에서는 에이전트를 실제 실행하지 않는다.
3. 결과는 `PreparedProject`로 반환된다.

즉 Agent Factory도 준비와 실행을 나누지만, GSD처럼 slash-command 문서 시스템으로 분리한 게 아니라 Python pipeline으로 분리했다.

### 9.2 Research 단계

`prepare()` 내부에서 bootstrap researcher가 프로젝트를 조사해 `project_brief.json`을 만든다.

1. 연구 에이전트가 project brief를 만든다.
2. 결과는 planning 디렉터리의 JSON 아티팩트로 저장된다.
3. route, requested_role, generated_at 같은 메타데이터가 같이 붙는다.

### 9.3 Planning 단계

같은 `prepare()` 안에서 planning director가 `role_plan.json`을 만든다.

1. task input과 project brief를 합쳐 role plan을 생성한다.
2. 이후 `build_project_board()`로 task board를 만든다.
3. `.todo.md`도 여기서 만들어진다.
4. 별도의 work-item 문서도 생성된다.

즉 Agent Factory의 planning 산출물은 GSD의 `phase/plan`보다 `role plan + board + work item` 중심이다.

### 9.4 Approval 단계

실행은 별도 `execute()`에서 한다.

1. `PreparedProject.gate()`를 통해 approval gate를 읽는다.
2. 승인되지 않았으면 실행하지 않는다.
3. 승인 후 문서가 바뀌면 gate를 invalidate한다.

GSD가 slash-command flow 안에서 상호작용적 gate를 가지는 반면, Agent Factory는 더 명시적인 `approval gate` 파일과 검증 로직을 둔다.

### 9.5 Role materialization 단계

`execute()`는 먼저 work-item 내용으로 board를 sync하고, 그 다음 `_materialize_roles()`를 수행한다.

이 단계에서 일어나는 일:

1. `role_plan`의 각 role을 순회한다.
2. role별 YAML agent 파일을 `workspace/agents/*.yaml`로 만든다.
3. baseline skills와 runtime rule을 merge한다.
4. role objective, required skills, owned modules, feature slices, planning_steps를 role metadata로 쓴다.
5. 필요하면 skill procurer로 skill build/install을 수행한다.

즉 Agent Factory는 GSD처럼 generic agent prompt를 재사용하는 게 아니라, 실행 전에 `프로젝트 맞춤형 agent 객체`를 materialize한다.

### 9.6 Dynamic orchestration 단계

실제 실행은 `DynamicOrchestrator`가 맡는다.

구조는 아래와 같다.

1. `state_board`에 `completed_subtasks`, `failed_subtasks`, `interrupted_subtasks`, `agents_status`, `current_status`를 둔다.
2. manifest store가 이 상태를 스냅샷한다.
3. resume 시 `.af_manifest.json`을 읽어 상태를 복원한다.
4. 각 role은 idle/working 상태를 가지며 assignment를 받아 실행된다.

즉 Agent Factory의 중심 상태는 `.planning` 문서보다 `state_board`와 manifest다.

### 9.7 Continuity 단계

`session_adapter.py`는 다음 실행을 위한 continuity context를 조립한다.

포함되는 것:

1. `.af_manifest.json`의 `state_board`
2. `.todo.md`의 open todo
3. provider별 runtime state의 `last_response_excerpt`
4. `resume_brief`

즉 Agent Factory는 문서만 읽는 게 아니라 runtime provider state와 resume brief까지 합쳐 세션 continuity를 만든다.

## 10. GSD와 Agent Factory의 구조 차이

| 관점 | GSD | Agent Factory |
| --- | --- | --- |
| 중심 단위 | `phase`, `PLAN.md`, `SUMMARY.md` | `role`, `task_board`, `state_board`, `agents/*.yaml` |
| 운영 철학 | 문서 중심 workflow OS | 역할 기반 멀티에이전트 플랫폼 |
| 메인 오케스트레이터 역할 | 얇은 coordinator | 상태 보드 중심 실행 관리자 |
| 상태 저장 방식 | `.planning/*.md` + 일부 config/CLI state | `.af_manifest.json` + `.todo.md` + `resume_brief` + provider state |
| 에이전트 형태 | 고정 역할 프롬프트 파일 | 프로젝트별로 materialize된 YAML agent |
| 계획 단위 | phase -> small plan | project brief -> role plan -> task board |
| 검증 방식 | plan checker + verifier + UAT | approval gate + orchestrator status + session continuity |
| 브라운필드 대응 | `/gsd:map-codebase`로 먼저 문서화 | researcher와 planner가 현재 코드와 상태를 같이 다룸 |
| 컨텍스트 전략 | fresh subagent + path-based context | role agent + continuity context + manifest resume |
| 구현 스타일 | promptware + thin JS tooling | Python orchestration code + runtime provider adapters |

## 11. 같은 요구를 넣었을 때의 단계 차이

예시 요구: "기존 서비스에 주문 취소 기능을 추가해라"

### 11.1 GSD에서의 단계

1. brownfield면 `/gsd:map-codebase`로 현재 구조를 문서화한다.
2. `/gsd:new-project` 또는 milestone 문맥에서 요구를 프로젝트/phase에 편입한다.
3. `/gsd:discuss-phase N`에서 취소 정책, 환불 규칙, UI 흐름, 권한을 고정한다.
4. `/gsd:plan-phase N`에서 researcher, planner, checker가 작은 PLAN들로 쪼갠다.
5. `/gsd:execute-phase N`에서 PLAN wave별 executor가 병렬 실행한다.
6. 각 PLAN은 task별 commit과 SUMMARY를 남긴다.
7. verifier가 phase goal을 backward 검증한다.
8. `verify-work`에서 사람이 실제 취소 흐름을 UAT한다.
9. 문제 있으면 `--gaps` planning으로 돌아간다.
10. 통과하면 ship한다.

### 11.2 Agent Factory에서의 단계

1. 사용자의 요구가 pipeline input으로 들어온다.
2. researcher가 `project_brief`를 만든다.
3. planning director가 `role_plan`, `task_board`, `.todo.md`, work-item 문서를 만든다.
4. 사용자가 approval gate를 승인한다.
5. system이 역할별 YAML agent를 materialize한다.
6. 필요한 skill을 설치하거나 조달한다.
7. orchestrator가 role별 assignment를 분배한다.
8. 실행 중 `state_board`와 manifest가 계속 갱신된다.
9. 세션이 끊겨도 resume_brief와 provider state로 이어간다.

이 비교에서 보이듯 GSD는 `plan-first`, Agent Factory는 `agent-first`다.

## 12. 둘의 단계적 차이를 더 명시적으로 보면

### 12.1 GSD의 기준 질문

GSD는 매 단계에서 주로 아래 질문을 묻는다.

1. 지금 phase goal이 무엇인가
2. 그 goal을 만족하는 must-have는 무엇인가
3. 그것을 몇 개 PLAN으로 쪼갤 것인가
4. 어떤 PLAN이 어떤 wave에서 실행되어야 하는가
5. 결과가 goal을 달성했는가

### 12.2 Agent Factory의 기준 질문

Agent Factory는 매 단계에서 주로 아래 질문을 묻는다.

1. 이 프로젝트를 어떤 역할로 분해할 것인가
2. 각 역할은 무엇을 소유하는가
3. 어떤 skill이 필요한가
4. 현재 board와 manifest 상태는 무엇인가
5. 어떤 role이 지금 idle이고 어떤 subtask를 맡길 수 있는가
6. 다음 세션에서도 이어질 수 있는가

즉 GSD는 `phase decomposition`, Agent Factory는 `role orchestration`에 더 최적화되어 있다.

## 13. 장단점

### 13.1 GSD의 장점

1. workflow가 매우 선명하다.
2. `.planning/` 문서만 봐도 현재 상태를 사람이 따라가기 쉽다.
3. planner/checker/verifier 루프가 강해서 계획 품질이 높다.
4. fresh context 설계가 구조적으로 잘 되어 있다.
5. 여러 런타임에 이식 가능하다.

### 13.2 GSD의 약점

1. agent 자체의 장기 진화나 개별 personalization은 상대적으로 약하다.
2. state가 문서 중심이라 런타임 내부 상태 추적은 제한적이다.
3. role-specific agent materialization보다는 generic role 프롬프트에 의존한다.

### 13.3 Agent Factory의 장점

1. 프로젝트별 agent YAML을 materialize하므로 역할 분화가 강하다.
2. skill procurement/build/install이 platform 레벨에 있다.
3. orchestrator state와 resume 흐름이 코드 수준에서 관리된다.
4. 세션 continuity가 풍부하다.

### 13.4 Agent Factory의 약점

1. GSD만큼 명시적인 `phase workflow UX`는 약하다.
2. 외부에서 볼 때 지금 어디까지 왔는지 문서만으로는 GSD보다 덜 직관적일 수 있다.
3. planning critique loop가 GSD처럼 독립된 workflow 계약으로 선명하게 드러나진 않는다.

## 14. 실제로 가장 중요한 차이

가장 중요한 차이는 "무엇을 1급 객체로 보는가"다.

1. GSD의 1급 객체는 `phase artifact`다.
2. Agent Factory의 1급 객체는 `agent runtime state`다.

이 차이 때문에:

1. GSD는 문서 기반 spec-driven delivery에 강하다.
2. Agent Factory는 역할 분화, specialization, continuity, skill composition에 강하다.

## 15. 통합 관점에서 보면

둘은 경쟁재이기도 하지만, 결합도 가능하다.

가장 자연스러운 결합 방식은 아래다.

1. GSD의 `new-project -> discuss-phase -> plan-phase` UX를 Agent Factory의 prepare 단계 앞단에 둔다.
2. GSD가 만든 phase artifact를 Agent Factory의 `role_plan` 입력으로 변환한다.
3. 실제 실행은 Agent Factory가 materialized agent와 orchestrator로 맡는다.
4. 검증은 GSD의 goal-backward verifier 스타일을 Agent Factory의 board/manifest 구조 위에 얹는다.

즉 GSD는 `명세 운영체계`, Agent Factory는 `실행 플랫폼`으로 결합할 수 있다.

## 16. 최종 평가

정리하면 GSD는 "좋은 메타 프롬프트 모음" 수준이 아니라 분명한 아키텍처를 가진다.

그 아키텍처의 본질은 아래와 같다.

1. installer가 runtime별 진입점을 설치한다.
2. command wrapper가 workflow를 호출한다.
3. workflow가 상태를 읽고 역할 에이전트를 조합한다.
4. 역할 에이전트가 research, planning, checking, execution, verification을 분리 수행한다.
5. templates와 references가 산출물 형식과 품질 규칙을 고정한다.
6. `gsd-tools`가 문서 기반 상태를 구조적으로 조작한다.

반면 Agent Factory는 아래 구조를 가진다.

1. Python pipeline이 prepare와 execute를 분리한다.
2. researcher와 planning director가 project brief와 role plan을 만든다.
3. system이 역할별 YAML agent를 materialize한다.
4. skills를 설치하거나 조달한다.
5. dynamic orchestrator가 state board를 기반으로 역할을 병렬 운영한다.
6. manifest, todo, resume brief, provider state로 continuity를 유지한다.

따라서 결론은 명확하다.

1. GSD는 `스펙 중심 delivery workflow`를 가장 잘 설명한다.
2. Agent Factory는 `개별화된 역할 에이전트 운영 플랫폼`을 가장 잘 설명한다.
3. 둘은 목적이 겹치지만, 구조적 무게중심은 다르다.

## 부록 A. GSD 품질 루프의 Agent Factory 1:1 매핑

앞서 비교를 단순화해서 보이면 오해가 생길 수 있다. 더 정확히 말하면 `Agent Factory`에도 GSD의 품질 루프에 대응되는 요소가 이미 있다. 차이는 `존재 여부`보다 `전면화된 정도`, `분리된 역할`, `강제되는 workflow의 선명도`다.

### A.1 대응표

| GSD 요소 | Agent Factory 대응 요소 | 현재 판단 | 설명 |
| --- | --- | --- | --- |
| `.planning` 문서 상태 | `planning/project_brief.json`, `planning/role_plan.json`, `task_board`, `task_execution_plan`, `.todo.md`, work-item 문서, `approval-gate.md`, `.af_manifest.json`, `resume_brief.md` | 대응됨 | `ProjectPipeline.prepare()`가 planning 산출물을 만들고, `session_adapter`가 manifest/todo/resume_brief를 다시 읽는다. 다만 GSD처럼 `.planning/*.md`가 단일 중심은 아니고 상태가 문서와 런타임 파일에 분산된다. |
| phase/plan 분해 | `planning_steps`, `modules`, `tasks`, `task_board`, work-item의 `scope/build/integrate/verify` 단계 | 대응됨 | role plan과 work-item generator가 이미 단계 분해를 수행한다. 다만 최상위 UX가 GSD처럼 `phase -> PLAN.md` 중심은 아니고 `role -> task board` 중심이다. |
| planner | `ProjectPlanningDirector.plan()` | 대응됨 | planner는 분명히 있다. `project_brief`를 받아 역할, planning_steps, modules, tasks를 만든다. |
| planner + checker 분리 | planning-first gate, approval gate, QA 강제, 일부 critic/evaluator 계열 | 부분 대응 | 계획 없이 바로 실행하는 것을 막는 장치는 있다. 하지만 GSD의 `gsd-plan-checker`처럼 planner와 별도의 독립 checker가 `계획 자체`를 비판하고 수정 루프를 강제하는 전면 workflow는 아직 약하다. |
| verifier | `qa_engineer` 강제, verify-phase 모듈 강제, acceptance/artifacts, QA 검증 태스크 | 부분 대응 | 검증 단계는 존재하고 강제도 있다. 다만 GSD의 `goal-backward verifier`처럼 phase goal과 must-have를 기준으로 별도 산출물을 남기며 판정하는 구조는 덜 분리돼 있다. |
| UAT | work-item review, approval gate, QA 리포트, resume/continuity 메모 | 약함 | 사람 중심 UAT를 세션 넘김까지 이어가는 `UAT.md`급 1급 객체는 현재 명시적이지 않다. 검토/승인/QA는 있지만 GSD식 UAT workflow와는 다르다. |
| 실행 후 상태 갱신 | `state_board`, `.af_manifest.json`, `resume_brief.md`, provider state, dashboard run | 대응됨 | 실행 후 상태를 남기고 이어가는 능력은 오히려 Agent Factory가 더 강하다. 다만 이것은 GSD의 `STATE.md`, `SUMMARY.md` 같은 인간 친화적 phase 로그와는 성격이 다르다. |

### A.2 로컬 코드 근거

아래는 Agent Factory 쪽 대응 요소의 핵심 근거다.

1. planning 산출물 생성
`core/project_pipeline.py:237-302`는 `project_brief.json`, `role_plan.json`, `task_board`, `task_execution_plan`, `.todo.md`, work-item 문서를 만든다.

2. 실행 전 gate와 문서 무결성 확인
`core/approval_gate.py:6-11`과 `core/project_pipeline.py:328-346`은 승인 전 실행 차단과 승인 후 문서 변경 감지를 수행한다.

3. 단계 분해
`core/bootstrap_roles.py:415-430`은 task phase를 `scope|build|integrate|verify`로 정의하고, QA verify-phase를 필수화한다.

4. verify 단계 강제
`core/bootstrap_roles.py:294-365`는 QA 역할이 없으면 강제로 추가하고, verify-phase 모듈이 없으면 `qa_verification` 모듈을 자동 삽입한다.

5. continuity와 상태 회수
`core/providers/session_adapter.py:156-201`은 `.af_manifest.json`, `.todo.md`, runtime state, `resume_brief.md`를 읽어 다음 세션 컨텍스트를 구성한다.

### A.3 왜 그래도 GSD가 더 "품질 워크플로우"처럼 보였는가

차이는 아래 세 가지다.

1. GSD는 `planner`, `checker`, `verifier`, `UAT`가 전부 이름 붙은 독립 단계다.
Agent Factory는 대응 기능이 있지만 `approval`, `QA`, `continuity`, `evaluator` 같은 다른 축에 분산돼 있다.

2. GSD는 최상위 사용자 UX가 곧 품질 루프다.
사용자가 `/gsd:discuss-phase`, `/gsd:plan-phase`, `/gsd:execute-phase`, `/gsd:verify-work`를 순서대로 밟는다. Agent Factory는 내부적으로는 planning과 verification이 있지만, 외부 UX는 아직 그만큼 노골적이지 않다.

3. GSD는 `계획 비판`과 `사람 중심 UAT`를 1급 객체로 취급한다.
현재 저장소를 기준으로 `core/`에는 GSD의 `plan checker`, `goal-backward verifier`, `UAT.md`와 정확히 같은 위상을 가진 독립 실행 경로는 보이지 않는다.

### A.4 더 정확한 결론

따라서 더 정확한 비교 문장은 아래다.

1. `Agent Factory`는 이미 GSD의 품질 루프 구성 요소 상당수를 갖고 있다.
2. 다만 그것들이 `GSD만큼 명시적이고 일관된 workflow 계약`으로 전면화돼 있지는 않다.
3. 반대로 상태 관리, 세션 연속성, 역할 기반 materialization은 Agent Factory가 더 강하다.

### A.5 Agent Factory가 GSD식 품질 루프를 완전히 흡수하려면

아래 다섯 가지를 추가하면 된다.

1. `phase`를 1급 객체로 승격하고 `discuss-phase` 성격의 요구 고정 단계를 만든다.
2. `ProjectPlanningDirector`와 별도의 `PlanChecker`를 두고 `plan -> critique -> revise` 루프를 강제한다.
3. `must_haves`, `truth checks`, `wiring checks`를 가진 `verification-report.md`를 표준 산출물로 만든다.
4. `UAT.md`를 1급 문서로 만들고 사람 검증이 세션을 넘어 이어지게 한다.
5. 검증 실패 시 `gap planning`으로 되돌아가는 재계획 루트를 표준화한다.

이 다섯 가지만 붙으면, Agent Factory는 GSD의 품질 워크플로우를 대부분 흡수하면서도 자신의 강점인 역할 기반 오케스트레이션과 continuity를 유지할 수 있다.

## 17. 참고 소스

### 17.1 GSD 공식 소스

- 공식 저장소: https://github.com/gsd-build/get-shit-done
- 공식 사이트: https://gsd.build/
- README: https://github.com/gsd-build/get-shit-done/blob/main/README.md
- `package.json`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/package.json
- `commands/gsd/new-project.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/commands/gsd/new-project.md
- `commands/gsd/plan-phase.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/commands/gsd/plan-phase.md
- `commands/gsd/execute-phase.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/commands/gsd/execute-phase.md
- `get-shit-done/workflows/new-project.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/get-shit-done/workflows/new-project.md
- `get-shit-done/workflows/plan-phase.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/get-shit-done/workflows/plan-phase.md
- `get-shit-done/workflows/execute-phase.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/get-shit-done/workflows/execute-phase.md
- `get-shit-done/workflows/map-codebase.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/get-shit-done/workflows/map-codebase.md
- `get-shit-done/workflows/verify-work.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/get-shit-done/workflows/verify-work.md
- `agents/gsd-planner.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/agents/gsd-planner.md
- `agents/gsd-executor.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/agents/gsd-executor.md
- `agents/gsd-verifier.md`: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/agents/gsd-verifier.md

### 17.2 Agent Factory 로컬 소스

- `README.md`
- `core/project_pipeline.py`
- `core/dynamic_orchestrator.py`
- `core/providers/session_adapter.py`
