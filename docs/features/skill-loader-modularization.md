# 스킬 로더 모듈화 및 적응형 컨텍스트 관리

**작성일**: 2026-03-16
**상태**: 구현 대기 중

---

## 📋 목적

현재 하드코딩된 `MAX_SKILLS_IN_CONTEXT = 12` 상수로 인한 다음 문제들을 해결:

1. **의존성 일관성 파괴** (Option C)
   - 스킬 선택 후 의존성 주입 시, 이미 선택된 스킬이 제외될 수 있음
   - 재귀적 의존성 주입 후 강제 자르기로 인한 불완전한 그래프

2. **모델별 컨텍스트 차이 미반영** (Option A)
   - Haiku: 8K tokens
   - Sonnet/Opus: 200K tokens
   - 모든 모델에 동일한 12개 제한으로 리소스 낭비 & 제약

3. **이중 선언 문제**
   - `skill_loader.py:MAX_SKILLS_IN_CONTEXT`
   - `agent_runner.py:MAX_ACTIVE_SKILLS`
   - 변경 시 두 곳을 모두 수정해야 함

4. **성능 문제**
   - `DynamicSkillLoader` 매번 새로 생성
   - 모델별 로더 캐싱 없음

5. **인터페이스 부재**
   - `SkillRelevance`와 `OptimizedSkillRelevance` 공통 계약 없음

---

## 🎯 설계 원칙

| 원칙 | 설명 |
|------|------|
| **SSOT** | 모든 MAX 상수는 `skill_context_config.py`에서만 정의 |
| **적응형** | 모델 이름으로 자동 계산: `(context_tokens * usage_ratio - reserved) / avg_signature` |
| **하위 호환** | 레거시 코드는 변경 없이 동작 |
| **의존성 우선** | 의존성 초과 시 필수 스킬을 희생하지 않음 |
| **캐싱** | 모델별 로더 한 번만 생성 |

---

## 📊 설정 로직

```
Model Name: "claude-haiku-4-5"
  ↓ get_context_tokens()
  ↓ 8,000 tokens
  ↓ 계산: (8000 * 0.70 - 3500) / 600
  ↓ (5600 - 3500) / 600 = 3.5 → 3개 (MIN_SKILLS 적용)

Model Name: "claude-sonnet-4-6"
  ↓ get_context_tokens()
  ↓ 200,000 tokens
  ↓ 계산: (200000 * 0.70 - 3500) / 600
  ↓ (140000 - 3500) / 600 = 227.5 → 228개 (MAX_SKILLS_ABSOLUTE 제한)
```

**상수 정의**:
- `SKILL_SIGNATURE_TOKENS_AVG = 600` : 평균 스킬 메타데이터 크기
- `RESERVED_TOKENS = 3500` : 시스템 프롬프트 + 버퍼
- `CONTEXT_USAGE_RATIO = 0.70` : 전체 컨텍스트의 70%만 스킬에 할당
- `MIN_SKILLS = 3` : 최소 3개 (필수)
- `MAX_SKILLS_ABSOLUTE = 200` : 최대 200개 (오버헤드 방지)

---

## 🔄 의존성 주입 전략 (Option C)

**문제**: 현재 선택된 스킬 + 새로운 의존성 = MAX 초과 → 강제 자르기

**해결**:
1. 재귀적으로 의존성 수집 (최대 5단계)
2. 초과 시 우선순위: **필수 의존성 > 스킬**
   - 모든 선택된 스킬의 의존성은 보존
   - 필수 의존성이 MAX_SKILLS 이상이면 경고 & 의존성만 반환
   - 그 외 경우, 의존성 + (MAX_SKILLS - len(deps))개의 스킬 반환

**흐름**:
```
입력: [skill_a, skill_b] (점수 있음)
  ↓ 의존성 수집: skill_a → dep_x, skill_b → [dep_y, dep_z]
  ↓ 결과: [skill_a, skill_b, dep_x, dep_y, dep_z]
  ↓ 초과 확인: len(결과) <= MAX_SKILLS?
  ├─ YES: 반환
  └─ NO: 의존성 우선 보존 알고리즘 적용
    ↓ core_deps = 모든 선택 스킬의 의존성
    ↓ non_deps = 선택 스킬 자체
    ↓ 반환: core_deps + non_deps[:MAX - len(core_deps)]
```

---

## 📝 수정 파일 목록

| 파일 | 변경 유형 | 영향 범위 |
|------|----------|---------|
| `core/skill_context_config.py` | **신규 생성** | 모든 스킬 로더 |
| `core/skill_loader.py` | 3곳 수정 | `_inject_missing_deps()`, `load_skills_for_task()`, `AdaptiveSkillLoader` 추가 |
| `core/agent_runner.py` | 4곳 수정 | `load_skills()` 내부, 캐싱 추가 |

---

## ✅ 검증 체크리스트

### 기능 검증
- [ ] Haiku 모델: 최대 3-5개 스킬 계산
- [ ] Sonnet 모델: 200개 이상 스킬 계산 가능
- [ ] 의존성 초과 시 필수 의존성 보존 확인
- [ ] 레거시 DynamicSkillLoader() 호출 정상 작동
- [ ] 모델별 로더 캐싱 (동일 모델 2회 호출 = 동일 객체)

### 하위 호환성
- [ ] `from core.skill_loader import DynamicSkillLoader` 정상 작동
- [ ] 기존 테스트 통과 (test_skill_loader_phase2.py)
- [ ] agent_runner.py 기존 호출부 변경 없음

### 성능
- [ ] 첫 호출: 로더 생성 (500ms 이내)
- [ ] 재호출: 캐시 히트 (< 10ms)

---

## 🚀 구현 단계

1. **`core/skill_context_config.py` 생성**
   - 모델별 토큰 맵핑
   - `get_max_skills_for_model()` 함수
   - `SkillLoaderConfig` 데이터클래스

2. **`core/skill_loader.py` 수정**
   - `_inject_missing_deps()` 재귀 알고리즘 적용
   - `load_skills_for_task()` 시그니처 확장 (`max_skills` 파라미터)
   - `AdaptiveSkillLoader` 클래스 추가

3. **`core/agent_runner.py` 수정**
   - `MAX_ACTIVE_SKILLS` 제거
   - `_skill_loader_cache` 추가
   - `_get_skill_loader()` 메서드 추가
   - `load_skills()` 내부 로더 캐싱 적용
   - `run()` 메서드에서 `model_name` 저장

4. **검증**
   ```bash
   python -m pytest tests/test_skill_loader_phase2.py -v
   python -m pytest tests/ -x -q
   ```

---

## 📌 기타 참고사항

- 모든 구현은 하위 호환성 유지
- 기존 코드는 변경 없이 자동으로 적응형 계산 수혜
- 옵션 A (적응형) + C (의존성)를 모두 해결
- 향후 동적 설정 변경 가능 (SkillLoaderConfig 확장)

---

## 승인자 서명

- [ ] 기능 설계 승인
- [ ] 코드 리뷰 완료
- [ ] 테스트 검증 완료

