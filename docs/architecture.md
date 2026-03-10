# Architecture

이 문서는 현재 `agent-factory`가 프로젝트를 만들고, AI 엔진을 세션 기반으로 실행하고, 훅과 프롬프트를 통해 파일 수정까지 반영하는 전체 구조를 설명하는 기준 문서다.
설계, 워크플로우, 인터페이스, 데이터 흐름, 구현 전략이 바뀌면 이 문서와 `docs/change_history.md`를 같은 작업에서 함께 갱신해야 한다.

## Metadata
- Last updated: 2026-03-10
- Status: active
- Scope: `agent_launcher.py`, `core/project_init.py`, `core/project_pipeline.py`, `core/agent_runner.py`, `core/providers/*`, `core/hooks/*`, `scripts/cli_hook_bridge.py`, `scripts/session_bridge.py`
- Note: `core/builder.py` now prefers configured CLI providers for skill code generation and falls back to Gemini SDK only when CLI is unavailable and a Google API key exists.
- Note: external skill procurement now supports source-priority resolution (`claude_repo -> codex_repo -> registry/external_cache`) before builder fallback, and importer support exists via `scripts/import_external_skill_candidates.py`.

## 문서 언어 규칙
- 이 저장소에서 생성하거나 수정하는 모든 문서는 운영체제 언어 코드에 맞는 언어로 작성한다.
- 현재 기준 운영체제 언어 코드는 `ko-KR`이며, 문서 제목·본문·요약·변경 이력 설명은 한국어로 작성한다.
- 코드, 파일 경로, 명령어, API 식별자는 필요한 경우 원문 그대로 유지한다.

## 1. 핵심 결론

현재 시스템은 실행 평면이 2개다.

1. 내부 SDK 실행 평면
   `AgentRunner`가 Gemini SDK와 내부 Tool Registry를 직접 붙여서 ReAct 루프를 돌린다.
   이 경로에서는 툴 호출이 파이썬 함수로 노출되고, 파일 수정은 `write_file`, `hash_edit` 같은 로컬 스킬 함수가 실제로 수행한다.

2. 외부 CLI 세션 실행 평면
   `claude_cli`, `gemini_cli`, `codex_cli` 같은 외부 에이전트 CLI를 subprocess로 실행한다.
   이 경로에서는 실제 편집은 외부 CLI의 네이티브 툴이 수행하고, `session_adapter`는 세션 상태, 훅, 연속성 컨텍스트, 결과 수집을 담당한다.

즉, 이 시스템의 진짜 구조는 "하나의 실행기"가 아니라 "프로젝트/오케스트레이션 계층 위에 내부 SDK 경로와 외부 CLI 경로를 동시에 얹은 이중 런타임"이다.

## 2. 구성요소 지도

### 엔트리포인트와 경로
- `agent_launcher.py`
  팩토리 조립 지점이다. import 시점에 `ensure_project_files()`를 호출해 기본 프로젝트 파일을 보장한다.
- `core/config_paths.py`
  `AGENT_PROJECT_ID`, `AGENT_PROJECT_ROOT`, `AGENT_GLOBAL_USER_KEY`, `AGENT_GLOBAL_PROJECT_ROOT`를 읽어 현재 프로젝트 루트와 글로벌 메모리 루트를 정한다.
- `core/project_init.py`
  `policies.yaml`, `context_schema.yaml`, `skill-lock.yaml`, `dashboard.json`, `workflow.yaml`, `settings.yaml`를 만든다.
  지금은 `docs/architecture.md`, `docs/change_history.md`도 함께 보장한다.

### 프로젝트 생성과 계획
- `core/request_router.py`
  요청을 `single` 실행으로 보낼지 `project` 파이프라인으로 보낼지 결정한다.
- `core/project_pipeline.py`
  프로젝트 생성/리서치/계획/역할 물질화/오케스트레이션까지 맡는다.
- `core/researcher.py`
  Himari가 `project_brief`를 만드는 경로다.
- `core/bootstrap_roles.py`
  프로젝트 Planning Director가 `role_plan`과 `.todo.md` 기반 역할 계획을 만든다.
- `core/dynamic_orchestrator.py`
  Lilith 스타일의 중앙 오케스트레이터로 각 역할을 병렬 디스패치한다.

### 단일 실행과 툴 런타임
- `core/agent_runner.py`
  단일 에이전트 실행의 중심이다.
  모델 선택, 시스템 프롬프트 조립, 스킬 로딩, 툴 호출, 승인, 훅 버스, SDK/CLI 분기를 모두 여기서 처리한다.
- `core/tool_runtime.py`
  스킬 파이썬 모듈을 LLM이 호출 가능한 툴 함수로 감싼다.
- `core/registry.py`
  실제 활성 툴 목록을 들고 있는 툴 레지스트리다.

### 세션/훅/연속성
- `core/hooks/event_bus.py`
  내부 SDK 경로에서 쓰는 런타임 훅 버스다.
- `core/hooks/guardrails.py`
  모호한 요청 차단, `.todo.md` 강제, 출력 truncate 같은 기본 가드레일을 제공한다.
- `core/providers/cli.py`
  외부 CLI 실행 명령, 환경변수, 프롬프트, auto-install을 담당한다.
- `core/providers/session_adapter.py`
  외부 CLI 세션의 준비/종료/훅 이벤트 수집/continuity context 생성을 담당한다.
- `scripts/cli_hook_bridge.py`
  외부 CLI의 JSON hook payload를 받아 `session_adapter.handle_hook_event()`로 넘긴다.
- `scripts/session_bridge.py`
  Claude/Gemini/Codex 세션 transcript를 읽어 메모리/연속성 쪽으로 브리지한다.
- `core/continuity/resume_brief.py`
  `resume_brief.md`를 생성하고 다음 세션을 위한 최소 요약을 남긴다.

## 3. 작업공간과 프로젝트 루트 해석 방식

현재 프로젝트 루트는 `core/config_paths.py`에서 결정된다.

1. `AGENT_PROJECT_ROOT`가 있으면 그 절대 경로를 `PROJECT_ROOT`로 사용한다.
2. 없으면 `projects/<AGENT_PROJECT_ID>`를 쓴다.
3. 둘 다 없으면 `projects/default`가 기본 프로젝트가 된다.

이 때문에 같은 코드베이스라도 두 가지 운용 방식이 가능하다.

- 저장소 내부 프로젝트 모드
  `projects/<id>` 아래에 생성된다.
- 외부 워크스페이스 모드
  예: `D:\hoonProJect\worktrees\ai-workflow-course`
  이 경우에도 동일한 런타임이 동작하지만, 프로젝트 산출물은 외부 폴더에 만들어진다.

## 4. 프로젝트 생성 전체 단계

아래는 "새 프로젝트를 만든다"는 요청이 실제로 처리되는 전체 단계다.

### 4.1 부트스트랩 단계
1. 프로세스가 `agent_launcher.py`를 로드한다.
2. import 시점에 `core/project_init.ensure_project_files()`가 호출된다.
3. 프로젝트 기본 파일이 없으면 생성된다.
   `policies.yaml`, `context_schema.yaml`, `skill-lock.yaml`, `dashboard.json`, `workflow.yaml`, `settings.yaml`
4. 현재는 문서 계약 때문에 `docs/architecture.md`, `docs/change_history.md`도 함께 보장된다.

### 4.2 실행 진입 단계
1. `AgentFactory.run(task_input, role_spec, workspace, pipeline_mode)`가 호출된다.
2. `RequestRouter.route()`가 입력을 보고 `single` 또는 `project`를 선택한다.
3. `pipeline_mode="project"`면 강제로 프로젝트 파이프라인으로 들어간다.
4. 힌트 키워드, IntentGate 분류, 역할 정보까지 합쳐 프로젝트 점수를 계산한다.

### 4.3 프로젝트 파이프라인 단계
1. `ProjectPipeline.run()`이 `target_workspace`를 확정한다.
2. `ensure_documentation_files(target_workspace)`로 문서 뼈대를 먼저 만든다.
3. `planning/` 디렉터리를 만든다.
4. Himari bootstrap agent를 만들고 `research_project_brief()`를 호출한다.
5. 결과를 `planning/project_brief.json`에 저장한다.
6. Lilith bootstrap agent를 만들고 `ProjectPlanningDirector.plan()`을 호출한다.
7. 결과를 `planning/role_plan.json`에 저장한다.
8. `.todo.md`를 생성한다.
   여기에는 역할별 todo뿐 아니라 문서화 TODO도 자동 포함된다.
9. `_materialize_roles()`가 각 역할별 `agents/<role>.yaml`을 만든다.
10. 필요 스킬이 있으면 install 또는 build 단계가 붙는다.
11. `DynamicOrchestrator.run_project()`가 각 역할을 병렬 디스패치한다.
12. 결과 보드는 `dashboard.json`, `.af_manifest.json`, `runs/...` 계열에 축적된다.

### 4.4 프로젝트 생성의 산출물
- `planning/project_brief.json`
- `planning/role_plan.json`
- `.todo.md`
- `agents/*.yaml`
- `dashboard.json`
- `runs/*`
- `resume_brief.md`

## 5. 내부 SDK 실행 전체 단계

이 경로는 외부 CLI를 쓰지 않고, `AgentRunner`가 직접 Gemini SDK와 로컬 툴을 붙여 실행하는 경로다.

### 5.1 실행 준비
1. `AgentFactory.run()`이 `single` 경로를 선택한다.
2. `AgentManager.get_or_create()`가 역할에 해당하는 agent yaml을 찾는다.
3. 없으면 글로벌 agent template를 복사하거나, LLM으로 agent spec을 생성한다.
4. `RequirementAnalyzer.analyze()`가 목표, missing skills, constraints, risk level을 추론한다.
5. `load_skills()`가 agent yaml에 선언된 스킬을 파이썬 모듈 또는 markdown knowledge로 로드한다.

### 5.2 툴 레지스트리 구성
1. `ctx`가 만들어진다.
   필수 값은 `agent`, `data_dir`, `artifacts_dir`, `workspace`, `project_id`다.
2. `validate_context_with_schema()`가 `context_schema.yaml` 기준으로 ctx를 검증한다.
3. `ToolRuntimeWrapper.build_registry()`가 스킬 함수들을 툴 함수로 감싼다.
4. `ToolRegistry`가 활성 툴 목록을 보관한다.

이때 툴 함수 이름은 보통 `skillid_funcname` 형태지만, 충돌이 없으면 `write_file`, `read_file`처럼 짧은 이름으로도 마운트된다.

### 5.3 내부 훅 가드레일
1. `HookEventBus`가 만들어진다.
2. 기본 훅 3개가 등록된다.
   `IntentGateHook`, `TodoContinuationEnforcer`, `ToolOutputTruncator`
3. `run_pre_execute()`가 먼저 실행된다.
4. 요청이 모호하거나, 복잡 작업인데 `.todo.md`나 board state가 없으면 실행이 차단된다.

### 5.4 모델과 시스템 프롬프트 조립
1. 복잡도 분류가 수행된다.
   특정 역할은 강제로 complex 취급되고, 일반 역할은 분류기를 통해 simple/complex를 나눈다.
2. `ModelRouter.pick("chat", ...)`가 모델을 선택한다.
3. 시스템 프롬프트는 다음 순서로 조립된다.
   agent yaml의 `system_ko`
   문서 계약 주입
   knowledge skill prompt
   `core_memory` 안내문
   signature line

즉, 시스템 프롬프트는 단순히 agent yaml 문자열 하나가 아니라, 런타임이 추가 문맥을 덧붙여 만든 합성 결과물이다.

### 5.5 Gemini SDK ReAct 루프
1. CLI provider가 설정되지 않았고 `GOOGLE_API_KEY`가 있으면 Gemini SDK 경로로 들어간다.
2. `genai.Client`와 chat session이 생성된다.
3. `system_instruction=sys_prompt`, `tools=tool_functions`가 모델에 전달된다.
4. 첫 사용자 입력은 `Task: {task_input}` 형태로 전송된다.
5. 응답에서 text part가 나오면 그대로 출력/trace 된다.
6. function_call이 나오면 툴 실행 루프로 들어간다.

### 5.6 툴 호출과 코드 수정 적용
1. 모델이 함수 호출을 요청한다.
2. policy approval과 hook pre-tool 검사를 거친다.
3. 허용되면 실제 파이썬 함수가 호출된다.
4. 결과는 post-tool hook과 post-execute hook을 거쳐 다시 모델로 반환된다.

파일 수정은 이 지점에서 실제 반영된다.

- `skills/core/file_handler.py`
  `read_file`, `write_file`, `list_files`
- `skills/hash_edit/skill.py`
  `get_file_with_hashes`, `apply_edit`, `apply_block_edit`

중요한 점은 내부 SDK 경로에서는 "코드 수정"이 곧 "로컬 스킬 함수 실행"이라는 점이다.
즉, 모델이 직접 디스크를 만지는 게 아니라, Tool Registry에 마운트된 파이썬 함수가 워크스페이스 기준으로 파일을 읽고 쓴다.

## 6. 외부 CLI 세션 실행 전체 단계

이 경로는 `AGENT_CHAT_PROVIDER`가 설정됐을 때 우선된다.
예를 들어 `gemini_cli`, `claude_cli`, `codex_cli`가 여기에 해당한다.

### 6.1 분기 조건
1. `AgentRunner`는 `get_requested_cli_providers(os.getenv("AGENT_CHAT_PROVIDER"))`를 읽는다.
2. 하나 이상 있으면 `_run_with_cli_provider()`를 먼저 시도한다.
3. 성공한 CLI provider가 있으면 내부 SDK 경로로 내려가지 않고 그 결과를 바로 채택한다.

즉, 현재 구조는 "CLI 우선, SDK 후순위 fallback"에 가깝다.

### 6.2 CLI 실행 준비
1. `execute_cli_chat()`가 호출된다.
2. `prepare_cli_session()`이 실행된다.
3. 여기서 `.af_runtime/cli_sessions/` 아래 state json과 events jsonl 경로가 생성된다.
4. provider별 세션 사양이 적용된다.

provider별 차이:
- `claude_cli`
  `.claude/settings.local.json`에 native hook을 심는다.
  승인 프롬프트를 없애기 위해 `--permission-mode bypassPermissions`로 시작한다.
- `gemini_cli`
  `.af_runtime/cli_sessions/*_gemini_defaults.json`를 만들고 `GEMINI_CLI_SYSTEM_DEFAULTS_PATH`로 넘긴다.
  승인 프롬프트를 없애기 위해 `--sandbox --approval-mode yolo`로 시작한다.
- `codex_cli`
  wrapper bridge 모드이며 종료 시 `run_bridge()`를 통해 transcript를 메모리 쪽으로 넘긴다.
  승인 프롬프트를 막기 위해 top-level에서 `--ask-for-approval never --sandbox workspace-write`를 먼저 건 뒤 `exec`를 호출한다.

### 6.3 CLI 환경변수 정리
`_build_cli_env()`에서 provider별 인증 방식이 정리된다.

- `claude_cli`
  `ANTHROPIC_API_KEY` 제거
- `codex_cli`
  `OPENAI_API_KEY` 제거
- `gemini_cli`
  `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI` 제거
  `GOOGLE_GENAI_USE_GCA=true` 강제

즉, Gemini CLI는 현재 설계상 API Key 경로보다 Google 계정 기반 CLI 인증 경로를 우선 사용하도록 맞춰져 있다.

### 6.4 CLI 프롬프트 입력 조립
`core/providers/cli.py`의 `_compose_prompt()`가 provider별 prompt 포맷을 만든다.

Gemini CLI는 다음 형식이다.

```text
Task: ...
Return the final answer directly. Do not inspect files or use tools unless the task explicitly requires it.

Workspace: ...

System instructions:
...
```

combine_system_prompt 방식의 provider는 아래처럼 합쳐진다.

```text
[System Prompt]
...

[Workspace]
...

[Task]
...
```

즉, 프롬프트 입력은 최소 3층으로 나뉜다.
- task_input
- workspace 힌트
- runtime에서 조립한 system prompt

### 6.5 CLI 명령 구성
`build_cli_command()`가 다음을 붙인다.

- 모델 플래그
- headless edit 플래그
- workspace access 플래그
- output format 플래그
- prompt 문자열

예를 들어 Gemini CLI는 `--output-format json`, `--sandbox`, `--approval-mode yolo`, `--include-directories ...`가 붙는다.

### 6.6 CLI 실행과 결과 수집
1. subprocess가 실제 CLI를 실행한다.
2. stdout은 `_extract_text()`로 파싱된다.
3. 성공이면 `result["text"]`가 AgentRunner로 돌아간다.
4. 실패면 auto-install 또는 fallback 경로가 붙는다.

중요한 점은 외부 CLI 경로에서는 실제 파일 수정이 내부 Tool Registry를 통하지 않는다는 점이다.
파일 수정은 Claude/Gemini/Codex CLI가 가진 네이티브 편집 툴이 워크스페이스에서 직접 수행한다.
이 저장소는 그 세션을 감싸고, 상태와 훅과 continuity를 붙이는 역할을 한다.

## 7. Hook과 Session 구조

현재 훅 구조도 2층이다.

### 7.1 내부 훅 버스
내부 SDK 경로에서는 `HookEventBus`가 직접 함수 호출 lifecycle을 제어한다.

- `pre_execute`
  실행 전 요청 차단 가능
- `pre_tool_call`
  툴 호출 직전 차단/인자 수정 가능
- `post_tool_call`
  툴 결과 후처리 가능
- `post_execute`
  최종 결과 후처리 가능

기본 훅:
- `IntentGateHook`
  너무 모호한 요청 차단
- `TodoContinuationEnforcer`
  복잡 작업에 `.todo.md` 또는 board state 요구
- `ToolOutputTruncator`
  큰 stdout을 잘라서 컨텍스트 오염 방지

### 7.2 외부 CLI native hook 브리지
외부 CLI 경로에서는 provider 자체 hook event를 `cli_hook_bridge.py`가 받아서 `session_adapter.handle_hook_event()`로 전달한다.

이때 provider별 이벤트는 다음과 같다.

- Claude CLI
  `SessionStart`, `UserPromptSubmit`, `PreCompact`, `Stop`, `SessionEnd`
- Gemini CLI
  `SessionStart`, `BeforeAgent`, `AfterAgent`, `PreCompress`, `SessionEnd`
- Codex CLI
  native hook 대신 wrapper bridge 방식

### 7.3 Hook 이벤트 처리 단계
1. hook payload가 stdin으로 들어온다.
2. `handle_hook_event()`가 payload를 읽는다.
3. `.af_runtime/cli_sessions/*_events.jsonl`에 이벤트를 append한다.
4. session state json을 갱신한다.
5. 필요 시 `run_bridge()`를 호출해 transcript를 메모리 브리지로 넘긴다.
6. `resume_brief.md`를 다시 쓴다.
7. continuity context를 계산해 provider hook output으로 반환한다.

### 7.4 continuity context에 들어가는 것
`_build_continuity_context()`는 다음을 합쳐 provider로 되돌린다.

- provider / run_id / workspace
- `.af_manifest.json`의 state_board
- `.todo.md`의 open todos
- 마지막 CLI session state의 last response excerpt
- `resume_brief.md` excerpt

즉, hook의 목적은 "이벤트를 로그로 남긴다"보다 "다음 턴이 이어서 생각할 수 있게 continuity context를 계속 주입한다"에 더 가깝다.

## 8. 프롬프트 입력과 출력이 적용되는 정확한 위치

### 8.1 입력이 흘러가는 경로
사용자 입력 `task_input`은 여러 단계에서 재사용된다.

1. `AgentFactory.run()`
   최초 raw task
2. `RequestRouter.route()`
   single/project 분기 판단
3. `RequirementAnalyzer.analyze()`
   missing skill과 risk 추론용 prompt
4. `HimariResearchAgent.research_project_brief()`
   프로젝트 브리프 생성용 prompt
5. `ProjectPlanningDirector.plan()`
   역할 계획용 prompt
6. `AgentRunner`
   실제 실행용 task
7. `CLI _compose_prompt()` 또는 `Gemini SDK safe_send()`
   최종 엔진 입력

즉, 사용자의 한 줄 입력은 단 한 번만 쓰이지 않는다.
분기, 계획, 역할 생성, 실제 실행에서 서로 다른 프롬프트로 재해석된다.

### 8.2 시스템 프롬프트가 구성되는 순서
실행용 시스템 프롬프트는 아래 순서로 누적된다.

1. agent yaml의 `system_ko`
2. 문서 계약
3. knowledge skill prompt
4. `core_memory` 안내문
5. signature line

즉, 실행 시점의 실제 system prompt는 정적 파일이 아니라 동적 합성물이다.

### 8.3 출력이 흘러가는 경로

내부 SDK 경로:
1. assistant text가 console과 trace에 찍힌다.
2. function_call 결과는 tool result로 다시 모델에 반환된다.
3. 최종 result dict가 `ok`, `reason`, `latency_ms`, `approval_rejects`로 정리된다.

CLI 경로:
1. CLI stdout이 JSON 또는 text로 나온다.
2. `_extract_text()`가 최종 텍스트를 추출한다.
3. `finalize_cli_session()`이 last response excerpt를 state json에 기록한다.
4. `resume_brief.md`가 갱신된다.
5. `AgentRunner`는 provider id를 `reason`으로 한 result dict를 반환한다.

## 9. 코드 수정이 실제로 반영되는 방식

### 9.1 내부 SDK 경로
코드 수정은 파이썬 툴 함수가 한다.

대표 경로:
- `file_handler.write_file`
  전체 파일 내용을 새로 써서 저장
- `hash_edit.apply_edit`
  해시 앵커 기반 부분 수정

이 툴 함수들은 `ctx["workspace"]` 기준 상대경로를 해석하므로, 같은 에이전트 로직도 workspace만 바꾸면 다른 프로젝트에 그대로 적용된다.

### 9.2 외부 CLI 경로
코드 수정은 외부 CLI의 네이티브 edit tool이 직접 수행한다.

예를 들어 Gemini CLI는 실제 실행 결과에 `write_file`, `replace` 같은 툴 사용 통계를 남길 수 있다.
하지만 그 편집 자체는 이 저장소의 `ToolRegistry`가 수행한 것이 아니라 Gemini CLI 프로세스 내부에서 수행된 것이다.

이 저장소가 담당하는 것은 아래다.
- workspace 접근 범위 설정
- system prompt 전달
- hook 설정
- continuity context 제공
- 세션 결과/상태 기록

## 10. 저장 아티팩트 정리

### 프로젝트 기본 파일
- `policies.yaml`
- `context_schema.yaml`
- `skill-lock.yaml`
- `dashboard.json`
- `workflow.yaml`
- `settings.yaml`

### 계획/문서 파일
- `planning/project_brief.json`
- `planning/role_plan.json`
- `.todo.md`
- `docs/architecture.md`
- `docs/change_history.md`

### 세션/런타임 파일
- `.af_runtime/cli_sessions/*.json`
- `.af_runtime/cli_sessions/*_events.jsonl`
- `.af_runtime/cli_sessions/*_gemini_defaults.json`
- `resume_brief.md`

### 실행 기록
- `runs/*/chat_trace.json`
- `runs/*/state.json`
- `.af_manifest.json`

## 11. 한 요청을 추적하는 실전 절차

현재 시스템을 디버깅하거나 구조를 따라가려면 아래 순서가 가장 빠르다.

1. 현재 프로젝트 루트를 먼저 확인한다.
   `AGENT_PROJECT_ROOT`, `AGENT_PROJECT_ID`, `PROJECT_ROOT`
2. 실행 모드를 본다.
   `AGENT_CHAT_PROVIDER`가 있으면 CLI 경로를 먼저 의심한다.
3. 요청이 `single`인지 `project`인지 확인한다.
   `RequestRouter.route()`
4. 프로젝트 생성이면 `planning/project_brief.json`, `planning/role_plan.json`, `.todo.md`를 먼저 본다.
5. 단일 실행이면 `AgentRunner`의
   스킬 로드
   툴 레지스트리
   훅 버스
   system prompt 조립
   provider 분기
   순으로 본다.
6. CLI 세션이면 `.af_runtime/cli_sessions/*.json`과 `*_events.jsonl`을 본다.
7. continuity 이슈면 `resume_brief.md`와 `session_adapter._build_continuity_context()`를 본다.
8. 파일 수정이 왜 안 됐는지 보려면
   내부 SDK 경로면 mounted tool 함수와 policy/hook 차단을 본다.
   CLI 경로면 CLI 자체가 어떤 tool을 썼는지와 workspace access flag를 본다.

## 12. 운영상 중요한 해석 포인트

1. 프로젝트 생성은 단순 디렉터리 생성이 아니다.
   리서치 브리프, 역할 계획, todo, 역할 yaml, orchestrator까지 포함한 파이프라인이다.

2. hook은 한 종류가 아니다.
   내부 함수 훅과 외부 provider session hook이 공존한다.

3. prompt는 한 번 만들어져 엔진에 들어가는 게 아니다.
   분석, 계획, 실행 단계마다 다른 prompt로 재구성된다.

4. 코드 수정 경로도 한 종류가 아니다.
   내부 SDK는 로컬 툴 함수 편집,
   외부 CLI는 provider 네이티브 툴 편집이다.

5. continuity는 부가 기능이 아니라 세션 구조의 핵심이다.
   `.todo.md`, `.af_manifest.json`, `resume_brief.md`, hook events, transcript bridge가 함께 동작한다.

## Documentation Rule
- 설계가 바뀌면 이 문서를 같은 작업에서 갱신한다.
- 같은 변경에 대한 요약을 `docs/change_history.md`에 append한 뒤 작업을 닫는다.

## 13. Destructive Action Guard

삭제/리셋 금지 가드는 단일 계층이 아니라 공용 프롬프트 계약과 provider별 네이티브 정책을 함께 사용한다.

1. 공용 계약
   `core/destructive_guard.py` 의 `inject_destructive_guard_contract()` 가 런타임 시스템 프롬프트에 금지 규칙을 주입한다.
   이 계약은 `AgentRunner` 경로, CLI provider 경로, Codex fallback 경로에 공통으로 적용된다.
2. Claude CLI hard deny
   `core/providers/session_adapter.py` 가 `.claude/settings.local.json` 에 `permissions.deny` 규칙을 병합한다.
   금지 대상은 `rm`, `del`, `erase`, `Remove-Item`, `git clean`, `git reset --hard`, `git checkout --`, `git restore --worktree`, `git restore --staged` 계열이다.
3. Gemini CLI hard deny
   같은 세션 준비 단계에서 `.af_runtime/cli_sessions/*_destructive_guard.toml` 정책 파일을 생성한다.
   생성된 정책 파일은 `GEMINI_CLI_SYSTEM_DEFAULTS_PATH` 로 전달되는 defaults JSON의 `policyPaths` 에 연결된다.
   정책 엔진은 `run_shell_command` 에 대해 command prefix 와 args pattern 을 기준으로 delete/reset 계열 명령을 거부한다.
4. Codex CLI shell proxy
   현재 설치된 Codex CLI 기준으로는 Claude/Gemini 수준의 per-command deny 설정이 확인되지 않았다.
   대신 `prepare_cli_session()` 이 Windows 에서 `.af_runtime/cli_sessions/*_shell_guard/` 아래 guard wrapper 를 생성한다.
   이때 `COMSPEC` 은 guard `cmd.cmd` 로 교체되고, `PATH` 와 `PATHEXT` 도 wrapper 우선 순서로 조정된다.
   결과적으로 Codex 가 shell 문자열을 실행할 때는 guard `cmd` proxy 가 먼저 개입해 `git reset --hard`, `git clean`, `Remove-Item` 같은 파괴 명령을 차단한다.
   추가로 `git.cmd`, `powershell.cmd`, `pwsh.cmd` wrapper 도 생성되어 PATH 기반 해석 경로에서 한 번 더 막는다.
   공용 시스템 프롬프트 계약도 그대로 유지되므로 Codex 는 shell proxy + prompt contract 의 이중 가드 구조가 된다.
5. 관찰 지점
   세션 준비 결과는 `.af_runtime/cli_sessions/*.json` 에 기록된다.
   Gemini 는 `guard_path`, Codex 는 `guard_dir`, Claude/Gemini 는 `destructive_guard_mode=native_deny_rules`, Codex 는 `destructive_guard_mode=shell_proxy_and_system_prompt_contract` 로 남는다.
