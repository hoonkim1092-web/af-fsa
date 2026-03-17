# Documentation Workflow

## 목적

이 문서는 `agent-factory`의 새 문서 체계와 승인 기반 작업 흐름을 정의한다.

## 공통 기준 문서

- `docs/project-product-overview.md`
- `docs/project-technical-overview.md`
- `docs/repository-structure.md`
- `docs/standards/api-standards.md`
- `docs/standards/testing-standards.md`

이 문서들은 작업별 문서보다 상위 기준으로 취급한다.

## 작업 문서 위치

- 경로: `docs/work-items/<slug>/`
- `<slug>`는 작업 이름을 소문자, 하이픈 중심의 안전한 폴더명으로 정리한 값이다.

## 작업 문서 세트

- `feature-plan.md`
- `feature-spec.md` 또는 `bug-fix-spec.md`
- `implementation-design.md`
- `implementation-tasks.md`
- `approval-gate.md`
- `change-request.md`
- `verification-report.md`

## 명시적 단계

1. 작업 등록
   - `feature-plan.md` 작성
2. 명세 작성
   - 기능이면 `feature-spec.md`
   - 버그면 `bug-fix-spec.md`
3. 설계 작성
   - `implementation-design.md`
4. 작업 분해
   - `implementation-tasks.md`
5. 승인
   - `approval-gate.md`
6. 구현
   - 승인 스냅샷과 현재 문서 상태가 같을 때만 가능
7. 변경 요청
   - 승인 후 의미 있는 문서 수정이 생기면 `change-request.md`
8. 검증과 종료
   - `verification-report.md`

## 승인 규칙

- 승인 대상은 최신 문서 상태다.
- 문서를 편집한 뒤에는 편집된 최신 상태로 다시 승인받아야 한다.
- 승인 후 문서가 바뀌면 이전 승인은 무효다.
- `approval-gate.md`의 `execution_open: true`가 아니면 구현을 시작하지 않는다.

## 편집과 재승인 규칙

- 오탈자, 링크, 표현 정리처럼 의미가 바뀌지 않는 수정은 경미 수정으로 본다.
- 요구사항, 범위, 완료 조건, 설계, 작업 순서의 의미가 바뀌면 재승인이 필요하다.

## 레거시 문서와의 관계

현재 저장소에는 아래 레거시 문서가 이미 존재한다.

- `docs/functional_spec.md`
- `docs/technical_plan.md`
- `docs/task.md`

이 파일들은 기존 자동화와 참조를 위해 유지한다. 새 작업은 `docs/work-items/` 구조를 우선 사용하고, 레거시 문서는 점진적으로 대체한다.
