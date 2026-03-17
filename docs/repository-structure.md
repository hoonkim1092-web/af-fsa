# Repository Structure

## 목적

이 문서는 저장소의 주요 디렉터리 역할과 수정 경계를 정의한다.

## 최상위 구조

- `core/`
  - 실행 코어, 오케스트레이션, 훅, 메모리, 검색, 정책 처리
- `skills/`
  - 스킬 구현, 스킬 메타데이터, 레지스트리
- `agents/`
  - 전역 에이전트 정의
- `projects/`
  - 프로젝트별 설정, 데이터, 메모리, 런 기록
- `docs/`
  - 아키텍처, 계획, 표준, 작업 문서
- `tests/`
  - 자동 검증 코드
- `artifacts/`
  - 생성 산출물, 스키마, 보조 출력

## 문서 구조

- `docs/project-product-overview.md`
  - 프로젝트 목적과 범위
- `docs/project-technical-overview.md`
  - 기술 스택과 제약
- `docs/repository-structure.md`
  - 저장소 구조와 경계
- `docs/standards/`
  - 공통 API 및 테스트 기준
- `docs/work-items/`
  - 작업별 계획, 명세, 설계, 승인, 검증 문서

## 수정 원칙

- `core/` 변경은 인터페이스 영향 범위를 먼저 문서화한다.
- `skills/` 변경은 레지스트리, 호출 계약, 사용 조건을 함께 검토한다.
- `projects/`는 런타임 데이터가 많으므로 수동 정리와 문서 파일을 혼동하지 않는다.
- `docs/work-items/`의 작업 문서는 실제 구현 범위를 통제하는 기준 문서다.

## 작업 문서 배치 원칙

- 작업 하나당 하나의 slug 폴더를 사용한다.
- 기능과 버그는 같은 폴더 형식을 쓰되 명세 파일만 다르게 선택한다.
- 승인 전에는 구현을 시작하지 않는다.
