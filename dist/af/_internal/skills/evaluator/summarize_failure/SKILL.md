# Summarize Failure

실패한 에이전트 실행 로그를 분석하여 에러 원인, 심각도, 수정 제안을 반환하는 스킬.

## 사용 시점
- 에이전트 실행이 실패했을 때 원인 파악
- 에러 패턴 분석 및 재발 방지 대책 수립
- 유사 실패 사례 검색

## 입력
- `log_file` (필수): 실패한 실행의 JSONL 파일 경로
- `run_id` (선택): 실행 ID
- `include_suggestions` (선택, 기본=True): 수정 제안 포함 여부

## 출력
- `failure_type`: timeout | skill_error | invalid_input | hook_blocked | unknown
- `root_cause`: 근본 원인 설명
- `severity`: critical | high | medium | low
- `suggestions`: 수정 제안 목록
- `similar_failures`: 유사 실패 목록
