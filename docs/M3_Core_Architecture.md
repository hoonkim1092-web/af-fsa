# [Agent Factory] M3 Core Architecture 기술 설계도 (Technical Plan)

본 설계도는 V22 기반의 Agent Factory 코어 엔진을 차세대 '범용 자율 AI 플랫폼(M3)'으로 격상시키기 위한 아키텍처 개조 계획서입니다. (참고: 프론트엔드 표현 계층인 Generative UI는 코어 엔진 로직에서 제외됨)

## 목표 (Goal Description)
현재의 '수동 점호(Polling)' 기반 멀티 오케스트레이션을 **'마스터 중재형 비동기 이벤트망(Hub-and-Spoke Pub/Sub)'**으로 전면 전환합니다. 동시에 비용/성능 효율을 극대화하는 **'초동적 지능형 라우터'**와 통제 불능을 막는 **'고도화된 거버넌스'**를 코어 모듈에 이식합니다.

---

## 제안하는 변경 사항 (Proposed Changes)

### 1. 🔀 `core/event_bus` (신규 컴포넌트: Hub-and-Spoke 통신망)
기존 `dynamic_orchestrator.py`의 중앙 큐(Queue) 로직을 분리하여 완벽한 이벤트 구독 모델을 구축합니다.

#### [NEW] `core/event_bus/message_bus.py`
*   발행(Publish) 및 구독(Subscribe) 인터페이스 (`asyncio.Queue` 기반 또는 Redis/MQTT 인터페이스 추상화).
*   모든 이벤트는 `(Event_ID, Sender_Role, Target_Role, Payload, Spec_ID, Risk_Level)` 형태의 표준화된 JSON 규격을 따릅니다.

#### [NEW] `core/event_bus/event_hub.py` (마스터 요격 컨트롤러)
*   **Hub-and-Spoke 구현**: 에이전트 간 직접 통신 차단. 모든 Publish 이벤트는 이곳(Hub)으로 수집됩니다.
*   **Interceptor 역할**: 이벤트를 Sub 큐로 넘기기 전에 아래 `governance` 모듈을 호출하여 무결성을 검증합니다.

---

### 2. 🛡️ `core/governance` (신규 컴포넌트: 5대 통제 장치)
M3 Plan에 정의된 '통제 불능' 마지노선 방어 티어.

#### [NEW] `core/governance/dual_tracker.py`
*   `Spec ID` 기반 매핑. 기획서(`plan.md`)의 목차 파싱 후 수신된 이벤트가 A트랙(허가제)인지 B트랙(샌드박스)인지 분류.

#### [NEW] `core/governance/multi_verifier.py`
*   1차: AST 정적 검사 / 2차: 모의 샌드박스 실행(옵션) / 3차: o-series 등 추론 모델(Validator 페르소나) 검증.
*   에이전트 결과물이 허용 기준(Threshold) 미달 시 이벤트 버스에서 Reject 이벤트 전송 (Fail-fast).

#### [NEW] `core/governance/loop_breaker.py`
*   무한 티키타카 방지. 이벤트 해시(Hash) 중복 검사, TTL(Time To Live) 관리, Token 예산 소진 시 즉각 통신망 Suspend 처리.

#### [NEW] `core/governance/hitl_gateway.py`
*   위험도(Risk) 기반 보스 승인 대기열. `High Risk` 이벤트 발생 시 파이프라인 정지 후 `[Aprove/Reject]` CLI 또는 대시보드 핑 발송.

#### [MODIFY] `core/policy.yaml` (기존 로직 확장)
*   기존 도구 권한 필터링을 고도화하여 각 페르소나별 **Capability Token Base** 로직으로 선언적 변경.

---

### 3. 🧠 `core/router` (기존 `model_utils.py` 전면 리팩토링)
초동적 지능형 라우터 이식.

#### [MODIFY] `core/model_utils.py` -> `core/router/dynamic_router.py` (파일명 변경 및 고도화)
*   정적 매핑 테이블(`_ROLE_ENGINE_MAP`) 삭제.
*   **4-D Metrics Engine**: [Complexity, Budget, Latency, API Health] 4가지 요소를 런타임에 계산하여 최적 모델 동적 반환 (`Model Selector`).

#### [NEW] `core/router/model_registry.json`
*   지원 가능한 모든 LLM의 '단가, 지연 시간(초기/스트리밍), 컨텍스트 크기, 강점 영역'을 메타데이터로 관리하는 레지스트리 (수정 용이성 확보).

#### [NEW] `core/router/cascade_failover.py`
*   API 에러, Rate Limit 초과, 구문 생성 짤림 시 0.1초 만에 차순위 최적 모델로 스폰하여 다시 요청을 쏘는 핫스왑 제어기.

---

### 4. 🧠 `syncCompyne` (기억망 연동 고도화)

#### [MODIFY] `syncCompyne/memory_store.py`
*   `event_hub.py`에서 승인된 모든 이벤트를 **Immutable Audit Trail(영구 감사 로그)**로 적재하는 `append_audit_log` 인터페이스 추가. (추후 Graph Memory의 데이터 소스로 활용)

---

## 검증 계획 (Verification Plan)

이 코어 엔진은 철저하게 백엔드/클러스터 환경으로 동작하므로 다음 단위 테스트 및 통합 시뮬레이션을 통해 검증합니다.

### Automated Tests (자동 검증)
1. **[Unit Test] 다차원 라우터 연산 검증**: 
   * `pytest tests/test_dynamic_router.py`
   * 높은 예산 + 복잡한 로직 요구 시 `Claude Opus/Pro` 리턴, 단순 로직 + 극한 속도 요구 시 `Gemma-1b/Flash`가 리턴되는지 어서션 체크.
   * `cascade_failover.py` 작동 시 Mock API 429 에러 발생 후 차순위 모델로 핫스왑 스케이프 성공 여부.
2. **[Unit Test] Loop Breaker 알고리즘 검증**:
   * 동일한 해시(Hash)를 가진 이벤트를 `event_bus`에 3회 이상 Publish 했을 때, `loop_breaker.py`가 강제로 이를 `REJECTED_LOOP` 처리하는지 테스트.
3. **[Integration] Hub-and-Spoke 샌드박스 모의망 가동**:
   * 가상의 에이전트 3기(A,B,C)를 띄워놓고 Dummy 이벤트를 초당 10회씩 쏘게 함. `event_hub` 컨트롤러가 병목 없이 이 이벤트들을 순서대로 가로채서 `multi_verifier`를 거친 후 다시 서브 큐에 내려보내는지 동시성(Asyncio) 부하 테스트 진행.

### Manual Verification (수동 검증 - HITL 게이트웨이 테스트)
1. **Human-in-the-Loop 반응 로그 확인**:
   * 터미널에서 팩토리 콘솔을 띄운 뒤, 강제로 'DB 강제 삭제` 명령이 포함된 `High Risk` 조작 이벤트를 버스에 태움.
   * 콘솔 파이프라인이 즉각 Suspend 상태로 멈추고 보스(사용자)에게 **[승인/거절 대기 중...]** 프롬프트가 정상 노출되는지 육안 확인.
   * `Reject` 입력 시 해당 이벤트가 완전히 폐기(Discard)되고 팩토리가 다음 작업을 이어가는지 점검.
