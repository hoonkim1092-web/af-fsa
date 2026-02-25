# [Agent Factory] Auto-Harness (5-Step) 구현 체크리스트

각 작업은 `functional_spec.md` 및 `technical_plan.md`의 아키텍처를 따릅니다.

## 0. 기초 공사 (Foundation)
- [x] `policy.yaml` 단일 정책 엔진 파일 생성 및 스킬별 리스크 / **티어 기반 모델 하네스 룰** 매핑
- [x] 롤백 백업을 위한 `backup_registry/` 로컬 디렉토리 생성
- [x] 감사 로그 파일(`.system_generated/logs/audit.log`) 환경 셋업 및 초기화 (해시 체인 방식의 불변성 확보)

## 1. 기반 로직 구현 (`factory_manager.py`)
- [x] **Multi-Model Auto-Harness 연동**: 필요 스킬 리스트를 기반으로 `policy.yaml` 스캔 후, 가장 최적화된 프론티어 Tier(1:Economy, 2:Precision, 3:Reasoning)에 따른 메인 엔진을 동적 주입(Injection)하는 할당 함수 작성
- [x] **Snapshot 함수 구현**: (조립 단계에서 호출) `registry.yaml`을 복사하여 타임스탬프 파일명 지정(`backup_registry/registry_YYYYMMDD_HHMMSS.yaml`)으로 백업하는 로직 추가
- [x] **병렬 워크 가동 엔진 구축 (`Ghost-Pilot`)**: `ThreadPoolExecutor`를 활용하여 생성된 '서로 다른 Tier의 뇌'를 가진 에이전트들이 100% 자율 및 병렬로 태스크를 동시 수행하는 비동기 프레임워크 구현
- [x] **Policy Check 함수 구현**: 생성될 에이전트의 스펙이 단일 정책을 위배하지 않는지 검사하는 `Deny-by-default` 검증 로직 추가

## 2. CLI 입출력 흐름 제어 (`project_orchestrator.py`)
- [x] **[STEP 1]** 사용자의 자연어 입력 파싱 모듈 연동 및 **`고스트-파일럿`** 키워드 감지 시 자동화 플래그(`is_ultra_work`) 활성화 로직 구현
- [x] **[STEP 2]** (`is_ultra_work == False` 일 때) 초안을 시각화하되, `[LOW]`, `[CRITICAL]` 리스크 태그를 `policy.yaml`에서 조회해 화면에 띄우는 가이드 위저드 구현
- [x] **[STEP 3]** 사용자가 비워둔 설정(모델 등)을 위 `factory_manager`의 동적 Tier 하네스 로직으로 덮어쓰기
- [x] **[STEP 4]** 3단계 리스크 제어 및 Immutable Logging 엔진 적용
    - 최고 권한이 CRITICAL일 때 콘솔에서 `Y/N` 블로킹 팝업 대기 (풀 Auto 모드라도 강제 개입)
    - HIGH/CRITICAL 승인 발생 시 이전 해시값 결합 등 **해시 체인(Hash Chain) 방식**으로 무결성이 보장된 텍스트 포맷을 `audit.log`에 즉시 추가(Append) 기록
- [x] **[STEP 5]** 모든 템플릿과 권한이 통과되면, 백업(Snapshot) 생성 명령을 호출하고 최종 시스템 출고 (Ghost-Pilot의 경우 즉시 병렬 실행기로 패스루)

## 3. 통합 테스트 (Verification DoD 충족)
- [ ] CLI 구동 후 시나리오에 따른 Definition of Done 충족 여부 테스트
    - [ ] **테스트 1 (동적 티어 라우팅 검증):** '로컬 로그 파일 읽기' 요청 시, LOW 로직 탑재 및 특정 모델명 픽스가 아닌 `Tier 1 (Economy/Speed)` 동적 배정 성공 확인
    - [ ] **테스트 2 (해시 체인 로그 수학적 검증):** '웹 검색' 요청 시, `Tier 3 (Reasoning)` 배정 및 `audit.log` 에 저장된 연쇄 해시 기록을 `verify_audit_hash.py` 스크립트로 검증하여 무결성(True) 확인
    - [ ] **테스트 3 (블로킹 로직 및 스냅샷 검증):** '명령어 실행' 등 Critical 요청 시 즉각적인 `[Y/N]` 팝업 블로킹 및 스냅샷 정상 생성 확인
    - [ ] **테스트 4 (Ghost-Pilot 병렬 시뮬레이션):** CLI에 "고스트-파일럿" 명령 입력 시, 위저드를 스킵하고 서로 다른 Tier 엔진을 단 다중 에이전트들이 병렬 컨테이너에서 동시 가동됨을 시스템 로그로 증명
