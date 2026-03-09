# 지뢰찾기 프로젝트 (Gemini 3 Flash) 3차 실패 분석 및 최종 대안

## 1. 에러 현상 및 원인 분석 (Root Cause Analysis)

프로젝트 포지가 다시 중단된 원인은 크게 세 가지의 기술적 충돌문제가 겹쳤기 때문입니다.

### ① 파이썬 변수 스코프 에러 (UnboundLocalError)

- **현상**: `UnboundLocalError: local variable 'workspace' referenced before assignment` 발생.
- **원인**: `dynamic_orchestrator.py` 의 `_lilith_decide_next` 함수 내에서 인자로 받은 `workspace` 변수를 함수 내부에서 다시 `workspace = workspace or os.getcwd()` 와 같이 재할당하고 있습니다. 파이썬에서는 함수 내에서 변수에 값을 할당하면 해당 변수를 함수 전체의 로컬 변수로 간주하는데, 이 과정에서 인자(Argument)와의 이름 충돌이나 할당 전 참조 문제가 발생하여 오케스트레이터 루프가 시작되기도 전에 충돌하며 종료되었습니다.

### ② 모델 자동 업그레이드 로직의 간섭

- **현상**: 로그에 `[Auto-Upgrade] 'models/gemini-3-flash-preview' -> 'models/gemini-2.0-flash'` 출력.
- **원인**: `llm_engine.py`에 구현된 "Flash 계열 모델 자동 최신화" 로직이 작동하여, 사용자가 지정한 Gemini 3.0 Flash를 더 안정적인 버전(?)이라고 판단되는 2.0으로 강제로 바꾸고 있습니다. 이로 인해 사용자의 의도와 다른 모델이 호출되었습니다.

### ③ 쉘(Shell) 명령어 이스케이프 문제

- **현상**: `Gemini : 'Gemini' 용어가 cmdlet... 으로 인식되지 않습니다.`
- **원인**: 프로젝트 명에 포함된 괄호`()`가 PowerShell에서 특수 문자로 해석되어 명령어가 끊겼습니다. (배치 파일로 해결 시도했으나 내부 환경 변수 처리에서 다시 문제가 발생)

---

## 2. 대안 및 해결 방안 (Alternative Solutions)

사용자께서 코드 수정을 금지하셨으므로, **코드 수정 없이 실행할 수 있는 방법**과 **사용자께서 직접 수정해주셔야 할 부분**을 제안합니다.

### 대안 A: 변수 명칭 분리 (코드 수정 제안)

- `_lilith_decide_next`와 `_orchestration_loop` 함수 내에서 인자명(`workspace`)하고 내부 사용 변수명(예: `target_workspace`)을 명확히 분리하여 스코프 에러를 해결해야 합니다.

### 대안 B: 엔진 자동 업그레이드 비활성화 (환경 변수 우회 시도)

- `LLMEngine`의 자동 업그레이드 로직은 현재 모델명에 `gemini`와 `flash`가 모두 들어있을 때 작동합니다. 이를 우회하거나 해당 로직을 일시적으로 주석 처리해야 합니다.

---

## 3. 복구 및 실행 상세 단계 (Detailed Steps)

### 1단계: 코드 안정화 (사용자 직접 수정 또는 승인 필요)

- `dynamic_orchestrator.py`의 `workspace = workspace or ...` 라인을 `_workspace = workspace or ...` 로 변경하여 스코프 충돌을 제거합니다.

### 2단계: 모델 고정 초기화

- `AGENT_FORCE_MODEL` 환경 변수뿐만 아니라, `llm_engine.py`의 자동 업그레이드 기능을 잠시 끄는 조치가 필요합니다.

### 3단계: 특수 문자 제외 실행

- 프로젝트 설명에서 `()` 와 같은 특수 문자를 완전히 제거하고 영어/숫자 위주의 단순한 이름으로 실행합니다.

---

> [!IMPORTANT]
> **사용자 결정 필요**: 코드 수정을 제가 직접 수행해서 버그를 잡고 Gemini 3 Flash를 강제 고정해도 될까요? 아니면 현재의 엔진 구조(2.0 Flash 강제 업그레이드)를 유지하시겠습니까?
