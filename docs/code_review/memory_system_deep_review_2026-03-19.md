# Agent Factory Memory System — 깊은 코드 리뷰 (2026-03-19)

## 요약

Phase 10-16 (Next-Gen Memory System) 구현을 깊게 분석했습니다.
**전체적인 아키텍처는 우수하지만, 다음 영역에서 개선이 필요합니다:**

1. **Facade 검색 로직** — relevance 점수 미적용, 순차 어댑터 호출
2. **Router 라우팅** — 실제 라우팅 미구현, 중복된 코드
3. **Config 스레드 안전성** — 싱글톤 미보호
4. **Episode 추출** — 정보 손실, 중첩 호출 문제
5. **Node 프로젝트 ID 로직** — 불명확한 조건

---

## 파일별 상세 분석

### 1. `core/memory_system/models.py` (249줄) ✅ **양호**

**강점:**
- 깔끔한 dataclass 설계
- UTC 타임스탐프 일관성
- 중복 제거용 content_hash 계산 자동화 (__post_init__)
- 직렬화/역직렬화 완벽 (to_dict/from_dict)

**개선사항:** 없음 (모범 코드)

---

### 2. `core/memory_system/facade.py` (287줄) ⚠️ **중대 문제 2개**

#### **Issue #1: relevance_score 미적용**
```python
# 라인 179-181 (search_semantic)
deduped = _deduplicate(all_records)
deduped.sort(key=lambda r: r.updated_at, reverse=True)  # ❌ 문제
return deduped[:limit]
```

**문제:**
- `decay_manager.score_relevance()`를 호출하지 않음
- 단순히 updated_at 기준 정렬 (Phase 14 relevance 공식 무시)
- semantic_similarity 정보 손실

**영향:** 검색 품질 저하, Relevance 공식이 사용되지 않음

**수정안:**
```python
# Relevance 점수 계산 후 정렬
from core.memory_system.decay import MemoryDecayManager
decay_manager = MemoryDecayManager()
scored = decay_manager.rank_by_relevance(deduped)
return [r for r, _ in scored[:limit]]
```

---

#### **Issue #2: 순차 어댑터 호출 (비효율)**
```python
# 라인 158-168 (search_semantic)
for adapter in self._adapters.values():
    try:
        results = await asyncio.wait_for(
            adapter.search(...),
            timeout=timeout,
        )  # ❌ 순차 처리
        all_records.extend(results)
    except asyncio.TimeoutError:
        ...
```

**문제:**
- 어댑터들이 순차적으로 호출됨 (병렬화 X)
- timeout이 설정되어도, N개 어댑터 × 5초 = 최대 대기 시간
- 병렬 처리로 개선 가능

**수정안:**
```python
tasks = [
    asyncio.wait_for(
        adapter.search(query, limit=limit, project_id=self.project_id),
        timeout=timeout,
    )
    for adapter in self._adapters.values()
]
results = await asyncio.gather(*tasks, return_exceptions=True)
for res in results:
    if isinstance(res, Exception):
        logger.error("Adapter search failed: %s", res)
    else:
        all_records.extend(res)
```

---

#### **Issue #3: search_all_backends 프로젝트 ID 처리**
```python
# 라인 213 (search_all_backends)
results = await adapter.search(query, limit=limit, project_id=None)
```

**문제:**
- `project_id=None`이 하드코딩됨
- 메서드 의도에 불명확 (모든 프로젝트 vs. self.project_id 무시?)

**수정안:** 명확한 주석 추가 또는 인자 추가

---

### 3. `core/memory_system/decay.py` (146줄) ⚠️ **경고 1개**

#### **Issue #4: confidence_norm 로직 불명확**
```python
# 라인 104-107
confidence = record.metadata.get("confidence", 1.0)
# Normalize: confidence is typically 0-2, map to 0-1
confidence_norm = min(max(confidence, 0.0) / 2.0, 1.0)
```

**문제:**
- 주석: "confidence는 일반적으로 0-2" → 근거 불명확
- KnowledgeNode.confidence는 기본 1.0 (0-2 범위 가정 ?)
- 정규화 로직 검증 필요

**확인:**
```python
# graph_builder.py:92
def promote_to_global(...):
    node.boost_confidence(0.2)  # 기본: 1.0 → 1.2
    # 라인 193: confidence = min(self.confidence + delta, cap=2.0)
    # → 0-2 범위 맞음
```

**결론:** 맞지만, 예상 범위를 MemoryRecord.metadata 정의에 문서화 필요

---

### 4. `core/memory_system/episode_extractor.py` (116줄) ⚠️ **3개 문제**

#### **Issue #5: 결과 값 정보 손실**
```python
# 라인 76-77
if isinstance(result_val, str) and len(result_val) > 500:
    result_val = result_val[:500] + "..."
```

**문제:**
- 500자 초과 결과 자르기
- 복잡한 스킬 결과 정보 손실 가능
- 원본은 EpisodeRecord.metadata에도 저장되지 않음

**영향:** Knowledge Graph 추출 시 불완전한 정보

**수정안:**
```python
# 결과를 display(500자)와 full 분리
actions[matched_idx]["result"] = result_val
actions[matched_idx]["result_full"] = ev.get("skill_result", "")
# 또는 메타데이터에 저장
```

---

#### **Issue #6: 중첩 스킬 호출 매칭 불완전**
```python
# 라인 73-82 (FIFO 스택)
elif etype == "skill_call_end":
    sname = ev.get("skill_name", "?")
    stack = pending_stacks.get(sname, [])
    if stack:
        matched_idx = stack.pop(0)  # ❌ FIFO만 지원
        actions[matched_idx]["result"] = result_val
```

**문제:**
- 같은 스킬이 재귀적으로 호출되면 처음 호출과 매칭될 수 있음
- 예: `skill_call_start(A)` → `skill_call_start(A)` → `skill_call_end(A)` → `skill_call_end(A)`
  - 첫 번째 end가 첫 번째 start와 매칭 (의도한 순서와 다를 수 있음)

**수정안:** 스택 깊이 추적 (LIFO 또는 명시적 ID)

```python
pending_stacks: dict[str, list[tuple[int, int]]] = {}  # (idx, depth)
# 또는 skill 이벤트에 call_id/trace_id 사용
```

---

#### **Issue #7: 타임스탐프 파싱 실패 처리**
```python
# 라인 104
ts = start_ev.get("timestamp")
created = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
```

**문제:**
- 타임스탐프가 존재하지만 ISO 형식이 아니면 런타임 에러
- 예: `"2026-03-19T14:30:45.123Z"` (Z 포함) → fromisoformat 실패

**수정안:**
```python
try:
    created = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
except (ValueError, TypeError):
    logger.warning("Invalid timestamp format: %s", ts)
    created = datetime.now(timezone.utc)
```

---

### 5. `core/memory_system/config.py` (90줄) ⚠️ **스레드 안전성 문제**

#### **Issue #8: 싱글톤 미보호**
```python
# 라인 77-90
_config: MemorySystemConfig | None = None

def get_config() -> MemorySystemConfig:
    global _config
    if _config is None:
        _config = MemorySystemConfig()  # ❌ 레이스 컨디션
    return _config
```

**문제:**
- 멀티스레드 환경에서 double-check locking 없음
- 여러 스레드가 동시에 초기화할 수 있음

**수정안:**
```python
import threading

_config_lock = threading.Lock()

def get_config() -> MemorySystemConfig:
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = MemorySystemConfig()
    return _config
```

---

### 6. `core/memory_system/router.py` (131줄) ⚠️ **2개 문제**

#### **Issue #9: max() 결과 불명확**
```python
# 라인 85-86
best_type = max(scores, key=scores.get)
best_score = scores[best_type]
```

**문제:**
- scores = {EPISODIC: 0.0, GRAPH: 0.0, WORKING: 0.0, SEMANTIC: 0.0}인 경우
- Python의 `max(dict, key=...)` → 동점 시 **삽입 순서** 반환
- dict 순서가 보장되므로 항상 첫 번째 열거된 타입 반환 (의도 불명확)

**수정안:** 명시적 처리
```python
best_type = max(scores, key=scores.get)  # type: ignore
if all(v < 0.1 for v in scores.values()):
    logger.debug("No pattern matched, defaulting to SEMANTIC")
    best_type = MemoryQueryType.SEMANTIC_RECALL
    best_score = 0.1
else:
    best_score = scores[best_type]
```

---

#### **Issue #10: 라우팅 미구현 (모두 search_semantic)**
```python
# 라인 115-131
async def _recall_episodic(...) -> list[MemoryRecord]:
    return await self._facade.search_semantic(
        query, limit=limit, memory_type=MemoryType.EPISODIC,
    )

async def _recall_graph(...) -> list[MemoryRecord]:
    return await self._facade.search_semantic(
        query, limit=limit, memory_type=MemoryType.GRAPH,
    )

async def _recall_working(...) -> list[MemoryRecord]:
    return await self._facade.search_semantic(
        query, limit=limit, memory_type=MemoryType.WORKING,
    )
```

**문제:**
- 모든 메서드가 `search_semantic(memory_type=X)` 호출 (단순한 필터)
- 진정한 라우팅이 아님 (예: EPISODIC_RECALL → timeline 기반 검색 X)
- GRAPH_TRAVERSE → graph_query.bfs() 호출 예상했으나 미구현

**영향:** Router가 쿼리 분류만 하고, 실제 검색 전략은 없음

**수정안:** 각 라우트별 실제 로직 구현
```python
async def _recall_episodic(...) -> list[MemoryRecord]:
    # Timeline 기반 순차 검색 또는 time range filter
    from core.memory_system.graph_query import GraphQuery
    records = await self._facade.search_semantic(
        query, limit=limit, memory_type=MemoryType.EPISODIC,
    )
    # 시간순 정렬
    records.sort(key=lambda r: r.created_at, reverse=True)
    return records

async def _recall_graph(...) -> list[MemoryRecord]:
    # Graph 순회 (BFS/DFS)
    from core.memory_system.graph_query import GraphQuery
    # graph_query.bfs() 또는 dfs() 호출
    ...
```

---

### 7. `core/memory_system/graph_builder.py` (134줄) ⚠️ **2개 문제**

#### **Issue #11: project_id 조건 불명확**
```python
# 라인 38, 51, 64
project_id=failure.project_id or None,
project_id=failure.project_id or None,
project_id=success.project_id or None,
```

**문제:**
- `x or None` → x가 falsy이면 None, 아니면 x
- 하지만 이미 KnowledgeNode.project_id의 타입이 `str | None`
- 의도: failure.project_id가 비어있으면 None, 아니면 값

**분석:**
```python
failure.project_id = ""  # EpisodeRecord 기본값
"" or None → None  # 맞음
failure.project_id = "agent_factory"
"agent_factory" or None → "agent_factory"  # 맞음
```

**결론:** 동작 맞지만, 명확성 개선
```python
project_id=failure.project_id if failure.project_id else None,
# 또는
project_id=failure.project_id or None  # 주석 추가
```

---

#### **Issue #12: promote_to_global 로직**
```python
# 라인 98-104
def promote_to_global(..., project_hits: list[str] | None = None) -> bool:
    if node.project_id is None:
        return False  # Already global
    if project_hits and len(set(project_hits)) >= min_projects:
        node.project_id = None  # Global
        node.boost_confidence(0.2)
        return True
    return False
```

**문제:**
- `project_hits` 인자가 사용되지만, 호출처에서 전달되는지 미확인
- 일반적 사용 시나리오: 여러 프로젝트에서 같은 문제 발생 → 글로벌 지식으로 승격
- 하지만 호출자가 프로젝트 히트를 추적해야 함 (부담)

**확인:** 호출처 검색 필요 (추후 분석)

---

### 8. `core/memory_system/adapters/base.py` (75줄) ✅ **양호**

**강점:**
- 깔끔한 ABC 인터페이스
- 모든 메서드 명확
- `_tag()` 헬퍼 유용

**개선:** 없음

---

### 9. `core/memory_system/adapters/cortex_vector.py` (194줄) ⚠️ **2개 문제**

#### **Issue #13: requests 임포트 (매번 발생)**
```python
# 라인 65
import requests
url = f"{self._client.sb_url}/rest/v1/cortex_memory"
```

**문제:**
- 메서드 호출마다 `import requests` (매번 로드)
- 성능 낭비, PEP 8 위반

**수정안:** 모듈 상단에 임포트
```python
import requests  # 상단
```

---

#### **Issue #14: _available 플래그 미사용**
```python
# 라인 47-57 (initialise)
async def initialise(self) -> None:
    CortexClient = _try_import_cortex()
    if CortexClient is None:
        logger.warning("CortexClient unavailable — adapter disabled")
        return  # ❌ _available 설정 안 함
    ...
```

**문제:**
- `CortexClient is None`일 때 `self._available` 설정 안 함
- 다른 메서드에서 `if not self._available` 체크는 하지만, 초기화 실패 시 플래그 불일치

**수정:**
```python
async def initialise(self) -> None:
    CortexClient = _try_import_cortex()
    if CortexClient is None:
        logger.warning("CortexClient unavailable — adapter disabled")
        self._available = False  # 명시적 설정
        return
    ...
```

---

## 요약 테이블

| 심각도 | 파일 | Issue | 분류 |
|--------|------|-------|------|
| 🔴 CRITICAL | facade.py | #1: relevance_score 미적용 | 기능 |
| 🟠 HIGH | facade.py | #2: 순차 어댑터 호출 | 성능 |
| 🟡 MEDIUM | episode_extractor.py | #5: 결과 정보 손실 | 데이터 손실 |
| 🟡 MEDIUM | episode_extractor.py | #6: 중첩 호출 매칭 불완전 | 정확성 |
| 🟡 MEDIUM | router.py | #10: 라우팅 미구현 | 기능 |
| 🟡 MEDIUM | config.py | #8: 싱글톤 미보호 | 동시성 |
| 🟢 LOW | decay.py | #4: confidence 정규화 문서화 | 문서 |
| 🟢 LOW | router.py | #9: max() 동점 처리 | 명확성 |
| 🟢 LOW | graph_builder.py | #11: project_id 조건 명확성 | 명확성 |
| 🟢 LOW | cortex_vector.py | #13: requests 임포트 위치 | 스타일 |
| 🟢 LOW | cortex_vector.py | #14: _available 플래그 불일치 | 일관성 |

---

## 다음 단계

1. ✅ CRITICAL 이슈 (#1) 수정 — relevance_score 적용
2. ✅ HIGH 이슈 (#2) 수정 — 병렬 어댑터 호출
3. ✅ MEDIUM 이슈들 수정
4. ✅ 테스트 업데이트
5. ✅ 통합 테스트 실행

