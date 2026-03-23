c# Agent Factory Retrieval Integration Architecture

## Metadata
- Last updated: 2026-03-15
- Status: proposal
- Scope: `agent_launcher.py`, `core/project_pipeline.py`, `core/researcher.py`, `core/skill_procurer.py`, `core/external_skill_sources.py`, `core/semantic_embedder.py`, `core/skill_loader.py`, `core/skill_cache.py`, `core/builder.py`, `core/registry_manager.py`
- Goal: 현재 `agent-factory`의 실행/오케스트레이션 강점을 유지하면서, 논의된 `agentic search + hybrid RAG + optional RAG-Anything` 계층을 결합한 차세대 구조를 정의한다.

## 1. 문서 목적

이 문서는 다음 질문에 답하기 위해 작성되었다.

1. 현재 `agent-factory`에 이미 구현되어 있는 시스템은 무엇을 잘하는가.
2. 지금까지 논의한 `Claude Code식 direct search`, `하이브리드 RAG`, `reranker`, `optional RAG-Anything`를 결합하면 어떤 시너지가 나는가.
3. 무엇을 유지하고, 무엇을 교체하거나 확장해야 하는가.
4. 실제 도입 순서는 어떻게 설계해야 하는가.

핵심 결론은 단순하다.

- 현재 `agent-factory`는 `execution-first orchestration system`이다.
- 지금까지 논의한 시스템은 `retrieval-first context engineering system`이다.
- 최적 구조는 `대체`가 아니라 `계층 결합`이다.

## 2. 현재 시스템 요약

### 2.1 현재 구조의 본질

현재 `agent-factory`는 이미 다음 실행 흐름을 가진다.

1. `AgentFactory`가 `HimariResearchAgent`, `SkillOrchestrator`, `ProjectPipeline`을 조립한다.
2. `ProjectPipeline.run()`이 프로젝트 브리프와 역할 계획을 생성한다.
3. Himari가 역할별 필요 스킬과 후보 근거를 만든다.
4. `SkillOrchestrator.procure_multiple()`이 기존 스킬 재사용, 외부 설치, 신규 생성 중 하나를 수행한다.
5. `Builder`가 evidence-first 규칙과 isolated test를 통과한 스킬만 등록한다.
6. `DynamicOrchestrator`가 Lilith 중심으로 역할들을 실행한다.

즉, 현재 시스템은 "근거 수집"보다 "행동 결정과 실행"에 더 최적화되어 있다.

### 2.2 현재 구현된 검색 관련 자산

현재 저장소에는 retrieval 계층의 씨앗이 이미 있다.

#### A. 스킬 의미 유사도 계층

- `core/semantic_embedder.py`
  - Gemini embedding 기반으로 스킬 메타데이터를 벡터화한다.
  - `description`, `when_to_use`, `semantic_tags` 중심의 의미 유사도를 계산한다.
- `core/skill_loader.py`
  - `keyword + semantic + category` 가중치로 스킬 적합도를 계산한다.
- `core/skill_cache.py`
  - keyword, semantic, category 점수를 캐시하며 동일 입력에 대한 재계산 비용을 줄인다.

이 계층은 "문서 retrieval"은 아니지만 "스킬 retrieval"의 초기 형태라고 볼 수 있다.

#### B. Himari 리서치 계층

- `core/researcher.py`
  - 로컬 registry skill catalog를 읽는다.
  - NotebookLM 인사이트를 가져와 evidence에 반영한다.
  - LLM 기반 후보 추천과 fallback token match를 수행한다.
  - `evidence_pack`을 만들어 후속 단계로 넘긴다.

즉, Himari는 이미 `retrieval director`의 역할 일부를 수행하고 있다. 다만 검색 기반이 얕고, 장기 기억과 문서 인덱싱 구조가 부족하다.

#### C. 외부 설치 및 빌드 계층

- `core/external_skill_sources.py`
  - source priority에 따라 외부 후보를 점수화하고 설치를 시도한다.
- `core/skill_procurer.py`
  - 정확 일치, 검증된 후보, 외부 설치, 신규 생성 순으로 의사결정을 수행한다.
- `core/builder.py`
  - evidence-first, quick guard, isolated test를 통해 생성 스킬을 검증한다.
- `core/registry_manager.py`
  - quality gate와 installable status를 관리한다.

이 계층은 이미 강력하다. 따라서 새 구조는 이 계층을 대체하면 안 되고, 더 좋은 근거를 공급해야 한다.

### 2.3 현재 시스템의 강점

- Lilith 중심 오케스트레이션이 이미 존재한다.
- 역할 계획과 프로젝트 파이프라인이 이미 존재한다.
- 외부 설치, 신규 생성, registry 등록까지 end-to-end 흐름이 있다.
- 생성 스킬에 대한 planning-first 및 isolated test 방어가 있다.
- 스킬 선택에 semantic scoring이 이미 일부 적용되어 있다.

### 2.4 현재 시스템의 한계

- 문서 파싱과 청킹 계층이 없다.
- 장기 문서 지식베이스가 없다.
- dense + sparse hybrid retrieval이 없다.
- reranker가 없다.
- provenance와 trust score가 약하다.
- NotebookLM 및 LLM 기반 추정 의존도가 높다.
- 동일한 리서치를 반복할 가능성이 높다.

## 3. 지금까지 논의한 시스템의 정체

지금까지 논의한 1~7 구조는 본질적으로 다음과 같다.

- 문서 수집
- 파싱
- 청킹
- 임베딩
- 벡터 저장
- sparse retrieval
- hybrid retrieval
- reranking
- context assembly
- generation

즉, 이 구조는 `검색 품질 최적화 시스템`, 더 정확히는 `retrieval-first context engineering system`이다.

이 구조는 Lilith 같은 PM 오케스트레이터 자체가 아니다.
이 구조는 Builder나 Registry 자체도 아니다.
이 구조는 Himari가 쓸 `지식 검색 백엔드`에 가깝다.

## 4. 논의된 외부 개념과 현재 시스템의 관계

### 4.1 Claude Code식 direct search

Claude Code식 방식은 로컬 코드와 설정 파일을 `Read/Grep/Glob/LS` 중심으로 직접 탐색하는 모델이다.

장점:

- 함수명, 파일명, import path처럼 정확 문자열에 강하다.
- 최신 상태의 로컬 파일을 직접 읽는다.
- 별도 인덱싱 없이도 빠르게 사용할 수 있다.

제한:

- 긴 문서 자산과 장기 누적 지식에는 약하다.
- 여러 문서와 과거 로그를 통합 검색하는 데 한계가 있다.

따라서 이 방식은 `코드와 로컬 구조` 검색에 유지해야 한다.

### 4.2 Hybrid RAG

Hybrid RAG는 dense semantic retrieval과 sparse keyword retrieval을 결합한다.

장점:

- skill_id, package name, repo name 같은 정확 키워드도 잡는다.
- 기능 설명, 의미 유사 질의도 잡는다.
- 장기 문서 아카이브와 과거 evidence를 재사용할 수 있다.

따라서 이 방식은 `스킬 문서`, `README`, `내부 위키`, `과거 실행 로그`, `외부 조사 결과`에 적합하다.

### 4.3 RAG-Anything

RAG-Anything는 멀티모달 문서 retrieval에 강한 프레임워크다.

장점:

- PDF, Office, 이미지, 표, 수식 같은 문서를 파싱하고 인덱싱하기 쉽다.
- text-only 문서보다 복잡한 enterprise 문서에 적합하다.

제한:

- Lilith 오케스트레이션을 대체하지 않는다.
- 외부 스킬 설치, 승인, registry 등록을 대체하지 않는다.
- 최신 웹 검색 자체를 대체하지 않는다.

따라서 `RAG-Anything`는 선택적으로 Himari의 문서 인텔리전스 백엔드에 붙일 수 있다.

## 5. 결합 후 목표 구조

### 5.1 핵심 철학

결합 후의 구조는 두 층으로 나뉜다.

#### A. Execution Plane

현재 `agent-factory`가 이미 잘하는 영역이다.

- Lilith 오케스트레이션
- ProjectPipeline
- SkillOrchestrator
- Builder
- Registry
- DynamicOrchestrator

#### B. Knowledge Plane

이번에 강화할 영역이다.

- direct local search
- hybrid RAG
- reranker
- provenance store
- optional multimodal ingestion
- feedback indexing

### 5.2 목표 아키텍처

```text
User Request
  -> Intent Router
  -> Retrieval Planner (Himari)
      -> Local Direct Search
      -> Hybrid RAG Search
      -> Live Web Search
      -> Optional Multimodal Ingestion/RAG-Anything
  -> Evidence Fusion + Rerank
  -> Lilith Planning / Role Decomposition
  -> Skill Resolution
      -> Reuse Existing Skill
      -> Install External Skill
      -> Build New Skill
  -> Builder Guard + Isolated Test + Registry Gate
  -> Dynamic Execution
  -> Feedback / Reindex / Memory Update
```

## 6. 단계별 상세 플로우

이 섹션은 결합 후 실제 동작을 단계별로 명시적으로 설명한다.

### Step 1. 요청 해석과 라우팅

현재:

- `ProjectPipeline.run()`이 거의 바로 브리프 생성과 역할 계획으로 진입한다.

결합 후:

- 라우터가 요청을 다음으로 분류한다.
  - 코드 중심
  - 문서 중심
  - 최신 웹 중심
  - 멀티모달 문서 중심
  - 혼합형

시너지:

- 잘못된 검색 방식을 초기에 줄인다.
- 코드 탐색과 문서 탐색을 분리할 수 있다.

### Step 2. 로컬 direct search

현재:

- 일부 로컬 registry와 skill catalog를 읽지만, 구조화된 direct search plane은 없다.

결합 후:

- 코드, YAML, 설정, registry, existing agent files는 direct search 우선으로 본다.
- symbol, import path, file path, status file, policy file 등은 벡터 검색보다 direct search를 우선한다.

시너지:

- Claude Code식 강점을 내부 시스템에 흡수한다.
- 코드 검색에서 RAG 과사용을 막는다.

### Step 3. 문서 파싱과 청킹

현재:

- 스킬 메타데이터 embed 수준은 있지만, 문서 청킹 파이프라인은 없다.

결합 후:

- README, SKILL.md, docs, 위키, 외부 조사 결과, 테스트 로그를 ingestion 대상으로 삼는다.
- 문서를 parser로 정규화하고 chunk로 분할한다.
- 각 chunk에 source, freshness, repo, skill_id, language, section metadata를 붙인다.
- 문서가 복잡하면 optional하게 RAG-Anything 계층을 사용한다.

시너지:

- 긴 문서도 근거 단위로 재사용할 수 있다.
- NotebookLM에 긴 문서를 통째로 던지는 구조보다 안정적이다.

### Step 4. 인덱싱

현재:

- `SemanticEmbedder`는 skill metadata embedding 캐시를 유지한다.

결합 후:

- 문서 chunk에 대해 dense embedding index를 만든다.
- 동시에 BM25 또는 equivalent sparse index를 구축한다.
- 성공/실패 설치 결과, 평가 로그도 retrieval 자산으로 축적한다.

시너지:

- "현재 문서", "과거 evidence", "실패 경험"이 모두 재사용 가능해진다.

### Step 5. Hybrid Retrieval

현재:

- skill relevance는 keyword/category/semantic 혼합 점수를 사용한다.
- Himari 후보 점수는 token overlap, 파일 존재, last_test_ok 중심이다.

결합 후:

- 질의마다 dense retrieval과 sparse retrieval을 동시에 수행한다.
- metadata filter를 함께 사용한다.
  - role filter
  - repo filter
  - source filter
  - freshness filter
  - trusted source filter

시너지:

- exact match와 semantic match를 동시에 확보한다.
- 같은 이름, 비슷한 이름, 오래된 버전 문제를 줄인다.

### Step 6. Reranker와 evidence pack 생성

현재:

- 상위 후보를 정렬해 `top_candidate` 중심으로 evidence_pack을 만든다.

결합 후:

- top-N 검색 결과를 reranker로 다시 평가한다.
- evidence_pack에는 다음을 포함한다.
  - why selected
  - source
  - freshness
  - trust score
  - matching rationale
  - conflicting evidence

시너지:

- Lilith의 의사결정 품질이 올라간다.
- 외부 설치 오판과 중복 생성이 감소한다.

### Step 7. Lilith 계획 수립

현재:

- Lilith는 project_brief와 role_plan 중심으로 역할을 나누고 실행한다.

결합 후:

- Lilith는 retrieval-backed evidence를 받는다.
- 이 evidence를 기준으로 다음 셋 중 하나를 선택한다.
  - reuse
  - install
  - build

시너지:

- PM 판단이 LLM 감에 덜 의존한다.
- 역할 계획과 skill plan이 더 정교해진다.

### Step 8. 외부 설치

현재:

- source priority와 candidate score로 외부 설치를 시도한다.

결합 후:

- source trust, repo allowlist, license, checksum, signature, last verification time을 함께 본다.
- retrieval score가 높아도 trust gate를 통과하지 못하면 설치하지 않는다.

시너지:

- 잘못된 외부 후보 설치 위험을 줄인다.
- "찾기는 잘했는데 위험한 코드" 문제를 별도로 통제할 수 있다.

### Step 9. 신규 생성

현재:

- Builder는 evidence-first, quick guard, isolated test를 사용한다.

결합 후:

- Builder는 더 풍부한 evidence_pack을 받는다.
- 예시 코드, API usage, failure pattern, prior attempt log까지 참조한다.

시너지:

- 생성 품질이 좋아지고 재시도 횟수가 감소한다.
- 빌드 실패가 더 설명 가능해진다.

### Step 10. 등록, 실행, 회고

현재:

- Registry와 workflow_apply가 built skill을 반영한다.

결합 후:

- 실행 결과, 설치 실패 이유, 생성 실패 이유, 평가 결과를 다시 knowledge plane에 인덱싱한다.
- retrieval은 다음 요청에서 이 경험을 다시 활용한다.

시너지:

- system-wide learning loop가 생긴다.
- 반복 작업에서 품질과 속도가 함께 오른다.

## 7. 결합 시 시너지

### 7.1 검색 품질

- Himari가 단순 조사 에이전트에서 `Retrieval Director`로 진화한다.
- NotebookLM 의존도를 줄이고 자체 retrieval 품질을 높일 수 있다.
- exact match와 semantic match를 동시에 잡을 수 있다.

### 7.2 스킬 재사용률

- 이미 있는 스킬을 못 찾고 새로 만드는 중복이 줄어든다.
- 설치 가능한 외부 후보를 더 정확하게 찾는다.
- 실패했던 후보를 다시 뽑는 비율이 줄어든다.

### 7.3 안전성

- 공급망 위험과 검색 품질 위험을 분리해 통제할 수 있다.
- 현재 Builder/Registry gate에 provenance gate를 추가할 수 있다.

### 7.4 속도와 비용

- 반복 질문은 더 빨라진다.
- 매번 NotebookLM과 웹 검색만으로 해결하는 구조보다 비용이 낮아질 가능성이 높다.
- reranker는 비용이 들지만 top-K에만 적용하면 통제 가능하다.

### 7.5 설명 가능성

- "왜 이 스킬을 설치했는가"
- "왜 새로 만들었는가"
- "왜 이 문서를 근거로 삼았는가"

를 evidence_pack으로 설명할 수 있다.

### 7.6 멀티모달 확장성

- PDF, 스크린샷, 표, 이미지 문서가 늘어날 경우 optional RAG-Anything 도입이 쉬워진다.

## 8. 현재 시스템과 결합 시스템의 차이점

### 8.1 구조적 차이

현재 시스템:

- orchestration-first
- request-time research
- shallow semantic retrieval
- NotebookLM and LLM suggestion heavy
- skill-centric evidence

결합 시스템:

- orchestration-first + retrieval-first
- persistent knowledge reuse
- document-centric and skill-centric retrieval
- hybrid search + reranker
- provenance-aware evidence

### 8.2 유지할 것

- Lilith 중심 의사결정
- ProjectPipeline
- SkillOrchestrator
- Builder guard와 isolated test
- Registry quality gate
- direct local search

### 8.3 바꿀 것

- Himari 내부 검색 방식
- evidence_pack 구조
- external candidate ranking 기준
- notebook-centric research dependency
- long-term retrieval memory

## 9. 주요 위험과 통제 전략

### Risk 1. 코드 검색까지 RAG로 처리하려는 과잉 설계

대응:

- 코드와 설정은 direct search 우선
- 문서와 로그는 hybrid RAG 우선

### Risk 2. 인덱스 stale 문제

대응:

- freshness timestamp
- source TTL
- repo update hook

### Risk 3. 외부 스킬 공급망 위험

대응:

- allowlist
- checksum
- license check
- trust score
- human approval

### Risk 4. 비용과 지연 증가

대응:

- reranker는 top-K에만 적용
- multimodal ingestion은 optional
- direct search로 불필요한 retrieval 호출 최소화

### Risk 5. 근거 충돌

대응:

- evidence_pack에 conflicting evidence 명시
- latest web와 internal memory의 우선순위를 정책으로 관리

## 10. 권장 도입 순서

### Phase 1. Retrieval Router와 Evidence Schema

- 요청 라우팅 계층 추가
- evidence_pack schema 확장
- source, freshness, trust, rationale 필드 추가

### Phase 2. Internal Hybrid Retrieval

- README, SKILL.md, docs, logs 인덱싱
- dense + sparse retrieval 도입
- metadata filtering 추가

### Phase 3. Reranker와 External Trust Gate

- reranker 추가
- source trust score 추가
- external install preflight 강화

### Phase 4. Feedback Memory

- 설치/생성/실행 결과를 재인덱싱
- 실패 패턴을 retrieval 자산으로 저장

### Phase 5. Optional Multimodal Layer

- PDF, 표, 이미지, 수식 문서 수요가 충분할 때만 RAG-Anything 계층 도입

## 11. 구현 판단 기준

### 단순 hybrid retrieval만으로 충분한 경우

- 검색 대상이 Markdown, README, SKILL.md, 코드 설명 위주일 때
- 문서 포맷이 단순하고 text extraction 난도가 낮을 때

### RAG-Anything 도입이 타당한 경우

- PDF와 Office 문서가 많을 때
- 표, 이미지, 수식이 retrieval 품질에 중요할 때
- 문서 인텔리전스가 Himari 품질의 병목일 때

## 12. 최종 결론

가장 좋은 결합 방식은 다음과 같다.

1. 현재 `agent-factory`의 Execution Plane은 유지한다.
2. Himari를 Retrieval Director로 재정의한다.
3. 코드와 로컬 구조는 direct search로 처리한다.
4. 문서와 과거 지식은 hybrid RAG로 처리한다.
5. top-K 후보는 reranker로 다시 평가한다.
6. Lilith는 retrieval-backed evidence를 바탕으로 reuse/install/build를 결정한다.
7. Builder와 Registry는 최종 실행 게이트로 유지한다.
8. 멀티모달 문서가 필요할 때만 RAG-Anything를 아래 계층으로 도입한다.

즉, 결합 후의 `agent-factory`는 "에이전트를 실행하는 공장"에서 "근거 기반으로 에이전트를 계획, 조달, 생성, 검증, 학습하는 공장"으로 진화한다.

## 13. 참고 자료

외부 참고:

- Anthropic Claude Code documentation
  - https://docs.anthropic.com/en/docs/claude-code/overview
  - https://docs.anthropic.com/en/docs/claude-code/settings
  - https://docs.anthropic.com/en/docs/claude-code/memory
- RAG-Anything
  - https://github.com/HKUDS/RAG-Anything
  - https://arxiv.org/abs/2510.12323
  - https://pypi.org/project/raganything/

내부 참고:

- `agent_launcher.py`
- `core/project_pipeline.py`
- `core/researcher.py`
- `core/skill_procurer.py`
- `core/external_skill_sources.py`
- `core/semantic_embedder.py`
- `core/skill_loader.py`
- `core/skill_cache.py`
- `core/builder.py`
- `core/registry_manager.py`
