---
name: af-test-runner
description: "Agent Factory 구현 완료 후 3단계 테스트 실행 절차. py_compile → pytest → 모듈 임포트 검증."
---

<overview>
AF 코드 구현 완료 후 반드시 실행해야 하는 테스트 절차를 정의한다.
구현 완료 = 테스트 통과까지. 테스트 없으면 작성.
</overview>

<when-to-use>
- 코드 구현을 완료한 직후
- PR 생성 전 최종 검증
- 새 core/*.py 파일 추가 후
</when-to-use>

<procedure>

## 3단계 테스트 절차

### Step 1: Syntax 검증

변경된 모든 Python 파일에 대해:
```bash
python -m py_compile core/{변경파일}.py
```

실패 시 → syntax 수정 후 재실행.

### Step 2: 테스트 스위트 실행

```bash
cd D:/hoonProJect/worktrees/agent-factory
python -m pytest tests/ -x -q --timeout=60
```

- `-x`: 첫 실패에서 중단 (빠른 피드백)
- `--timeout=60`: 무한 대기 방지
- 실패 시 → 원인 분석 → 코드 수정 → Step 1부터 재실행

### Step 3: 신규 파일 빌드 체크

새 `core/*.py` 파일을 추가했다면:
1. `af.spec`의 `hiddenimports` 리스트에 모듈 추가 확인
2. frozen 빌드 테스트:
```bash
python -c "from core.{신규모듈} import {주요클래스}; print('OK')"
```

## 테스트 없는 모듈

기존 테스트가 없는 모듈을 수정한 경우:
- 최소한의 임포트 테스트 + 핵심 함수 호출 테스트 작성
- `tests/test_{모듈명}.py`에 저장

## 주의사항

- `tests/` 디렉토리 내 `_tmp_*` 패턴은 임시 파일 — 커밋하지 않을 것
- 테스트에서 실제 LLM API를 호출하지 않도록 mock 사용
- timeout이 걸리는 테스트는 `@pytest.mark.timeout(30)` 부착

</procedure>
