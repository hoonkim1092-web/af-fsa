---
name: af-code-review
description: "Agent Factory 코드 리뷰 체크리스트. 기존 코드 리뷰에서 발견된 버그 패턴 기반. PR 또는 리뷰 요청 시 트리거."
---

<overview>
`docs/code_review/2026-04-03-code-review.md`에서 발견된 실제 버그 패턴을 기반으로 한 코드 리뷰 체크리스트.
새 코드가 같은 유형의 버그를 도입하지 않도록 검증한다.
</overview>

<when-to-use>
- PR 생성 전 코드 리뷰
- `/review` 요청 시
- 핵심 서브시스템(agent_runner, dynamic_orchestrator 등) 수정 시
</when-to-use>

<checklist>

## Critical 체크 (크래시/데이터 손실)

### 1. Non-atomic 파일 쓰기 (C2 유형)
```python
# BAD: 쓰기 중 크래시 시 파일 손상
with open(path, 'w') as f:
    json.dump(data, f)

# GOOD: 임시 파일 → rename (atomic)
import tempfile
with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(path), delete=False, suffix='.tmp') as f:
    json.dump(data, f)
    tmp_path = f.name
os.replace(tmp_path, path)
```

### 2. Shell injection (C4 유형)
```python
# BAD: 사용자 입력이 shell 명령에 직접 삽입
subprocess.run(f"gh issue create --title {title}", shell=True)

# GOOD: 리스트 형태 + shell=False
subprocess.run(["gh", "issue", "create", "--title", title], shell=False)
```

### 3. 스레드 종료 미처리 (C3 유형)
```python
# BAD: join(timeout) 후 스레드가 계속 실행
thread.join(timeout=10)

# GOOD: 이벤트 기반 중단
stop_event = threading.Event()
# 스레드 내부에서 stop_event.is_set() 체크
```

## High 체크 (잘못된 동작)

### 4. asyncio Lock 누락 (H1 유형)
공유 상태(dict, list)를 여러 코루틴에서 수정 시 `asyncio.Lock()` 필수.

### 5. 캐시 무한 증가 (H2 유형)
dict/list 캐시에 항목만 추가하고 제거 안 하면 메모리 누수.
LRU 또는 maxsize 설정 필요.

### 6. 스레드 안전성 (H5 유형)
글로벌 dict/list를 멀티스레드에서 접근 시 `threading.Lock()` 필수.

### 7. Silent fallback (H3 유형)
```python
# BAD: 파싱 실패 시 조용히 넘어감 → 무한 retry 가능
try:
    data = json.loads(response)
except:
    pass

# GOOD: 실패 횟수 제한 + 명시적 로깅
```

## Medium 체크 (성능/유지보수)

### 8. 매직넘버
timeout, threshold 등 하드코딩된 수치에는 상수 또는 설정값 사용.

### 9. Dead code
import 되지 않는 파일이나 도달 불가 코드 블록 확인.

### 10. af.spec hiddenimports
새 `core/*.py` 파일을 추가했다면 `af.spec` hiddenimports에 포함 확인.

</checklist>
