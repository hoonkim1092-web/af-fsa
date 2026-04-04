# 세션 핸드오프 — 2026-04-03 (집 PC → 회사 PC)

> 브랜치: `agent-factory_harness_Claude_Setup_and_Pipeline_v1`
> 마지막 push 커밋: `238a9af` (22:06)
> 이 문서 이후 작업은 push 안 됨 — 회사 PC에서 이어서 진행

---

## 1. 완료된 코드 변경 (push 됨)

### A. `scripts/code_review_updater.py` — 신규 생성

독립 실행 코드 리뷰 스크립트. 3곳에서 호출됨:
- Claude Code PostToolUse hook (*.py 편집 후)
- git post-commit hook
- 수동: `python scripts/code_review_updater.py [workspace] [--context "..."]`

주요 기능:
- `git diff HEAD`로 변경 파일 감지
- 선택적 LLM 리뷰 (`--no-llm`이면 스킵)
- `docs/code-review.md`에 append
- 커밋 해시 기반 중복 방지 (`_last_logged_commit`)
- atomic write (tmp+fsync+replace)

버그 수정 3건 (238a9af):
- B1: 동일 커밋 중복 엔트리 방지
- B2: `_atomic_write` tmp 파일 정리
- B3: `_atomic_append`에 fsync 추가

### B. `.claude/settings.local.json` — PostToolUse hook 추가

```json
{
  "type": "command",
  "command": "fp=$TOOL_INPUT_file_path; case \"$fp\" in *.py) python /c/Project/agent-factory/scripts/code_review_updater.py --no-llm 2>/dev/null ;; esac",
  "timeout": 15
}
```
Write|Edit으로 .py 파일 수정할 때마다 자동 실행.

### C. `docs/code-review.md` — 신규 생성

살아있는 코드 리뷰 문서. 초기 상태 요약 (10개 버그 수정 이력) 포함.

### D. `run_factory_cli.py` — .env 자동 로드 추가

**문제**: `TAVILY_API_KEY`가 `.env`에 있지만 로드 안 됨 → Tavily/NotebookLM 작동 불가
**수정**: `_load_dotenv()` 함수 추가 (라인 17~29), 시작 시 자동 호출

### E. `core/project_pipeline.py` — revision_loop 학습 루프 전면 개편

`revision_loop_with_convergence()` 메서드 변경:

**이전**: 고정 critique로 반복 수정 (학습 없음)
**이후**:
- `critique_fn` 파라미터 추가 — 매 iteration 재비평
- `revision_history` 누적 — ISE StrategyLedger 패턴
- `best_artifact` 추적 — 항상 최고 점수 버전 반환
- 이전 시도 이력을 rewrite_fn에 주입 → 같은 수정 반복 방지

핵심 코드 구조:
```python
def revision_loop_with_convergence(
    self, draft, critique_feedback, evidence, rewrite_fn, score_fn,
    max_iterations=5, convergence_threshold=0.02,
    min_iterations=2, critique_fn=None,    # ← NEW
):
    revision_history: list[dict] = []       # ← NEW: 학습 이력

    for i in range(max_iterations):
        # Step 1: 매 iteration 재비평 (critique_fn)
        # Step 2: revision_history를 피드백에 주입
        # Step 3: rewrite_fn(current, live_feedback, evidence)
        # Step 4: 이력 기록 + best_artifact 갱신
```

---

## 2. 완료된 설계 (push 안 됨 — 이 문서에 포함)

### 교차검증 학습 루프 설계 — JudgmentLedger

**문제**: `CrossVerificationLoop`가 라운드를 돌지만 학습하지 않음
- 라운드 2 판정자가 라운드 1에서 뭘 틀렸는지 모름
- 리뷰어가 이전에 놓친 것을 반복해서 놓침
- 반복 실패 패턴을 감지 못함

**해결**: `JudgmentLedger` 도입 (ISE StrategyLedger의 교차검증 특화 버전)

#### 핵심 데이터

```python
@dataclass
class JudgmentEntry:
    round_num: int
    verdict: str                    # pass/fail/partial/abort
    confidence: float
    failure_patterns: list[str]     # 실패 패턴
    missed_issues: list[str]        # 이전에 놓친 이슈 (회고)
    recurring_issues: list[str]     # 2+회 반복 이슈
    review_blind_spots: list[str]   # 리뷰어가 못 잡은 것
    what_improved: list[str]        # 이전 대비 개선
    what_regressed: list[str]       # 이전 대비 악화
    confidence_delta: float
```

#### 학습 주입 흐름

```
R1: 실행 → 리뷰 → 판정(fail) → Ledger 기록
                                    ↓
R2: [실패패턴 주입]실행 → [맹점 주입]리뷰 → [판정이력 주입]판정 → Ledger 기록
                                                                      ↓
R3: [누적패턴]실행 → [누적맹점]리뷰 → [전체이력+반복이슈]판정
```

#### 3곳에 학습 데이터 주입

| 단계 | 주입 데이터 | Ledger 메서드 |
|------|-----------|---------------|
| 실행 (`_refine_task`) | 이전 실패 패턴 + 반복 이슈 | `failed_patterns_summary()` |
| 리뷰 (`_cross_verify`) | 이전 리뷰어 맹점 | `review_lessons()` |
| 판정 (`_judge_phase`) | 전체 판정 이력 + 트렌드 | `judgment_lessons()` |

#### 변경 파일

| 파일 | 변경 |
|------|------|
| `core/judgment_ledger.py` | **신규** ~120줄 |
| `core/cross_verification.py` | `__init__`, `run`, `_cross_verify`, `_judge_phase`, `_refine_task` 수정 + `_record_to_ledger` 추가 |
| `core/cross_verification.py` | `JudgmentResult`에 4필드 추가 (missed_issues, review_blind_spots, what_improved, what_regressed) |
| `af.spec` | hiddenimport 추가 |

#### 6번째 학습 레이어

| # | 레이어 | 학습 대상 |
|---|--------|----------|
| 1 | SkillSelfEvolutionHook | 스킬 메타데이터 |
| 2 | CrossVerification._trigger_evolution | 스킬 코드 |
| 3 | CrossVerification._refine_task | 태스크 입력 |
| 4 | ISE StrategyLedger | 실행 전략 |
| 5 | revision_loop revision_history | 문서 수정 |
| **6** | **JudgmentLedger (NEW)** | **판정/리뷰 품질** |

---

## 3. 논의된 결정 사항

### 판정도 에이전트 기반이어야 한다

- QA, 교차검증 리뷰, Opus 판정 모두 프로젝트 컨텍스트 필요
- 현재 `_judge_phase()`는 `output[:800]`만 보고 판정 — 전체 코드, 테스트, 보드 참조 불가
- **Phase 1**: JudgmentLedger로 학습 루프 먼저 (이 설계)
- **Phase 2**: 에이전트가 파일 직접 읽기 + 테스트 실행 + 보드 참조

### Codex 교차검증 파이프라인 (회사에서 구현)

회사에서 구현한 내용:
- Claude 설계 → Codex가 code-review.md + 설계문서 보고 피드백
- Claude가 Codex 피드백 판단 → 설계문서 수정 → 최종본
- 이 전체를 자동으로 실행

**이 코드는 회사 PC에만 있음 (push 안 됨)**
→ 월요일에 push 후 리뷰 예정

---

## 4. 미완료 작업

| 항목 | 상태 | 다음 단계 |
|------|------|----------|
| `notebooklm_tools` 패키지 설치 | 미완료 | `pip install notebooklm-tools` |
| JudgmentLedger 구현 | 설계 완료 | 코드 작성 |
| `core/project_pipeline.py` 커밋 | 변경됨, 미커밋 | 커밋+push |
| `run_factory_cli.py` 커밋 | 변경됨, 미커밋 | 커밋+push |
| Master_Blueprint.md 업데이트 | 미완료 | 코드 변경과 같은 커밋 |
| 회사 PC 코드 push | 미완료 | 월요일 push |
| Codex 교차검증 파이프라인 리뷰 | push 대기 | push 후 리뷰 |

---

## 5. 현재 로컬 미커밋 변경 파일

```
M  .claude/settings.local.json
M  run_output.txt
M  skill-eval-report.json
M  skills/new_skill/meta.yaml
M  skills/registry.yaml
M  core/project_pipeline.py        ← revision_loop 학습 루프 개편
M  run_factory_cli.py              ← .env 자동 로드 추가
```

---

## 6. 회사에서 이어서 할 때 체크리스트

1. [ ] 회사 PC 작업 push
2. [ ] 집 PC 변경 pull (`run_factory_cli.py`, `project_pipeline.py`)
3. [ ] 충돌 있으면 해결
4. [ ] JudgmentLedger 구현 시작 (설계는 이 문서 §2)
5. [ ] Codex 교차검증 파이프라인 + JudgmentLedger 통합
6. [ ] Blueprint 업데이트
7. [ ] 커밋+push
