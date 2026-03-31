# Improved Artifact Pipeline Architecture v2.0

> 기존 설계(20260330)를 기반으로, 2025-2026 최신 연구와 agent-factory 코드베이스 현황을 반영한 개선 아키텍처

---

## 1. 기존 설계 분석 및 개선 필요 영역

### 1.1 기존 설계의 강점 (유지할 것)

| 강점 | 근거 |
|------|------|
| 2-pass semantic critique | 단일 생성 대비 ~20% 품질 향상 (Self-Refine 연구) |
| 코드 기반 structural gate | 결정론적 검증이 LLM 판정보다 안정적 |
| Final rewrite 단계 | 비평 + 검증 반영 재작성이 품질 차이를 만듦 |
| Aggregated verdict (코드 집계) | 주관적 judge agent 단일 의존 회피 |
| Evidence -> Draft -> Critique -> Enrich -> Rewrite 흐름 | 검증된 패턴 |

### 1.2 개선이 필요한 6개 영역

| # | 영역 | 현재 | 개선 방향 | 기대 효과 |
|---|------|------|-----------|-----------|
| 1 | Critique 모델 동질성 | 동일 모델 draft+critique | 이종 모델(heterogeneous) 비평 | 정확도 4-6% 향상 (A-HMAD 연구) |
| 2 | 비용 일률 투입 | 모든 산출물 동일 파이프라인 | 신뢰도 기반 적응형 라우팅 | 비용 최대 60% 절감 |
| 3 | 평가 rubric 부재 | 자유형 LLM 판정 | 실행 가능한 rubric 컴파일 | 평가 일관성 + 재현성 |
| 4 | 근거-주장 추적 없음 | evidence_summary만 존재 | claim-to-source traceability | grounding score 측정 가능 |
| 5 | 수렴 조건 없음 | 고정 2-pass | 수렴 감지 + 최대 반복 제한 | 불필요한 재작성 방지 |
| 6 | 장애 복구 미흡 | 단계 실패 시 전체 중단 | 단계별 독립 복구 + graceful degradation | 파이프라인 안정성 |

---

## 2. 개선된 아키텍처 전체 흐름

```
Request
  -> [Stage 1] Task Profiler + Complexity Classifier
  -> [Stage 2] Evidence Acquisition (Deep / Lite)
  -> [Stage 3] Evidence Verification + Per-Source Scoring
  -> [Gate A] Confidence Gate (Fast Path 분기)
  -------- Fast Path: 간단한 요청은 여기서 Draft -> Structural Gate -> 완료 --------
  -> [Stage 4] Draft Artifact Generation (Mid-tier Model)
  -> [Stage 5] Heterogeneous Parallel Critique
  -> [Stage 6] Gap-Driven Enrichment + Claim Traceability
  -> [Stage 7] Revised Artifact (with convergence check)
  -> [Stage 8] Structural Gate (Executable Rubric)
  -> [Gate B] Debate Necessity Classifier (iMAD)
  -------- Skip Path: 고품질 초안은 Agent QA 건너뜀 --------
  -> [Stage 9] Agent QA / Execution Check (Frontier Model)
  -> [Stage 10] Final Targeted Rewrite
  -> [Stage 11] Aggregated Verdict (Deterministic Formula)
  -> Accepted Artifact
```

### 핵심 변경점 3가지

1. **두 개의 적응형 게이트** 추가 (Gate A, Gate B) - 비용 최적화
2. **이종 모델 병렬 비평** - 품질 향상
3. **실행 가능한 rubric 기반 평가** - 일관성 향상

---

## 3. 각 단계 상세 설계

### Stage 1: Task Profiler + Complexity Classifier (개선)

기존 Task Profiler에 **복잡도 분류기**를 추가합니다.

**추가되는 판단 항목:**
- `complexity_tier`: simple | moderate | complex | critical
- `estimated_critique_value`: 비평이 품질을 실제로 올릴 확률 (0.0-1.0)
- `recommended_path`: fast | standard | gold

**복잡도 분류 기준:**

| Tier | 조건 | 경로 |
|------|------|------|
| simple | 단일 개념, 기존 템플릿 재사용 가능 | Fast Path (Gate A에서 분기) |
| moderate | 다중 요소, 표준 구조로 해결 가능 | Standard (Critique 1-pass) |
| complex | 기술 선택 필요, 다중 역할, 높은 리스크 | Gold (2-pass critique + Agent QA) |
| critical | 법적/보안/아키텍처 핵심 결정 | Gold + 강제 Agent QA + 인간 리뷰 요청 |

**출력 스키마 (기존 + 추가분):**
```json
{
  "artifact_type": "architecture_plan",
  "quality_mode": "gold",
  "freshness_needed": true,
  "comparison_mode": true,
  "execution_check_required": false,
  "risk_level": "high",
  "complexity_tier": "complex",
  "estimated_critique_value": 0.85,
  "recommended_path": "gold",
  "model_routing": {
    "draft": "mid-tier",
    "critique": "frontier",
    "rewrite": "mid-tier",
    "judge": "frontier"
  }
}
```

**구현 위치:** `core/project_pipeline.py` - `prepare()` 메서드 시작부

---

### Stage 2: Evidence Acquisition (기존 유지 + 소폭 개선)

기존 설계가 이미 충분히 깊습니다. 소폭 개선만 추가합니다.

**추가할 것: Per-Source Quality Metadata**

각 수집된 근거에 아래 메타데이터를 태깅합니다:
```json
{
  "source_id": "web_001",
  "source_type": "web|local|llm_prior|notebook",
  "relevance_score": 0.87,
  "recency": "2026-03-15",
  "authority_level": "primary|secondary|tertiary",
  "content_hash": "sha256:..."
}
```

**왜 필요한가:**
- RAG Triad (Context Relevance, Faithfulness, Answer Relevance) 측정의 전제 조건
- Stage 3 Evidence Verification에서 개별 소스 품질 판단 가능
- Stage 6에서 claim-to-source 추적의 기반

**구현 위치:** `core/researcher.py` - `HimariResearchAgent` 의 evidence_bundle 생성 로직

---

### Stage 3: Evidence Verification + Per-Source Scoring (개선)

기존 6-signal 검증에 **개별 소스 품질 점수**를 추가합니다.

**추가 검사 항목:**

| 검사 | 설명 | 가중치 |
|------|------|--------|
| source_diversity | 소스 유형이 2개 이상인지 | 0.15 |
| relevance_mean | 전체 소스 평균 관련도 | 0.20 |
| recency_compliance | 최신성 필요 요청에 최근 소스가 있는지 | 0.15 |
| authority_check | primary 소스가 1개 이상인지 | 0.10 |
| (기존 6-signal) | local_count, external_present, etc. | 0.40 |

**출력 (기존 + 확장):**
```json
{
  "status": "pass|partial|warn",
  "score": 0.78,
  "gaps": [],
  "per_source_scores": [
    {"source_id": "web_001", "quality": 0.87, "keep": true},
    {"source_id": "local_003", "quality": 0.42, "keep": false}
  ],
  "filtered_evidence_ids": ["web_001", "local_001", "local_002"]
}
```

**구현 위치:** `core/research_verifier.py` - `ResearchVerifier` 클래스 확장

---

### Gate A: Confidence Gate (신규)

**목적:** 간단한 요청을 전체 파이프라인에 태우지 않고 빠르게 처리

```
IF complexity_tier == "simple" AND evidence.status == "pass":
    -> Draft (fast model) -> Structural Gate -> Accept
ELSE:
    -> 정상 파이프라인 계속
```

**판단 기준:**
- Task Profiler의 `complexity_tier`가 `simple`
- Evidence Verification의 `score` >= 0.7
- `comparison_mode` == false
- `risk_level` == "low"

**비용 효과:**
- 전체 요청의 약 30-40%가 simple tier로 분류 가능 (기존 연구 기반)
- 이 요청들에 대해 Critique + Enrich + Agent QA 비용 100% 절감

**구현 위치:** `core/project_pipeline.py` - `prepare()` 와 `execute()` 사이에 라우팅 로직 추가

---

### Stage 4: Draft Artifact Generation (개선: 모델 라우팅)

**핵심 변경:** Draft에는 frontier 모델 대신 **mid-tier 모델**을 사용합니다.

**근거:**
- 최신 연구 결론: "생성보다 평가에 지능을 투자하라" (spend intelligence on evaluation, not generation)
- GPT-4 수준 품질이 2024년 $30/M → 2026년 $2-3/M으로 하락
- Draft는 어차피 Critique에서 수정되므로 완벽할 필요 없음

**모델 라우팅 매핑:**

| 단계 | 모델 티어 | 예시 |
|------|-----------|------|
| Draft | mid-tier | Claude Sonnet 4.6, GPT-4.1 |
| Critique | frontier | Claude Opus 4.6 |
| Rewrite | mid-tier | Claude Sonnet 4.6 |
| Agent QA Judge | frontier | Claude Opus 4.6 |

**구현 위치:** `core/model_router.py` - `ModelRouter.pick()` 에 단계별 라우팅 규칙 추가

---

### Stage 5: Heterogeneous Parallel Critique (핵심 개선)

이것이 가장 큰 품질 향상을 가져오는 변경입니다.

**기존 문제:**
- 동일 모델이 생성+비평 → 자기 편향(self-bias) 발생
- 순차 2-pass → 시간 비용

**개선: 이종 모델 병렬 비평**

```
Draft Artifact
  ├─> [Critic A: Claude Opus] 정합성 + 누락 검토
  ├─> [Critic B: Gemini Pro] 실행 가능성 + 기술 선택 검토
  └─> [Critic C: GPT-4.1] 근거 충분성 + 대안 검토 (선택적)
      │
      v
  Critique Aggregator (deterministic merge)
```

**왜 이종 모델인가:**
- A-HMAD 연구: 이종 모델 토론이 동종 대비 정확도 91% vs 82% (GSM-8K 기준)
- 각 모델이 다른 편향을 가지므로 상호 보완
- 이미 agent-factory에 multi-provider 인프라 존재 (`cross_verification.py`)

**Critique Aggregator 로직:**
```python
def aggregate_critiques(critiques: list[CritiqueResult]) -> MergedCritique:
    # 1. 모든 gap을 합집합으로 수집
    all_gaps = union(c.gaps for c in critiques)

    # 2. 2개 이상의 critic이 지적한 gap은 "confirmed"
    confirmed_gaps = [g for g in all_gaps if count(g) >= 2]

    # 3. 1개만 지적한 gap은 "suspected" (enrichment에서 확인)
    suspected_gaps = [g for g in all_gaps if count(g) == 1]

    # 4. 점수는 가중 평균 (frontier 모델에 높은 가중치)
    score = weighted_average(c.score for c in critiques, weights=[0.4, 0.35, 0.25])

    return MergedCritique(
        confirmed_gaps=confirmed_gaps,
        suspected_gaps=suspected_gaps,
        score=score,
        revision_instructions=prioritize(confirmed_gaps + suspected_gaps)
    )
```

**비평 차원 (기존 유지 + 확장):**

| 차원 | 설명 | 평가 주체 |
|------|------|-----------|
| goal_alignment | 원 요청과의 정합성 | Critic A |
| completeness | deliverable 누락 여부 | Critic A |
| feasibility | 구현 가능한 수준의 구체성 | Critic B |
| tech_choice | 기술 선택의 이유와 대안 | Critic B |
| evidence_grounding | 근거 없는 주장 비율 | Critic C |
| risk_coverage | risk마다 대응이 있는지 | Critic A |
| testability | acceptance criteria의 관찰 가능성 | Critic B |

**구현 위치:** `core/cross_verification.py` - 기존 multi-CLI 인프라를 critique 단계에도 재활용

---

### Stage 6: Gap-Driven Enrichment + Claim Traceability (개선)

기존 설계에 **claim-to-source 추적**을 추가합니다.

**Claim Traceability란:**
산출물의 모든 핵심 주장(claim)에 대해 어떤 근거(source)가 뒷받침하는지 명시적으로 연결

```json
{
  "claims": [
    {
      "claim_id": "C001",
      "text": "React 19의 서버 컴포넌트가 초기 로드 시간을 40% 단축",
      "supporting_sources": ["web_001", "web_003"],
      "grounding_score": 0.92,
      "status": "grounded"
    },
    {
      "claim_id": "C002",
      "text": "마이크로서비스 아키텍처가 이 규모에서 최적",
      "supporting_sources": [],
      "grounding_score": 0.0,
      "status": "ungrounded"
    }
  ]
}
```

**Enrichment 우선순위:**
1. `ungrounded` claim → 근거 수집 또는 claim 제거
2. `confirmed_gap` (2+ critics) → 즉시 보강
3. `suspected_gap` (1 critic) → 확인 후 판단

**구현 위치:** `core/researcher.py` - enrichment 함수 추가, evidence_bundle에 claim_map 필드 추가

---

### Stage 7: Revised Artifact + Convergence Detection (개선)

**핵심 추가: 수렴 감지**

고정 2-pass 대신 **품질 점수 변화량(delta)** 기반 종료 조건을 사용합니다.

```python
MAX_ITERATIONS = 3
CONVERGENCE_THRESHOLD = 0.05  # score delta

for i in range(MAX_ITERATIONS):
    revised = rewrite(draft, critique_feedback, enriched_evidence)
    new_score = evaluate(revised)

    delta = new_score - previous_score
    if delta < CONVERGENCE_THRESHOLD:
        # 수렴: 더 이상 개선되지 않음
        break

    previous_score = new_score
    draft = revised
```

**왜 필요한가:**
- 첫 rewrite: ~20% 품질 향상
- 두 번째 rewrite: ~5-8% 추가 향상
- 세 번째 이후: 수확 체감 (diminishing returns)
- 수렴 감지로 불필요한 반복 비용 절감

**구현 위치:** `core/project_pipeline.py` - revision loop에 delta 체크 추가

---

### Stage 8: Structural Gate with Executable Rubric (핵심 개선)

**기존:** 필수 섹션/필드 존재 여부만 체크
**개선:** **Rulers 패턴** 적용 - 자연어 rubric을 실행 가능한 사양으로 컴파일

**Rulers 패턴이란 (2026년 1월 발표):**
자연어로 작성된 평가 기준을 실행 가능한 사양으로 변환하여, 결정론적 근거 검증과 구조화된 채점을 수행하는 프레임워크

**적용 방법:**

```yaml
# rubrics/architecture_plan.yaml
rubric:
  name: "Architecture Plan Quality"
  dimensions:
    - name: completeness
      weight: 0.25
      levels:
        5: "모든 deliverable에 owner가 있고, 모든 risk에 대응 전략이 있음"
        3: "주요 deliverable에 owner가 있지만, 일부 risk 대응이 누락"
        1: "deliverable이나 risk가 대부분 누락"
      evidence_check: "deliverables[].owner != null AND risks[].mitigation != null"

    - name: evidence_grounding
      weight: 0.20
      levels:
        5: "모든 핵심 주장의 90%+ 가 출처와 연결됨"
        3: "50-89% 연결됨"
        1: "50% 미만 연결됨"
      evidence_check: "claims.grounded_ratio >= threshold"

    - name: feasibility
      weight: 0.20
      levels:
        5: "모든 기술 선택에 이유와 대안이 명시, 구현 단계가 구체적"
        3: "기술 선택은 있지만 대안 분석이 부족"
        1: "기술 선택이 불명확하고 구현 경로가 추상적"
      evidence_check: "modules[].tech_rationale != null"

    - name: testability
      weight: 0.15
      levels:
        5: "모든 acceptance criteria가 자동화 가능한 수준으로 구체적"
        3: "대부분 관찰 가능하지만 일부 추상적"
        1: "대부분 추상적이고 검증 방법 불명확"
      evidence_check: "acceptance_criteria[].verification_method != null"

    - name: consistency
      weight: 0.20
      levels:
        5: "역할/모듈/태스크 간 모순 없음, 의존성 그래프에 순환 없음"
        3: "사소한 불일치 1-2건"
        1: "명백한 모순이나 순환 의존성 존재"
      evidence_check: "no_circular_dependencies(task_graph)"

  thresholds:
    pass: 4.0
    pass_with_warnings: 3.0
    fail: "<3.0"
```

**실행 흐름:**
```
Rubric YAML → Rubric Compiler → Executable Checks + LLM Scoring Instructions
                                        ↓
                              Artifact → Check Runner → Dimension Scores → Weighted Aggregate
```

**기존 코드 검사와의 관계:**
- 기존 필드/섹션 존재 검사 → rubric의 `evidence_check` 필드로 흡수
- 기존 개수/임계값 검사 → rubric의 threshold로 흡수
- 추가: 차원별 점수가 생기므로 어디가 약한지 진단 가능

**구현 위치:**
- 신규: `core/rubric_compiler.py` - rubric YAML → 실행 가능 사양 변환
- 신규: `rubrics/` 디렉토리 - 산출물 유형별 rubric 정의
- 수정: `core/project_pipeline.py` - structural gate에서 rubric runner 호출

---

### Gate B: Debate Necessity Classifier (신규, iMAD 패턴)

**목적:** 모든 산출물에 비싼 Agent QA를 돌리지 않고, 실제로 필요한 경우만 선별

**iMAD (intelligent Multi-Agent Debate) 패턴:**
단일 에이전트의 자기 비평에서 추출한 언어적 특징으로 multi-agent debate가 품질을 올릴지 예측

**판단 로직:**
```python
def needs_agent_qa(critique_result, structural_gate_result, task_profile):
    # 강제 조건: critical tier는 무조건 Agent QA
    if task_profile.complexity_tier == "critical":
        return True
    if task_profile.execution_check_required:
        return True

    # 면제 조건: 모든 게이트 통과 + 높은 점수
    if (structural_gate_result.pass
        and critique_result.score >= 0.85
        and len(critique_result.confirmed_gaps) == 0):
        return False

    # 그 외: confirmed_gap이 있거나 점수가 낮으면 필요
    return (critique_result.score < 0.75
            or len(critique_result.confirmed_gaps) > 0
            or structural_gate_result.warnings_count > 2)
```

**비용 효과:**
- iMAD 연구: 불필요한 debate를 건너뛰어도 정확도 손실 < 2%
- S2-MAD 연구: 토큰 비용 최대 94.5% 절감

**구현 위치:** `core/project_pipeline.py` - Agent QA 호출 전 게이트 로직

---

### Stage 9: Agent QA / Execution Check (개선: 이종 병렬 검증)

기존 설계를 유지하되, cross_verification.py의 기존 인프라를 활용한 **병렬 이종 검증**으로 강화합니다.

**개선 구조:**
```
Revised Artifact
  ├─> [QA Agent: Claude Opus] 요구사항-산출물 매핑, 누락 찾기
  ├─> [QA Agent: Gemini] 코드/파일 관점 실행 가능성 검증
  └─> [Evaluator: Deterministic] 실패 원인 분석 + retry/escalate 판단
```

**이미 구현된 것 활용:**
- `cross_verification.py`의 multi-CLI 실행 + circular review
- `evaluator.py`의 StrategyEvaluator (retry/pivot/abort)

**추가할 것:**
- QA 결과에 **다차원 점수** 부여 (기존은 단일 pass/fail)
```json
{
  "execution_pass": true,
  "dimension_scores": {
    "requirement_coverage": 0.92,
    "implementation_feasibility": 0.85,
    "verification_completeness": 0.78,
    "regression_safety": 0.90
  },
  "qa_findings": [],
  "evaluator_action": "accept"
}
```

**구현 위치:** `core/cross_verification.py` - dimension_scores 추가

---

### Stage 10: Final Targeted Rewrite (개선: 전체 재작성 → 타겟 수정)

**기존 문제:** Final Rewrite가 전체 산출물을 다시 쓰면 이미 좋은 부분을 훼손할 수 있음

**개선:** Agent QA findings 기반 **타겟 수정만** 수행

```python
def final_targeted_rewrite(artifact, qa_findings, critique_gaps, gate_warnings):
    # 수정이 필요한 섹션만 식별
    sections_to_fix = identify_affected_sections(
        qa_findings + critique_gaps + gate_warnings
    )

    # 해당 섹션만 재작성
    for section in sections_to_fix:
        artifact[section] = rewrite_section(
            section=artifact[section],
            feedback=relevant_feedback(section, qa_findings),
            evidence=relevant_evidence(section)
        )

    # 변경되지 않은 섹션은 그대로 유지
    return artifact
```

**왜 타겟 수정인가:**
- 전체 재작성 시 이미 검증된 부분의 품질이 흔들릴 수 있음
- 타겟 수정은 변경 범위가 명확하므로 regression 위험 낮음
- 비용 효율적 (전체 재생성 대비 토큰 50-70% 절감)

**구현 위치:** `core/project_pipeline.py` - final_rewrite 로직 변경

---

### Stage 11: Aggregated Verdict with Deterministic Formula (개선)

**기존:** 임계값 기반 3단계 판정 (accepted / accepted_with_warnings / rejected)
**개선:** **다차원 점수 기반 결정론적 공식** + 주기적 인간 교정

**개선된 판정 공식:**

```python
def aggregate_verdict(evidence_v, critique_v, gate_v, qa_v, rewrite_v):
    # 1. 차단 조건 (어느 하나라도 실패하면 rejected)
    if gate_v.structural_pass == False:
        return "rejected", "structural gate failure"
    if qa_v.evaluator_action == "abort":
        return "rejected", "agent QA abort"

    # 2. 다차원 가중 점수
    dimensions = {
        "evidence_quality": evidence_v.score * 0.15,
        "semantic_quality": critique_v.score * 0.25,
        "structural_quality": gate_v.rubric_score * 0.20,  # 신규: rubric 점수
        "execution_quality": qa_v.dimension_scores.mean() * 0.25,
        "grounding_ratio": rewrite_v.grounded_claims_ratio * 0.15
    }

    total = sum(dimensions.values())

    # 3. 판정
    if total >= 0.80 and min(dimensions.values()) >= 0.10:
        return "accepted", dimensions
    elif total >= 0.65 and no_blockers(qa_v, gate_v):
        return "accepted_with_warnings", dimensions
    else:
        return "rejected", dimensions
```

**인간 교정 (Calibration) 추가:**
```
매 N번째 판정마다:
  1. 판정 결과와 실제 산출물을 사람에게 보여줌
  2. 사람이 동의/수정
  3. 수정된 결과를 calibration log에 저장
  4. calibration log 기반으로 가중치/임계값 조정
```

**구현 위치:**
- 수정: `core/project_pipeline.py` - aggregator 로직
- 신규: `data/calibration_log.jsonl` - 인간 교정 기록

---

## 4. 장애 복구 설계 (신규)

각 단계를 **독립적으로 복구 가능**하게 설계합니다.

### 4.1 단계별 복구 전략

| 단계 | 실패 유형 | 복구 전략 |
|------|-----------|-----------|
| Evidence Acquisition | 외부 소스 불가 | Graceful degradation: 로컬 근거만으로 진행, 품질 경고 태깅 |
| Evidence Verification | 점수 계산 실패 | Fallback: 기존 6-signal만으로 판정 |
| Draft Generation | LLM 타임아웃 | Retry (다른 모델) → 캐시된 템플릿 기반 생성 |
| Critique | 특정 Critic 실패 | 나머지 Critic 결과만으로 진행 (최소 1개 필요) |
| Enrichment | 추가 수집 실패 | 기존 evidence로 rewrite 진행 |
| Structural Gate | Rubric 파싱 실패 | Fallback: 기존 코드 기반 필드 체크 |
| Agent QA | Agent 실행 실패 | Fallback: structural gate 결과 + critique 결과로 판정 |
| Final Rewrite | LLM 실패 | Revised artifact을 최종본으로 승격 |

### 4.2 체크포인트 설계

```
각 단계 완료 시:
  1. 출력을 .checkpoint/{stage_name}.json 에 저장
  2. 재시작 시 마지막 성공 체크포인트부터 재개
  3. 체크포인트에 타임스탬프 + 입력 해시 포함 (변경 감지)
```

**구현 위치:** `core/project_pipeline.py` - 각 단계 wrapper에 checkpoint 로직 추가

---

## 5. 구현 단계 (Implementation Roadmap)

### Phase 1: 기반 인프라 (1-2주)

| 단계 | 작업 | 영향 범위 | 우선순위 |
|------|------|-----------|----------|
| 1-1 | `core/model_router.py`에 단계별 모델 라우팅 규칙 추가 | model_router.py | 높음 |
| 1-2 | Evidence per-source metadata 스키마 추가 | researcher.py, research_verifier.py | 높음 |
| 1-3 | Checkpoint 인프라 구축 | project_pipeline.py | 중간 |
| 1-4 | `rubrics/` 디렉토리 + 첫 rubric YAML 작성 | 신규 | 중간 |

### Phase 2: 핵심 품질 개선 (2-3주)

| 단계 | 작업 | 영향 범위 | 우선순위 |
|------|------|-----------|----------|
| 2-1 | Heterogeneous Parallel Critique 구현 | cross_verification.py 재활용 | 최고 |
| 2-2 | Rubric Compiler + Executable Rubric Gate | 신규: rubric_compiler.py | 높음 |
| 2-3 | Claim-to-source traceability | researcher.py, enrichment 로직 | 높음 |
| 2-4 | Convergence detection in revision loop | project_pipeline.py | 중간 |

### Phase 3: 비용 최적화 (1-2주)

| 단계 | 작업 | 영향 범위 | 우선순위 |
|------|------|-----------|----------|
| 3-1 | Gate A: Confidence Gate (Fast Path) | project_pipeline.py | 높음 |
| 3-2 | Gate B: Debate Necessity Classifier | project_pipeline.py | 중간 |
| 3-3 | Final Targeted Rewrite (전체→타겟) | project_pipeline.py | 중간 |

### Phase 4: 안정성 + 운영 (1주)

| 단계 | 작업 | 영향 범위 | 우선순위 |
|------|------|-----------|----------|
| 4-1 | 단계별 장애 복구 로직 | project_pipeline.py | 높음 |
| 4-2 | Aggregated Verdict 다차원 공식 | project_pipeline.py | 중간 |
| 4-3 | Calibration log 인프라 | 신규: data/calibration_log.jsonl | 낮음 |

---

## 6. 기존 설계 vs 개선 설계 비교

| 항목 | 기존 (v1.0) | 개선 (v2.0) |
|------|-------------|-------------|
| Critique 모델 | 동일 모델 2-pass | 이종 모델 병렬 (A-HMAD) |
| 비용 라우팅 | 일률 적용 | 복잡도 기반 적응형 (3 경로) |
| 평가 기준 | 암묵적 (LLM 자유 판단) | Executable Rubric (Rulers) |
| 근거 추적 | evidence_summary만 | claim-to-source traceability |
| 반복 종료 | 고정 2-pass | 수렴 감지 (delta < threshold) |
| 장애 복구 | 없음 | 단계별 독립 복구 + checkpoint |
| Final Rewrite | 전체 재작성 | 타겟 수정만 |
| Agent QA 필요성 | 항상 (gold mode) | iMAD 분류기로 선별 |
| 최종 판정 | 3차원 임계값 | 5차원 가중 공식 + 인간 교정 |
| Fast Path | 없음 | Gate A로 간단한 요청 빠르게 처리 |

---

## 7. 핵심 원칙 (기존 유지 + 확장)

**기존 (유지):**
1. 원문이 항상 source of truth다
2. NotebookLM은 저장소가 아니라 심층 분석기다
3. 최종 판정은 가능하면 코드 집계로 닫는다
4. 에이전트는 실제로 품질을 올리는 구간에서만 쓴다
5. 중요한 산출물은 생성 → 비평 → 보강 → 재작성으로 간다

**추가:**
6. **생성보다 평가에 지능을 투자한다** - Draft는 mid-tier, Judge는 frontier
7. **이종 모델이 동종 모델보다 낫다** - 비평과 검증에 서로 다른 모델 사용
8. **모든 주장은 근거와 연결되어야 한다** - claim-to-source traceability
9. **불필요한 비용은 게이트로 차단한다** - 적응형 라우팅
10. **각 단계는 독립적으로 실패하고 복구할 수 있어야 한다** - graceful degradation

---

## 8. 한 줄 요약

> **깊게 수집하고, 이종 모델로 병렬 비평하고, rubric으로 측정하고, 필요한 곳만 정밀 수정한다**

```
Deep Evidence
  -> Draft (mid-tier)
  -> Heterogeneous Parallel Critique (frontier x2-3)
  -> Targeted Enrich (claim traceability)
  -> Rewrite (convergence-gated)
  -> Executable Rubric Gate
  -> [iMAD gate] Agent QA (frontier, selective)
  -> Targeted Final Rewrite
  -> Deterministic Aggregated Verdict
```

---

## References

- A-HMAD: Adaptive Heterogeneous Multi-Agent Debate (Springer, 2025)
- iMAD: Intelligent Multi-Agent Debate for Efficient LLM Inference (arXiv:2511.11306)
- S2-MAD: Multi-Agent Debate with Adaptive Stability Detection (arXiv:2510.12697)
- Rulers: Locked Rubrics and Evidence-Anchored Scoring (arXiv:2601.08654, Jan 2026)
- RAGAS: Retrieval Augmented Generation Assessment (ragas.io)
- Unified Routing and Cascading for LLMs (ICLR 2026, arXiv:2410.10347)
- GATEKEEPER: Confidence Tuning for Model Cascades (OpenReview)
- Self-Refine: Iterative Refinement with Self-Feedback (NeurIPS 2023)
- Reflexion: Language Agents with Verbal Reinforcement Learning (NeurIPS 2023)
