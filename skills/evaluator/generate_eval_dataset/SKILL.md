# Generate Evaluation Dataset

여러 실행 로그를 분석하여 에이전트 행동 검증용 평가 데이터셋을 자동 생성하는 스킬.

## 사용 시점
- 에이전트 성능 벤치마크 데이터셋 생성
- 스킬별 성공률/응답시간 분석
- 회귀 테스트를 위한 테스트 케이스 자동 생성

## 입력
- `log_dir` (필수): trace_*.jsonl 파일이 있는 디렉토리
- `min_runs` (선택, 기본=1): 최소 로그 수
- `success_ratio` (선택, 기본=0.7): 목표 성공률
- `output_file` (선택, 기본=eval_dataset.jsonl): 출력 파일 경로

## 출력
- `dataset_file`: 생성된 JSONL 파일 경로
- `total_cases`, `success_cases`, `failure_cases`
- `coverage`: 스킬별 커버리지 및 성공률
