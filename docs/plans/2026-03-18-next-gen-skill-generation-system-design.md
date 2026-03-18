# 차세대 스킬 생성 시스템 설계안

> **구현 시 참고:** 이 설계안을 실제 구현할 때는 `superpowers:executing-plans` 흐름으로 작업 단위를 쪼개 실행하는 것을 권장한다.

**목표:** `agent-factory`의 현재 스킬 생성 파이프라인을 `정확도`, `재사용 우선`, `검증 신뢰도`, `운영 수명주기` 기준으로 한 단계 끌어올린 차세대 시스템으로 재설계한다.

**아키텍처:** 현재의 `RequirementAnalyzer -> Researcher -> SkillProcurer -> Builder -> RegistryManager` 흐름은 유지하되, 각 단계를 더 똑똑한 검색, 더 강한 검증, 더 엄격한 승격 정책으로 교체한다. `Claude Code`의 선언형 스킬 계약과 `OpenAI Codex`의 운영형 skill lifecycle을 가져오고, `agent-factory`의 자동 조달/생성 메타 파이프라인을 유지한다.

**기술 스택:** Python, YAML, Markdown, 임베딩, 격리 실행, 기존 registry/runner/orchestrator 스택

---

## 1. 왜 새 설계가 필요한가

현재 파이프라인은 이미 좋은 점이 많다.

- 필요한 스킬을 실제로 추론한다
- 외부 재사용을 먼저 시도한다
- 없으면 builder가 실제 스킬을 생성한다
- guard와 isolated test가 있다

하지만 정확도와 신뢰도 관점에서 네 가지 약점이 분명하다.

1. 필요한 스킬 탐지가 requirement prompt 실패 시 급격히 약해진다
2. 후보 재사용 순위 산정이 lexical overlap에 너무 의존한다
3. 생성된 스킬 검증이 self-test 중심이라 참 정확도를 보장하지 못한다
4. build 이후 승격 수명주기가 얕아 운영 중 학습과 정제가 어렵다

즉 현재 시스템은 "생성 능력"은 높지만 "검증된 생성 시스템"이라고 보긴 어렵다.

## 2. 설계 목표

차세대 시스템은 아래 다섯 목표를 가진다.

1. 재사용 우선
가능하면 기존 skill을 찾고, 정말 없을 때만 생성한다.

2. 의미 기반 매칭
이름이 비슷한 skill이 아니라 기능적으로 맞는 skill을 찾는다.

3. 외부 검증 중심
skill이 스스로 쓴 `test(ctx)`만 통과해서는 active가 되지 못한다.

4. 단계적 승격
생성 즉시 active가 아니라 `draft -> candidate -> canary -> active -> archived` 수명주기를 따른다.

5. 운영 중 학습
실제 사용 결과가 다음 retrieval, ranking, retirement 판단에 반영된다.

## 3. 차세대 시스템 한 줄 정의

차세대 시스템은 단순한 `Skill Builder`가 아니다.

정확하게는 아래다.

`역량 발견 + 재사용 검색 + 스킬 생성 + 평가 + 승격 + 수명주기 관리`

즉 "스킬 생성기"가 아니라 "스킬 생성 운영 시스템"이어야 한다.

## 4. 목표 아키텍처

차세대 파이프라인은 아래 8단계로 정의한다.

1. 역량 의도 분석
2. 재사용 후보 검색
3. 재사용 대 생성 결정
4. SkillSpec 합성
5. 다단계 스킬 생성
6. 외부 평가 하니스
7. 승격과 점진 배포
8. 런타임 피드백 루프

## 5. 단계별 설계

### 5.1 Capability Intent Analysis

현재 `RequirementAnalyzer`는 `missing_skills`를 직접 내보낸다.  
차세대 시스템에서는 먼저 "필요 capability"를 더 구조적으로 뽑는다.

예시 산출물:

```json
{
  "goal": "API 경로 수정과 회귀 테스트 추가",
  "capabilities": [
    {"id": "repo_read", "required": true},
    {"id": "python_edit", "required": true},
    {"id": "pytest_regression", "required": true}
  ],
  "constraints": ["offline_only", "safe_file_edit"],
  "risk_level": "elevated"
}
```

핵심 변화:

- `missing_skills`를 바로 뽑지 않는다
- 먼저 capability graph를 만든다
- skill은 capability를 충족하는 구현 단위로 본다

효과:

- retrieval 품질 향상
- skill naming mismatch 완화
- 향후 role planning과 연결이 쉬워진다

### 5.2 Reuse Candidate Retrieval

현재는 local exact match, verified candidate, external, builder fallback 순서다.  
이 순서는 유지하되 ranking 방식을 바꾼다.

새 retrieval 점수는 아래 네 요소의 가중 합으로 계산한다.

1. semantic similarity
capability 설명과 skill description/usage examples 간 의미 유사도

2. symbolic compatibility
provider, role, language, file type, execution mode 일치도

3. provenance quality
active/canary/shared 여부, 최근 성공 사용량, owner 존재 여부

4. task fit evidence
비슷한 task에서 성공한 이력, 실패한 이력

즉 지금의 token overlap matching을 `semantic + symbolic + historical` matching으로 바꾼다.

### 5.3 Reuse vs Forge Decision

현재는 외부 설치가 안 되면 바로 builder로 내려간다.  
차세대에서는 confidence gate를 둔다.

의사결정 규칙:

- top candidate confidence >= 0.85: 재사용
- 0.60 ~ 0.85: shadow evaluation 후 재사용 또는 partial adaptation
- < 0.60: forge 후보

이 단계의 목적은 두 가지다.

1. 비슷하지만 틀린 skill을 억지로 재사용하지 않기
2. 조금만 수정하면 되는 skill을 새로 만들지 않기

### 5.4 SkillSpec Synthesis

builder는 코드를 만들기 전에 먼저 `SkillSpec`을 만든다.

예시:

```yaml
id: pytest_regression_guard
kind: action
goal: pytest 회귀 테스트를 추가하고 실행한다
inputs:
  - repo files
outputs:
  - updated test files
success_criteria:
  - 숨은 테스트 90% 이상 통과
  - forbidden imports 없음
failure_modes:
  - self-test만 통과하고 실사용 실패
policy:
  allowed_tools:
    - read_file
    - write_file
    - run_pytest
```

핵심 변화:

- 코드보다 spec이 먼저 나온다
- 이후 구현, 검증, 승격이 이 spec에 맞춰 평가된다

### 5.5 Multi-Pass Skill Forge

현재 builder는 한 모델이 코드와 self-test를 같이 만든다.  
차세대에서는 최소 3패스로 분리한다.

1. Implementer pass
spec을 보고 구현 코드 생성

2. Critic pass
금지 패턴, 설계 누락, edge case, policy mismatch 지적

3. Repair pass
critic 피드백 반영 후 수정

선택적으로 4번째 pass를 둔다.

4. Test author pass
구현자와 다른 시각에서 contract test와 adversarial case 생성

핵심 원칙:

- 구현자와 검증자를 같은 응답에 두지 않는다
- 최소한 역할을 분리해 self-confirmation bias를 줄인다

### 5.6 External Eval Harness

이 단계가 가장 중요하다.

현재의 `test(ctx)`는 유지하되, 그것만으로 승격하지 않는다.  
차세대 검증은 세 층으로 나눈다.

1. Static safety gate

- AST guard
- forbidden import/call
- file/network policy
- timeout budget

2. Contract eval

- spec 기반 공개 테스트
- hidden test
- negative/adversarial test

3. Shadow task eval

- 실제 task replay에서 기존 active skill과 비교
- 성공률, 수정량, rollback 빈도, human override 여부 측정

즉 `test(ctx)`는 내부 self-check로 강등하고, 진짜 승격 판단은 외부 harness가 한다.

### 5.7 Promotion and Rollout

새 수명주기는 아래를 따른다.

1. `draft`
생성 직후. self-test와 static gate만 통과한 상태

2. `candidate`
external eval pass

3. `canary`
일부 task와 일부 role에만 제한 사용

4. `active`
충분한 usage evidence와 안정성 확인 후 전면 사용

5. `archived`
더 이상 우선 사용하지 않음

중요 규칙:

- 새 skill은 바로 `active`가 될 수 없다
- 기존 active skill보다 낫다는 evidence가 있어야 승격된다
- 실패가 누적되면 자동으로 `candidate` 또는 `archived`로 강등된다

### 5.8 Runtime Feedback Loop

차세대 시스템은 운영 중에도 계속 학습해야 한다.

필수 수집 이벤트:

- selection reason
- actual tool usage
- task success/failure
- rollback
- human override
- retry count
- replacement by other skill

이 정보는 세 곳에 연결된다.

1. retrieval ranking
2. promotion decision
3. retirement decision

## 6. 정확도 개선 포인트

정확도는 아래 다섯 지표로 본다.

### 6.1 필요 역량 탐지 정밀도 / 재현율

정의:

- 정말 필요한 capability를 놓치지 않는가
- 필요 없는 capability를 과하게 만들지 않는가

개선 방법:

- direct missing skill 대신 capability graph 사용
- role/context/file type 입력 포함
- 실패 task replay 데이터 재학습

### 6.2 재사용 검색 Top-1 정확도

정의:

- 가장 적합한 기존 skill을 첫 번째로 고르는가

개선 방법:

- semantic embedding
- provenance score
- historical success signal
- lexical overlap는 보조 지표로만 사용

### 6.3 숨은 평가 통과율

정의:

- 새 skill이 숨은 테스트를 통과하는 비율

개선 방법:

- 구현/비평/수정 pass 분리
- self-test 외 hidden eval 추가
- adversarial case 자동 생성

### 6.4 승격 안정성

정의:

- active 승격 후 production-like task에서 실패하지 않는가

개선 방법:

- canary 단계 도입
- rollback-trigger 자동화
- shadow comparison

### 6.5 수명주기 효율

정의:

- 오래된 skill을 계속 끌고 가지 않고 적절히 retirement하는가

개선 방법:

- usage decay
- duplicate capability detection
- stronger replacement candidate가 나타나면 archive 제안

## 7. Claude Code와 OpenAI Codex에서 가져와야 할 점

### Claude Code에서 가져올 것

1. 선언형 skill contract
2. invocation policy
3. tool allowlist/denylist 개념
4. forked context와 subagent 분리

### OpenAI Codex에서 가져올 것

1. skill을 작업 패키지로 다루는 방식
2. 여러 표면 간 재사용
3. 팀 공유와 team config
4. automations 연결
5. review queue 감각의 승격 UX

### agent-factory가 유지해야 할 것

1. missing capability 탐지
2. external-first procurement
3. builder fallback
4. provider-agnostic runtime
5. role-based orchestration

## 8. 새 컴포넌트 제안

### 8.1 CapabilityIntentAnalyzer

책임:

- task를 capability graph로 변환
- constraints와 risk를 구조화

예상 파일:

- `core/capability_intent.py`

### 8.2 SkillRetrievalEngine

책임:

- local/global/user/external source 통합 검색
- semantic + symbolic + historical ranking

예상 파일:

- `core/skill_retrieval_engine.py`

### 8.3 SkillSpecSynthesizer

책임:

- 생성 전 canonical `SkillSpec` 작성
- success criteria와 eval contract 생성

예상 파일:

- `core/skill_spec_synthesizer.py`

### 8.4 SkillForge

책임:

- implementer/critic/repair pass orchestration
- provider별 generation fallback

예상 파일:

- `core/skill_forge.py`

### 8.5 SkillEvalHarness

책임:

- static gate
- contract eval
- hidden eval
- shadow task eval

예상 파일:

- `core/skill_eval_harness.py`

### 8.6 SkillPromotionManager

책임:

- draft/candidate/canary/active/archive 상태 전환
- rollback와 retirement 관리

예상 파일:

- `core/skill_promotion.py`

## 9. 기존 코드와의 연결 방식

### 9.1 `RequirementAnalyzer`

현재:

- `missing_skills` 직접 출력

변경:

- `capabilities` 중심 출력
- `missing_skills`는 하위 호환 필드로 유지

### 9.2 `Researcher`

현재:

- local catalog + LLM suggestion + NotebookLM evidence

변경:

- retrieval engine 호출
- evidence를 `capability -> candidate -> confidence` 구조로 정규화

### 9.3 `SkillProcurer`

현재:

- exact -> verified -> external -> build

변경:

- exact -> ranked reuse -> shadow reuse -> forge

### 9.4 `Builder`

현재:

- single-pass code generation + self-test

변경:

- multi-pass forge + external eval gate

### 9.5 `RegistryManager`

현재:

- register, lock, workflow apply

변경:

- provenance, eval score, lifecycle stage, usage stats까지 관리

## 10. 아티팩트 설계

### 10.1 `skill-spec.yaml`

역할:

- canonical contract

### 10.2 `skill-eval-report.json`

역할:

- hidden/public/shadow eval 결과 저장

### 10.3 `skill-promotion.json`

역할:

- lifecycle state와 승격 사유 저장

### 10.4 `skill-usage.jsonl`

역할:

- 런타임 usage evidence 축적

## 11. 롤아웃 순서

### Phase 1. Capability-first 분석 도입

- `RequirementAnalyzer` 출력 확장
- capability graph 도입

### Phase 2. Retrieval 정교화

- lexical match에 semantic rank 추가
- provenance/history score 추가

### Phase 3. SkillSpec 선행 생성

- build 전에 spec을 먼저 생성
- existing skill도 spec으로 정규화

### Phase 4. 다단계 생성

- implementer/critic/repair 분리

### Phase 5. 외부 평가 하니스

- hidden eval
- adversarial eval
- shadow task eval

### Phase 6. 승격 수명주기

- candidate/canary/active/archive 단계 도입

## 12. 테스트 전략

필수 테스트는 아래다.

1. capability analysis regression test
2. retrieval top-1/top-3 accuracy benchmark
3. forge hidden eval pass rate benchmark
4. promotion rollback test
5. archived skill replacement test
6. shadow eval comparison test

## 13. 성공 기준

최소 성공 기준은 아래처럼 둔다.

1. 필요한 역량 탐지 재현율 20% 이상 개선
2. 기존 스킬 재사용 Top-1 정확도 15% 이상 개선
3. 생성 스킬의 숨은 평가 통과율 2배 이상 개선
4. active 승격 후 rollback 비율 50% 이상 감소
5. archived되지 않은 중복 skill 비율 감소

## 14. 비목표

이번 설계에서 바로 하지 않는 것:

- UI 기반 skill marketplace 구축
- 외부 공개 스토어 운영
- 모든 기존 skill의 즉시 전면 마이그레이션
- memory 시스템 전면 개편

## 15. 최종 판단

차세대 스킬 생성 시스템의 핵심은 "더 많이 생성"이 아니다.

핵심은 아래다.

1. 먼저 더 잘 찾기
2. 정말 필요할 때만 만들기
3. 만든 뒤에는 외부 기준으로 검증하기
4. 바로 활성화하지 말고 단계적으로 승격하기
5. 운영 데이터를 다음 생성 품질에 다시 반영하기

즉 다음 세대의 목표는 `Skill Builder`가 아니라 `스킬 생성 운영 시스템`이다.

