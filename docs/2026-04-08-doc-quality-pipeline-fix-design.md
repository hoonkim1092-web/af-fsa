# 문서 품질 파이프라인 수정 설계

**날짜**: 2026-04-08  
**상태**: 설계 완료 / 구현 대기

---

## 근본 원인

```
요청 진입
 → execute_requirement_prompt() → CLI 우선 → 타임아웃
 → _collect_llm_prior_knowledge() → 빈 결과 (조용히 실패)
 → research_project_brief() → 타임아웃 → except
 → _fallback_project_brief() → 키워드 매칭 복붙 출력
```

### 원인 1: CLI가 requirement 단계 LLM 우선
`requirement_llm.py:list_requirement_candidates()` 순서:
- **현재**: CLI → anthropic_api → openai_api → google_api
- CLI subprocess는 타임아웃(120s) 빈번, JSON만 반환해야 하는데 도구 호출 시도

### 원인 2: _fallback_project_brief가 LLM 없이 키워드 매칭만
LLM 실패 시 즉시 `("game", "lotto", "poker")` 등 키워드 매칭 템플릿 반환.
LLM 재시도 경로 없음.

### 원인 3: Quality Plane 미연결
`prepare()` 라이브 경로에 critique → revision → verdict 호출 없음.
`pipeline_quality.py`에 구현체는 있으나 호출 안 됨.

### 원인 4: MaintenancePipeline workspace 버그 (라이브 크래시)
```python
# maintenance_pipeline.py:164 — workspace 누락 → TypeError
prepared = self._pipeline.prepare(task_input)
# ProjectPipeline.prepare()는 workspace가 필수 인자
```

### 원인 5: 무음 실패 (except: pass)
`_collect_llm_prior_knowledge`, virtual chunk 인덱싱 등 다수 지점이
`except Exception: pass`로 조용히 넘어가 디버깅 불가.

---

## 수정 5단계

### Step 1 — MaintenancePipeline workspace 버그 (즉시, 위험도: 없음)

**파일**: `core/control/maintenance_pipeline.py:164`

```python
# Before
prepared = self._pipeline.prepare(task_input)

# After
prepared = self._pipeline.prepare(task_input, workspace=self._workspace)
```

### Step 2 — LLM 후보 순서: API 우선 → CLI 마지막 (위험도: 낮음)

**파일**: `core/requirement_llm.py:149-157`

```python
# Before: CLI 먼저
for provider_id in cli_providers:
    add(provider_id, ..., "cli")
if get_engine_api_key("anthropic"):
    add("anthropic_api", ...)
...

# After: API 먼저
if get_engine_api_key("anthropic") or get_configured_engine_api_key("anthropic"):
    add("anthropic_api", _pick_anthropic_model("sonnet") or "claude", "api")
if get_engine_api_key("openai") or get_configured_engine_api_key("openai"):
    add("openai_api", _pick_openai_model(prefer_reasoning=False) or "gpt-5", "api")
if get_engine_api_key("google") or get_configured_engine_api_key("google"):
    add("google_api", get_best_model([...]), "api")
# CLI는 마지막
for provider_id in cli_providers:
    add(provider_id, ..., "cli")
```

API 키 없는 환경에서는 기존처럼 CLI만 후보. 하위 호환 유지.

### Step 3 — 무음 실패 가시성 개선 (위험도: 없음)

**파일**: `core/researcher.py`, `core/requirement_llm.py`

```python
# Before
except Exception:
    return []

# After
except Exception as exc:
    print(f"[Himari] _collect_llm_prior_knowledge failed: {type(exc).__name__}: {exc}")
    return []
```

적용 지점:
- `researcher.py:433` — `_collect_llm_prior_knowledge`
- `researcher.py:541` — virtual chunk 인덱싱
- `requirement_llm.py:215` — 전체 후보 실패 시 에러 출력

### Step 4 — fallback_project_brief LLM 재시도 (위험도: 중간)

**파일**: `core/researcher.py`

1. `research_project_brief()` 내 prompt 문자열을 `_build_brief_prompt(task_input, evidence)` 메서드로 추출
2. `_try_api_brief_fallback(task_input, evidence, workspace)` 신규 추가
   - transport='api'인 후보만 필터링해서 재시도
   - CLI 타임아웃 후에도 API가 살아있으면 결과 획득
3. except 블록 수정

```python
# Before
except Exception:
    return self._merge_project_brief_evidence(
        self._fallback_project_brief(task_input), task_input, evidence
    )

# After
except Exception as _brief_exc:
    print(f"[Himari] primary brief failed: {type(_brief_exc).__name__}: {_brief_exc}")
    _api_brief = self._try_api_brief_fallback(task_input, evidence, target_workspace)
    if _api_brief:
        return _api_brief
    print("[Himari] all LLM paths exhausted — using keyword heuristic fallback")
    return self._merge_project_brief_evidence(
        self._fallback_project_brief(task_input), task_input, evidence
    )
```

### Step 5 — Quality Plane 라이브 경로 연결 (위험도: 중간)

**파일**: `core/project_pipeline.py`

`prepare()` 내 `return PreparedProject(...)` 직전에 삽입:

```python
# Quality Plane (critique → structural gate → verdict)
_quality_result = _guard.run(
    stage="quality_plane",
    fn=lambda: self._run_quality_plane(
        project_brief=project_brief,
        evidence=research_evidence,
        run_id=run_id,
    ),
    fallback=lambda exc: {"_stage_degraded": "quality_plane", "_warnings": [str(exc)]},
    timeout_sec=60,
)
if isinstance(_quality_result, dict) and _quality_result.get("verdict"):
    verdict = _quality_result["verdict"]
    print(f"[ProjectPipeline] quality verdict={verdict.decision} score={verdict.quality_score:.3f}")
    project_brief["_quality_result"] = {
        "decision": getattr(verdict, "decision", "unknown"),
        "quality_score": getattr(verdict, "quality_score", 0.0),
    }
    self._write_json(project_brief_path, project_brief)
```

`_run_quality_plane()` 신규 메서드: structural gate → critique → AggregatedVerdict.  
60초 타임아웃 초과 시 graceful skip (prepare 중단 없음).

---

## 수정 후 흐름

```
요청 진입
 → execute_requirement_prompt()
    → anthropic_api 먼저 시도 (API 키 있으면) ✅
    → openai_api → google_api → CLI (마지막)
 → collect_project_evidence() → evidence 충실
 → research_project_brief() → LLM 성공
    → 실패 시 _try_api_brief_fallback() → API 재시도
    → 전부 실패 시에만 키워드 휴리스틱
 → Quality Plane (critique → verdict) 60초 이내 실행
    → 실패 시 graceful skip
```

---

## 검증 방법

```bash
# Step 1 — workspace 버그
python -c "
from unittest.mock import MagicMock
from core.control.maintenance_pipeline import MaintenancePipeline
mp = MaintenancePipeline.__new__(MaintenancePipeline)
mp._workspace = '/tmp/test'
mp._pipeline = MagicMock()
mp._pipeline.prepare.return_value = {}
# TypeError 없이 통과해야 함
"

# Step 2 — 후보 순서
python -c "
from core.requirement_llm import list_requirement_candidates
cs = list_requirement_candidates()
transports = [c.transport for c in cs]
print('순서:', transports)
# API 키 있으면 api가 cli보다 앞에 와야 함
"

# 전체 syntax check
python -m py_compile core/requirement_llm.py core/researcher.py \
  core/project_pipeline.py core/control/maintenance_pipeline.py
```

---

## 관련 파일

| 파일 | Step |
|------|------|
| `core/control/maintenance_pipeline.py:164` | 1 |
| `core/requirement_llm.py:149-157, 215` | 2, 3 |
| `core/researcher.py:433, 541, 669-712, 726` | 3, 4 |
| `core/project_pipeline.py` | 5 |
| `core/pipeline_quality.py` | 5 (참조) |
