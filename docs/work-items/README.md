# Work Items

## 목적

이 디렉터리는 기능 개발, 버그 수정, 리팩터링, 운영 변경 같은 작업 단위를 문서로 관리하기 위한 위치다.

## 폴더 규칙

- 경로 형식: `docs/work-items/<slug>/`
- 예시:
  - `docs/work-items/next-generation-memory-system/`
  - `docs/work-items/fix-sync-timeout-bug/`

## slug 규칙

- 소문자 사용
- 공백 대신 하이픈 사용
- 특수문자는 가능한 한 제거
- 작업 이름이 드러나게 유지

## 시작 방법

1. `docs/work-items/_template/`의 템플릿을 새 slug 폴더로 복사한다.
2. `feature-plan.md`부터 작성한다.
3. 기능이면 `feature-spec.md`, 버그면 `bug-fix-spec.md`를 채운다.
4. `implementation-design.md`, `implementation-tasks.md`를 작성한다.
5. `approval-gate.md` 승인 전에는 구현하지 않는다.

## 필수 규칙

- 승인 후 문서를 바꾸면 다시 승인받는다.
- 구현 결과는 `verification-report.md`에 기록한다.
- 승인 후 범위 변경이 생기면 `change-request.md`를 작성한다.
