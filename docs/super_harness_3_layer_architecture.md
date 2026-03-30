# Super Harness 3계층 아키텍처 설계 문서 v1

작성일: 2026-03-31

## 1. 문서 목적

이 문서는 `초기 프로젝트 생성`과 `장기 유지보수/업데이트`를 모두 감당하는 차세대 슈퍼 하네스를 위한 기준 아키텍처를 정의한다.

핵심 목표는 두 가지를 동시에 만족하는 것이다.

- `운영 지속성`
  프로젝트가 끝나지 않고 계속 굴러가며, 이슈, 세션, 승인, 역할 배정, 복구가 가능해야 한다.
- `산출물 완성도`
  분석, 설계, 계획, 구현 지시, QA, 최종 결과물이 매번 높은 품질을 유지해야 한다.

이 두 목표를 동시에 달성하려면 단일 엔진이 아니라 `3계층 분리 아키텍처`가 필요하다.

## 2. 왜 3계층으로 분리해야 하는가

### 2.1 최적화 목표가 다르다

Control Plane은 `계속 일하게 만드는 것`이 목표다.
Quality Plane은 `좋은 결과물을 만들게 하는 것`이 목표다.
Memory Plane은 `다시 배우지 않게 하는 것`이 목표다.

하나로 합치면 결국 셋 다 어중간해진다.

### 2.2 비용과 지연 특성이 다르다

Control Plane은 빠르고 안정적으로 돌아야 한다.
Quality Plane은 느리더라도 깊은 추론과 검증이 필요하다.
Memory Plane은 지속 저장, 검색, 축적이 핵심이다.

동일 루프에 섞으면 운영도 느려지고 품질도 흔들린다.

### 2.3 실패 유형이 다르다

Control Plane 실패는 `멈춤`, `중복 실행`, `복구 실패`다.
Quality Plane 실패는 `누락`, `근거 부족`, `설계 미흡`이다.
Memory Plane 실패는 `망각`, `잘못된 회상`, `오염된 지식`이다.

서로 다른 실패를 같은 방식으로 처리하면 디버깅이 어려워진다.

### 2.4 지속성 범위가 다르다

Control Plane은 이슈와 세션을 이어간다.
Quality Plane은 산출물과 평가 결과를 이어간다.
Memory Plane은 프로젝트 경험과 패턴을 이어간다.

각 지속성 단위가 다르므로 저장 구조도 달라야 한다.

### 2.5 교체 가능성이 다르다

운영 엔진은 나중에 scheduler나 approval 체계가 바뀔 수 있다.
품질 엔진은 critique 체인이나 QA 정책이 바뀔 수 있다.
메모리 엔진은 vector DB나 graph backend가 바뀔 수 있다.

계층 분리는 개별 진화를 가능하게 한다.

### 2.6 진짜 장기 운영은 생성이 아니라 축적의 문제다

한 번 잘 만드는 시스템은 많다.
하지만 `계속 유지보수하면서 더 나아지는 시스템`은 메모리와 운영 계층이 없으면 성립하지 않는다.

## 3. 3계층이 필요한 최종 결론

슈퍼 하네스는 아래 세 가지를 동시에 가져야 한다.

- `Control Plane`
  일의 흐름을 책임진다.
- `Quality Plane`
  결과물의 품질을 책임진다.
- `Memory Plane`
  과거를 현재 성능으로 전환한다.

즉 슈퍼 하네스의 본질은 `오케스트레이션`, `품질 보장`, `기억 축적`의 분리와 결합이다.

## 4. 전체 아키텍처 개요

```text
User / PM / API / Trigger
          |
          v
+-----------------------------+
| 1) Control Plane            |
| issue / session / roles     |
| approval / schedule / run   |
+-----------------------------+
          | request artifact
          v
+-----------------------------+
| 2) Quality Plane            |
| research / plan / critique  |
| QA / rewrite / verdict      |
+-----------------------------+
          | read/write memory
          v
+-----------------------------+
| 3) Memory Plane             |
| continuity / episodic       |
| semantic / graph / recall   |
+-----------------------------+
```

```text
Control Plane  -> 무엇을 언제 누가 할 것인가
Quality Plane  -> 무엇을 어떤 근거로 얼마나 잘 만들 것인가
Memory Plane   -> 과거 경험을 어떻게 다시 쓰게 할 것인가
```

## 5. 계층별 설명

| 계층 | 핵심 질문 | 주요 책임 | 산출물 |
| --- | --- | --- | --- |
| Control Plane | 일을 어떻게 계속 굴릴 것인가 | intake, routing, issue, session, workspace, approval, scheduling, audit | issue state, run state, assignment, approval state |
| Quality Plane | 좋은 결과물을 어떻게 만들 것인가 | evidence collection, drafting, critique, QA, rewrite, final verdict | evidence bundle, brief, plan, board, QA report, verdict |
| Memory Plane | 다음 번에 더 잘하게 만들려면 무엇을 기억할 것인가 | continuity, episodic memory, semantic recall, graph knowledge, cross-project reuse | resume state, episodes, graph nodes, reusable patterns |

## 6. 계층별 상세 설계

### 6.1 Control Plane

목적은 `프로젝트와 유지보수 작업을 멈추지 않고 굴리는 것`이다.

핵심 컴포넌트:

- `Intake Router`
  새 요청인지, 유지보수 요청인지, 버그인지, 기능 추가인지 분류한다.
- `Issue Manager`
  작업을 issue 단위로 관리한다.
- `Session Manager`
  기존 실행 상태를 복원하고 이어간다.
- `Workspace Manager`
  프로젝트 루트, 브랜치, 런타임 파일, 실행 컨텍스트를 관리한다.
- `Role Allocator`
  어떤 agent/role이 어떤 작업을 맡는지 배정한다.
- `Approval Gate`
  고위험 작업, 배포, 구조 변경에 승인 규칙을 적용한다.
- `Scheduler / Heartbeat`
  유휴 agent를 깨우고 재시도, 대기, 후속 작업을 스케줄한다.
- `Audit / Activity Log`
  누가 무엇을 언제 왜 했는지 기록한다.

운영 원칙:

- Control Plane은 품질 판단을 직접 하지 않는다.
- Control Plane은 품질 작업을 `언제 호출할지`만 결정한다.
- 모든 장기 상태는 재시작 가능해야 한다.

### 6.2 Quality Plane

목적은 `각 요청마다 최고 품질의 분석, 설계, 실행 지시 산출물`을 만드는 것이다.

핵심 컴포넌트:

- `Task Profiler`
  요청의 성격과 품질 모드를 분류한다.
- `Evidence Orchestrator`
  로컬, 웹, 메모리에서 근거를 수집한다.
- `Evidence Verifier`
  근거의 깊이, 최신성, 커버리지를 검사한다.
- `Artifact Draft Generator`
  brief, architecture, plan, task board 초안을 만든다.
- `Semantic Critic`
  목표 정합성, 누락, 대안, 근거 부족을 비평한다.
- `Gap Enricher`
  부족한 근거를 추가 수집한다.
- `Structural Validator`
  구조, 필드, 연결 관계, acceptance criteria를 검증한다.
- `Execution QA`
  테스트, 회귀, 구현 가능성, 검증 가능성을 점검한다.
- `Final Rewriter`
  critique와 QA를 반영한 최종본을 만든다.
- `Verdict Aggregator`
  accepted, warning, rejected를 결정한다.

품질 원칙:

- 원문 근거가 항상 source of truth다.
- 중요 산출물은 `1회 생성`으로 끝내지 않는다.
- `draft -> critique -> enrich -> rewrite -> QA -> final rewrite`를 기본으로 한다.

### 6.3 Memory Plane

목적은 `다음 유지보수와 다음 프로젝트에서 더 잘하게 만드는 것`이다.

핵심 컴포넌트:

- `Continuity Store`
  현재 상태, interrupted tasks, open todos, resume brief를 저장한다.
- `Working Memory`
  현재 세션의 핵심 상태를 유지한다.
- `Episodic Memory`
  실행 단위의 성공, 실패 에피소드를 저장한다.
- `Semantic Memory`
  재검색 가능한 일반 지식과 프로젝트 맥락을 저장한다.
- `Knowledge Graph`
  문제, 원인, 해결 패턴을 구조화한다.
- `Cross-Project Recall`
  다른 프로젝트의 유사 해결책을 호출한다.
- `Memory Consolidation`
  실행 로그를 메모리로 정리하고, 실패-성공 페어를 지식으로 승격한다.

메모리 원칙:

- 메모리는 단순 로그가 아니라 `재사용 가능한 자산`이어야 한다.
- 메모리는 저장만이 아니라 `선별`, `압축`, `구조화`가 필요하다.
- 잘못된 기억은 품질을 해치므로 검증된 기억과 비검증 기억을 분리해야 한다.

## 7. 핵심 데이터 객체

슈퍼 하네스는 아래 객체를 중심으로 동작해야 한다.

- `IssueContext`
  요청 ID, 유형, 우선순위, 위험도, 승인 필요 여부
- `ContinuitySnapshot`
  현재 상태, interrupted tasks, failed tasks, open todos, latest session
- `EvidenceBundle`
  local refs, web refs, memory refs, notebook synthesis, coverage map
- `ArtifactPackage`
  brief, architecture, plan, task board, acceptance criteria
- `QualityReport`
  evidence score, critique gaps, QA findings, final warnings
- `RunState`
  active assignments, role status, scheduler state, retry state
- `MemoryEpisode`
  task, actions, result, failure reason, causal links
- `KnowledgePattern`
  problem, cause, solution, confidence, reuse scope

## 8. 명시적 단계 흐름

아래 흐름은 `버그 수정`, `기능 추가`, `대형 리팩터`, `초기 프로젝트 생성`에 공통으로 적용되는 기준 흐름이다.

### 1단계. Request Intake

Layer: Control Plane
입력: 사용자 요청, 이슈 생성 이벤트, 배포 후 오류 트리거
처리: 요청 유형을 `new`, `maintenance`, `bugfix`, `feature`, `refactor`로 분류한다.
출력: `IssueContext`

### 2단계. Continuity Rehydration

Layer: Control + Memory
입력: IssueContext, workspace, project ID
처리: 기존 manifest, board, resume brief, latest session, open todos, interrupted tasks를 복원한다.
출력: `ContinuitySnapshot`

### 3단계. Workflow Policy Selection

Layer: Control Plane
입력: IssueContext, ContinuitySnapshot
처리: 작업 모드를 `quick fix`, `standard update`, `deep redesign`, `full project bootstrap` 중 하나로 결정한다.
출력: `ExecutionPolicy`

### 4단계. Context Recall

Layer: Memory Plane
입력: 현재 요청, ContinuitySnapshot
처리: episodic recall, semantic recall, graph recall, cross-project recall을 수행한다.
출력: `MemoryRecallPack`

### 5단계. Evidence Acquisition

Layer: Quality Plane
입력: IssueContext, MemoryRecallPack, workspace context
처리: 로컬 문서, 코드 검색, 필요 시 Tavily 웹 원문 수집, 필요 시 NotebookLM deep synthesis를 수행한다.
출력: `EvidenceBundle`

### 6단계. Evidence Verification

Layer: Quality Plane
입력: EvidenceBundle
처리: 근거 수, 최신성, 커버리지, 근거 다양성, 주요 엔티티 커버 여부를 점수화한다.
출력: `EvidenceReport`

### 7단계. Draft Artifact Generation

Layer: Quality Plane
입력: EvidenceBundle, EvidenceReport, ExecutionPolicy
처리: brief, architecture, plan, role decomposition, task board 초안을 생성한다.
출력: `ArtifactDraft`

### 8단계. Semantic Critique

Layer: Quality Plane
입력: ArtifactDraft, original request
처리: 목표 정합성, 누락, 근거 부족, 역할 불명확성, 리스크 대응 부재를 비평한다.
출력: `CritiqueReport`

### 9단계. Gap-Driven Enrichment

Layer: Quality + Memory
입력: CritiqueReport
처리: 부족한 근거만 다시 수집하고, 필요한 경우 cross-project patterns와 external evidence를 추가한다.
출력: `EnrichedEvidenceBundle`

### 10단계. Revised Artifact + Structural Gate

Layer: Quality Plane
입력: ArtifactDraft, EnrichedEvidenceBundle
처리: 산출물을 재작성하고, 필수 필드, 소유자, acceptance criteria, task coverage를 기계적으로 검증한다.
출력: `ArtifactPackage`

### 11단계. Execution Board Commit

Layer: Control Plane
입력: ArtifactPackage
처리: board, run plan, work items, role assignments를 확정하고 실행 대기 상태로 전환한다.
출력: `RunState`

### 12단계. Agent Execution

Layer: Control Plane
입력: RunState, workspace, assigned roles
처리: 역할별 agent를 깨우고 실제 수정, 테스트, 생성 작업을 수행한다.
출력: `ExecutionResults`

### 13단계. Execution QA / Regression Check

Layer: Quality Plane
입력: ExecutionResults, ArtifactPackage
처리: 테스트 실행, 구현-요구사항 매핑, 회귀 위험, acceptance criteria 충족 여부를 검사한다.
출력: `QAReport`

### 14단계. Final Rewrite

Layer: Quality Plane
입력: ArtifactPackage, QAReport
처리: QA findings를 반영해 최종 문서와 최종 지시문을 재정리한다.
출력: `FinalArtifactPackage`

### 15단계. Final Verdict

Layer: Quality Plane
입력: EvidenceReport, CritiqueReport, QAReport, FinalArtifactPackage
처리: accepted, accepted_with_warnings, rejected 판정을 내린다.
출력: `QualityVerdict`

### 16단계. Release / Approval / Update Close

Layer: Control Plane
입력: QualityVerdict, FinalArtifactPackage
처리: 승인 필요 시 approval route로 보내고, 통과 시 상태를 closed 또는 deployed로 갱신한다.
출력: `ResolvedIssueState`

### 17단계. Memory Consolidation

Layer: Memory Plane
입력: ExecutionResults, QAReport, FinalArtifactPackage
처리: 에피소드 저장, 성공/실패 패턴 추출, knowledge graph 업데이트, cross-project reusable pattern 생성을 수행한다.
출력: `MemoryEpisode`, `KnowledgePattern`, `UpdatedContinuityState`

### 18단계. Continuous Loop

Layer: 전체
입력: UpdatedContinuityState
처리: 다음 유지보수, 업데이트에서 다시 1단계로 들어갈 준비를 마친다.
출력: `Persistent Project Intelligence`

## 9. 단계 흐름 도식

```text
[1 Intake]
   -> [2 Continuity Rehydration]
   -> [3 Workflow Policy]
   -> [4 Memory Recall]
   -> [5 Evidence Acquisition]
   -> [6 Evidence Verification]
   -> [7 Draft Artifact]
   -> [8 Semantic Critique]
   -> [9 Gap Enrichment]
   -> [10 Revised Artifact + Structural Gate]
   -> [11 Execution Board Commit]
   -> [12 Agent Execution]
   -> [13 Execution QA]
   -> [14 Final Rewrite]
   -> [15 Final Verdict]
   -> [16 Release / Approval]
   -> [17 Memory Consolidation]
   -> [18 Continuous Loop]
```

## 10. 왜 이 구조가 초기 프로젝트 생성과 유지보수, 업데이트를 동시에 커버하는가

초기 프로젝트 생성에서는:

- Evidence Acquisition이 요구사항과 기술 스택을 깊게 파악한다.
- Draft Artifact가 초기 brief, architecture, role plan, task board를 만든다.
- Control Plane이 프로젝트를 실행 가능한 워크플로로 바꾼다.

유지보수와 업데이트에서는:

- Continuity Rehydration이 과거 상태를 복구한다.
- Memory Recall이 과거 실패와 유사 해결책을 불러온다.
- Quality Plane이 단순 수정이 아니라 정확한 변경 설계와 회귀 방지를 담당한다.
- Control Plane이 여러 업데이트를 장기적으로 운영한다.

즉 같은 아키텍처 안에서 `초기 생성`은 `0에서 1`, `유지보수`는 `1에서 n`을 담당한다.

## 11. 이 구조를 택했을 때 기대 효과

1. `운영이 끊기지 않는다`
   세션, board, interrupted tasks, approvals가 살아 있으므로 장기 프로젝트에 강하다.

2. `산출물 품질이 시스템적으로 올라간다`
   품질이 agent 개인 능력에만 의존하지 않고 pipeline으로 보장된다.

3. `시간이 갈수록 더 잘한다`
   실패와 성공이 Memory Plane에 축적되어 다음 업데이트 품질을 높인다.

4. `작은 버그 수정부터 큰 리팩터까지 같은 운영 틀 안에서 처리된다`
   정책만 다르게 적용하면 된다.

5. `감사 가능성과 품질 설명 가능성을 동시에 가진다`
   누가 무엇을 했는지와 왜 그렇게 했는지를 모두 남긴다.

## 12. 최종 결론

차세대 슈퍼 하네스는 단순한 `에이전트 실행기`도 아니고, 단순한 `좋은 설계 생성기`도 아니다.
그 본질은 아래 세 문장으로 요약된다.

- `Control Plane`이 일을 계속 굴린다.
- `Quality Plane`이 결과물을 계속 좋게 만든다.
- `Memory Plane`이 시간이 갈수록 더 잘하게 만든다.

따라서 슈퍼 하네스의 정답은 단일 루프가 아니라 `운영`, `품질`, `기억`의 3계층 분리 아키텍처다.
