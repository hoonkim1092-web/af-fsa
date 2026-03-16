# 🏠 집 PC에서 할 일 - Phase 4 가이드

**상태**: Phase 1-3 완료 (95/100)
**다음**: Phase 4 ENHANCEMENT (5개 항목)
**소요시간**: ~3시간
**난이도**: 🟢 쉬움

---

## 📋 집 PC에서 시작하기

### 1단계: 최신 코드 받기

```bash
cd D:\hoonProJect\worktrees\agent-factory
git fetch origin
git pull origin feat/unified-project-pipeline-subscription
```

### 2단계: 현재 상태 확인

```bash
# 최신 커밋 확인
git log --oneline -5

# 점수 확인
python -c "
from core.skill_context_config import get_max_skills_for_model
print('✓ Haiku:', get_max_skills_for_model('claude-haiku-4-5'))
print('✓ Sonnet:', get_max_skills_for_model('claude-sonnet-4-6'))
"
```

---

## 🎯 Phase 4: ENHANCEMENT (다음 할 일)

### [ENH-1] 타입 힌트 추가 ⏱️ 20분

**파일**: `core/agent_runner.py`
**위치**: 클래스 멤버 변수 초기화 부분

```python
# 현재 (L88-89)
self._skill_loader_cache: dict = {}
self._current_model_name: str = "default"

# 수정
from typing import Dict
self._skill_loader_cache: Dict[str, "AdaptiveSkillLoader"] = {}
self._current_model_name: str = "default"
```

**검증**:
```bash
python -c "from core.agent_runner import AgentRunner; print('✅ 타입 힌트 추가 완료')"
```

---

### [ENH-2] 입력값 검증 추가 ⏱️ 20분

**파일**: `core/skill_context_config.py`
**함수**: `get_context_tokens()`

```python
# 추가할 코드 (L39 함수 시작 후)
def get_context_tokens(model_name: str) -> int:
    """..."""
    if not model_name or not isinstance(model_name, str):
        logger.warning(f"Invalid model_name: {model_name}, using default")
        return MODEL_CONTEXT_TOKENS["default"]

    name = model_name.lower()
    # ... 이하 기존 코드
```

**검증**:
```bash
python -c "
from core.skill_context_config import get_context_tokens
print('✓ None:', get_context_tokens(None))
print('✓ Empty:', get_context_tokens(''))
print('✓ Valid:', get_context_tokens('claude-sonnet-4-6'))
"
```

---

### [ENH-3] 상세 로깅 추가 ⏱️ 30분

**파일**: `core/skill_loader.py`
**메서드**: `_inject_missing_deps()`

```python
# L285 이후에 로깅 추가
logger.debug(f"[의존성 주입 시작] 초기: {len(result)}개")

# L288 루프 내에 추가
for iteration in range(5):
    missing = dep_graph.get_required_deps(result)
    if not missing:
        logger.debug("✓ 필수 의존성 모두 충족")
        break

    added = False
    for dep_id in missing:
        if dep_id in all_skills and dep_id not in result:
            result.append(dep_id)
            logger.debug(f"  [+] 의존성 추가: {dep_id}")
            added = True

    if added:
        logger.debug(f"  반복 {iteration+1}: {len(result)}개")
    else:
        logger.warning("⚠️ 더 이상 추가할 의존성 없음")
        break

logger.debug(f"[의존성 주입 완료] 최종: {len(result)}개")
```

**검증**:
```bash
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from core.skill_loader import DynamicSkillLoader
# 로깅이 출력되는지 확인
"
```

---

### [ENH-4] 단위 테스트 작성 ⏱️ 1시간

**파일**: `tests/test_skill_loader_phase4.py` (새로 생성)

```python
import pytest
from core.skill_context_config import (
    get_context_tokens,
    get_max_skills_for_model,
    SkillLoaderConfig,
)
from core.skill_loader import AdaptiveSkillLoader

class TestSkillContextConfig:
    """스킬 컨텍스트 설정 테스트"""

    def test_get_context_tokens_haiku(self):
        """Haiku 컨텍스트 토큰 확인"""
        assert get_context_tokens("claude-haiku-4-5") == 8_000

    def test_get_context_tokens_sonnet(self):
        """Sonnet 컨텍스트 토큰 확인"""
        assert get_context_tokens("claude-sonnet-4-6") == 200_000

    def test_get_context_tokens_default(self):
        """기본값 확인"""
        assert get_context_tokens("unknown-model") == 8_000

    def test_get_max_skills_haiku(self):
        """Haiku 최대 스킬 개수 확인"""
        result = get_max_skills_for_model("claude-haiku-4-5")
        assert result == 3  # MIN_SKILLS

    def test_get_max_skills_sonnet(self):
        """Sonnet 최대 스킬 개수 확인"""
        result = get_max_skills_for_model("claude-sonnet-4-6")
        assert result == 200  # MAX_SKILLS_ABSOLUTE

    def test_skill_loader_config_max_skills(self):
        """SkillLoaderConfig max_skills 프로퍼티 확인"""
        # 자동 계산
        config = SkillLoaderConfig(model_name="claude-sonnet-4-6")
        assert config.max_skills == 200

        # 수동 지정
        config = SkillLoaderConfig(max_skills_override=50)
        assert config.max_skills == 50

class TestAdaptiveSkillLoader:
    """적응형 스킬 로더 테스트"""

    def test_for_model_haiku(self):
        """Haiku 로더 생성 확인"""
        loader = AdaptiveSkillLoader.for_model("claude-haiku-4-5")
        assert loader.config.model_name == "claude-haiku-4-5"
        assert loader.config.max_skills == 3

    def test_for_model_sonnet(self):
        """Sonnet 로더 생성 확인"""
        loader = AdaptiveSkillLoader.for_model("claude-sonnet-4-6")
        assert loader.config.model_name == "claude-sonnet-4-6"
        assert loader.config.max_skills == 200

# 실행 방법:
# pytest tests/test_skill_loader_phase4.py -v
```

**테스트 실행**:
```bash
pytest tests/test_skill_loader_phase4.py -v
```

---

### [ENH-5] 통합 테스트 실행 ⏱️ 30분

**파일**: 기존 테스트 실행

```bash
# 1. 모든 테스트 실행
pytest tests/ -v --tb=short

# 2. 커버리지 확인
pytest tests/ --cov=core --cov-report=html

# 3. 특정 모듈만 테스트
pytest tests/test_skill_loader_phase2.py -v
pytest tests/test_skill_context_config.py -v
```

---

## ✅ 각 단계별 체크리스트

### ENH-1 완료 후
- [ ] 타입 힌트 추가
- [ ] IDE에서 타입 체크 확인
- [ ] 코드 재실행 확인

### ENH-2 완료 후
- [ ] 입력값 검증 로직 추가
- [ ] None, "", 잘못된 값 처리 확인
- [ ] 로그 메시지 확인

### ENH-3 완료 후
- [ ] 로깅 코드 추가
- [ ] DEBUG 레벨에서 로그 출력 확인
- [ ] 로그 메시지 명확함 확인

### ENH-4 완료 후
- [ ] 테스트 파일 생성
- [ ] 모든 테스트 통과 확인 (100%)
- [ ] 커버리지 80% 이상 확인

### ENH-5 완료 후
- [ ] 전체 테스트 통과
- [ ] 통합 테스트 성공
- [ ] 커버리지 리포트 생성

---

## 🚀 집 PC에서 빠르게 시작하기

### 한 줄로 모두 준비

```bash
# 1. 코드 받기
cd D:\hoonProJect\worktrees\agent-factory && git pull origin feat/unified-project-pipeline-subscription

# 2. 현재 상태 확인
python -m py_compile core/agent_runner.py core/skill_loader.py core/skill_context_config.py && echo "✅ 모두 준비 완료"

# 3. Phase 4 시작!
```

---

## 📞 완료 후 할 일

Phase 4 완료 후:
1. 각 ENH 항목 커밋 (1개씩 또는 모두 한번에)
2. 테스트 결과 확인
3. 최종 점수: **98/100** 목표

```bash
# 최종 커밋 예시
git add .
git commit -m "feat: Phase 4 ENHANCEMENT 완료 (5개 항목)

- ENH-1: 타입 힌트 추가
- ENH-2: 입력값 검증
- ENH-3: 상세 로깅
- ENH-4: 단위 테스트
- ENH-5: 통합 테스트

최종 점수: 98/100"
```

---

## 📊 진행 상태

| Phase | 상태 | 점수 | 소요시간 |
|-------|------|------|---------|
| Phase 1-3 | ✅ 완료 | 95/100 | 2시간 35분 |
| Phase 4 | 🔄 **대기 중** | 98/100 | ~3시간 |
| 최종 | ⏳ 다음 | 98/100 | 5.5시간 |

---

**다음 상태**: 집 PC에서 코드를 받아서 Phase 4 (ENH-1~5)를 수행하면 됩니다! 🎯

