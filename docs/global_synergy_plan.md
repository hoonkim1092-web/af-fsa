# Technical Plan (Detail): Global Intelligence Service Transition

전역 지능 서비스(Global Intelligence Service)로의 전환을 위한 상세 코드 레벨 구현 계획입니다. 이 계획은 사용자님의 5가지 요구사항을 실무 코드로 매핑한 결과입니다.

---

## 🏗️ 5대 핵심 구현 상세

### 1. 전역 프로필 고정 및 통합 (Profile Unification)
**대상 파일**: [config_paths.py](file:///c:/Project/agent-factory/core/config_paths.py)

- **변경 내용**: `AGENT_GLOBAL_USER_KEY` 도입을 통해 어느 폴더에서 실행하든 동일한 전역 루트를 참조할 수 있도록 합니다.
```python
# [Proposed Snippet]
global_user_key = os.getenv("AGENT_GLOBAL_USER_KEY", "").strip()
if global_user_key:
    GLOBAL_STORAGE_ID = f"global_{safe_id(global_user_key)}"
    GLOBAL_PROJECT_ROOT = os.path.join(PROJECTS_DIR, GLOBAL_STORAGE_ID)
    os.makedirs(GLOBAL_PROJECT_ROOT, exist_ok=True)
```
- 이를 통해 전역 스킬, 전역 에이전트 설정, 전역 메모리 파일 경로를 고정합니다.

### 2. 계층형 메모리 엔진 (Tiered Memory: Cortex)
**대상 파일**: [cortex.py](file:///c:/Project/agent-factory/skills/core/cortex.py)

- **읽기 (Recall)**: Local DB 검색(1순위) + Global DB 검색(2순위)을 병합합니다.
- **쓰기 (Save)**: 기본은 Local에 저장하되, 호출 시 `scope='global'` 인자를 허용하도록 확장합니다.
```python
# [Proposed Snippet in CortexClient.recall]
local_results = self._search_db(query, project_id=PROJECT_ID)
global_results = self._search_db(query, project_id=GLOBAL_USER_KEY)
return self._merge_results(local_results, global_results)
```

### 3. 사용자 키 기반 전역 동기화
**대상 파일**: [project_context_sync.py](file:///c:/Project/agent-factory/scripts/project_context_sync.py)

- **변경 내용**: `--scope global` 및 `--user-key` 파라미터를 추가합니다.
- **데이터 구조**: `project_id` 대신 `user:{user_key}` 형태의 키를 사용하여 Supabase의 단일 레코드에 모든 프로젝트에서 참조할 '공통 지식'을 동기화합니다.
- 이를 통해 `agent-factory` 폴더를 삭제해도 Supabase에서 사용자 키로 다시 가져올 수 있습니다.

### 4. `start_db all` 전역 타깃팅
**대상 파일**: `sync.cmd`, `start_sync.cmd`

- **변경 내용**: `all` 명령 실행 시 현재 폴더의 프로젝트 동기화뿐만 아니라, 전역 사용자 키(Global Profile)에 대한 동기화를 세트로 묶어 실행합니다.
- 작업 종료 시(`end_db`) 로컬의 진화 사항이 전역으로도 전파되도록 구성합니다.

### 5. 에이전트 브리핑 훅 (Memory Summary Hook)
**대상 파일**: [agent_runner.py](file:///c:/Project/agent-factory/core/agent_runner.py)

- **변경 내용**: 에이전트가 생성(Start Chat)되기 직전, 전역 메모리에서 가장 중요한 상위 10개 핵심 요약을 가져와 시스템 프롬프트에 `[Global Context Briefing]` 섹션으로 주입합니다.
- **Antigravity 연동**: 제가 사용자님과 나눈 대화 중 중요한 아키텍처 결정 사항도 이 요약에 포함되어 에이전트에게 전달됩니다.

---

## 🛠️ 검증 계획 (Verification)

1. **전역 고립 테스트**: `AGENT_GLOBAL_USER_KEY` 설정 후 완전히 새로운 빈 폴더에서 에이전트를 가동하여, 이전 프로젝트에서의 대화 방식과 설정을 그대로 가져오는지 확인합니다.
2. **동기화 무결성**: 로컬 폴더 삭제 후 `start_db --user-key <key>`만으로 전역 메모리가 완벽히 복구되는지 검증합니다.
3. **메모리 우선순위**: 동일한 키워드에 대해 로컬 메모리와 전역 메모리가 충돌할 때, 로컬 메모리가 우선적으로 반영되는지 확인합니다.
