# Design Review: 2026-04-07-forge-skill-quality-enhancement

> Source: docs/features/2026-04-07-forge-skill-quality-enhancement.md
> Date: 2026-04-07 15:16
> Providers: critic=claude, cross=codex, judge=claude
> Mode: cross-review (3 providers)
> Trigger: manual-sync

---

코드 검증이 완료되었습니다. Critic의 주요 주장들을 실제 코드로 확인했습니다. Cross Review는 타임아웃(600s)으로 결과가 없으므로, Critic 단독 검증 기반으로 최종 판정합니다.

---

## Final Design Review

### Verdict: BLOCK

Critical 결함 2건 확인. 설계 문서 수정 후 구현 진행 필요.

### Aggregated Findings (8 total)

#### 1. [ACCEPT] [Critical] `auto_load_from_directories(force=True)`는 forge 디렉토리를 SKIP함

- **Critic**: `skill_registry.py:178`에 `_SKIP_DIRS = {"forge", ...}` 정의. `auto_load_from_directories()`는 forge를 스킵하므로, §3.6의 "메모리-디스크 동기화" 설계가 동작하지 않음.
- **Cross**: 타임아웃 (미검토)
- **Judgment**: 코드에서 직접 확인 — `_SKIP_DIRS`에 `"forge"`가 포함되어 있어, `auto_load_from_directories(force=True)` 호출로는 forge 스킬이 메모리 레지스트리에 절대 로드되지 않는다. 설계 의도와 실제 코드가 **정면 충돌**.
- **Action Required**: §3.6에서 `auto_load_from_directories` 의존 제거. 대안: `register_skill()` 내부에서 `get_global_registry().register(SkillMetadata(...))` 직접 호출로 인메모리 등록. 또는 `register_skill()` 함수가 YAML 저장 + 메모리 등록을 원자적으로 수행하도록 재설계.

#### 2. [ACCEPT] [Critical] LLM 호출 budget 카운터가 어댑터에 미연결

- **Critic**: §3.8의 `_counted_generate()`는 정의만 되고, §3.2의 `_llm_generate_adapter` / `_llm_text_adapter`는 `llm.generate()`를 직접 호출. Budget 카운터가 사문화됨.
- **Cross**: 타임아웃 (미검토)
- **Judgment**: 설계 코드를 대조하면 명백. `_counted_generate`를 정의했으나 어댑터에서 참조하지 않는다. 최악 ~18회 LLM 호출이 제한 없이 실행됨.
- **Action Required**: `_llm_generate_adapter`와 `_llm_text_adapter` 내부에서 `llm.generate()` 대신 `_counted_generate()`를 호출하도록 §3.2 코드 수정. evals 생성(§3.5)의 `llm.generate()` 호출도 동일하게 `_counted_generate()`로 교체.

#### 3. [ACCEPT] [High] `decide_reuse(skill_name, evidence={})` — 빈 evidence는 항상 forge 모드

- **Critic**: `_rank_candidates({})` → 빈 리스트 → `best = {}` → `candidate_skill_id = ""` → `confidence = 0.0` → 100% `mode="forge"`. reference candidate가 나올 수 없음.
- **Cross**: 타임아웃 (미검토)
- **Judgment**: `skill_retrieval_engine.py:72-87` 코드 확인. 빈 dict 전달 시 `ranked_candidates`가 비어 모든 값이 기본값(빈 문자열, 0점)이 되며, 항상 forge 모드로 빠진다. §3.4가 "primary path"로 제시하지만, 실질적으로 **항상 LLM fallback**으로만 동작.
- **Action Required**: 두 가지 옵션 중 택 1: (1) `decide_reuse()` 경로를 "optional optimization"으로 격하하고 LLM 사전 조회를 primary로 승격, 또는 (2) registry를 직접 탐색하여 유사 스킬 evidence를 사전 조립한 후 `decide_reuse()` 호출.

#### 4. [ACCEPT] [High] `evaluate_and_promote()` 추출 시 에러 핸들링 소실

- **Critic**: 기존 `_evaluate_and_promote_built_skill()`(line 458-478)은 `try/except`로 fallback dict 반환. 제안된 standalone 함수에는 이 방어 로직이 없음.
- **Cross**: 타임아웃 (미검토)
- **Judgment**: `skill_procurer.py:458-478` 확인. 기존 함수는 `except Exception as exc:` 블록에서 `log("EVAL", ...)` + fallback dict를 반환한다. §3.9의 `evaluate_and_promote()`에는 이 패턴이 없어 예외 시 caller까지 전파되며, §3.8의 artifact 보존도 누락됨.
- **Action Required**: §3.9 `evaluate_and_promote()` 내부에 `try/except` 추가. 실패 시 `{"installable": False, "reason": str(exc), ...}` 반환. 또는 caller(`forge_new_skill`)에서 eval 예외를 catch하여 artifact 보존 후 재시도/실패 처리.

#### 5. [ACCEPT] [High] §5 영향 범위 테이블의 import 경로 오류

- **Critic**: `read_skill_lock()`은 `core/utils.py:149`에 정의됨. §5는 `core/file_io.py | from core.skill_registry import read_skill_lock`으로 기재 — 파일과 import source 모두 틀림.
- **Cross**: 타임아웃 (미검토)
- **Judgment**: `core/utils.py:149`에서 `def read_skill_lock` 확인. `core/skill_registry.py`에는 해당 함수 없음. 구현 시 잘못된 import로 즉시 실패.
- **Action Required**: §5 테이블을 `core/skill_procurer.py | from core.utils import read_skill_lock` 으로 정정.

#### 6. [ACCEPT] [Medium] `forge_new_skill()` 단일 함수 비대화 (8개 책임)

- **Critic**: step 0~8의 모든 책임이 하나의 함수에 집중. closure 변수 다수, 중간 단계 테스트 어려움.
- **Cross**: 타임아웃 (미검토)
- **Judgment**: 유효한 우려이나, 구현 단계에서 자연스럽게 헬퍼로 분리 가능. 설계 수준에서 BLOCK할 사안은 아님.
- **Action Required**: §8 체크리스트에 "forge_new_skill 내부 헬퍼 분리 검토 (`_forge_and_evaluate`, `_generate_evals`)" 항목 추가.

#### 7. [ACCEPT] [Medium] `_load_skill_callable` path 추가 시 forge 스킬 간 네임스페이스 충돌

- **Critic**: `FORGE_DIR`이 `sys.path`에 추가되면 다른 forge 스킬의 동명 모듈을 잘못 import할 수 있음.
- **Cross**: 타임아웃 (미검토)
- **Judgment**: `_load_skill_callable`이 `parent_dir`을 `sys.path.insert`하는 동작은 여러 forge 스킬이 있을 때 충돌 가능성이 있다. 디렉토리 구조 전환과 맞물려 검증 필요.
- **Action Required**: §8 체크리스트에 "forge 디렉토리 구조에서 `_load_skill_callable` sys.path 충돌 테스트" 항목 추가.

#### 8. [ACCEPT] [Low] evals YAML 재생성 시 동일 프롬프트 재사용

- **Critic**: 1차 LLM 실패 후 동일 프롬프트로 재시도하므로 성공 확률 낮음.
- **Cross**: 타임아웃 (미검토)
- **Judgment**: 유효하나 Low 심각도. 구현 시 피드백 augmented 프롬프트로 개선 가능.
- **Action Required**: §3.5 재시도 시 "이전 시도에서 N개 케이스만 생성됨. 최소 3개 이상 필수." 등의 피드백을 프롬프트에 추가하도록 설계 보완.

### Summary Table

| # | Title | Severity | Verdict | Source |
|---|-------|----------|---------|--------|
| 1 | `auto_load_from_directories` forge SKIP | Critical | ACCEPT | Critic |
| 2 | LLM budget 카운터 미연결 | Critical | ACCEPT | Critic |
| 3 | `decide_reuse({})` 항상 forge 모드 | High | ACCEPT | Critic |
| 4 | `evaluate_and_promote()` 에러 핸들링 소실 | High | ACCEPT | Critic |
| 5 | §5 `read_skill_lock` import 경로 오류 | High | ACCEPT | Critic |
| 6 | `forge_new_skill()` 함수 비대화 | Medium | ACCEPT | Critic |
| 7 | forge 스킬 간 sys.path 충돌 가능성 | Medium | ACCEPT | Critic |
| 8 | evals 재생성 동일 프롬프트 | Low | ACCEPT | Critic |

### Recommendations

구현 전 설계 문서에서 다음을 수정:

1. **[Critical]** §3.6: `auto_load_from_directories` 의존 제거 → `register_skill()` 내부에서 `get_global_registry().register()` 직접 호출로 메모리 등록 메커니즘 재설계
2. **[Critical]** §3.2 + §3.5: 모든 `llm.generate()` 호출을 `_counted_generate()`로 통일 (어댑터 2개 + evals 생성 2회)
3. **[High]** §3.4: `decide_reuse({})` 경로를 optional로 격하하거나, evidence 사전 조립 로직 추가
4. **[High]** §3.9: `evaluate_and_promote()`에 `try/except` + fallback dict 반환 추가
5. **[High]** §5: import 경로를 `core/skill_procurer.py | from core.utils import read_skill_lock`으로 정정
6. **[Medium]** §8 체크리스트에 함수 분리 검토 + sys.path 충돌 테스트 항목 추가
7. **[Low]** §3.5 재시도 프롬프트에 피드백 augmentation 추가

**참고**: Cross Review가 타임아웃으로 미수행되어, 이 판정은 Critic 단독 리뷰 + 코드 직접 검증 기반입니다. Critical 2건은 코드로 확인된 사실이므로 신뢰도는 충분합니다.