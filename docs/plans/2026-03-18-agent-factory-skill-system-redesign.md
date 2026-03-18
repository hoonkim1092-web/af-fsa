# Agent Factory 스킬 시스템 V2 설계안

> **구현 시 참고:** 이 설계안을 실제 구현할 때는 `superpowers:executing-plans` 흐름으로 작업 단위를 쪼개 실행하는 것을 권장한다.

**목표:** `agent-factory`의 스킬 시스템을 선언형 메타데이터, 명시적 실행 정책, 팀 공유 수명주기, 멀티에이전트 오케스트레이션 친화 구조를 갖춘 V2로 재설계한다.

**아키텍처:** 현재의 `registry -> loader -> runner -> orchestrator` 흐름은 유지하되, 스킬의 의미를 코드 내부 규칙이 아니라 정규화된 스펙으로 끌어올린다. `Claude Code`에서 선언형 skill spec과 invocation policy를, `OpenAI Codex`에서 공유/운영 수명주기를 가져오고, `agent-factory`의 role-based orchestration과 provider-agnostic runtime은 유지한다.

**기술 스택:** Python, YAML, Markdown, existing `SkillMetadata`/`SkillRegistry`/`AdaptiveSkillLoader`/`ProjectPipeline`/`DynamicOrchestrator`

---

## 1. 문제 정의

현재 구조는 이미 강한 토대를 갖고 있다.

- `SkillRegistry`는 프로젝트 스킬, 전역 스킬, 사용자 전역 스킬 루트를 병합한다.
- `AdaptiveSkillLoader`는 task input 기반 자동 선택, 의존성 해소, conflict 해소를 수행한다.
- `AgentRunner`는 action skill과 knowledge skill을 모두 로드한다.
- `ProjectPipeline`은 role plan의 `required_skills`를 materialize 과정에 반영한다.

관련 코드:

- [`core/skill_registry.py`](../../core/skill_registry.py)
- [`core/skill_loader.py`](../../core/skill_loader.py)
- [`core/agent_runner.py`](../../core/agent_runner.py)
- [`core/project_pipeline.py`](../../core/project_pipeline.py)

하지만 스킬 시스템을 제품 수준으로 끌어올리기에는 네 가지 공백이 있다.

1. 스킬 스펙이 충분히 선언적이지 않다.
2. 스킬 호출 정책이 runner와 runtime rule에 분산돼 있다.
3. 역할별 오케스트레이션과 스킬 계약이 느슨하게 연결돼 있다.
4. 팀 공유, 승격, 잠금, provenance를 다루는 운영 수명주기가 약하다.

## 2. 설계 목표

이번 V2 설계는 아래 목표를 가진다.

1. 하위 호환 유지
기존 `skills/`, project skill roots, Codex skill roots, agent YAML의 `skills` 배열을 계속 지원한다.

2. 선언형 스킬 계약 도입
스킬이 무엇인지, 언제 쓰는지, 누가 자동 호출 가능한지, 어떤 tool과 context를 요구하는지 스펙으로 표현한다.

3. 역할-스킬 결합 명확화
`role_plan -> skill requirement -> runtime resolution -> execution evidence` 흐름을 명시화한다.

4. 공유와 재현성 강화
어느 source에서 어떤 버전의 스킬을 불러왔는지 기록하고, 실행 단위마다 잠금 결과를 남긴다.

5. 점진적 마이그레이션
한 번에 기존 스킬 디렉터리를 깨지 않고, metadata adapter를 통해 점진적으로 V2 spec으로 이행한다.

## 3. 목표 모델 개요

V2의 핵심은 세 가지다.

1. `SkillSpec`
모든 스킬 소스를 정규화하는 표준 문서 모델

2. `SkillPolicy`
호출/도구/승인/격리 규칙을 담는 실행 정책 모델

3. `SkillResolution`
특정 run에서 어떤 이유로 어떤 스킬이 선택되고 제외됐는지 기록하는 결과 모델

즉, V2에서는 "스킬 파일이 있다"가 아니라 "스킬 계약이 있고, 정책이 있고, 선택 결과가 남는다"로 바뀐다.

## 4. Skill Package V2

권장 디렉터리 구조는 아래와 같다.

```text
skills/
  <skill_id>/
    skill.yaml
    SKILL.md
    tools/
      *.py
    resources/
      ...
    tests/
      ...
```

하위 호환을 위해 다음도 계속 허용한다.

- 기존 Python 단일 파일 action skill
- 기존 markdown knowledge skill
- 기존 `meta.yaml` 기반 번들
- 외부 root에서 로드되는 Codex/Claude 계열 skill 디렉터리

다만 로드 시점에는 모두 `SkillSpec`으로 변환한다.

## 5. SkillSpec 스키마

`skill.yaml`의 최소 스키마는 아래를 권장한다.

```yaml
id: file_ops
name: File Operations
version: 2
kind: action
description: 파일 생성, 읽기, 수정 도구 모음
when_to_use:
  - 파일 수정이 필요할 때
when_not_to_use:
  - 단순 질문 응답만 필요할 때
entrypoints:
  - tools/write_file.py
  - tools/read_file.py
dependencies:
  - path_utils
incompatible_with:
  - raw_shell_edit
invocation:
  auto: true
  user_invocable: true
  planner_invocable: true
  context_mode: inline
policy:
  allowed_tools:
    - read_file
    - write_file
  approval_required_tools:
    - delete_file
  destructive: false
compatibility:
  providers:
    - claude_cli
    - codex_cli
    - gemini_cli
  roles:
    - backend
    - architect
distribution:
  visibility: project
  maturity: stable
  source: project
observability:
  emit_usage_event: true
```

핵심 필드 의미:

- `kind`: `action | knowledge | bundle | hybrid`
- `invocation.context_mode`: `inline | fork | isolated`
- `policy.allowed_tools`: skill이 정상적으로 사용할 수 있는 canonical tool 목록
- `compatibility.roles`: 어떤 role에 자연스럽게 붙는지
- `distribution`: 공유/승격/안정성 상태

## 6. SkillPolicy 모델

현재는 `AgentRunner._build_policy()`와 runtime rule이 로컬 로직으로 섞여 있다. V2에서는 스킬 정책을 별도 모델로 끌어낸다.

```python
class SkillPolicy(TypedDict):
    skill_id: str
    allowed_tools: list[str]
    approval_required_tools: list[str]
    context_mode: str
    auto_invocable: bool
    planner_invocable: bool
    destructive: bool
```

정책 병합 우선순위는 아래로 한다.

1. workspace `policies.yaml`
2. role runtime rules
3. skill spec policy
4. runner safe baseline

이 순서를 택하는 이유는 프로젝트 안전정책이 스킬 패키지보다 우선해야 하기 때문이다.

## 7. Skill Resolution 파이프라인

V2의 resolution flow는 아래 순서로 고정한다.

1. Source discovery
`SkillRegistry`가 project/global/user-wide/external roots를 스캔한다.

2. Metadata normalization
각 스킬 소스를 `SkillSpec`으로 정규화한다.

3. Candidate filtering
provider, role, execution mode, workspace policy로 1차 필터링한다.

4. Intent-based selection
`AdaptiveSkillLoader`가 task input과 role plan을 함께 사용해 후보를 점수화한다.

5. Dependency/conflict resolution
기존 dependency graph와 incompatibility 처리 로직을 유지하되, V2 spec 기준으로 계산한다.

6. Policy merge
role/runtime/project rule을 병합해 최종 `SkillPolicy`를 만든다.

7. Lock and record
실행에 들어간 실제 스킬 목록, source, version, 제외 사유를 `skill-lock.yaml`과 `skill-resolution.json`에 남긴다.

## 8. 호출 경로 설계

V2에서는 스킬 호출 경로를 네 가지로 구분한다.

### 8.1 Explicit Agent Skills

agent YAML에 명시된 `skills`는 최우선 명시 요구사항이다.

용도:

- 반드시 필요한 core skill
- role별 baseline capability

### 8.2 Planned Skills

`ProjectPipeline`의 `role_plan.required_skills`는 planner가 요구한 스킬이다.

용도:

- 프로젝트별 임무에 따른 capability 주입
- build/install 대상 추론

### 8.3 Auto-selected Skills

명시 skill이 부족한 경우 `AdaptiveSkillLoader`가 task 기반으로 자동 선택한다.

용도:

- 일반 작업 자동 보강
- 지식형 skill 자동 주입

### 8.4 Delegated/Forked Skills

`context_mode=fork|isolated`인 스킬은 동일 runner inline 실행이 아니라 delegated execution 경로로 보낸다.

용도:

- 장문 분석
- 고위험 수정
- 독립 context가 필요한 subtask

이 경로는 장기적으로 `subagent skill execution`의 핵심이 된다.

## 9. 오케스트레이션 연결

현재 `ProjectPipeline -> DynamicOrchestrator -> AgentRunner` 흐름은 유지한다.  
대신 각 단계가 스킬 시스템과 맺는 계약을 명확히 바꾼다.

### 9.1 ProjectPipeline

`role_plan` 항목에 아래 필드를 추가한다.

```json
{
  "required_skills": ["repo_reader"],
  "preferred_skills": ["python_test_writer"],
  "forbidden_skills": ["raw_shell_edit"],
  "context_mode": "inline",
  "skill_goal": "API 경로 수정 + 회귀 테스트 추가"
}
```

`ProjectPipeline._materialize_roles()`는 이 정보를 role artifact와 install plan 양쪽에 반영한다.

### 9.2 DynamicOrchestrator

오케스트레이터 board에 아래 필드를 추가한다.

- `resolved_skills`
- `missing_skills`
- `skill_resolution_artifact`
- `delegated_skills`

이렇게 하면 role 재개 시에도 어떤 스킬 구성이 있었는지 잃지 않는다.

### 9.3 AgentRunner

`load_skills()`는 더 이상 단순 import 함수가 아니라 아래 산출물을 만드는 단계가 된다.

- loaded runtime modules
- loaded knowledge prompts
- `SkillResolution`
- merged `SkillPolicy`

즉 `AgentRunner`는 "스킬을 불러온다"가 아니라 "스킬 실행 컨텍스트를 조립한다" 쪽으로 책임이 올라간다.

## 10. 아티팩트 설계

기존 `skill-lock.yaml`을 유지하되 의미를 강화한다.

### 10.1 skill-lock.yaml

역할:

- run에 실제 들어간 스킬의 최종 잠금 결과
- source, version, provenance 기록

예시:

```yaml
run_id: run_123
role_id: backend_dev
skills:
  - id: repo_reader
    version: 2
    source: project
    root: D:/hoonProJect/worktrees/agent-factory/skills/repo_reader
    selected_by: planner
  - id: python_test_writer
    version: 1
    source: user
    root: C:/Users/HOON/.codex/skills/python_test_writer
    selected_by: auto_loader
```

### 10.2 skill-resolution.json

역할:

- 왜 선택됐는지, 왜 제외됐는지 설명하는 디버그 아티팩트

기록 필드:

- candidate scores
- filtered out reason
- dependency additions
- conflict removals
- policy merge summary

### 10.3 skill-usage.jsonl

역할:

- 런타임 중 실제 어떤 tool/knowledge skill이 사용됐는지 추적

이 파일은 향후 skill ranking, pruning, promotion 자동화의 입력이 된다.

## 11. 공유와 수명주기

V2는 스킬 공유를 네 단계 상태로 본다.

1. `draft`
로컬 실험용

2. `project`
프로젝트 공유 가능

3. `shared`
사용자 전역 또는 팀 공유 가능

4. `external`
가져온 외부 스킬

추천 규칙:

- `draft -> project` 승격은 로컬 테스트 통과가 조건
- `project -> shared` 승격은 usage evidence와 owner 지정이 조건
- `external`은 provenance와 checksum을 기록해야 함

이 모델은 Codex의 팀 운영 수명주기에서 가져온 개념이다.

## 12. 메타데이터 어댑터 전략

기존 자산을 깨지 않기 위해 adapter 계층을 둔다.

### 12.1 Legacy Python Skill Adapter

대상:

- 단일 `.py` 파일 skill
- `@skill_metadata`가 있거나 없는 기존 action skill

전략:

- 메타데이터가 있으면 그대로 사용
- 없으면 파일명, docstring, 경로 기반 기본 `SkillSpec` 생성

### 12.2 Markdown Knowledge Adapter

대상:

- `skill.md`, `SKILL.md` 기반 지식 스킬

전략:

- frontmatter가 있으면 반영
- 없으면 제목과 요약으로 기본 메타 생성

### 12.3 Bundle Adapter

대상:

- `meta.yaml` + `sub_skills`

전략:

- 기존 `convert_yaml_config_to_metadata()` 흐름을 확장해 `SkillSpec`으로 변환

이 adapter 전략 덕분에 V2는 "새 형식만 지원"이 아니라 "기존 형식을 정규화해서 함께 지원"하는 방식이 된다.

## 13. 권한 및 안전성 모델

`Claude Code`에서 가져와야 할 핵심은 "스킬 단위 정책의 명시성"이다.  
V2에서는 아래 원칙을 둔다.

1. skill은 자기 allowed tool 집합을 가진다.
2. project policy는 skill policy보다 우선한다.
3. destructive operation은 skill이 아니라 workspace rule이 최종 승인한다.
4. `context_mode=fork|isolated`는 고위험 작업을 격리 실행 경로로 밀어낸다.

이렇게 해야 policy가 runner 내부 구현 디테일이 아니라 검토 가능한 문서 계약이 된다.

## 14. 관측성 설계

V2에서는 스킬 시스템도 관측 대상이어야 한다.

필수 이벤트:

- `skill_discovered`
- `skill_selected`
- `skill_rejected`
- `skill_loaded`
- `skill_tool_invoked`
- `skill_policy_blocked`

이 이벤트는 기존 hook/event bus와 연결해 남긴다.  
장기적으로는 memory 시스템과 연결해 "어떤 프로젝트에서 어떤 skill 조합이 성공했는가"를 학습할 수 있다.

## 15. 단계별 롤아웃

### Phase 1. Canonical SkillSpec 도입

- `core/skill_spec.py` 추가
- 기존 metadata adapter를 `SkillSpec` 반환으로 확장
- `SkillRegistry`는 registry 내부 저장 타입을 canonical spec으로 전환

### Phase 2. Resolution Artifact 도입

- `SkillResolution` 모델 추가
- `AgentRunner.load_skills()`가 resolution 결과를 반환하도록 변경
- `skill-lock.yaml`, `skill-resolution.json` 기록 추가

### Phase 3. Policy 모델 분리

- `SkillPolicy` 도입
- `_build_policy()`를 skill-aware 병합 함수로 분리
- role/runtime/workspace rule 우선순위 정리

### Phase 4. Role-Orchestrator Integration

- `role_plan`에 `preferred_skills`, `forbidden_skills`, `context_mode`, `skill_goal` 추가
- orchestrator board에 resolved skill state 추가

### Phase 5. Shared Lifecycle

- maturity/provenance/promotion 규칙 도입
- external imported skill provenance 기록

### Phase 6. Delegated Skill Execution

- `context_mode=fork|isolated` 스킬을 subagent 또는 별도 실행 경로로 분기
- 장기적으로 subagent skill execution으로 확장

## 16. 테스트 전략

필수 테스트는 아래다.

1. Registry normalization test
기존 python skill, markdown skill, bundle skill이 모두 동일한 `SkillSpec`으로 정규화되는지 검증

2. Policy precedence test
workspace policy가 skill policy를 덮고, role runtime rule이 그 다음 우선순위를 갖는지 검증

3. Resolution artifact test
선택/제외/의존성/충돌 정보가 `skill-resolution.json`에 기록되는지 검증

4. Planner-to-runner contract test
`required_skills`, `preferred_skills`, `forbidden_skills`가 실제 로딩 결과에 반영되는지 검증

5. Delegated skill routing test
`context_mode=fork|isolated` 스킬이 inline 실행되지 않고 분기되는지 검증

## 17. 비목표

이번 설계에서 바로 하지 않는 것:

- 모든 기존 스킬을 한 번에 새 포맷으로 마이그레이션
- 외부 skill marketplace 구축
- memory system 전면 개편
- UI 기반 skill browser 구축

이 항목들은 V2 기반이 안정화된 뒤 별도 단계로 분리하는 것이 맞다.

## 18. 차용 출처

이 설계안은 특정 제품을 그대로 복제하려는 문서가 아니다.  
`agent-factory`의 기존 강점을 유지하면서, 외부 제품의 강한 설계 요소만 선택적으로 가져오는 것이 목적이다.

### 18.1 Claude Code에서 차용한 요소

1. 선언형 스킬 스펙
`SkillSpec`, `skill.yaml`, `SKILL.md` 중심 구조는 Claude Code의 문서형 skill 모델에서 가져온 발상이다.  
핵심은 스킬을 "코드 파일"이 아니라 "검토 가능한 계약"으로 다룬다는 점이다.

2. 명시적 호출 정책
`invocation.auto`, `user_invocable`, `planner_invocable`, `context_mode` 같은 필드는 Claude Code의 호출 제어 개념을 반영한다.  
누가 호출할 수 있는지와 자동 호출 가능 여부를 명시적으로 드러내기 위한 차용이다.

3. 스킬 단위 권한 경계
`allowed_tools`, `approval_required_tools`, `destructive` 같은 정책 필드는 Claude Code의 skill-level control에서 가져왔다.  
현재 `agent-factory`에 흩어진 제어 로직을 스킬 계약 차원으로 끌어올리기 위한 선택이다.

4. forked context 개념
`context_mode=inline|fork|isolated`는 Claude Code의 subagent/forked context 감각을 차용한 것이다.  
긴 작업, 위험한 수정, 독립 문맥이 필요한 작업을 같은 실행 루프에 억지로 밀어 넣지 않기 위한 설계다.

### 18.2 OpenAI Codex에서 차용한 요소

1. 스킬을 운영 가능한 작업 패키지로 보는 관점
`tools/`, `resources/`, `tests/`를 포함한 skill package 구조는 Codex의 workflow bundle 관점을 반영한다.  
단순 지침이 아니라 반복 가능한 작업 단위로 스킬을 다루기 위한 차용이다.

2. 팀 공유 수명주기
`draft -> project -> shared -> external` 상태 모델은 Codex의 팀 운영형 skill lifecycle에서 가져왔다.  
로컬 실험용 스킬과 팀 공용 스킬을 같은 수준에서 섞어 쓰지 않기 위한 장치다.

3. provenance와 잠금 아티팩트
`skill-lock.yaml`, `skill-resolution.json`은 Codex식 운영 안정성에서 가져온 요소다.  
어떤 스킬이 어디서 왔고 왜 선택됐는지 남겨, 재현성과 디버깅 가능성을 확보하기 위한 것이다.

4. usage evidence 기반 운영
`skill-usage.jsonl` 같은 usage artifact는 Codex식 제품 운영 관점을 반영한다.  
나중에 ranking, pruning, promotion 같은 운영 결정을 데이터로 하려는 목적이다.

### 18.3 agent-factory가 그대로 유지하는 요소

이 설계안은 외부 개념을 가져오되, 아래 핵심은 유지한다.

- role-based orchestration
- multi-agent parallel dispatch
- provider-agnostic runtime
- planner -> runner 연결 구조
- project/global/user-wide skill root 병합 전략

즉 이 설계안은 `Claude Code`나 `Codex`의 복제품이 아니라, `agent-factory`의 멀티에이전트 운영 구조 위에 외부 장점을 접목한 하이브리드 설계다.

## 19. 최종 판단

이 설계의 핵심은 단순하다.

- `Claude Code`식 선언형 spec을 가져온다
- `Codex`식 공유/운영 수명주기를 가져온다
- `agent-factory`의 역할 기반 멀티에이전트 오케스트레이션은 유지한다

즉 V2의 목표는 "더 많은 스킬"이 아니라 "더 설명 가능하고, 더 재현 가능하고, 더 운영 가능한 스킬 시스템"이다.



