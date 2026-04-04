# 교차검증 학습 루프 설계 — JudgmentLedger

> 날짜: 2026-04-03
> 상태: Draft
> 대상: `core/cross_verification.py`

---

## 1. 문제: 교차검증 루프에 학습이 없다

현재 `CrossVerificationLoop`는 **라운드를 돌지만 학습하지 않는다**.

```
라운드 1: 실행 → 리뷰 → 판정(fail) → feedback 생성
라운드 2: 실행 → 리뷰 → 판정(fail) → feedback 생성
라운드 3: 실행 → 리뷰 → 판정(fail) → MAX 소진

문제: 라운드 2의 판정자는 라운드 1에서 뭘 틀렸는지 모른다
      라운드 3의 리뷰어는 라운드 1~2에서 반복된 패턴을 모른다
```

비교: 이미 학습이 동작하는 곳들

| 시스템 | 학습 메커니즘 | 핵심 |
|--------|-------------|------|
| `revision_loop` | `revision_history` + `critique_fn` | 매 iteration 재비평, 이전 시도 이력 주입 |
| ISE | `StrategyLedger` | 실패 전략 기록, 동일 전략 반복 차단 |
| SkillEvolution | `execution_count` + 품질 감사 | 10회 실행마다 스킬 메타 갱신 |

**교차검증에는 이 학습 메커니즘이 없다.**

---

## 2. 설계 목표

> 교차검증 루프의 세 단계(실행, 리뷰, 판정) 모두에 **라운드 간 학습**을 도입한다.

핵심 원칙:
- **같은 실수를 반복하지 않는다** (StrategyLedger 패턴)
- **매 라운드 재비평한다** (revision_loop critique_fn 패턴)
- **이전 라운드의 발견을 다음 라운드에 전달한다** (누적 학습)

---

## 3. 핵심 개념: JudgmentLedger

ISE의 `StrategyLedger`를 교차검증 판정에 특화한 버전.

### 3.1 데이터 구조

```python
@dataclass
class JudgmentEntry:
    """단일 라운드의 판정 기록."""
    round_num: int
    timestamp: str

    # 실행 결과 요약
    providers_used: list[str]
    execution_scores: dict[str, int]    # {provider_id: review_score}

    # 판정 결과
    verdict: str                        # pass/fail/partial/abort
    confidence: float
    selected_provider: str

    # 학습 데이터 (핵심)
    failure_patterns: list[str]         # 발견된 실패 패턴
    missed_issues: list[str]            # 이전 라운드에서 놓쳤던 이슈 (회고)
    recurring_issues: list[str]         # 2회 이상 반복되는 이슈
    review_blind_spots: list[str]       # 리뷰어가 못 잡은 것 (판정자가 발견)

    # 개선 추적
    what_improved: list[str]            # 이전 대비 개선된 점
    what_regressed: list[str]           # 이전 대비 악화된 점
    confidence_delta: float             # 이전 confidence와의 차이


class JudgmentLedger:
    """교차검증 판정 원장 — 라운드 간 학습 데이터 축적."""

    def __init__(self):
        self.entries: list[JudgmentEntry] = []

    def record(self, entry: JudgmentEntry) -> None:
        self.entries.append(entry)

    # ── 학습 데이터 생성 (프롬프트 주입용) ──

    def failed_patterns_summary(self) -> str:
        """반복 실패 패턴 요약 — 실행 에이전트 프롬프트에 주입."""
        # StrategyLedger.failed_strategies_summary()와 동일 패턴
        ...

    def review_lessons(self) -> str:
        """리뷰어가 놓친 것들 요약 — 리뷰 에이전트 프롬프트에 주입."""
        ...

    def judgment_lessons(self) -> str:
        """이전 판정의 실수/개선 요약 — 판정 에이전트 프롬프트에 주입."""
        ...

    def recurring_issues(self) -> list[str]:
        """2라운드 이상 반복되는 이슈 목록."""
        ...

    def confidence_trend(self) -> str:
        """confidence 추이 — stall 감지 정밀화."""
        ...
```

### 3.2 학습 주입 흐름

```
라운드 1:
  ① 실행: (초기 — 학습 데이터 없음)
  ② 리뷰: (초기 — 학습 데이터 없음)
  ③ 판정: verdict=fail, failure_patterns=["보안:인증누락", "테스트:경계값"]
  → JudgmentLedger.record()

라운드 2:
  ① 실행: _refine_task()에 ledger.failed_patterns_summary() 주입
     → "이전에 '보안:인증누락'으로 실패했다. 이번에는 인증을 반드시 포함하라"
  ② 리뷰: 프롬프트에 ledger.review_lessons() 주입
     → "이전 라운드에서 리뷰어가 인증 누락을 못 잡았다. 보안 항목을 집중 검토하라"
  ③ 판정: 프롬프트에 ledger.judgment_lessons() 주입
     → "라운드 1에서 fail 판정했는데 인증 누락이 주요 원인이었다.
        이번에 인증이 추가됐는지 확인하라. confidence 0.45→? 추이 참고."
  → JudgmentLedger.record(missed_issues=["이전에 못 잡은 것"], what_improved=["인증 추가됨"])

라운드 3:
  ① 실행: 누적된 2라운드 이력 기반 태스크 리파인
  ② 리뷰: recurring_issues 집중 검토 지시
  ③ 판정: 전체 학습 이력 + 트렌드 기반 판정
```

---

## 4. 변경 상세

### 4.1 새 파일: `core/judgment_ledger.py` (~120줄)

```python
"""
core/judgment_ledger.py
========================
교차검증 판정 원장 — StrategyLedger의 교차검증 특화 버전.
라운드 간 학습 데이터를 축적하여 동일 실패 반복을 방지한다.
"""

@dataclass
class JudgmentEntry:
    round_num: int
    timestamp: str
    providers_used: list[str]
    execution_scores: dict[str, int]
    verdict: str
    confidence: float
    selected_provider: str
    failure_patterns: list[str]
    missed_issues: list[str]          # 회고: 이전에 놓친 것
    recurring_issues: list[str]       # 2+회 반복 이슈
    review_blind_spots: list[str]     # 리뷰가 못 잡은 것
    what_improved: list[str]
    what_regressed: list[str]
    confidence_delta: float


class JudgmentLedger:
    def __init__(self):
        self.entries: list[JudgmentEntry] = []

    def record(self, entry: JudgmentEntry) -> None: ...

    # ── 프롬프트 주입용 학습 데이터 ──

    def failed_patterns_summary(self, max_items: int = 5) -> str:
        """실행 에이전트용: 이전 라운드에서 실패한 패턴 목록.
        → _refine_task()에서 사용."""
        failed = []
        for e in self.entries:
            if e.verdict != "pass":
                for p in e.failure_patterns:
                    if p not in failed:
                        failed.append(p)
        if not failed:
            return ""
        lines = [f"  - R{e.round_num}: {p}" for e in self.entries for p in e.failure_patterns if e.verdict != "pass"]
        return (
            "[이전 라운드 실패 패턴 — 이번에 반드시 해결하세요]\n"
            + "\n".join(lines[-max_items:])
        )

    def review_lessons(self) -> str:
        """리뷰 에이전트용: 이전 리뷰어가 놓친 것.
        → _cross_verify()에서 사용."""
        if not self.entries:
            return ""
        blind_spots = []
        for e in self.entries:
            blind_spots.extend(e.review_blind_spots)
        if not blind_spots:
            return ""
        return (
            "[이전 라운드에서 리뷰어가 놓친 항목 — 이번에 집중 검토하세요]\n"
            + "\n".join(f"  - {b}" for b in blind_spots[-5:])
        )

    def judgment_lessons(self) -> str:
        """판정 에이전트용: 이전 판정의 맥락과 결과.
        → _judge_phase()에서 사용."""
        if not self.entries:
            return ""
        lines = []
        for e in self.entries:
            lines.append(
                f"- R{e.round_num}: verdict={e.verdict}, confidence={e.confidence:.2f} "
                f"(delta={e.confidence_delta:+.2f})\n"
                f"  실패 패턴: {e.failure_patterns}\n"
                f"  개선된 점: {e.what_improved}\n"
                f"  악화된 점: {e.what_regressed}"
            )
        return (
            "[이전 라운드 판정 이력 — 동일한 판정 실수를 반복하지 마세요]\n"
            + "\n".join(lines)
        )

    def recurring_issues(self) -> list[str]:
        """2라운드 이상 반복되는 이슈 — 구조적 문제 신호."""
        from collections import Counter
        all_patterns = []
        for e in self.entries:
            all_patterns.extend(e.failure_patterns)
        return [p for p, c in Counter(all_patterns).items() if c >= 2]
```

### 4.2 `cross_verification.py` 변경 (기존 메서드 수정)

#### A. `__init__` — Ledger 초기화

```python
def __init__(self, workspace, level="dynamic", max_rounds=None, providers=None):
    # ... 기존 코드 ...
    self.history: list[JudgmentResult] = []
    self.ledger = JudgmentLedger()  # ← 추가
```

#### B. `run()` — 라운드마다 Ledger 기록

```python
for round_num in range(1, self.max_rounds + 1):
    # ① 병렬 실행
    results = self._execute_parallel(current_task, system_prompt)

    # ② 교차 검증 — 학습 데이터 주입
    verified = self._cross_verify(results, current_task, round_num)  # ← round_num 추가

    # ③ Opus 최종 판정 — 학습 데이터 주입
    judgment = self._opus_judge(verified, task, round_num)
    self.history.append(judgment)

    # ③' Ledger 기록 (NEW)
    self._record_to_ledger(round_num, verified, judgment)

    # ... 기존 탈출 조건 ...

    # 태스크 리파인 — Ledger 기반으로 강화
    current_task = self._refine_task(task, judgment)  # 내부에서 ledger 참조
```

#### C. `_cross_verify()` — 리뷰 학습 주입

```python
def _cross_verify(self, results, original_task, round_num=1):
    # 이전 라운드에서 리뷰어가 놓친 것을 주입
    review_lessons = self.ledger.review_lessons()

    for i, reviewer in enumerate(results):
        target = results[(i + 1) % n]

        review_prompt = (
            f"[원래 태스크]\n{original_task}\n\n"
            f"[검토 대상 ({target.provider_id} 결과)]\n{target.output[:3000]}\n\n"
        )

        # ── 학습 주입 (라운드 2+) ──
        if review_lessons:
            review_prompt += f"\n{review_lessons}\n\n"

        recurring = self.ledger.recurring_issues()
        if recurring:
            review_prompt += (
                f"[반복 이슈 — 특히 집중 검토]\n"
                + "\n".join(f"  - {r}" for r in recurring) + "\n\n"
            )

        review_prompt += (
            f"다음 항목을 분석하고 JSON으로 답변하세요:\n"
            f'{{"score": 0~100, "issues": ["문제1", ...], '
            f'"feedback": "전체 코멘트"}}'
        )
        # ... execute_cli_chat 호출 (기존과 동일)
```

#### D. `_judge_phase()` — 판정 학습 주입

```python
def _judge_phase(self, verified, original_task, round_num, judge_provider):
    summaries = [...]  # 기존 요약 (변경 없음)

    # ── 학습 데이터 주입 ──
    judgment_lessons = self.ledger.judgment_lessons()

    judge_prompt = (
        f"[원래 태스크]\n{original_task}\n\n"
        f"[{len(verified)}개 구현 결과 + 교차 리뷰 요약]\n\n"
        + "\n\n---\n\n".join(summaries)
    )

    # 라운드 2+ 학습 주입
    if judgment_lessons:
        judge_prompt += f"\n\n{judgment_lessons}\n"

    recurring = self.ledger.recurring_issues()
    if recurring:
        judge_prompt += (
            f"\n[구조적 반복 이슈 — 이것이 해결되지 않으면 pass 불가]\n"
            + "\n".join(f"  - {r}" for r in recurring) + "\n"
        )

    judge_prompt += (
        f"\n\n## 판정 지침\n\n"
        # ... 기존 JSON 스키마 ...

        # 추가: 회고 필드
        f'  "missed_issues": ["이전 라운드에서 놓쳤지만 이번에 발견한 이슈"],\n'
        f'  "review_blind_spots": ["리뷰어가 못 잡았지만 판정자가 발견한 것"],\n'
        f'  "what_improved": ["이전 대비 개선된 점"],\n'
        f'  "what_regressed": ["이전 대비 악화된 점"],\n'
    )
    # ... execute_cli_chat 호출 (기존과 동일)
```

#### E. `_refine_task()` — Ledger 기반 리파인 강화

```python
def _refine_task(self, original_task, judgment):
    parts = []

    # 1. Ledger 누적 학습 데이터
    failed_summary = self.ledger.failed_patterns_summary()
    if failed_summary:
        parts.append(failed_summary)

    # 2. 반복 이슈 경고
    recurring = self.ledger.recurring_issues()
    if recurring:
        parts.append(
            "[구조적 반복 이슈 — 접근 방식을 바꾸세요]\n"
            + "\n".join(f"  - {r}" for r in recurring)
        )

    # 3. 직전 라운드 피드백 (기존)
    if judgment.feedback:
        parts.append(f"[직전 라운드 피드백]\n{judgment.feedback}")

    # 4. 원래 태스크
    parts.append(f"[원래 태스크]\n{original_task}")

    return "\n\n".join(parts)
```

#### F. `_record_to_ledger()` — 라운드 결과 기록 (새 메서드)

```python
def _record_to_ledger(self, round_num, verified, judgment):
    """라운드 완료 후 Ledger에 학습 데이터를 기록한다."""
    prev = self.ledger.entries[-1] if self.ledger.entries else None

    entry = JudgmentEntry(
        round_num=round_num,
        timestamp=now_iso(),
        providers_used=[r.provider_id for r in verified],
        execution_scores={r.provider_id: r.review_score for r in verified},
        verdict=judgment.verdict,
        confidence=judgment.confidence,
        selected_provider=judgment.selected_provider,
        failure_patterns=judgment.failure_patterns,
        missed_issues=judgment.missed_issues,           # 판정 JSON에서 파싱
        recurring_issues=self.ledger.recurring_issues(), # 현재까지 누적
        review_blind_spots=judgment.review_blind_spots,  # 판정 JSON에서 파싱
        what_improved=judgment.what_improved,
        what_regressed=judgment.what_regressed,
        confidence_delta=(
            judgment.confidence - prev.confidence if prev else 0.0
        ),
    )
    self.ledger.record(entry)
```

---

## 5. 학습 레이어 통합 — 6번째 레이어

기존 5개 학습 레이어에 교차검증 학습이 추가:

| # | 레이어 | 학습 대상 | 메커니즘 |
|---|--------|----------|----------|
| 1 | SkillSelfEvolutionHook | 스킬 메타데이터 | 10회 실행마다 품질 감사 |
| 2 | CrossVerification._trigger_evolution | 스킬 코드 | failure_patterns → evolve_skill |
| 3 | CrossVerification._refine_task | 태스크 입력 | 피드백 → 다음 라운드 태스크 보강 |
| 4 | ISE StrategyLedger | 실행 전략 | 실패 전략 기록 → 반복 방지 |
| 5 | revision_loop revision_history | 문서 수정 | 비평→수정→재비평 학습 루프 |
| **6** | **JudgmentLedger (NEW)** | **판정/리뷰 품질** | **라운드 간 판정 이력 학습** |

레이어 3(기존 `_refine_task`)과 레이어 6(JudgmentLedger)의 차이:
- 레이어 3: 실행 에이전트에게 "뭘 고쳐라" 전달 (단방향)
- 레이어 6: 실행·리뷰·판정 **세 단계 모두**에 학습 데이터 주입 (양방향, 누적)

---

## 6. JudgmentResult 확장

`JudgmentResult` 데이터 클래스에 학습 필드 추가:

```python
@dataclass
class JudgmentResult:
    # ... 기존 필드 유지 ...
    verdict: str
    selected_provider: str
    merged_output: str
    feedback: str
    failure_patterns: list[str] = field(default_factory=list)
    confidence: float = 0.0
    round_num: int = 0
    keep_parts: list[str] = field(default_factory=list)
    discard_parts: list[str] = field(default_factory=list)

    # ── 학습 필드 (NEW) ──
    missed_issues: list[str] = field(default_factory=list)
    review_blind_spots: list[str] = field(default_factory=list)
    what_improved: list[str] = field(default_factory=list)
    what_regressed: list[str] = field(default_factory=list)
```

`_parse_judgment_json()`에서 새 필드도 파싱:
```python
def _parse_judgment_json(self, text):
    parsed = self._extract_json_by_depth(text)
    # ... 기존 파싱 ...
    # 새 필드 (없으면 빈 리스트 — 하위호환)
    parsed.setdefault("missed_issues", [])
    parsed.setdefault("review_blind_spots", [])
    parsed.setdefault("what_improved", [])
    parsed.setdefault("what_regressed", [])
    return parsed
```

---

## 7. 토큰 비용 분석

### 추가되는 토큰

| 주입 위치 | 라운드 1 | 라운드 2 | 라운드 3 |
|-----------|:---:|:---:|:---:|
| 실행 (failed_patterns_summary) | 0 | ~200 tok | ~400 tok |
| 리뷰 (review_lessons) | 0 | ~150 tok | ~300 tok |
| 판정 (judgment_lessons) | 0 | ~300 tok | ~500 tok |
| **라운드별 추가** | **0** | **~650 tok** | **~1200 tok** |

### 절감 효과

- 정확한 판정 → 불필요한 라운드 감소 (enterprise 3→2라운드)
- 반복 이슈 조기 발견 → fail→partial→pass가 아닌 fail→pass
- **3라운드 enterprise 기준: 추가 ~1,850 tok vs 1라운드 절감 ~15,000 tok**

---

## 8. 에이전트 기반 확장 (Phase 2)

JudgmentLedger가 정착된 후, 이전 설계(파일 직접 접근)를 결합:

```
Phase 1 (이 설계): 학습 루프 도입
  → 프롬프트에 학습 데이터 주입
  → 기존 execute_cli_chat 그대로 사용
  → 변경 최소, 효과 즉시

Phase 2 (후속): 에이전트 컨텍스트 확장
  → artifact 파일 저장 + 직접 읽기 지시
  → 프로젝트 보드/메모리 참조
  → Phase 1의 학습 + Phase 2의 컨텍스트 = 완전한 에이전트 판정
```

Phase 1만으로도 "학습하는 교차검증"의 핵심 가치를 달성한다.

---

## 9. 변경 파일 목록

| 파일 | 변경 | 규모 |
|------|------|------|
| `core/judgment_ledger.py` | **신규** | ~120줄 |
| `core/cross_verification.py` | 수정 | `__init__`, `run`, `_cross_verify`, `_judge_phase`, `_refine_task` + `_record_to_ledger` 추가 |
| `core/cross_verification.py` | 수정 | `JudgmentResult` 데이터 클래스에 4필드 추가 |
| `af.spec` | 수정 | `core.judgment_ledger` hiddenimport 추가 |
| `Master_Blueprint.md` | 수정 | §3 Hooks/Cross-verification, §12 이력 |

---

## 10. 구현 순서

1. `core/judgment_ledger.py` 생성 (JudgmentEntry + JudgmentLedger)
2. `JudgmentResult` 데이터 클래스에 학습 필드 추가
3. `_parse_judgment_json()` 새 필드 파싱 추가
4. `__init__`에 `self.ledger` 초기화
5. `run()`에 `_record_to_ledger()` 호출 추가
6. `_cross_verify()` 학습 주입
7. `_judge_phase()` 학습 주입 + 회고 필드 요청
8. `_refine_task()` Ledger 기반 강화
9. `af.spec` hiddenimport 추가
10. Blueprint 업데이트

---

## 11. 검증 방법

```bash
# 1. Enterprise 3라운드 실행
af -p test_learn -t "JWT 인증 API 구현" --mode fsa --pipeline project

# 2. 학습 동작 확인
# - 라운드 2 실행 프롬프트에 "이전 라운드 실패 패턴" 포함되는지
# - 라운드 2 리뷰 프롬프트에 "이전 리뷰어가 놓친 항목" 포함되는지
# - 라운드 2 판정 프롬프트에 "이전 판정 이력" 포함되는지

# 3. Ledger 기록 확인
# - JudgmentLedger.entries 에 각 라운드의 학습 데이터 존재

# 4. 회귀 확인
# - Dynamic 1라운드: ledger가 비어있으므로 기존과 동일 동작
# - Starter: 교차검증 비활성이므로 영향 없음
```
