# 스킬 로더 모듈화 - 코드 리뷰 보고서

**검토 날짜**: 2026-03-16
**총점**: 72/100 ⚠️ 심각한 버그 2개 존재

---

## 🔴 심각한 버그 (CRITICAL) - 2개

### CR-1: `_current_model_name` 타이밍 버그 ⭐ 최우선 수정

**파일**: `core/agent_runner.py` L730, L783
**영향**: **치명적** - 모든 모델에서 Haiku(3개) 설정으로만 작동

**현재 실행 순서**:
```python
# L730: load_skills() 호출 시점
modules = self.load_skills(agent, task_input=task_input)
  └─ self._current_model_name = "default"  ⚠️ 초기값!
      └─ AdaptiveSkillLoader.for_model("default")
          └─ max_skills = 3 ❌ 항상 3개로 제한!

# L783: 모델 이름 설정 (너무 늦음!)
model_name = normalize_model_name(...)
self._current_model_name = model_name  ⚠️ 이미 load_skills() 실행됨!
```

**결과**:
- Sonnet/Opus (200개까지 가능): 3개로 제한됨
- 대부분의 스킬이 로드되지 않음
- 적응형 컨텍스트 기능 완전히 무효

**수정 방법**:
```python
# run() 메서드에서
# 1단계: 모델 이름 먼저 결정
model_name = normalize_model_name(agent.get("preferred_model") or 
                                  self.mr.pick(...) or ...)
self._current_model_name = model_name  # ← load_skills() 전에 설정!

# 2단계: load_skills() 호출
modules = self.load_skills(agent, task_input=task_input)
```

**소요시간**: 15분

---

### CR-2: 의존성 계산의 재귀 누락 ⭐ 최우선 수정

**파일**: `core/skill_loader.py` L304-327 (_inject_missing_deps)
**영향**: **심각** - 깊은 의존성 체인에서 하위 의존성 누락

**문제 사례**:
```
의존성 체인:
  skill_a (선택됨)
    └─ dep_b (의존)
        └─ dep_c (의존)  ← 누락됨!

현재 코드 (L308-309):
  for sid in skill_ids:  # skill_ids = [skill_a]만 처리
    dep_set.update(dep_graph.get_required_deps([sid]))
  
  결과: dep_set = {dep_b}  ❌ dep_c 없음!

max_skills 초과 시:
  core_deps = [dep_b]        # dep_c 안 들어감
  non_deps = [skill_a]
  
  최종: [dep_b, skill_a]  ❌ dep_c 제외 = 의존성 깨짐!
```

**수정 방법** (재귀적 의존성 수집):
```python
def get_all_required_deps(skill_ids):
    """재귀적으로 모든 의존성 수집."""
    visited = set()
    
    def _collect(sids):
        for sid in sids:
            if sid not in visited:
                visited.add(sid)
                missing = dep_graph.get_required_deps([sid])
                if missing:
                    _collect(missing)  # ← 재귀!
    
    _collect(skill_ids)
    return visited

# L307-309 교체
dep_set = get_all_required_deps(skill_ids)
```

**소요시간**: 30분

---

## 🟠 중요한 버그 (MAJOR) - 3개

### MAJ-1: 일반 스킬 정렬 순서 손실

**파일**: `core/skill_loader.py` L312-325
**영향**: 점수 높은 스킬이 제외될 수 있음

**예시**:
```python
scores = {"skill_a": 0.9, "skill_b": 0.5, "dep_x": 0.01}

# result 순서: [skill_b, skill_a, dep_x] (의존성 주입 순서)
non_deps = [skill_b, skill_a]  # 점수 순서 아님!
result = [dep_x] + [skill_b]   # skill_a(0.9) 제외됨! ❌

# 수정: non_deps 정렬
non_deps_sorted = sorted(non_deps, 
                         key=lambda s: scores.get(s, 0), 
                         reverse=True)
result = core_deps + non_deps_sorted[:max_skills - len(core_deps)]
```

**소요시간**: 20분

---

### MAJ-2: max_skills 의미 모호

**파일**: `core/skill_loader.py` L220
**영향**: 코드 가독성 저하

```python
def load_skills_for_task(..., max_skills: int = 0):
    effective_max = max_skills if max_skills > 0 else MAX_SKILLS_IN_CONTEXT
    # ↑ max_skills=0은 "사용하지 않겠다"는 뜻? 명확하지 않음
```

**개선**:
```python
def load_skills_for_task(..., max_skills: int | None = None):
    """
    Args:
        max_skills: 최대 스킬 개수
                    - None: 모델에 맞게 자동 계산
                    - 양수: 지정된 개수만 선택
    """
    if max_skills is None:
        max_skills = get_max_skills_for_model(...)  # 자동
```

**소요시간**: 10분

---

### MAJ-3: 중복 스킬 수 제한

**파일**: `core/agent_runner.py` L662-667
**영향**: 스킬 로드 로직 혼란

```python
# AdaptiveSkillLoader가 이미 max_skills 적용
loader = self._get_skill_loader(...)
selected, scores = loader.load_skills_for_task(task_input)

# ...로드 중...

# 또 다시 제한! (왜?)
max_skills = get_max_skills_for_model(self._current_model_name)
if len(loaded_skills) > max_skills:
    loaded_skills = loaded_skills[:max_skills]
```

**문제**: 명시적 skills 리스트 경우, 사용자 의도를 무시하고 제거

**수정**: 자동 선택과 명시적 선택 구분
```python
if not skill_ids and task_input:
    # 자동 선택: AdaptiveSkillLoader만 사용, 재제한 없음
    loader = self._get_skill_loader(...)
    selected, scores = loader.load_skills_for_task(...)
else:
    # 명시적 선택: 사용자 리스트 존중
    # load_skills() 로직 계속...

# 어느 경우든 마지막 제한 제거
# (이미 AdaptiveSkillLoader가 했거나, 사용자 선택)
```

**소요시간**: 15분

---

## 🟡 경미한 버그 (MINOR) - 4개

| 버그 | 파일 | 문제 | 해결 | 소요시간 |
|------|------|------|------|---------|
| MIN-1 | skill_context_config.py L73 | 음수 available 미처리 | `available = max(0, ...)` | 5분 |
| MIN-2 | skill_context_config.py L70 | docstring 부정확 ("228" → "200") | 예제값 수정 | 5분 |
| MIN-3 | skill_context_config.py L85-95 | 미사용 필드 3개 (enforce_dep_consistency, use_adaptive_limit, dep_inject_max_iter) | 제거하거나 실제로 사용 | 15분 |
| MIN-4 | agent_runner.py L582, L566 | 중복 import | 파일 상단에서 1회만 import | 10분 |

---

## 🟢 개선 사항 (ENHANCEMENT)

| 개선 | 심각도 | 설명 | 효과 |
|------|--------|------|------|
| ENH-1 | 성능 | 타입 힌트 추가 | IDE 지원 향상 |
| ENH-2 | 설계 | SkillLoaderConfig 필드를 실제로 사용 | 유연성 ↑ |
| ENH-3 | 보안 | 입력값 검증 | 오류 처리 ↑ |
| ENH-4 | 디버깅 | 의존성 주입 상세 로깅 | 추적성 ↑ |
| ENH-5 | 테스트 | 단위 테스트 작성 | 안정성 ↑ |

---

## 📋 수정 계획 (순서대로)

### Phase 1: 긴급 (지금 바로)
1. **CR-1**: load_skills() 호출 전 model_name 설정 → **15분**
2. **CR-2**: 재귀적 의존성 계산 → **30분**
> **소계**: 45분

### Phase 2: 필수 (1시간 내)
3. **MAJ-1**: non_deps 정렬 → **20분**
4. **MAJ-2**: max_skills 의미 명확화 → **10분**
5. **MAJ-3**: 중복 제한 제거 → **15분**
> **소계**: 45분

### Phase 3: 경미 (2시간 내)
6. **MIN-1**: 음수 available 처리 → **5분**
7. **MIN-2**: docstring 수정 → **5분**
8. **MIN-3**: 미사용 필드 정리 → **15분**
9. **MIN-4**: import 중복 제거 → **10분**
> **소계**: 35분

### Phase 4: 개선 (다음 스프린트)
10. ENH-1~5 → **1.5시간**

**전체 소요시간**: 약 **4시간** (Phase 1-3: 2시간)

---

## ✅ 검증 체크리스트

### Phase 1 후:
- [ ] Sonnet 모델에서 max_skills = 200 확인
- [ ] Haiku 모델에서 max_skills = 3 확인
- [ ] 깊은 의존성 체인(3단계 이상) 누락 없음 확인

### Phase 2 후:
- [ ] 점수 높은 스킬이 선택됨 확인
- [ ] max_skills 의미가 명확함 확인
- [ ] 명시적 skills 리스트가 존중됨 확인

### Phase 3 후:
- [ ] 음수 available 처리 확인
- [ ] docstring 정확함 확인
- [ ] 미사용 필드 정리됨 확인

### Phase 4 후:
- [ ] 단위 테스트 작성 (50개 이상)
- [ ] 통합 테스트 통과
- [ ] 커버리지 80% 이상

---

## 📊 요약

```
버그 분포:
  CRITICAL: 2개  🔴
  MAJOR:    3개  🟠
  MINOR:    4개  🟡
  ENHANCEMENT: 5개 🟢
  ────────────────
  총 14개

심각도별 소요시간:
  CRITICAL: 45분   ⭐ 즉시
  MAJOR:    45분   ⭐ 긴급
  MINOR:    35분   ✓ 오늘 내
  ENHANCEMENT: 1.5시간  ~ 다음

현재 상태: 72/100 (배포 불가)
→ Phase 1-2 완료 후: 90/100 (배포 가능)
→ Phase 3 완료 후: 95/100 (프로덕션)
→ Phase 4 완료 후: 98/100 (안정)
```

