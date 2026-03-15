# Phase 4: Evaluator 에이전트 스킬 구현

## 개요

Phase 3의 JSONL 트레이싱 인프라를 기반으로, 실행 로그를 자동 분석하는 3개 Evaluator 스킬을 구현했습니다.

## 구현된 스킬

### 1. trace_execution (`skills/evaluator/trace_execution/skill.py`)

**목적**: JSONL 로그에서 에이전트 실행 흐름 추출 및 구조화

**주요 기능**:
- JSONL 파싱 (손상된 줄 건너뜀)
- 스킬 호출 쌍 매칭 (start → end)
- 타임스탬프 기반 duration 계산
- 불완전한 로그 처리 (end 이벤트 누락)

**입출력**:
- 입력: `log_file` (필수), `run_id`, `include_details`
- 출력: `run_id`, `agent_name`, `skill_calls[]`, `status`, `total_duration_ms`

### 2. summarize_failure (`skills/evaluator/summarize_failure/skill.py`)

**목적**: 실패 실행의 에러 원인 분석, 심각도 판정, 수정 제안

**주요 기능**:
- 에러 패턴 매칭 (정규식 기반)
- 실패 타입 분류: `timeout`, `skill_error`, `invalid_input`, `hook_blocked`, `unknown`
- 심각도 판정: `critical`, `high`, `medium`, `low`
- 유사 실패 검색 (같은 디렉토리 내)
- 규칙 기반 수정 제안 생성

**입출력**:
- 입력: `log_file` (필수), `run_id`, `include_suggestions`
- 출력: `failure_type`, `root_cause`, `severity`, `suggestions[]`, `similar_failures[]`

### 3. generate_eval_dataset (`skills/evaluator/generate_eval_dataset/skill.py`)

**목적**: 다수의 실행 로그에서 평가 데이터셋 자동 생성

**주요 기능**:
- `trace_*.jsonl` 파일 자동 스캔
- 스킬별 성공률/응답시간 통계
- 평가 케이스 JSONL 파일 생성
- 커버리지 분석

**입출력**:
- 입력: `log_dir` (필수), `min_runs`, `success_ratio`, `output_file`
- 출력: `dataset_file`, `total_cases`, `success_cases`, `failure_cases`, `coverage`

## 디렉토리 구조

```
skills/evaluator/
├── __init__.py
├── trace_execution/
│   ├── skill.py        (propose/apply/test)
│   ├── meta.yaml
│   └── SKILL.md
├── summarize_failure/
│   ├── skill.py
│   ├── meta.yaml
│   └── SKILL.md
└── generate_eval_dataset/
    ├── skill.py
    ├── meta.yaml
    └── SKILL.md
```

## 테스트

`tests/test_phase4_evaluator_skills.py` — 15개 테스트

### trace_execution (5개)
1. 정상 JSONL 파일 파싱
2. 손상된 JSON 줄 처리
3. 불완전한 로그 (end 이벤트 누락) 처리
4. 출력 구조 필드 검증
5. 타임스탬프 기반 duration 정확도

### summarize_failure (5개)
1. 실패 케이스 감지
2. 마지막 스킬 호출 추적
3. 에러 타입 분류 (timeout)
4. 유사 에러 찾기
5. 수정 제안 생성

### generate_eval_dataset (5개)
1. 로그 디렉토리 스캔
2. 평가 케이스 생성 및 JSONL 형식
3. 성공률 계산
4. 스킬별 커버리지 분석
5. JSONL 형식 유효성 검증

## 실행 방법

```bash
# Phase 4 테스트만
python -m pytest tests/test_phase4_evaluator_skills.py -v

# 전체 테스트 (Phase 1-4)
python -m pytest tests/ -v
```

## 의존성

- Phase 3 (LangSmith Tracing Hook): JSONL 로그 생성 인프라
- 외부 패키지 의존성 없음 (stdlib만 사용)
