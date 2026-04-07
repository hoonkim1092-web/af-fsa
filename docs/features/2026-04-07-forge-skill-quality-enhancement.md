# Forge Skill 품질 강화 설계

> 날짜: 2026-04-07
> 상태: 설계 v4 (교차 검증 3차 피드백 반영)
> 교차 검증: `docs/reviews/2026-04-07-151607-*-review.md`

## 1. 문제 정의

현재 `forge_new_skill()`(skill_procurer.py:152)은 스킬이 없을 때 LLM으로 자동 생성하지만,
**생성된 스킬의 품질을 보장하는 장치가 충분히 연결되어 있지 않다.**

### 현재 흐름의 갭

```
procure_skill()
  ├─ registry 확인 → 있으면 반환
  ├─ warehouse 검색 → 있으면 반환
  ├─ forge 디렉토리 확인 → 있으면 반환
  └─ forge_new_skill()          ← ★ 문제 구간
       ├─ LLM에 범용 프롬프트로 코드 생성 (argparse CLI 구조)
       ├─ 파일 저장 (플랫: FORGE_DIR/skill_name.py)
       ├─ register_skill()
       └─ 반환                  ← 바로 사용됨 (검증 없음)
```

**갭 분석:**

| 구간 | 현재 상태 | 문제 |
|------|----------|------|
| forge_new_skill() → SkillForge | **미연결** | SkillForge의 Implementer→Critic→Repair 루프를 거치지 않음 |
| forge_new_skill() → SkillEvalHarness | **미연결** | eval 없이 바로 registry에 등록됨 |
| forge_new_skill() → SkillPromotion | **미연결** | draft 단계 없이 바로 사용 가능 |
| forge 프롬프트 | **범용적** | 도메인별 라이브러리/패턴 힌트 없음 |
| evals.yml 자동 생성 | **없음** | eval 케이스 없으면 eval 단계가 무조건 통과 |
| **프롬프트 ↔ Forge/Eval 구조 비호환** | **Critical** | 프롬프트는 argparse CLI 생성, Forge/Eval은 propose/apply/test 함수 기대 |
| **SkillForge 인터페이스 불일치** | **Critical** | LLMEngine.generate()는 str 반환, SkillForge는 tuple[str, dict] 기대 |

### 기존 파이프라인과의 관계

`SkillOrchestrator._evaluate_and_promote_built_skill()`(skill_procurer.py:431-491)에 이미
**Eval→Promotion 파이프라인이 완전 구현**되어 있다:

```
_evaluate_and_promote_built_skill()
  ├─ SkillEvalHarness().evaluate()
  ├─ SkillPromotionManager().apply()
  └─ meta dict에 결과 반영 (status, installable, report paths)
```

`forge_new_skill()`에 동일 로직을 재구현하면 **2중 경로 → 유지보수 불일치 위험**.
따라서 이 설계에서는 **기존 함수를 재사용 가능한 형태로 추출**하여 양쪽에서 호출한다.

## 2. 목표

forge된 스킬이 기존 품질 파이프라인(Forge→Eval→Promotion)을 **반드시 통과**한 후에만 사용되도록 연결한다.

## 3. 설계

### 3.1 스킬 함수 구조 통일 (Critical #1 해결)

**문제:** 현재 forge 프롬프트(skill_procurer.py:170-173)는 argparse CLI를 생성하지만,
`_local_critic()`(skill_forge.py:172-186)은 `propose()/apply()/test()` 함수를 검사하고,
`_load_skill_callable()`(skill_eval_harness.py:570-614)은 `apply`/`test` callable을 찾는다.
→ 연결하면 action 스킬 100% 실패.

**결정:** 프롬프트를 `propose(ctx)/apply(ctx)/test(ctx)` 함수 기반으로 변경한다.
(Forge/Eval 모듈은 이미 이 구조를 기대하므로, 프롬프트 쪽을 맞추는 것이 변경 최소화)

**`apply()`가 주 실행 함수(eval entrypoint)이다.** `propose()`는 선택적 계획 함수,
`test()`는 자가검증. EvalHarness는 `apply()` 기준으로 테스트하므로,
LLM 프롬프트에서 핵심 로직은 반드시 `apply()`에 넣도록 가중치를 준다.

```python
# 변경 후 프롬프트 (skill_procurer.py)
prompt = (
    f"Write a Python skill module '{skill_name}.py' for the role '{role}'.\n"
    "The module MUST implement these three functions:\n"
    "  def propose(ctx: dict) -> dict:  # 실행 계획 제안. return {'ok': True/False, 'plan': ...}\n"
    "  def apply(ctx: dict) -> dict:    # ★ 핵심 실행 함수. return {'ok': True/False, 'result': ...}\n"
    "  def test(ctx: dict) -> dict:     # 자가 검증. return {'ok': True/False, 'details': ...}\n"
    "apply()가 메인 실행 함수이다. 핵심 로직은 반드시 apply()에 구현하라.\n"
    "ctx dict에는 'task', 'workspace', 'goal' 등의 키가 포함됩니다.\n"
    "Code docstrings and user output MUST be in Korean. Return ONLY the python code."
)
```

### 3.2 SkillForge 어댑터 (Critical #2 해결)

**문제:** `SkillForge.__init__()`(skill_forge.py:39-48)은 `generate_code: Callable[..., tuple[str, dict]]`을 요구하지만,
`LLMEngine.generate()`(llm_engine.py)는 `str`만 반환한다.
또한 `SkillForge.run()`(skill_forge.py:50-117)은 `workspace`, `run_id`, `synthesized_artifacts`, `reference_candidate` 파라미터가 필요하다.

**해결:** `forge_new_skill()` 내에서 어댑터 함수를 정의하여 인터페이스를 맞춘다.

**`LLMEngine.generate()` 시그니처:** `generate(prompt: str) -> str`. `system_prompt` 파라미터 미지원.
따라서 critic용 `system_prompt`는 user prompt에 병합한다. 이 제약은 critic 품질에 영향을 줄 수 있으나,
`LLMEngine`에 `system_prompt` 파라미터를 추가하는 것은 이번 설계 스코프 밖이다.
→ **리스크를 §6에 기재.**

```python
# forge_new_skill() 내부에 추가

# LLM 호출 카운터 — 모든 LLM 호출은 이 함수를 통과 (§3.8 budget)
MAX_LLM_CALLS = 15
_llm_call_count = 0

def _counted_generate(prompt):
    nonlocal _llm_call_count
    _llm_call_count += 1
    if _llm_call_count > MAX_LLM_CALLS:
        raise RuntimeError(f"LLM call budget exceeded: {_llm_call_count} > {MAX_LLM_CALLS}")
    return llm.generate(prompt)

def _llm_generate_adapter(*, prompt, workspace, run_id, **kwargs):
    """LLMEngine.generate() → tuple[str, dict] 래핑."""
    text = _counted_generate(prompt)  # ★ budget 카운터 경유
    return text, {"run_id": run_id, "model": coding_engine}

def _llm_text_adapter(*, prompt, system_prompt=None, workspace, run_id, **kwargs):
    """critic용 텍스트 생성 어댑터. system_prompt는 user prompt에 병합."""
    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
    text = _counted_generate(full_prompt)  # ★ budget 카운터 경유
    return text, {"run_id": run_id, "model": coding_engine}

forge = SkillForge(
    generate_code=_llm_generate_adapter,
    generate_text=_llm_text_adapter,
    max_repair_rounds=2,
)

result = forge.run(
    skill_id=skill_name,
    base_prompt=prompt,
    workspace=skill_dir,  # ★ FORGE_DIR가 아닌 스킬별 디렉토리 (§3.3)
    run_id=f"forge_{skill_name}_{now_iso()}",
    reference_candidate=reference_candidate,  # §3.4에서 조달
)
```

**LLM 빈 출력 방어:** `LLMEngine.generate()`는 실패 시 예외가 아닌 `""` 반환(llm_engine.py:179,182).
어댑터에서 빈 출력을 명시적 오류로 승격한다:

```python
def _llm_generate_adapter(*, prompt, workspace, run_id, **kwargs):
    text = _counted_generate(prompt)  # ★ budget 카운터 경유
    if not text or not text.strip():
        raise ValueError(f"LLM returned blank output (run_id={run_id})")
    return text, {"run_id": run_id, "model": coding_engine}
```

### 3.3 스킬 디렉토리 구조 전환

**문제:** action 스킬이 `FORGE_DIR/skill_name.py` 플랫 구조로 저장되면,
`_discover_evals_path()`(skill_eval_harness.py:529-534)가 `os.path.dirname(skill_path)`에서 evals.yml을 찾으므로
여러 스킬이 `FORGE_DIR/evals.yml` 하나를 공유/덮어씀.

**해결:** 스킬별 디렉토리 구조로 전환.

```
변경 전: FORGE_DIR/pptx.py
변경 후: FORGE_DIR/pptx/pptx.py
                       /evals.yml
                       /runs/<run_id>/attempt-0/  ← §3.8 재시도 artifact
                       /runs/<run_id>/attempt-1/
                       /skill-eval-report.json
                       /skill-promotion.json
```

```python
# skill_procurer.py 변경
skill_dir = os.path.join(FORGE_DIR, skill_name)
os.makedirs(skill_dir, exist_ok=True)
output_path = os.path.join(skill_dir, f"{skill_name}.py")
```

**`resolve_skill_paths()` 업데이트 필요** (utils.py:226-246):
현재 forge 경로 탐색이 `SKILLS_DIR/forge/{sid}.py` 플랫 구조만 지원(line 243-245).
디렉토리 구조도 탐색하도록 변경:

```python
# utils.py:243 변경
# 기존: forge_py = os.path.join(SKILLS_DIR, "forge", f"{sid}.py")
# 변경: flat + directory 양쪽 탐색
forge_py_flat = os.path.join(SKILLS_DIR, "forge", f"{sid}.py")
forge_py_dir = os.path.join(SKILLS_DIR, "forge", sid, f"{sid}.py")
for forge_py in [forge_py_dir, forge_py_flat]:  # 디렉토리 우선
    if os.path.exists(forge_py):
        return forge_py, None
return None, None
```

**`procure_skill()` forge 경로 탐색(line 135-137)도 동일 변경:**

```python
# 기존: forge_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
# 변경: flat + directory 양쪽 탐색
forge_path_dir = os.path.join(FORGE_DIR, skill_name, f"{skill_name}.py")
forge_path_flat = os.path.join(FORGE_DIR, f"{skill_name}.py")
for forge_path in [forge_path_dir, forge_path_flat]:
    if os.path.exists(forge_path):
        # ★ installable 확인 후 등록 (§3.6 참조)
        register_skill(skill_name, purpose_desc, forge_path, stype="action", source="forge")
        return forge_path
```

**기존 flat 구조 호환:** 기존 `FORGE_DIR/*.py` 파일은 그대로 둔다. 신규 forge만 디렉토리 구조.
양쪽 탐색으로 호환성 유지. 마이그레이션은 별도 설계.

**`resolve_skill_paths()` 소비자 영향 확인:**

| 소비자 | 파일:라인 | 영향 |
|--------|----------|------|
| `agent_runner.py` | :620 | `resolve_skill_paths()` 반환값 사용 → 자동 호환 |
| `researcher.py` | :98 | 동일 |
| `skill_spec_synthesizer.py` | :226 | 동일 |
| `builder.py` | :289 | 동일 |
| `registry_manager.py` | :121 | 동일 |
| `_evaluate_and_promote_built_skill` | :451 | `resolve_skill_paths()` 사용 → 자동 호환 |

`resolve_skill_paths()` 내부 변경이므로 소비자 코드 변경 불필요.
단, 통합 테스트에서 디렉토리 구조 스킬의 resolve를 검증해야 함.

### 3.4 도메인 인식 프롬프트 강화

현재 프롬프트에 도메인 힌트가 없어 `pptx` 요청 시 `python-pptx` 사용 여부가 불확실.

**해결 — 2단계 접근:**

1. **warehouse/forge 직접 탐색 (primary)** — `decide_reuse()`는 빈 evidence 시 항상 forge 모드를
   반환하므로(`_rank_candidates({})` → 빈 리스트 → confidence=0.0), 유사 스킬 참조에 적합하지 않다.
   대신 warehouse 디렉토리를 직접 glob 탐색하여 유사 이름의 스킬 코드를 reference로 사용.

2. **LLM 사전 조회 (보조)** — reference가 없으면 forge 전 "이 스킬에 필요한 라이브러리와 핵심 패턴은?" 질의하여 프롬프트에 힌트 추가

```python
# forge_new_skill() 내부
import glob as _glob

# 1. warehouse에서 유사 이름 스킬 직접 탐색
reference_candidate = None
warehouse_matches = _glob.glob(
    os.path.join(WAREHOUSE_DIR, "**", "*.py"), recursive=True
)
# 스킬명과 부분 매칭되는 파일을 reference로 사용
for match_path in warehouse_matches:
    match_name = os.path.splitext(os.path.basename(match_path))[0]
    if skill_name in match_name or match_name in skill_name:
        with open(match_path, encoding="utf-8") as f:
            reference_candidate = {
                "candidate_skill_id": match_name,
                "candidate_path": match_path,
                "confidence": 0.5,  # 이름 기반 매칭이므로 중간 신뢰도
                "code_excerpt": f.read()[:5000],
            }
        break

# 2. reference 없으면 LLM에 도메인 힌트 질의
if not reference_candidate:
    hint = _counted_generate(  # ★ budget 카운터 경유
        f"'{skill_name}' 스킬 구현에 필요한 Python 라이브러리와 핵심 패턴을 간략히 설명해."
    )
    if hint and hint.strip():
        prompt += f"\n\n도메인 힌트:\n{hint}"
```

**`reference_candidate` dict shape 명세:**

| 필드 | 타입 | 설명 |
|------|------|------|
| `candidate_skill_id` | `str` | 참조 스킬 ID |
| `candidate_path` | `str` | 참조 스킬 파일 경로 |
| `confidence` | `float` | 유사도 점수 (0.0~1.0) |
| `code_excerpt` | `str` | 참조 스킬 코드 (최대 5000자) |

### 3.5 evals.yml 자동 생성 + 최소 케이스 가드

**문제:** eval 케이스가 없으면 `total_cases == 0` → `pass_rate` 조건 자동 통과(skill_eval_harness.py:307).
LLM이 malformed YAML 생성 시 `_coerce_cases()`가 빈 리스트 반환 → 동일 문제.

**해결:**

```python
# forge_new_skill() 내부 — SkillForge.run() 후
from core.utils import strip_code_fences  # 기존 유틸 활용
import yaml

# 1. evals.yml 생성 — apply(ctx) 전용 케이스만 요구
evals_prompt = (
    f"다음 스킬의 apply(ctx) 함수에 대한 "
    f"테스트 케이스 3~5개를 YAML로 작성해.\n\n"
    f"```python\n{result.code}\n```\n\n"
    "형식:\n"
    "contract:\n"
    "  - name: '케이스명'\n"
    "    ctx: {task: '...', workspace: '/tmp'}\n"
    "    expect_ok: true/false\n"
    "YAML만 반환. 코드 펜스 없이."
)
evals_text = _counted_generate(evals_prompt)  # ★ budget 카운터 경유
if not evals_text or not evals_text.strip():
    raise ValueError("evals 생성 실패: LLM 빈 출력")

# 2. 코드 펜스 strip + YAML 파싱 검증
evals_text = strip_code_fences(evals_text)
try:
    parsed = yaml.safe_load(evals_text)
except yaml.YAMLError:
    parsed = None

# 3. contract 키 정규화 — LLM이 "cases:" 키로 생성할 수 있음
if isinstance(parsed, dict):
    contract_cases = parsed.get("contract") or parsed.get("cases") or []
else:
    contract_cases = []

# 4. 최소 케이스 수 가드
MIN_EVAL_CASES = 3
if not isinstance(contract_cases, list) or len(contract_cases) < MIN_EVAL_CASES:
    log("FORGE", f"Eval cases insufficient: {len(contract_cases)} < {MIN_EVAL_CASES}, regenerating...")
    # 1회 재시도 — 피드백 augmented 프롬프트
    retry_prompt = (
        f"이전 시도에서 {len(contract_cases)}개 케이스만 생성되었습니다. "
        f"최소 {MIN_EVAL_CASES}개 이상 반드시 생성하세요.\n\n{evals_prompt}"
    )
    evals_text = _counted_generate(retry_prompt)  # ★ budget 카운터 경유
    evals_text = strip_code_fences(evals_text) if evals_text else ""
    try:
        parsed = yaml.safe_load(evals_text) or {}
    except yaml.YAMLError:
        parsed = {}
    contract_cases = parsed.get("contract") or parsed.get("cases") or []
    if len(contract_cases) < MIN_EVAL_CASES:
        raise ValueError(f"evals 재생성 후에도 부족: {len(contract_cases)} < {MIN_EVAL_CASES}")

# 5. 정규화된 YAML 저장 (contract 키 통일)
evals_path = os.path.join(skill_dir, "evals.yml")
normalized = {"contract": contract_cases}
with open(evals_path, "w", encoding="utf-8") as f:
    yaml.dump(normalized, f, allow_unicode=True, default_flow_style=False)
```

**참고:** `skill_eval_harness.py`의 `total_cases == 0 → 통과` 로직은 변경하지 않는다.
(다른 호출자에게 영향. 대신 `forge_new_skill()` 래퍼 레벨에서 가드)

### 3.6 등록 시점 정리 + 조회 경로 일치

**문제 1:** `register_skill()`이 promotion 판정 전에 호출되면, `procure_skill()`이 다음 호출 시
`check_skill_exists()` → `True` → 품질 미달 스킬을 installable 여부 무관하게 반환.

**문제 2 (Cross finding #5):** `check_skill_exists()`는 메모리 `SkillRegistry`만 조회(skill_registry.py:474),
`register_skill()`은 `registry.yaml`만 저장(skill_registry.py:519,563).
autodiscovery는 forge/warehouse를 스킵(skill_registry.py:178,192).
→ `register_skill()` 시점만 옮겨도 조회 경로가 다른 저장소를 봄.

**해결 — 이중 방어:**

1. **등록 시점 이동:** `register_skill()`을 promotion 판정 **후** installable일 때만 호출
2. **`procure_skill()` gate 보강:** registry 조회 시 `read_skill_lock()`으로 installable 확인
3. **`register_skill()` 후 메모리 갱신:** `register_skill()` 내부에서 `_save_registry()` 후
   `get_global_registry().register(SkillMetadata(...))` 직접 호출로 인메모리 등록.
   ⚠️ `auto_load_from_directories(force=True)` 사용 불가 — `_SKIP_DIRS = {"forge", ...}`(skill_registry.py:178)로
   forge 디렉토리가 스킵됨

```
변경 후 파이프라인:
  SkillForge.run() → evals 생성 → EvalHarness.evaluate() → Promotion.apply()
    ├─ installable=True  → register_skill() + get_global_registry().register() 직접 호출 → 경로 반환
    └─ installable=False → register 안 함 → 경고 + None 반환
```

**⚠️ `auto_load_from_directories()` 사용 불가** — `_SKIP_DIRS`에 `"forge"` 포함(skill_registry.py:178).
forge 스킬 메모리 등록은 반드시 `get_global_registry().register(SkillMetadata(...))` 직접 호출.

**`procure_skill()` 레벨 보강:**

```python
if check_skill_exists(skill_name):
    lock_state = read_skill_lock().get("skills", {}).get(skill_name, {})
    if lock_state.get("installable", True):  # 기본값 True (기존 스킬 호환)
        existing_skill_path, _ = resolve_skill_paths(skill_name)
        if existing_skill_path and os.path.exists(existing_skill_path):
            return existing_skill_path
    # installable=False이면 forge 경로로 진행
```

### 3.7 knowledge 스킬 스코프

**스코프 선언:** 이번 설계는 **action 스킬만 대상**으로 한다.

**이유:**
- knowledge 스킬은 `.md` 파일이며 `propose/apply/test` 패턴이 적용되지 않음
- knowledge 스킬은 `skill_creator.create_skill()`(skill_procurer.py:187-206) 별도 경로 사용
- 별도 설계 문서에서 다룸

knowledge 스킬 경로(skill_procurer.py:187-206)는 현재 상태 유지.

### 3.8 품질 미달 시 처리 + artifact 보존

SkillEvalHarness 통과율 < 80% 또는 SkillPromotion 결과 `draft` 잔류 시:

1. **1차:** SkillForge Repair 루프 (max_repair_rounds=2, 이미 §3.2에서 설정)
2. **2차:** evals 케이스 부족 시 evals 재생성 (1회)
3. **3차:** 프롬프트 강화 후 전체 재생성 (1회)
4. **최종 실패:** registry에 등록하지 않음 + 사용자에게 경고 반환 + forge 결과는 `skill_dir/`에 보존 (디버깅용)

**`forge.run()` 후 repair 실패 확인:**

```python
result = forge.run(...)
if result.critique and result.critique.should_repair:
    # repair 라운드 소진 후에도 should_repair=True → 코드 품질 불충분
    log("FORGE", f"Repair rounds exhausted, code still has issues: {skill_name}")
    # eval 진행은 하되, 실패 가능성 높음을 인지
```

**artifact 보존 규약:**
현재 `skill-eval-report.json`(skill_eval_harness.py:17)과 `skill-promotion.json`(skill_promotion.py:15)은
고정 파일명으로 덮어쓴다. 재시도 시 이전 attempt 결과가 소실됨.

```
FORGE_DIR/skill_name/
  ├─ skill_name.py            ← 최종 코드
  ├─ evals.yml                ← 최종 evals
  ├─ skill-eval-report.json   ← 최종 eval report
  ├─ skill-promotion.json     ← 최종 promotion report
  └─ runs/
      └─ forge_skill_name_20260407/
          ├─ attempt-0/
          │   ├─ skill_name.py
          │   ├─ skill-eval-report.json
          │   └─ skill-promotion.json
          └─ attempt-1/
              ├─ skill_name.py
              ├─ skill-eval-report.json
              └─ skill-promotion.json
```

각 attempt 전에 현재 산출물을 `runs/<run_id>/attempt-N/`에 복사.
최종 성공한 attempt의 결과만 루트에 남김.

**LLM 호출 budget 하드 리밋:** `MAX_LLM_CALLS = 15`, `_counted_generate()` — §3.2에서 정의.
모든 LLM 호출(어댑터 2개 + evals 생성 + 도메인 힌트)이 이 함수를 경유.

### 3.12 forge_new_skill() 헬퍼 분리

`forge_new_skill()`에 8개 책임이 집중되면 중간 단계 테스트가 어렵고, closure 변수가 과다해진다.
다음 3개 헬퍼로 분리:

```python
def _prepare_forge_context(skill_name, role, coding_engine):
    """§4 step 0~1: LLM 초기화 + 도메인 힌트 조달 + 프롬프트 구성.
    Returns: (llm, prompt, reference_candidate, skill_dir, _counted_generate)
    """
    ...

def _generate_and_validate_evals(skill_dir, code, _counted_generate):
    """§4 step 5: evals.yml 생성 + 검증 + 재시도.
    Returns: evals_path
    """
    ...

def _evaluate_promote_and_register(skill_name, output_path, evals_path, skill_dir, ref_id):
    """§4 step 6~7: eval→promotion→조건부 등록.
    Returns: output_path or None
    """
    ...
```

`forge_new_skill()`은 이 3개를 순차 호출하는 오케스트레이터가 된다:

```python
def forge_new_skill(skill_name, role, coding_engine=None, skill_type="action"):
    if skill_type != "action":
        return _forge_knowledge_skill(skill_name, role, coding_engine)

    ctx = _prepare_forge_context(skill_name, role, coding_engine)
    result = ctx.forge.run(...)  # SkillForge 실행
    _save_code(ctx.skill_dir, skill_name, result.code)
    evals_path = _generate_and_validate_evals(ctx.skill_dir, result.code, ctx.counted_generate)
    return _evaluate_promote_and_register(skill_name, ctx.output_path, evals_path, ctx.skill_dir, ctx.ref_id)
```

### 3.13 forge 스킬 eval 시 sys.path 충돌 방지

**문제:** `_load_skill_callable()`(skill_eval_harness.py:590-602)이 `parent_dir`을 `sys.path`에 임시 추가.
forge 디렉토리 구조에서 `parent_dir = FORGE_DIR` → exec 동안 다른 forge 스킬 모듈이 import 가능.

**현재 코드의 방어:**
- `importlib.util.spec_from_file_location`으로 직접 파일 지정 로드 (sys.path 무관)
- `sys.path` 추가는 스킬 내부의 상대 import 해결용
- `finally`에서 즉시 제거

**실제 리스크:** 스킬 코드가 `import skill_name`으로 같은 FORGE_DIR 내 다른 스킬을 실수로 import할 때만 발생.
LLM 생성 코드가 외부 forge 스킬을 import할 가능성은 낮지만, 방어 가능.

**해결:** `_load_skill_callable`의 `parent_dir` 추가를 forge 스킬에서는 스킵.
forge 스킬은 디렉토리 구조(`FORGE_DIR/skill_name/skill_name.py`)이므로 `skill_dir`만 추가하면 충분.

```python
# _load_skill_callable 내부 변경
for candidate_path in (skill_dir, parent_dir):
    # forge 디렉토리면 parent_dir 스킵 — 다른 forge 스킬 오염 방지
    if candidate_path == parent_dir and "forge" in parent_dir:
        continue
    if candidate_path and candidate_path not in sys.path:
        sys.path.insert(0, candidate_path)
        added_paths.append(candidate_path)
```

**대안 (변경 없음 옵션):** 현재 `finally` 제거 패턴이 충분하고, 단일 프로세스 순차 실행이므로
실질적 충돌 가능성은 매우 낮다. 통합 테스트에서 검증 후 필요 시 적용.

### 3.9 Eval/Promotion 공통 함수 추출

**문제:** `_evaluate_and_promote_built_skill()`(skill_procurer.py:431-491)은
`SkillOrchestrator` 인스턴스 메서드이므로 `forge_new_skill()`에서 직접 호출 불가.
`SkillOrchestrator`는 `registry`, `research_agent`, `builder`, `agent_mgr` 4개 의존성을 요구.

**해결:** eval→promotion 로직을 standalone 함수로 추출.

```python
# core/skill_procurer.py 모듈 레벨에 추가
def evaluate_and_promote(
    *,
    skill_name: str,
    code_path: str,
    evals_path: str = "",
    reference_candidate_id: str = "",
    feedback_loop: SkillFeedbackLoop | None = None,
    workspace: str | None = None,
    current_stage: str = "draft",
    project_policies: dict | None = None,
) -> dict:
    """Eval→Promotion 공통 실행. forge_new_skill()과 SkillOrchestrator 양쪽에서 사용."""
    skill_id = safe_id(skill_name)
    baseline_skill_path = ""
    if reference_candidate_id:
        baseline_skill_path, _ = resolve_skill_paths(reference_candidate_id)
        baseline_skill_path = baseline_skill_path or ""

    feedback_path = str(getattr(feedback_loop, "feedback_path", "") or "")
    runs_dir = os.path.join(os.path.abspath(workspace), "runs") if workspace else None

    try:
        eval_report = SkillEvalHarness().evaluate(
            code_path,
            evals_path=evals_path or None,
            baseline_skill_path=baseline_skill_path or None,
            feedback_path=feedback_path or None,
            runs_dir=runs_dir,
        )
        decision = SkillPromotionManager(project_policies=project_policies).apply(
            skill_id,
            eval_report,
            current_stage=current_stage,
            feedback_loop=feedback_loop,
        )
        return {
            "next_stage": getattr(decision, "next_stage", current_stage),
            "installable": bool(getattr(decision, "installable", False)),
            "eval_report_path": str(getattr(eval_report, "report_path", "") or ""),
            "promotion_report_path": str(getattr(decision, "promotion_path", "") or ""),
            "reason": str(getattr(decision, "reason", "") or ""),
        }
    except Exception as exc:
        log("EVAL", f"evaluate_and_promote failed for '{skill_name}': {exc}")
        return {
            "next_stage": current_stage,
            "installable": False,
            "eval_report_path": "",
            "promotion_report_path": "",
            "reason": f"eval_error: {exc}",
        }
```

`_evaluate_and_promote_built_skill()`은 이 함수를 내부적으로 호출하도록 리팩터링.

### 3.10 installable_statuses forge 전용 정책 (Critical BLOCK 해제)

**문제:** `policy.py:31-33` 기본값 `installable_statuses = ["active"]`.
승격 경로: `draft → candidate`(contract/hidden eval), `candidate → canary`(shadow eval delta ≥ 0 필요),
`canary → active`(shadow + runtime 필요).
신규 forge 스킬은 shadow/runtime 데이터 전무 → 최대 `candidate` 정체 → `installable=False` → 항상 `None`.

**해결:** `forge_new_skill()` 호출 시 forge 전용 `project_policies`를 주입한다.

```python
# forge_new_skill() 내부
FORGE_POLICIES = {
    "quality_gate": {
        "installable_statuses": ["candidate", "canary", "active"],
        "default_stage_on_build": "draft",
    }
}

# evaluate_and_promote() 호출 시
result = evaluate_and_promote(
    skill_name=skill_name,
    code_path=output_path,
    evals_path=evals_path,
    reference_candidate_id=ref_id,
    workspace=skill_dir,
    current_stage="draft",
    project_policies=FORGE_POLICIES,  # ★ forge 전용 정책
)
```

**안전성 근거:** `candidate` 단계는 이미 contract eval + hidden eval을 통과한 상태.
forge 스킬이 이 두 검증을 통과했다면 사용 가능하다고 판단하는 것은 합리적.
`canary`/`active` 승격은 여전히 shadow/runtime 데이터가 축적된 후에만 가능.

### 3.11 JSON 원자적 쓰기

**기존 known issue:** `code-review.md:33`에 non-atomic JSON write가 High로 기록.
`_safe_write_json()`이 `core/dashboard.py:108`에 이미 존재.

eval report(`skill_eval_harness.py`)와 promotion report(`skill_promotion.py`)는 plain write 사용.
이번 설계에서 직접 수정하지는 않으나, **`evaluate_and_promote()` 래퍼에서 report 저장 시
`_safe_write_json()`을 사용하도록 명시:**

```python
from core.dashboard import _safe_write_json
# 또는 core/file_io.py로 이동 후 import
```

→ 이 리팩터링은 §5 영향 범위에 포함.

## 4. 전체 파이프라인 (변경 후)

```
forge_new_skill(skill_name, role)
  │
  ├─ 0. LLM 호출 카운터 초기화 (MAX_LLM_CALLS=15)
  │
  ├─ 1. 도메인 힌트 조달
  │     warehouse 디렉토리 glob 탐색 → 유사 이름 스킬 → reference_candidate dict
  │     없으면 LLM 사전 조회(_counted_generate) → 프롬프트 힌트 추가
  │
  ├─ 2. 스킬 디렉토리 생성
  │     FORGE_DIR/skill_name/ 생성
  │
  ├─ 3. SkillForge.run()
  │     어댑터로 LLMEngine 래핑 (빈 출력 → ValueError)
  │     Implementer → Critic → Repair (최대 2회)
  │     → result.code
  │     ★ result.critique.should_repair=True이면 경고 로그
  │
  ├─ 4. 코드 저장
  │     FORGE_DIR/skill_name/skill_name.py
  │
  ├─ 5. evals.yml 자동 생성
  │     LLM으로 apply(ctx) 전용 contract 케이스 3~5개 생성
  │     strip_code_fences() + yaml.safe_load() 검증
  │     contract/cases 키 정규화
  │     최소 3개 가드 (부족 시 재생성 1회)
  │     FORGE_DIR/skill_name/evals.yml
  │
  ├─ 6. evaluate_and_promote() (§3.9 공통 함수)
  │     FORGE_POLICIES 적용 (installable_statuses: [candidate, canary, active])
  │     EvalHarness.evaluate() → PromotionManager.apply()
  │     artifact를 runs/<run_id>/attempt-N/에 보존
  │
  ├─ 7. 결과 분기
  │     ├─ installable=True  → register_skill() + 메모리 갱신 → 경로 반환
  │     └─ installable=False → 재시도(§3.8) 또는 경고 + None 반환
  │
  └─ 8. procure_skill() 보강
        registry 조회 시 read_skill_lock()으로 installable 상태 확인
```

## 5. 영향 범위

| 파일 | 변경 내용 |
|------|----------|
| `core/skill_procurer.py` | `forge_new_skill()` 전면 개편 — 파이프라인 연결, 프롬프트 변경, 디렉토리 구조, 어댑터, 등록 시점, LLM budget |
| `core/skill_procurer.py` | `procure_skill()` — installable 상태 확인 + forge 경로 양쪽 탐색 |
| `core/skill_procurer.py` | `evaluate_and_promote()` standalone 함수 추출 |
| `core/skill_procurer.py` | `_evaluate_and_promote_built_skill()` → `evaluate_and_promote()` 위임 리팩터링 |
| `core/utils.py` | `resolve_skill_paths()` — forge 경로에 flat+directory 양쪽 탐색 추가 |
| `core/skill_forge.py` | 변경 없음 |
| `core/skill_eval_harness.py` | `_load_skill_callable()` — forge 스킬 eval 시 parent_dir sys.path 추가 조건부 스킵 (§3.13) |
| `core/skill_promotion.py` | 변경 없음 |
| `core/skill_feedback.py` | 변경 없음 |
| `core/skill_procurer.py` | `from core.utils import read_skill_lock` import 추가 (procure_skill에서 사용) |
| `af.spec` | hiddenimports 확인 — 새 모듈 없으므로 변경 불필요 |
| `Master_Blueprint.md` | §3 스킬 서브시스템 섹션 업데이트 |

**v2 대비 변경점:**
- `evaluate_and_promote()` 공통 함수 추출 (이중 경로 해소)
- `installable_statuses` forge 전용 정책 추가 (Critical BLOCK 해제)
- `find_similar()` → `decide_reuse()` 기반으로 재설계 (Critical BLOCK 해제)
- `resolve_skill_paths()` flat+directory 양쪽 탐색 + 소비자 영향 분석
- `reference_candidate` dict shape 명세
- artifact 보존 규약 (attempt별 디렉토리)
- LLM 빈 출력 방어 + evals YAML 파싱 방어
- JSON 원자적 쓰기 명시
- `register_skill()` 후 메모리-디스크 동기화

## 6. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| LLM 호출 횟수 증가 (정상: 5~6회 / 최악: ~18회) | 사용자 대기 시간 증가 | MAX_LLM_CALLS=15 하드 리밋 + 프로그레스 로그 |
| evals 자동 생성의 품질 | eval 자체가 틀리면 의미 없음 | local_critic으로 eval 케이스도 검증 + 최소 케이스 수 가드 + YAML 파싱 검증 |
| 도메인 힌트 매핑 유지 비용 | 정적 테이블은 누락 가능 | LLM 사전 조회 방식 우선, decide_reuse() 기반 warehouse 유사 스킬 참조 |
| 기존 플랫 구조 스킬 호환성 | FORGE_DIR에 기존 `.py` 파일 존재 | flat+directory 양쪽 탐색으로 호환 유지 |
| `system_prompt` 미지원으로 critic 품질 저하 | LLMEngine이 system_prompt 파라미터 미지원 | user prompt에 병합. 품질 저하 가능하나 이번 스코프 밖. 향후 LLMEngine 확장 시 개선 |
| 동시 forge 경합 (같은 skill_name 동시 요청) | 디렉토리 충돌 가능 | 현재 단일 프로세스이므로 낮은 위험. §6 리스크로 인지, 필요 시 lock file 추가 |
| `forge_new_skill()` 반환값이 `None` → caller 영향 | `procure_skill()` caller가 None 처리 미비 시 NPE | `procure_skill()` 이미 None 반환 가능(line 148). caller 확인 필요 |

## 7. 교차 검증 피드백 대응표

| # | 피드백 | 심각도 | 해결 섹션 | 상태 |
|---|--------|--------|-----------|------|
| 1 | `installable_statuses` Dead End | **Critical** | §3.10 | ✅ forge 전용 FORGE_POLICIES 주입 |
| 2 | `find_similar()` 메서드 미존재 + shape 미정의 | **Critical** | §3.4 | ✅ `decide_reuse()` 기반 + dict shape 명세 |
| 3 | 디렉토리 구조 전환 영향 범위 과소 | High | §3.3 | ✅ `resolve_skill_paths()` + `procure_skill()` 양쪽 탐색 + 소비자 분석 |
| 4 | `SkillOrchestrator` 기존 파이프라인과 중복 | High | §3.9 | ✅ `evaluate_and_promote()` 공통 함수 추출 |
| 5 | 등록 ≠ 조회 가능 (저장소 불일치) | High | §3.6 | ✅ 이중 방어: 등록 시점 이동 + installable 확인 + 메모리 갱신 |
| 6 | 재시도 시 artifact 덮어쓰기 | High | §3.8 | ✅ runs/<run_id>/attempt-N/ 규약 |
| 7 | `propose()` eval 미사용 불일치 | Medium | §3.1 | ✅ apply()가 주 실행 함수임을 명시 |
| 8 | LLM 출력 실패 모드 (펜스 + 빈 문자열) | Medium | §3.2, §3.5 | ✅ 빈 출력 → ValueError + strip_code_fences + YAML 검증 |
| 9 | JSON 비원자적 쓰기 악화 | Medium | §3.11 | ✅ `_safe_write_json()` 활용 명시 |
| 10 | LLM 호출 budget 미정의 | Low | §3.8 | ✅ MAX_LLM_CALLS=15 하드 리밋 |
| 11 | 배포 범위 불명확 | Medium | §5 | ✅ source-only 변경, af.spec 변경 불필요 명시 |
| 12 | EvalHarness zero-case 직접 수정 | Medium | §3.5 | ❌ REJECT — forge 래퍼 레벨 가드로 충분 |
| 13 | 설계 누락 (마이그레이션, 반환타입 등) | Low | §3.3, §6 | ✅ flat+directory 호환, caller 영향 §6에 기재 |

### v3→v4 교차 검증 피드백 (3차)

| # | 피드백 | 심각도 | 해결 섹션 | 상태 |
|---|--------|--------|-----------|------|
| 14 | `auto_load_from_directories` forge SKIP | **Critical** | §3.6 | ✅ `get_global_registry().register()` 직접 호출로 변경 |
| 15 | LLM budget 카운터 어댑터 미연결 | **Critical** | §3.2 | ✅ 모든 어댑터 + evals에서 `_counted_generate()` 경유 |
| 16 | `decide_reuse({})` 항상 forge 모드 | High | §3.4 | ✅ warehouse 직접 탐색으로 primary 변경 |
| 17 | `evaluate_and_promote()` 에러 핸들링 소실 | High | §3.9 | ✅ try/except + fallback dict 반환 추가 |
| 18 | §5 `read_skill_lock` import 경로 오류 | High | §5 | ✅ `core/utils import read_skill_lock`으로 정정 |
| 19 | `forge_new_skill()` 함수 비대화 | Medium | §3.12 | ✅ 3개 헬퍼 분리 설계: `_prepare_forge_context`, `_generate_and_validate_evals`, `_evaluate_promote_and_register` |
| 20 | forge 스킬 간 sys.path 충돌 | Medium | §3.13 | ✅ forge parent_dir sys.path 추가 조건부 스킵 + 대안 분석 |
| 21 | evals 재생성 동일 프롬프트 | Low | §3.5 | ✅ 피드백 augmented 재시도 프롬프트 |

## 8. 체크리스트

- [ ] 프롬프트 변경: argparse CLI → propose/apply/test 함수 기반, apply 가중치 (§3.1)
- [ ] 프롬프트 일본어→한글 수정: `には` → `에는` (§3.1)
- [ ] LLMEngine → SkillForge 어댑터 함수 구현 + 빈 출력 방어 (§3.2)
- [ ] 스킬 디렉토리 구조 전환: FORGE_DIR/name/name.py (§3.3)
- [ ] `resolve_skill_paths()` flat+directory 양쪽 탐색 (§3.3)
- [ ] `procure_skill()` forge 경로 탐색 양쪽 지원 (§3.3)
- [ ] `decide_reuse()` 기반 유사 스킬 참조 + reference_candidate shape (§3.4)
- [ ] evals.yml 자동 생성 + strip_code_fences + YAML 검증 + contract/cases 키 정규화 (§3.5)
- [ ] 최소 3개 eval 케이스 가드 + 재생성 1회 (§3.5)
- [ ] 등록 시점 이동: promotion 후 installable일 때만 (§3.6)
- [ ] `procure_skill()` installable 상태 확인 (read_skill_lock) (§3.6)
- [ ] `register_skill()` 후 메모리 레지스트리 갱신 (§3.6)
- [ ] `evaluate_and_promote()` standalone 함수 추출 (§3.9)
- [ ] `_evaluate_and_promote_built_skill()` → `evaluate_and_promote()` 위임 (§3.9)
- [ ] FORGE_POLICIES 정의 + evaluate_and_promote에 주입 (§3.10)
- [ ] JSON 원자적 쓰기 적용 (§3.11)
- [ ] LLM 호출 카운터 + MAX_LLM_CALLS=15 (§3.8)
- [ ] artifact 보존: runs/<run_id>/attempt-N/ (§3.8)
- [ ] `forge.run()` 후 should_repair 확인 분기 (§3.8)
- [ ] 테스트 작성 (forge→eval→promotion 통합 테스트, flat+directory 양쪽)
- [ ] forge 디렉토리 구조에서 `_load_skill_callable` sys.path 충돌 테스트
- [ ] `forge_new_skill()` 내부 헬퍼 분리 검토 (`_forge_and_evaluate`, `_generate_evals`)
- [ ] Master_Blueprint.md §3 업데이트
