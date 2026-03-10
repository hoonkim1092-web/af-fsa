# 코드 리뷰 리포트 검증 및 수정 계획

## 메타데이터
- 날짜: 2026-03-10
- 원본 리포트: `code_review_report.md`
- 검증 범위: 현재 `feat/unified-project-pipeline-subscription` 워크트리 기준으로 리포트 내용을 대조하고, 실제 런타임 구조를 다시 정리한 뒤, 우선순위가 명확한 수정 순서를 정의한다.
- 검증 입력:
  - 엔트리포인트, 러너, 오케스트레이터, 가드, 설정, 프로바이더, 유틸리티 모듈 코드 점검
  - 라우팅 및 오케스트레이션 관련 집중 테스트 실행 결과

## 1. 총평

이 리포트는 방향성은 유효하지만, 현재 브랜치 상태를 완전히 정확하게 반영한 문서는 아니다.

- 중복과 구조 문제의 상당수는 지금도 실제 문제다.
- 여러 항목의 문제 설명은 맞지만, 정확한 줄 수나 수정 경로는 일부 낡아 있다.
- 리포트가 언급한 파일 중 하나인 `core/model_utils.py`는 현재 트리에 존재하지 않으므로, 리포트의 범위를 현재 브랜치의 정확한 파일 목록으로 받아들이면 안 된다.
- 리포트에는 현재 중요한 라이브 회귀 하나가 빠져 있다. `tests/test_orchestrator_manifest.py`의 오케스트레이터 resume 상태 복원 테스트가 실패하고 있다.

현재 기준 판단:

- 확인됨: 중복 가드, wildcard import, import 시점 부작용, 프로젝트명 하드코딩, JSON 파싱 취약성, 루트 오염, 전역 mutable key 상태 문제는 대체로 맞다.
- 부분 확인: `AGENT_DISABLE_ENGINE_API_KEYS=1`, 하드코딩된 모델 권장 경로, 일부 위생 항목 수량은 현재 맥락을 같이 봐야 한다.
- 최신 아님 또는 불완전: 리포트는 현재 트리와 맞지 않는 파일/줄 수 전제를 포함하고 있고, `safe_id` 중복 범위도 실제보다 좁게 잡고 있다.

## 2. 현재 구조 요약

이 절은 리포트의 주장과 비교할 때 실제로 기준이 되는 현재 런타임 구조를 정리한 것이다.

### 2.1 진입점과 라우팅

1. `run_factory_cli.py:21-73`
   - CLI 진입점이다.
   - 프로젝트 환경변수를 설정하고 `AgentFactory`를 호출한다.
2. `agent_launcher.py:78-97`
   - `AgentFactory`가 `RequestRouter`, `ProjectPipeline`, `SkillOrchestrator`, 공용 런타임 서비스를 연결한다.
3. `agent_launcher.py:220-235`
   - `AgentFactory.run(...)`이 `RequestRouter.route(...)`를 호출한다.
4. `core/request_router.py:57-71`
   - `single`과 `project` 경로를 선택한다.

### 2.2 프로젝트 파이프라인과 오케스트레이션

1. `core/project_pipeline.py:136-184`
   - 프로젝트 단위 계획 산출물을 만든 뒤 `DynamicOrchestrator`를 실행한다.
2. `core/dynamic_orchestrator.py:334`
   - `run_project(...)`가 현재 오케스트레이터 진입점이다.
3. 검증 결과:
   - `python -m pytest tests/test_request_router.py tests/test_project_pipeline.py tests/test_dynamic_orchestrator_workspace_scope.py -q`
   - 2026-03-10 실행 결과: `8 passed, 1 warning`

### 2.3 단일 에이전트 SDK 경로

1. `core/agent_runner.py`
   - 단일 작업 실행을 담당하는 중심 SDK 런타임이다.
   - 현재도 라우팅, 프롬프트 조립, 정책 검사, SDK 실행, CLI fallback, trace 기록, 로컬 샌드박스 헬퍼가 한 파일에 섞여 있다.
2. `core/tool_runtime.py`
   - 스킬 함수를 레지스트리용 툴로 감싸는 역할을 한다.
3. `core/security_guard.py`
   - 공용 가드/샌드박스 헬퍼 모듈로 의도되어 있지만, 아직 단일 진실 공급원은 아니다.

### 2.4 CLI 프로바이더 경로

1. `core/providers/cli.py`
   - 프로바이더별 명령과 환경을 구성한다.
2. `core/providers/session_adapter.py`
   - CLI 세션 상태, continuity, guard 정책 파일을 준비한다.
3. `core/providers/registry.py`
   - CLI 프로바이더 목록과 엔진 API 키 비활성화 여부를 결정한다.

## 3. 검증 방법

리포트의 각 항목은 아래 세 가지 분류로 다시 판정했다.

- 확인됨:
  - 현재 코드에서 동일한 문제가 실제로 남아 있다.
- 부분 확인:
  - 문제의식은 맞지만, 현재 설계 의도나 실제 수정 방향은 리포트와 다르다.
- 최신 아님:
  - 파일, 줄 수, 구조 전제가 현재 트리와 정확히 일치하지 않는다.

## 4. 항목별 검증 결과

### 4.1 치명적 중복

| ID | 리포트 주장 | 상태 | 현재 근거 | 판단 |
| --- | --- | --- | --- | --- |
| 1-1 | `quick_guard`, `BANNED_IMPORT_TOPS`, `BANNED_CALLS` 중복 | 확인됨 | `core/agent_runner.py:110-145`, `core/security_guard.py:28-78` | 동일한 가드 로직이 두 파일에 그대로 남아 있다. `core/utils.py:35-38`이 이미 `security_guard` 버전을 re-export하고 있으므로 `agent_runner` 쪽 복사는 불필요하고 위험하다. |
| 1-2 | `run_isolated()` 중복, 그리고 `security_guard` 쪽이 더 약함 | 확인됨 | `core/agent_runner.py:161-309`, `core/security_guard.py:86-160` | `agent_runner`는 `socket.create_connection`까지 막지만, `security_guard`는 여전히 막지 못한다. 리포트가 맞다. |
| 1-3 | `build_child_env()` 중복 | 확인됨 | `core/agent_runner.py:150-156`, `core/security_guard.py:163-165` | 현재도 중복 상태다. |
| 1-4 | `safe_id()` 중복/삼중 정의 | 확인됨, 다만 범위 축소 | `core/utils.py:60`, `core/tool_runtime.py:7`, `core/config_paths.py:6`, 추가로 `core/memory.py:75`, `core/skill_registry.py:16` | 리포트의 문제의식은 맞지만, 실제 중복 범위는 3개보다 넓다. |

### 4.2 아키텍처 문제

| ID | 리포트 주장 | 상태 | 현재 근거 | 판단 |
| --- | --- | --- | --- | --- |
| 2-1 | `agent_runner.py`는 God object다 | 확인됨, 줄 수는 최신 아님 | `core/agent_runner.py`는 현재 891줄이며, 리포트의 994줄과 다르다 | 줄 수는 바뀌었지만 구조 문제는 그대로다. 이 파일은 여전히 라우팅 헬퍼, 가드 로직, CLI fallback, 모델 선택, 프롬프트 조립, 툴 정책 검사, trace, 실행 조정을 모두 가지고 있다. |
| 2-2 | `from core.utils import *`는 위험하다 | 확인됨 | `core/agent_runner.py:20` | 현재도 그대로 남아 있다. 심볼 출처를 숨기고 리팩터링 난도를 높인다. |
| 2-3 | `config_paths.py`가 import 시점에 디렉터리를 만든다 | 확인됨 | `core/config_paths.py:98` 주변 `os.makedirs(...)` 루프 | import 시점 파일시스템 변경이 지금도 존재한다. |

### 4.3 보안 및 정책 문제

| ID | 리포트 주장 | 상태 | 현재 근거 | 판단 |
| --- | --- | --- | --- | --- |
| 3-1 | `llm_engine.py`에 `minesweeper` 특권 경로가 하드코딩되어 있다 | 확인됨 | `core/llm_engine.py:87-89` | 프로젝트 격리 원칙을 지금도 깨고 있다. |
| 3-2 | `run_factory_cli.py`가 `AGENT_DISABLE_ENGINE_API_KEYS=1`를 강제한다 | 부분 확인 | `run_factory_cli.py:62`, `core/providers/registry.py:50-53`, `core/engine_auth.py:51-57` | 동작은 실제로 존재한다. 다만 이건 순수 보안 버그라기보다, CLI 진입 시 provider-native auth를 우선하고 SDK 엔진 키를 꺼버리는 런타임 정책 문제에 가깝다. 핵심은 운용성과 숨은 동작이다. |
| 3-3 | `security_guard.run_isolated()`가 `socket.create_connection` 차단을 놓친다 | 확인됨 | `core/security_guard.py:86-160` vs `core/agent_runner.py:203-207` | 더 약한 샌드박스 복사본이 아직 남아 있다. |

### 4.4 코드 품질 문제

| ID | 리포트 주장 | 상태 | 현재 근거 | 판단 |
| --- | --- | --- | --- | --- |
| 4-1 | `agent_runner.py`에 mojibake가 남아 있다 | 확인됨 | `core/agent_runner.py:159`, `core/agent_runner.py:795` | 깨진 주석과 출력 문자열이 아직 보인다. |
| 4-2 | deprecated stub 메서드가 남아 있다 | 확인됨 | `core/agent_runner.py:457-463` | 현재도 남아 있으며, 삭제하거나 명시적인 호환 계층으로 바꿔야 한다. |
| 4-3 | `generate_json()` 파싱이 취약하다 | 확인됨 | `core/llm_engine.py:184-201` | 어떤 fenced block이든 받아들이는 현재 로직은 여전히 취약하다. |
| 4-4 | 모델 버전이 하드코딩되어 있다 | 확인됨, 수정 경로는 최신 아님 | `core/llm_engine.py:66`, `core/agent_runner.py:101`, `core/agent_runner.py:723`, `core/agent_runner.py:851` | 하드코딩 자체는 실제 문제다. 다만 리포트가 제안한 목적지 모듈 `model_utils.py`는 현재 경로 기준으로 `core/` 아래에 존재하지 않으므로 수정 경로 제안은 일부 낡았다. |

### 4.5 프로젝트 위생 문제

| ID | 리포트 주장 | 상태 | 현재 근거 | 판단 |
| --- | --- | --- | --- | --- |
| 5-1 | 루트 debug/temp 산출물이 저장소를 오염시킨다 | 부분 확인 | `debug_log.txt`, `debug_log2.txt`, `final_test_log.txt`, `err.txt`, `out.txt`, `st_out.txt`, `status.txt`, `status_short.txt`, `nlm_answer.txt`, `agent_launcher.py.bak`가 루트에 남아 있다 | 위생 문제 자체는 맞다. 다만 리포트가 적은 파일 중 `pytest_output.txt`, `pytest_out.txt`는 현재는 없다. `.gitignore`도 일부 패턴은 이미 막고 있지만 현재 루트 오염을 전부 덮진 못한다. |
| 5-2 | 루트 테스트 파일은 `tests/` 아래로 옮겨야 한다 | 확인됨 | `test_complex_skill_build.py`, `test_fallback.py`, `test_hashline.py`, `test_hashline_v2.py`, `test_hound_librarian.py`, `test_llm.py`, `test_model_routing_v3.py`, `test_terminal.py`가 루트에 남아 있다 | 지금도 그대로다. |
| 5-3 | `run_factory_cli.py`에 `Logi-Mind`가 하드코딩되어 있다 | 확인됨 | `run_factory_cli.py:21`, `run_factory_cli.py:66` | 공유 엔트리포인트에 제품명 문맥이 섞여 있어 격리 원칙을 해친다. |

### 4.6 런타임 위험

| ID | 리포트 주장 | 상태 | 현재 근거 | 판단 |
| --- | --- | --- | --- | --- |
| 6-1 | `_gemini_keys` 전역 mutable 상태는 thread-safe하지 않다 | 확인됨 | `core/llm_engine.py:21-46` | 전역 리스트와 커서 상태가 여전히 프로세스 전역으로 유지된다. |
| 6-2 | rule-first 라우팅 대신 AI 분류 호출을 사용하고 있다 | 확인됨 | `core/agent_runner.py:703-734` | 특수 케이스가 아닌 일반 경로에서 여전히 LLM을 호출해 `simple`/`complex`를 분류하고 있어 비용과 실패면이 늘어난다. |

## 5. 리포트 신뢰도 관련 메모

이 리포트는 유용하지만, 그대로 실행 계획으로 쓰기 전에 아래 한계를 같이 봐야 한다.

### 5.1 파일 목록 불일치

리포트는 `code_review_report.md:14`에서 `core/model_utils.py`를 검토 대상이라고 적고 있지만, 현재 워크트리에는 그 파일이 없다. 현재 `ModelRouter`는 여전히 `core/agent_runner.py:62-105` 안에 있다.

영향:

- 이 리포트는 현재 브랜치에 딱 맞는 패치 계획이라기보다 방향성 리뷰로 봐야 한다.
- 실제 수정 계획은 현재 트리 기준으로 다시 세워야 한다.

### 5.2 `safe_id` 중복 범위는 리포트보다 더 넓다

리포트는 3개 복사본을 지적하지만, 현재 트리에는 최소 5개의 관련 ID 정규화 헬퍼가 있다.

- `core/config_paths.py:6`
- `core/utils.py:60`
- `core/tool_runtime.py:7`
- `core/memory.py:75`
- `core/skill_registry.py:16`

영향:

- 진짜 해결책은 `tool_runtime.py`만 치우는 게 아니라, 정규화 계약을 한곳으로 모으는 것이다.

### 5.3 리포트가 놓친 현재 회귀

집중 테스트 결과:

- `python -m pytest tests/test_orchestrator_manifest.py::test_dynamic_orchestrator_writes_manifest_and_restores_interruptions -q`
- 2026-03-10 실행 결과: `1 failed`
- 실패 원인: `tests/test_orchestrator_manifest.py:125`에서 `KeyError: 'interrupted_subtasks'`

영향:

- 현재 런타임 정확성 작업은 리포트 목록만 따라가면 부족하다.
- resume/manifest 복원 문제를 활성 수정 큐에 포함해야 한다.

## 6. 실제로 먼저 고쳐야 할 것

정답은 “리포트의 제안을 전부 바로 적용한다”가 아니다.

올바른 대응은 아래 순서다.

1. 정확성과 격리에 직접 영향을 주는 버그를 먼저 고친다.
2. 중복 가드와 런타임 헬퍼를 다음으로 정리한다.
3. 그 다음 구조와 위생 문제를 다룬다.
4. 마지막으로 깊은 런타임 최적화 항목을 처리한다.

## 7. 권장 수정 계획

### Phase 1: 정확성과 안전성

목표: 런타임 동작을 바꾸거나 격리를 약화시키는 버그를 먼저 제거한다.

#### Step 1. `core/security_guard.py` 중심으로 샌드박스 헬퍼 통합

1. `agent_runner.py` 안에 남아 있는 더 강한 구현을 `core/security_guard.py`로 옮긴다.
2. 아래 항목의 단일 진실 공급원을 `core/security_guard.py`로 만든다.
   - `BANNED_IMPORT_TOPS`
   - `BANNED_CALLS`
   - `quick_guard`
   - `build_child_env`
   - `run_isolated`
3. `core/agent_runner.py`는 로컬 복사본을 지우고 이 헬퍼들을 명시적으로 import하도록 바꾼다.
4. `socket.create_connection` 차단이 공유 구현에서 보장되는지 회귀 테스트를 추가한다.

#### Step 2. 프로젝트 전용 `minesweeper` 로직 제거

1. `core/llm_engine.py`의 `_flash_auto_upgrade_enabled()` 하드코딩을 제거한다.
2. 제어 소스를 명시적으로 하나로 정한다.
   - 예: 환경변수 `AGENT_FLASH_AUTO_UPGRADE`
   - 또는 프로젝트 설정 `settings.yaml`
3. 명시적으로 켜지지 않으면 기본값은 비활성으로 둔다.
4. 활성/비활성 두 경로에 대한 테스트를 추가한다.

#### Step 3. `generate_json()` 파싱 강화

1. `core/llm_engine.py:190-195`의 split 기반 로직을 교체한다.
2. 파싱 순서는 아래처럼 잡는다.
   - 직접 JSON
   - fenced `json` block만 허용
   - 마지막 fallback으로 제한된 범위의 객체 추출 regex
3. JSON이 아닌 fenced block은 묵살하지 말고 실패로 처리한다.
4. 아래 케이스를 테스트에 넣는다.
   - plain JSON
   - fenced JSON
   - braces가 들어 있는 fenced Python
   - 설명문 + JSON 객체 혼합 응답

#### Step 4. 오케스트레이터 resume 회귀 수정

1. `DynamicOrchestrator.run_project(...)`와 manifest 복원 흐름을 점검한다.
2. 중단된 할당을 복원할 때 `interrupted_subtasks`가 반드시 채워지게 만든다.
3. `tests/test_orchestrator_manifest.py::test_dynamic_orchestrator_writes_manifest_and_restores_interruptions`를 다시 돌린다.

### Phase 2: 동작 위험이 낮은 구조 정리

목표: 제품 동작을 크게 바꾸지 않으면서 중복을 줄이고 변경 안전성을 높인다.

#### Step 5. wildcard import 제거

1. `core/agent_runner.py`의 `from core.utils import *`를 제거한다.
2. 실제 사용하는 심볼만 명시적으로 import한다.
3. `security_guard` 헬퍼는 `core.utils`를 우회하지 말고 `core.security_guard`에서 직접 가져오게 한다.

#### Step 6. ID 헬퍼 정규화

1. 정규화 기준 함수를 한 위치로 정한다. 가장 자연스러운 후보는 `core.utils.safe_id`다.
2. 현재 변형들이 정말 동일 계약을 써야 하는지 결정한다.
3. 아래 중복을 정리한다.
   - `core/tool_runtime.py`
   - `core/memory.py`
   - `core/skill_registry.py`
   - `config_paths.py`는 순환 import를 피하는 방식으로 맞춘다.
4. 모든 호출 지점이 같은 정규화 결과를 내는지 테스트로 고정한다.

#### Step 7. `agent_runner.py` 책임 축소

1. `ModelRouter`를 `core/agent_runner.py` 밖으로 분리한다.
2. 더 이상 쓰지 않는 deprecated tool wrapper stub은 제거한다.
3. `AgentRunner`는 실행 조정에 집중하고, 헬퍼 정의는 외부 모듈로 뺀다.

### Phase 3: 부트스트랩과 설정 정리

목표: 시작 시 동작을 명시적으로 만들고 추론 가능성을 높인다.

#### Step 8. `config_paths.py`의 import 시점 디렉터리 생성 제거

1. `os.makedirs(...)` 루프를 `ensure_factory_directories()` 같은 명시적 부트스트랩 함수로 옮긴다.
2. 실제로 파일시스템 생성이 필요한 시작 경로에서만 호출한다.
3. 순수 설정 import는 가능하면 side-effect free 상태로 유지한다.
4. “import만 한 경우”와 “bootstrap 호출한 경우”를 분리해서 테스트한다.

#### Step 9. `AGENT_DISABLE_ENGINE_API_KEYS` 정책 재검토

1. 의도한 정책을 먼저 결정한다.
   - CLI 진입은 항상 provider-native auth를 우선한다.
   - 또는 CLI 프로바이더가 명시된 경우에만 엔진 키를 비활성화한다.
2. 현재 동작이 의도된 것이라면 CLI 문서와 런타임 문서에 명시한다.
3. 의도된 것이 아니라면 `run_factory_cli.py:62`의 기본 설정을 제거하고 `core/providers/registry.py`가 동적으로 판단하게 한다.
4. SDK 우선 기동과 CLI 우선 기동 두 경우를 테스트로 나눈다.

### Phase 4: 저장소 위생과 문맥 격리

목표: 저장소 잡음을 줄이고 낡은 브랜딩/문맥 문자열을 제거한다.

#### Step 10. 루트 오염 파일 정리 또는 격리

1. 루트 파일 중 실제 fixture인지, 버려도 되는 로그인지 구분한다.
2. 버려도 되는 로그는 임시 또는 runtime 폴더 아래로 옮긴다.
3. 현재 실제 산출물 패턴에 맞게 `.gitignore`를 확장한다.

#### Step 11. 루트 테스트 파일을 `tests/` 아래로 이동

1. 독립 `test_*.py` 파일을 `tests/` 아래로 옮긴다.
2. repo-root 기준 import가 있다면 같이 수정한다.
3. 테스트 디스커버리가 일관되게 동작하도록 맞춘다.

#### Step 12. 공용 CLI에서 `Logi-Mind` 브랜딩 제거

1. `run_factory_cli.py`의 하드코딩 브랜딩 문자열을 교체한다.
2. 공유 엔트리포인트에는 제품 전용 명칭이 섞이지 않게 한다.

### Phase 5: 런타임 강화와 효율 개선

목표: 동시성 안전성을 높이고 불필요한 모델 호출을 줄인다.

#### Step 13. `_gemini_keys` 전역 상태 제거

1. lock을 가진 작은 key manager 추상화를 도입한다.
2. key rotation 상태는 인스턴스 단위 또는 명시적 동기화 대상이 되게 만든다.
3. 런타임이 여러 스레드에서 key rotation을 건드릴 수 있다면 동시성 테스트도 추가한다.

#### Step 14. AI-first 복잡도 분류를 rule-first 라우팅으로 전환

1. 아래 신호를 쓰는 결정적 1차 휴리스틱을 먼저 넣는다.
   - 작업 길이
   - 명시적 키워드
   - 역할 유형
   - UI/UX, 아키텍처, 다중 파일, 리팩터링 신호
2. 애매한 작업에서만 LLM 분류기를 호출한다.
3. simple, complex, ambiguous 예제를 각각 테스트에 추가한다.

## 8. 권장 실행 순서

팀이 가장 짧은 경로로 코드베이스 상태를 개선하려면 아래 순서가 맞다.

1. `security_guard` 통합과 `socket.create_connection` 차단 보장
2. `generate_json()` 강화
3. 오케스트레이터 resume 회귀 수정
4. `minesweeper` 하드코드 제거
5. wildcard import 제거
6. `safe_id` 정규화
7. `agent_runner.py` 책임 분리
8. `config_paths.py` import side effect 제거
9. `AGENT_DISABLE_ENGINE_API_KEYS` 정책 명확화
10. 루트 위생 및 브랜딩 정리
11. `_gemini_keys` 동기화
12. rule-first 라우팅 전환

## 9. 결론

이 리포트는 보관할 가치가 있지만, 그대로 패치 체크리스트로 쓰면 안 된다.

올바른 해석은 아래와 같다.

- 문제 목록은 유지한다.
- 각 항목은 현재 브랜치 기준으로 다시 기준선을 맞춘다.
- 실제로 확인된 런타임/격리 버그부터 먼저 고친다.
- 그 다음 리포트를 리팩터링 로드맵으로 사용한다.

이 접근이 낡은 전제를 그대로 따르지 않으면서도, 리뷰의 유효한 부분을 최대한 살리는 방법이다.
