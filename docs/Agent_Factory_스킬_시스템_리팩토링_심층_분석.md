# Agent Factory 스킬 시스템 리팩토링 심층 분석 및 Claude Code Skills 2.0 비교 리포트

## 1. 전면 리팩토링된 Agent Factory 스킬 시스템 아키텍처 (현재 상태)

전체 코드를 심층 분석한 결과, Agent Factory의 스킬 시스템은 상당히 고도화된 구조를 갖추고 있으며 다음과 같은 핵심 컴포넌트로 분리, 설계되었습니다.

*   **`core/skill_metadata.py` (구조적 선언)**
    *   기존 YAML 기반에서 Python Native한 `@skill_metadata` 데코레이터 패턴으로 진화했습니다.
    *   `SkillCategory`, `SkillType`(action, knowledge, tool) 등 명확한 열거형과 `when_to_use`, `when_to_use_keywords`, `dependencies`, `incompatible_with` 등의 라우팅 제어 필드를 도입하여 결합도를 낮췄습니다.
*   **`core/skill_registry.py` (중앙 집중 관리)**
    *   Thread-safe 싱글톤 패턴으로 구현되어 동시 다발적인 에이전트(혹은 서버) 요청에서도 안전하게 메타데이터를 서빙합니다.
    *   기존 레거시 `registry.yaml` 형식을 자동으로 감지하고 새로운 `SkillMetadata`로 변환(Adapter)하는 호환성 계층을 제공합니다.
*   **`core/skill_loader.py` & `core/skill_cache.py` (12-Cap 동적 라우팅)**
    *   가장 큰 변화 중 하나입니다. LLM의 컨텍스트 윈도우 한계를 극복하기 위해 `SkillRelevance` 클래스가 사용자의 입력(Task Input)을 분석해 상위 12개(`MAX_SKILLS_IN_CONTEXT`)의 스킬만 프롬프트에 동적 삽입합니다.
    *   키워드 일치(60%)와 카테고리 일치(40%)를 기반으로 스코어링하며, 이 연산을 가속하기 위해 `SkillRelevanceCache`를 통해 해시된 입력값들을 LRU 캐싱 처리합니다.
    *   충돌 해결 방지 로직(`incompatible_with` 검사)이 로더에 내장되어 동일한 작업에서 상충하는 스킬이 동시에 주입되는 것을 막습니다.
*   **`core/skill_creator.py` (자기 진화와 스킬 포징)**
    *   LLM을 활용해 `SKILL.md`(Knowledge)와 `skill.py`(Action)를 자동 생성하는 파이프라인.
    *   초기화(`init_skill_dir`), 생성(`generate_skill_content`), 검증(`validate_skill`) 뿐만 아니라, 에러 로그와 피드백을 주입받아 자동으로 코드를 수정/진화시키는 `evolve_skill` 함수를 완비했습니다.
*   **`core/skill_procurer.py` & `core/external_skill_sources.py` (생태계 확보)**
    *   원격 웨어하우스(Git Repository) 기반 스킬 풀링, 동기화(`sync_warehouse`)를 제공합니다. 스킬이 로컬에 없을 경우 원격지를 탐색하고, 그래도 없으면 `forge_new_skill`을 호출해 실시간으로 스킬을 "포징(제작)"합니다.

---

## 2. Claude Code Skills 2.0 과 Agent Factory의 명확한 차이점

Claude Code가 최근 공개한 차세대 Skill 시스템과 Agent Factory를 비교하면 아키텍처 철학은 매우 유사하나 구현 수준과 플랫폼 접근 방식에서 다음과 같은 차이가 발생합니다.

| 분류 | Claude Code Skills 2.0 | Agent Factory (현재) | 차이점 상세 및 강약점 |
| :--- | :--- | :--- | :--- |
| **의도 매칭 검색** | 의미론적 임베딩 거리(Semantic Search) 기반 고차원 매칭 | 키워드 카운팅(60%) + 카테고리 기반 규칙 매칭(40%) | **AF의 약점**. AF 로더 파일 내 주석(`시맨틱 점수 미구현`) 그대로, 문맥적 의미 파악보다는 규칙/키워드에 의존함 |
| **진화 피드백 루프** | 런타임 중 에러 발생 시, 시스템이 스스로 Tool을 반복 디버깅하고 배포 | `evolve_skill()`을 통해 코드 개선 기능은 있으나, 완전 자율성 루프는 덜 매끄러움 | Claude는 에러 즉시 백그라운드에서 스킬 픽스를 수행하나, AF는 현재 의식적 호출이나 제한된 루프 내에서 수행 |
| **A/B 평가(Eval)** | LangSmith 수준의 테스트셋 평가를 플랫폼 단위로 스킬별 자동 지원 가능 | LangSmith_eval.py 등이 추가되었으나, 스킬 단일 객체 단위 통합 테스트 파이프라인의 내재화 부족 | Claude는 배포 전 무결점 입증 절차가 강력하나 AF의 평가는 아직 외장 모듈 형태 |
| **표준 상호 운용성** | 표준 MCP(Model Context Protocol) 기반 프로토콜 활용 유력 | 고유 방식 (SKILL.md, metadata.yaml의 개별 규약) | 외부 생태계(Open source MCP 서버 등) 연동성 측면에서 Claude Code가 유리 |
| **에이전트 이식성** | Claude CLI 환경에 종속 | 여러 LLM (Gemini, Opus) 자유 스위칭, 커스텀 컨텍스트 구축 | **AF의 강점**. 엔진 종속성 없이 LangGraph 파이프라인 전체를 하이브리드로 구성 가능 |

---

## 3. Agent Factory의 보완해야 할 점 (Weak Points)

1.  **시맨틱(Semantic) 매칭의 부재**: 현재 로더(`skill_loader.py`)는 단순 문자열 포함 여부를 해시 기반으로 체크합니다. "내 파일들이 이상해"라고 질문할 때 "디버깅", "버그"라는 키워드가 없어도 문맥적으로 디버깅 스킬을 띄워줄 임베딩(Vector) 기반 매칭이 시급합니다.
2.  **MCP(Model Context Protocol) 호환성 부재**: 세상에 존재하는 수백개의 오픈소스 MCP 서버들을 그대로 AF 스킬로 빨아들일(Import) 수 있는 브릿지가 래퍼가 보이지 않습니다.
3.  **격리성 (Stateful/Sandbox) 한계**: `meta.yaml`상에는 `stateful: true`나 격리 실행 등의 선언이 있지만, 실제 `apply(ctx)` 실행 시 도커 컨테이너나 V8 런타임처럼 완벽한 메모리 오염 방지(샌드박스)가 이루어지는 인프라 코드가 약합니다.
4.  **연쇄 충돌(Dependency Hell) 추적**: `dependencies`와 `incompatible_with`를 정의해두었으나, 스킬 개수가 100개가 넘어갈 경우 의존성 트리를 풀어내는 강력한 위상 정렬(Topological Sort) 패키지 매니저 수준의 로직이 아직 완성되지 않았습니다.

---

## 4. 시너지 (Synergy & Opportunities)

*   **Agent Factory의 범용성 + Claude Code의 Skill 사상 체계**:
    *   Claude는 SKILL.md 작성, Progressive Disclosure 등 아주 좋은 "문서/규약 기반 지능 확장" 모델을 제시했습니다.
    *   이를 Agent Factory의 `Knowledge Skill(SKILL.md)` 및 데코레이터 시스템(AF가 이미 개발해둔)과 결합 시, 구글 문서, 지라 티켓 등 사내 시스템의 문서를 즉시 SKILL.md 화 하여 에이전트에 무한 주입할 수 있는 B2B 엔터프라이즈의 무기(Weapon)가 됩니다.
*   **하이브리드 엔진 포징(Forging)**:
    *   Agent Factory는 특정 엔진에 종속되지 않습니다. Gemini-Pro를 이용해 엄청난 양의 로그를 리서치하여 문서를 뽑아내고, 코딩은 Claude Sonnet을 사용해 액션 스킬(Python)을 `forge_new_skill()`로 생성하는 압도적 시너지가 가능합니다.

---

## 5. 단계별 고도화 로드맵 (명시적/상세화)

AF를 진정한 "Claude Code Skills 2.0" 이상으로 도약시키기 위한 명시적 단계별 수행 지침입니다.

### [Phase 1] Semantic 기반 스킬 로더 고도화 (임베딩 도입)
1. **임베딩 모델 도입**: `core/skill_loader.py`에 로컬/경량 임베딩 모델(예: `sentence-transformers` 또는 OpenAI/Google Embedding API) 연동.
2. **벡터 스토어 연결**: 스킬 로드 시 `skill.description`, `when_to_use`를 벡터 연산하여 사용자 Task Input과의 Cosine Similarity를 구하도록 개선.
3. **가중치 변경**: 키워드 캐시 매칭을 30%, Semantic 매칭 40%, 카테고리 매칭 30%로 스코어 분배율 업데이트.
4. **결과**: "에러 로그를 저장소에 기록해"라는 요청 시, File I/O 관련 스킬을 키워드 없이도 최상위에 랭크시킴.

### [Phase 2] 자율 런타임 진화 (Autonomous Evolve Loop)
1. **FSA 루프 연동 강화**: `core/fsa_loop.py`에서 예외(Exception) 발생을 캐치하는 핸들러 확장.
2. **Auto-fallback Pipeline**: 스킬 실행 실패 시, 즉각적으로 에러 로그 기반 프롬프트를 조립하여 백그라운드에서 `core/skill_creator.py`의 `evolve_skill(error_log=...)` 함수를 호출.
3. **Self-Test & Deploy**: 진화된 코드가 `.bak`으로 롤백되지 않게 임시 샌드박스 디렉토리에서 자동 단위 테스트를 돌리고 통과하면 주 스킬 레지스트리로 바로 핫-리로딩(Hot-Reloading) 함.

### [Phase 3] 오픈 생태계 통합 (MCP Bridge)
1. **MCP 어댑터 개발**: `core/mcp_adapter.py` 모듈 신설.
2. **명세 변환기**: 표준 MCP 서버의 tool/resource 스키마 JSON을 스캔 시 자동으로 AF의 `@skill_metadata` 객체 메모리로 변환하여 로더에 투입하게 작성.
3. **결과**: `agent-factory --add-mcp [URL]` 등 CLI 커맨드 지원. 수백개의 오픈 프레임워크 스킬을 재개발 없이 Agent Factory 내 사용 가능.

### [Phase 4] Skill Evaluation 파이프라인 정립
1. **Eval Agent 자동화**: 생성된 `evaluator.py`를 활용, 하루에 한 번 (혹은 커밋마다) 모든 설치된 스킬을 강제 테스트(Unit test & LLM Quality Check).
2. **A/B Performance Tracking**: 같은 목표를 가진 스킬(예: `LangChain_search`, `Tavily_search`) 두 개의 스코어를 메트릭에 저장하여 성능 차이가 나면 성과가 낮은 스킬의 메타데이터 가중치를 깎아버리는 로직 구현.
