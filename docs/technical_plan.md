# [Agent Factory] 기술 구현 상세 계획 (technical_plan.md)

## 1. 아키텍처 개편 및 디렉토리 구조
기존 `agent-factory`의 동작을 Auto-Harness 파이프라인으로 전환하기 위한 기술적 구현 방향입니다.

```text
agent-factory/
├── project_orchestrator.py      # Entry point (CLI Wizard, Context 분석, 리스크 승인)
├── factory_manager.py           # Core Assembly (Model Routing, 템플릿 기본값 주입)
├── policy.yaml [NEW]            # 단일 정책 엔진 설정 파일 (스킬별 리스크 등급 정의)
├── registry.yaml                # 완성된 에이전트/스킬 DB
├── backup_registry/ [NEW]       # 1초 롤백을 위한 스냅샷 디렉토리
└── .system_generated/logs/
    └── audit.log [NEW]          # 불변성(Immutable)을 보장하는 보안 감사 텍스트 로그
```

---

## 2. 주요 모듈별 개발 스펙

### 2.1 `policy.yaml` (단일 정책 및 하네스 엔진)
*   **목적:** 스킬의 특징에 따라 리스크를 판별하고, **표준화된 동적 티어(Tier 1/2/3)**를 지능적으로 매핑(Harness)합니다.
*   **라우팅 기준:**
    *   **Tier 1 (Economy/Speed):** 대량의 데이터 읽기, 단순 텍스트 요약 (예: `read_file`, `read_system_info`)
    *   **Tier 2 (Precision/Code):** 정밀한 코드 생성, 시스템 명령어 실행 (예: `write_file`, `run_command`)
    *   **Tier 3 (Reasoning/Logic):** 복잡한 문맥 분석, 리서치 쿼리 기획 (예: `web_search`, `project_planning`)
*   **구조 (예시):**
```yaml
skills:
  read_file:
    risk: LOW
    preferred_tier: 1
  write_file:
    risk: HIGH
    preferred_tier: 2
  web_search:
    risk: LOW
    preferred_tier: 3
  run_command:
    risk: CRITICAL
    preferred_tier: 2
```

### 2.2 `project_orchestrator.py` (CLI & 리스크 제어망)
1.  **Context 분석 로직 (`step_1_context_absorption`)**
    *   마스터 프롬프트 입력을 받아 LLM API를 호출, `[역할, 필요 스킬 리스트]`를 JSON 형태로 파싱.
    *   사용자 프롬프트에 **`고스트-파일럿`** 키워드가 포함된 경우, `is_ultra_work = True` 플래그를 활성화하여 가이드 템플릿(STEP 2)을 스킵하고 즉시 조립으로 직행.
2.  **CLI 템플릿 가이드 (`step_2_wizard`)**
    *   (`is_ultra_work == False` 일 때만 작동) `print()`와 `input()`을 활용한 경량 CLI 생성기.
3.  **3단 리스크 승인 게이트 및 Immutable Logging (`step_4_risk_gate`)**
    *   스킬 리스트 중 가장 높은 리스크(`max_risk`)를 산출.
    *   **CRITICAL 일 경우:** `is_ultra_work` 상태라도 절대 무시하지 못하고 시스템 강제 일시정지 및 `[Y/N]` 승인 대기.
    *   **HIGH / CRITICAL 승인 시 Immutable Log (해시 체인) 구현:**
        *   구현 라이브러리: 순수 파이썬 `hashlib` 사용.
        *   로그 페이로드: `[TIMESTAMP] | PREV:[HASH] | [ACTION_DETAIL] | CURR:[HASH]`
        *   해시 생성 규칙: `CURR_HASH = SHA256( PREV_HASH + TIMESTAMP + ACTION_DETAIL )`
        *   검증: `verify_audit_hash.py`를 통해 이전 라인의 `CURR_HASH`가 현재 라인의 `PREV_HASH`와 일치하는지 및 데이터 무결성 수학적 검토.

### 2.3 `factory_manager.py` (다중 모델 조립 및 병렬 실행)
1.  **Multi-Model Auto-Harness (`step_3_smart_default`)**
    *   스킬 리스트를 순회하며 `policy.yaml`의 `preferred_tier` 데이터를 집계.
    *   산출된 Tier 값(1, 2, 3)을 바탕으로, 동적 환경 변수(`env.TIER_1_MODEL` 등)를 호출하여 최종 에이전트 JSON의 `model` 파라미터에 모델명 문자열을 주입.
2.  **Versioned Backup (`step_5_snapshot`)**
    *   조립 직전 파이썬 `shutil.copy`를 실행해 `backup_registry/registry_YYYYMMDD_HHMMSS.yaml` 스냅샷 무조건 생성.
3.  **병렬 워크 가동 엔진 (Parallel Execution - `Ghost-Pilot` 전용)**
    *   각기 다른 Tier 엔진이 할당된 봇들이 "추론(Wait) -> 뼈대 작성(Run) -> 검증(Run)"하는 **비동기 100% 자동화 병렬 파이프라인** 구축.

### 2.4 `core/llm_engine.py` (최신 LLM 동적 Auto-Upgrade 스캐너)
1.  **동적 버전 감지 (`get_latest_flash_model`)**
    *   API(`genai.list_models()`)를 쿼리하여 지원되는 모델 중 가장 리비전 숫자가 높은 `gemini-*.0-flash` 모델명을 정규식으로 추출 및 글로벌 캐싱.
2.  **강제 상향 및 다운그레이드 방어 (`__init__`)**
    *   초기화 시 인자로 받은 `model_name`과 API 쿼리로 얻은 최신 모델명(`latest`)의 버전을 Float 타입으로 추출해 수학적 비교.
    *   `API 최신 버전 > 사용자(템플릿) 요청 버전` 일 때만 엔진 이름을 자동 상향(Upgrade) 덮어쓰기 진행. (만약 구형 SDK 등으로 API에서 하위 버전을 반환하는 경우, 사용자가 명시한 3.0 등의 상위 버전을 그대로 유지하도록 방어 로직 탑재)

---

## 3. 통합 테스트 및 DoD (검증 기준)
기능 구현 후, 다음 3가지 핵심 검증 기준(Definition of Done)을 100% 충족해야 배포 가능합니다.

1.  **테스트 1 - 동적 티어 라우팅 검증:** "로컬 로그 파일 읽는 에이전트 생성"
    *   조건: 스킬 `read_file` 도출.
    *   DoD: 팝업 없이 승인 게이트가 자동 통과되며, 시스템이 모델명을 고정 하드코딩하지 않고 `Tier 1 (Economy/Speed)`에 해당하는 현재 설정 모델을 성공적으로 할당했음을 CLI 로그로 출력해야 함.
2.  **테스트 2 - Immutable Log 해시 체인 수학적 검증:** "크롤링 후 파일 쓰는 에이전트 생성"
    *   조건: 스킬 `web_search(Tier3)`, `write_file(HIGH)` 도출.
    *   DoD: `.system_generated/logs/audit.log` 내용에 해시값이 포함된 신규 라인이 추가됨. 생성 완료 직후 `python tests/verify_audit_hash.py` (추가 개발 필요) 실행 시, 1번째 줄부터 N번째 줄까지의 연쇄 해시값 비교 연산이 True(변조 0%)를 뱉어야 함.
3.  **테스트 3 - 블로킹 로직 및 스냅샷 검증:** "서버 상태 점검(명령어 실행) 에이전트 생성"
    *   조건: 스킬 `run_command(CRITICAL)` 도출.
    *   DoD: 화면에서 `[Y/N]` 하드 스탑(Hard-Stop)이 발생함을 지켜보고, `Y` 승인 시 로그 기록과 함께 `backup_registry_YYYYMMDD_HHMMSS.yaml` 스냅샷 파일이 생성 폴더에 무결하게 쓰여졌음을 파이썬 `os.path.exists()` 등으로 최종 확인.
