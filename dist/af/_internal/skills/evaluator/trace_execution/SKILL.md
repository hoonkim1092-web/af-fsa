# Trace Execution

JSONL 로그 파일에서 에이전트 실행 흐름을 추출하고 구조화된 분석 결과를 반환하는 스킬.

## 사용 시점
- 에이전트 실행 결과를 분석할 때
- 스킬 호출 순서와 타이밍을 파악할 때
- 디버깅을 위해 실행 흐름을 추적할 때

## 입력
- `log_file` (필수): JSONL 트레이스 파일 경로
- `run_id` (선택): 실행 ID (파일에서 자동 추출 가능)
- `include_details` (선택, 기본=True): 스킬 인자/결과 상세 포함 여부

## 출력
- `run_id`, `agent_name`, `total_duration_ms`
- `skill_calls`: 호출 순서, 스킬명, 시작/종료 시간, duration
- `status`: success | failure | incomplete
