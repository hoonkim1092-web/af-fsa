# Agent Factory에 GSD 품질 루프를 흡수하는 구조 설계

기준일: 2026-03-26

이 문서는 `Agent Factory` 전체 구조를 기준으로, `GSD(Get Shit Done)`에서 어떤 요소를 흡수해야 하는지와 그 흡수 순서를 정리한 설계 문서다. 결론부터 말하면, GSD를 통째로 이식하는 방식은 맞지 않는다. `Agent Factory`의 강점인 역할 기반 오케스트레이션, materialized agent, continuity는 유지하고, 그 위에 `phase/checker/verifier/UAT` 중심의 품질 프로토콜을 얹는 방식이 맞다.

## 1. 목표

이번 흡수 작업의 목표는 아래 다섯 가지다.

1. 계획 품질을 실행 전에 한 번 더 검증한다.
2. 실행 후 결과를 단순 완료가 아니라 goal 기준으로 검증한다.
3. 사람 검증을 일회성 메모가 아니라 1급 산출물로 남긴다.
4. 검증 실패 시 재계획 루프를 표준화한다.
5. 이 모든 과정을 기존 `Agent Factory` 구조를 깨지 않고 흡수한다.

## 2. 현재 Agent Factory 구조 요약

현재 구조는 크게 네 층으로 나뉜다.

### 2.1 준비층

핵심 파일:

- `core/project_pipeline.py`
- `core/bootstrap_roles.py`
- `core/project_task_board.py`
- `core/work_item_generator.py`
- `core/work_item_parser.py`

현재 prepare 흐름은 아래와 같다.

1. researcher가 `project_brief.json`을 만든다.
2. `ProjectPlanningDirector.plan()`이 `planning_steps`, `roles`, `modules`, `tasks`를 생성한다.
3. `build_project_board()`가 board를 만든다.
4. `generate_work_items()`가 사람 검토용 문서를 만든다.
5. `ApprovalGate`가 승인 전 실행을 차단한다.

즉 planning 자체는 이미 있다. 약한 부분은 `계획 비판`, `phase 계약`, `사람 검증의 전면화`다.

### 2.2 실행층

핵심 파일:

- `core/project_pipeline.py`
- `core/dynamic_orchestrator.py`
- `core/agent_runner.py`
- `core/message_broker.py`

현재 execute 흐름은 아래와 같다.

1. 승인과 문서 무결성을 확인한다.
2. role별 YAML agent를 materialize한다.
3. skill build/install을 수행한다.
4. `DynamicOrchestrator`가 board와 `.todo.md`, mailbox, state_board를 기준으로 다음 task를 고른다.
5. agent들이 병렬 실행된다.

즉 실행 엔진은 이미 충분히 강하다. GSD에서 새 엔진을 가져올 필요는 없다.

### 2.3 연속성/사람 개입층

핵심 파일:

- `core/approval_gate.py`
- `core/continuity/manifest_store.py`
- `core/continuity/resume_brief.py`
- `core/providers/session_adapter.py`
- `core/pdca_commands.py`

현재 구조는 아래를 이미 지원한다.

1. 승인 전 문서 검토
2. 승인 후 문서 변경 감지
3. `.af_manifest.json` 기반 resume
4. `resume_brief.md` 기반 작업 재개
5. PDCA 명령 기반 수동 진행

즉 continuity와 HITL의 바닥 공사는 끝나 있다.

### 2.4 합의/토론층

핵심 파일:

- `core/conversation_manager.py`
- `core/consensus_engine.py`
- `core/conversation_room.py`
- `core/conversation_task_adapter.py`

이 레이어는 GSD의 `discuss-phase`를 흡수할 핵심 기반이다.

현재 가능한 것:

1. 여러 agent가 topic 단위로 대화한다.
2. protocol별 토론, 리뷰, 브레인스토밍, 핸드오프를 수행한다.
3. 합의 결과를 task board에 merge할 수 있다.
4. 사람 개입과 consensus 승인도 지원한다.

즉 GSD의 `discuss-phase`는 새로 발명할 필요가 없다. 이 레이어 위에 명시적 UX만 붙이면 된다.

## 3. 무엇을 흡수해야 하는가

흡수 대상은 다섯 가지다.

### 3.1 Phase Contract

지금 `Agent Factory`는 role/task 중심이다. 여기에 `phase`를 1급 계약으로 올려야 한다.

필요한 이유:

1. 사람이 진행 상태를 더 쉽게 이해할 수 있다.
2. planner와 verifier가 같은 단위를 공유하게 된다.
3. gap replan 시 어느 수준으로 돌아가야 하는지 분명해진다.

중요한 원칙:

- `phase`는 실행 엔진을 대체하면 안 된다.
- source of truth는 여전히 `task_board`와 runtime state여야 한다.
- `phase`는 사람 검토와 품질 계약 레이어로 올라가야 한다.

### 3.2 Discuss-Phase

GSD의 강점 중 하나는 phase 시작 전에 회색지대를 줄이는 것이다.

Agent Factory에서도 필요한 이유:

1. planner가 애매한 요구를 추정으로 메우는 일을 줄일 수 있다.
2. 여러 역할 agent의 관점을 planning 전에 수렴할 수 있다.
3. 이후 checker와 verifier의 기준이 더 명확해진다.

### 3.3 Plan Checker

현재 planner는 있지만, planner가 만든 계획을 goal 관점에서 비판하는 독립 checker가 약하다.

필요한 이유:

1. planning-first와 plan quality는 다르다.
2. plan이 있어도 dependency hole, 검증 누락, deliverable 누락이 발생할 수 있다.
3. planner와 checker를 분리해야 계획 품질이 오른다.

### 3.4 Goal-Backward Verifier

현재 verify-phase와 QA는 존재한다. 하지만 `무엇을 구현했는가`와 `원래 목표를 달성했는가`는 다르다.

필요한 이유:

1. verify-phase task 완료만으로는 goal 충족 여부를 보장하지 못한다.
2. must-have, truth check, wiring check, regression check를 묶은 별도 보고서가 필요하다.

### 3.5 UAT + Gap Replan

현재 승인과 QA는 있지만, 사람 검증과 재계획이 하나의 프로토콜로 연결돼 있지는 않다.

필요한 이유:

1. 사람은 구현 전 승인과 구현 후 체감 검증을 다르게 본다.
2. UAT가 문서화되지 않으면 세션이 바뀔 때 품질 정보가 사라진다.
3. 검증 실패 후 어디로 돌아갈지 표준 루트가 필요하다.

## 4. 무엇은 흡수하지 말아야 하는가

세 가지는 가져오지 않는 편이 낫다.

### 4.1 Markdown을 유일한 상태 저장소로 두는 방식

Agent Factory는 이미 아래 구조가 더 강하다.

1. `project_board_state.json`
2. `.af_manifest.json`
3. provider runtime state
4. `resume_brief.md`

즉 markdown은 계약과 리뷰를 위한 층으로 두고, 실행 진실은 구조화된 상태에 남겨야 한다.

### 4.2 Generic agent-only 운영 모델

GSD는 고정 역할 프롬프트 기반이 강하다. 반면 Agent Factory는 project-specific YAML materialization이 핵심이다.

이건 유지해야 한다.

### 4.3 Orchestrator를 지나치게 얇게 만드는 것

Agent Factory의 강점은 orchestrator, continuity, mailbox, state_board에 있다. GSD의 `thin orchestrator` 철학을 그대로 들여와서 이 강점을 약화시키면 안 된다.

## 5. 흡수 원칙

실제 구현 원칙은 아래 다섯 가지로 고정하는 것이 좋다.

1. `phase`는 실행 단위가 아니라 품질 단위다.
2. `task_board`가 여전히 execution source of truth다.
3. `phase 문서`는 사람과 verifier를 위한 계약이다.
4. `planner`와 `checker`는 분리한다.
5. `verification`과 `UAT`는 독립 산출물로 남긴다.

## 6. 권장 흡수 순서

가장 좋은 순서는 아래다.

1. `Phase Overlay`
2. `Discuss-Phase`
3. `Plan Checker`
4. `Verification Report`
5. `UAT.md`
6. `Gap Replan`

이 순서를 권장하는 이유는 명확하다.

1. 먼저 phase 단위가 생겨야 토론과 검증이 같은 단위를 바라본다.
2. discuss-phase가 생겨야 planner 입력 품질이 올라간다.
3. checker는 plan이 나온 뒤에 붙일 수 있다.
4. verifier와 UAT는 execution 이후 루프다.
5. gap replan은 마지막에 붙여도 전체 루프가 닫힌다.

## 7. 단계별 상세 설계

### 7.1 Step 1: Phase Overlay 추가

목표:

`role_plan` 위에 `phase_plan`을 얹는다.

새 산출물:

- `planning/phase_plan.json`
- `docs/work-items/{slug}/phase-overview.md`

권장 스키마:

```json
{
  "phases": [
    {
      "id": "phase_1_scope_alignment",
      "name": "Scope Alignment",
      "goal": "이번 phase가 무엇을 끝내야 하는가",
      "must_haves": ["반드시 만족해야 하는 조건"],
      "entry_criteria": ["이 phase 시작 전 조건"],
      "exit_criteria": ["이 phase 종료 조건"],
      "depends_on": [],
      "modules": ["module_a", "module_b"],
      "task_ids": ["task_1", "task_2"],
      "review_questions": ["어디가 아직 애매한가"]
    }
  ]
}
```

수정 대상:

- `core/bootstrap_roles.py`
- `core/project_pipeline.py`
- `core/project_task_board.py`
- `core/work_item_generator.py`

구현 방법:

1. planner 출력에서 `planning_steps`를 phase candidate로 승격한다.
2. modules/tasks를 phase와 연결한다.
3. board summary에 phase 진행률을 계산하는 보조 필드를 추가한다.
4. work-item 문서에 phase overview를 추가한다.

완료 기준:

1. prepare 후 `phase_plan.json`이 생성된다.
2. 각 task가 어느 phase에 속하는지 추적 가능하다.
3. 사람이 phase 단위로 현재 상태를 읽을 수 있다.

### 7.2 Step 2: Discuss-Phase 구현

목표:

phase별 요구 고정과 회색지대 제거를 conversation 계층 위에서 수행한다.

활용 대상:

- `core/conversation_manager.py`
- `core/consensus_engine.py`
- `core/conversation_task_adapter.py`
- `core/pdca_commands.py`

새 명령/흐름:

1. `/discuss-phase <phase_id>`
2. `protocol=brainstorm|debate|review`
3. consensus 결과를 `phase_discussion.md`와 board note로 저장

새 산출물:

- `planning/phases/{phase_id}/discussion.md`
- `planning/phases/{phase_id}/consensus.json`

구현 방법:

1. phase goal과 review questions를 초기 context로 conversation room 생성
2. planner 역할, researcher 역할, relevant role owners를 participant로 연결
3. consensus result를 task board와 phase notes에 반영
4. unresolved item이 있으면 `open_questions`로 남김

완료 기준:

1. 특정 phase에 대해 discussion room을 열 수 있다.
2. consensus가 구조화 결과로 저장된다.
3. discussion 결과가 planner 입력으로 재사용된다.

### 7.3 Step 3: Plan Checker 추가

목표:

planner와 독립된 checker가 계획을 비판하고, 필요시 revise loop를 강제한다.

새 컴포넌트:

- `core/plan_checker.py`

입력:

1. `project_brief`
2. `role_plan`
3. `phase_plan`
4. discuss-phase 결과

출력:

```json
{
  "decision": "pass|revise|reject",
  "missing_must_haves": [],
  "dependency_holes": [],
  "verification_gaps": [],
  "risk_notes": [],
  "revision_instructions": []
}
```

수정 대상:

- `core/project_pipeline.py`
- `core/bootstrap_roles.py`
- `core/pdca_commands.py`

구현 방법:

1. `prepare()` 내부에서 planner 직후 checker 실행
2. checker 결과가 `pass`가 아니면 board 생성 전에 revise
3. revise 횟수는 2~3회 제한
4. 최종 checker 결과를 `planning/plan_check_report.json`으로 저장

완료 기준:

1. plan quality가 별도 리포트로 남는다.
2. dependency, verify gap, must-have 누락이 실행 전에 드러난다.
3. planner가 만든 초안이 그대로 실행되는 비율이 줄어든다.

### 7.4 Step 4: Goal-Backward Verification Report 추가

목표:

실행 완료 후 결과를 `goal 기준`으로 재판정한다.

새 산출물:

- `docs/work-items/{slug}/verification-report.md`
- `planning/verification_report.json`

검증 항목:

1. original goal 충족 여부
2. must_haves 충족 여부
3. artifact 존재 여부
4. wiring/data flow 연결 여부
5. regression risk
6. known gaps

수정 대상:

- `core/bootstrap_roles.py`
- `core/project_pipeline.py`
- `core/project_task_board.py`
- `core/evaluator.py` 또는 신규 verifier 모듈

구현 방법:

1. verify-phase task가 끝나면 verifier 실행
2. verifier는 board 상태, artifacts, work-item 문서, mailbox notes를 읽는다
3. verdict를 `pass|pass_with_gaps|fail`로 남긴다
4. fail이나 gaps면 gap planning 후보를 만든다

완료 기준:

1. `completed` 상태와 별개로 `verified` 상태가 생긴다.
2. 사람이 구현 완료와 goal 달성을 구분할 수 있다.

### 7.5 Step 5: UAT.md 도입

목표:

사람 검증을 세션을 넘어 이어지는 1급 산출물로 만든다.

새 산출물:

- `docs/work-items/{slug}/uat.md`

권장 섹션:

1. 시나리오
2. 기대 결과
3. 실제 결과
4. pass/fail
5. 발견된 gap
6. 다음 액션

수정 대상:

- `core/work_item_generator.py`
- `core/approval_gate.py`
- `core/continuity/resume_brief.py`
- `core/providers/session_adapter.py`

구현 방법:

1. work-item 생성 시 `uat.md` 템플릿도 같이 만든다.
2. verification 이후 UAT pending 상태를 명시한다.
3. `resume_brief`에 UAT 상태 요약을 포함시킨다.
4. UAT fail이면 auto-close하지 않는다.

완료 기준:

1. 사람 검증 상태가 문서와 continuity에 같이 남는다.
2. 다음 세션이 와도 UAT 미완료 항목을 이어받을 수 있다.

### 7.6 Step 6: Gap Replan 루프 추가

목표:

verification fail이나 UAT fail이 나왔을 때 표준적으로 재계획한다.

새 산출물:

- `planning/gap_report.json`
- `planning/gap_patch_plan.json`

수정 대상:

- `core/project_pipeline.py`
- `core/project_task_board.py`
- `core/work_item_parser.py`
- `core/pdca_commands.py`

구현 방법:

1. verifier/UAT에서 gap 목록 추출
2. gap을 새 task나 phase patch로 변환
3. 기존 board에 merge
4. 기존 완료 task는 유지하고 patch만 실행

완료 기준:

1. 검증 실패 후 새 프로젝트를 다시 짜지 않아도 된다.
2. 기존 실행 결과를 보존한 채 품질 patch loop를 돌 수 있다.

## 8. 파일 단위 권장 변경 맵

### 8.1 반드시 수정할 파일

- `core/project_pipeline.py`
- `core/bootstrap_roles.py`
- `core/project_task_board.py`
- `core/work_item_generator.py`
- `core/approval_gate.py`
- `core/continuity/resume_brief.py`
- `core/providers/session_adapter.py`
- `core/pdca_commands.py`

### 8.2 새로 추가하면 좋은 파일

- `core/phase_contract.py`
- `core/plan_checker.py`
- `core/goal_verifier.py`
- `core/gap_replan.py`

### 8.3 기존 자산을 재사용할 파일

- `core/conversation_manager.py`
- `core/consensus_engine.py`
- `core/conversation_task_adapter.py`
- `core/message_broker.py`
- `core/evaluator.py`

## 9. 우선순위

효과 대비 비용 기준 우선순위는 아래가 좋다.

1. `PlanChecker`
2. `Verification Report`
3. `UAT.md`
4. `Discuss-Phase`
5. `Gap Replan`
6. `Phase Overlay` 고도화

다만 구조 안정성 기준으로는 아래 순서가 더 안전하다.

1. `Phase Overlay`
2. `Discuss-Phase`
3. `PlanChecker`
4. `Verification Report`
5. `UAT.md`
6. `Gap Replan`

즉 빠른 품질 개선이 목적이면 checker부터, 구조적으로 예쁘게 가려면 phase overlay부터 가는 게 맞다.

## 10. 가장 현실적인 실행 전략

가장 현실적인 전략은 아래다.

### 10.1 1차 릴리스

범위:

1. `PlanChecker`
2. `verification-report.md`
3. `uat.md`

이유:

가장 적은 변경으로 결과물 완성도를 직접 올릴 수 있다.

### 10.2 2차 릴리스

범위:

1. `phase_plan.json`
2. `/discuss-phase`
3. conversation 결과를 planning 입력으로 반영

이유:

planning 품질을 upstream에서 끌어올릴 수 있다.

### 10.3 3차 릴리스

범위:

1. `gap_replan`
2. phase dashboard
3. verified 상태 기반 close policy

이유:

이 단계에서 비로소 GSD식 품질 루프가 닫힌다.

## 11. 최종 의견

핵심 결론은 아래다.

1. `Agent Factory`는 이미 실행 플랫폼으로 충분히 강하다.
2. 부족한 것은 planner/checker/verifier/UAT가 하나의 품질 프로토콜로 전면화되지 않았다는 점이다.
3. 따라서 GSD에서 흡수해야 하는 것은 `새 오케스트레이터`가 아니라 `품질 레일`이다.
4. 가장 중요한 구현 포인트는 `phase를 실행 엔진이 아니라 품질 계약으로 올리는 것`이다.
5. 가장 먼저 붙여야 할 것은 `PlanChecker + Verification Report + UAT.md`다.

한 줄로 정리하면 다음과 같다.

`Agent Factory는 role/state 엔진을 유지하고, GSD에서는 phase/checker/verifier/UAT만 전략적으로 흡수하는 것이 맞다.`
