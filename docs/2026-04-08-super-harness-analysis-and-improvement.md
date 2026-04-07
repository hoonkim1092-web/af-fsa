# Agent Factory 하네스 시스템 통합 심층 분석 및 개선 계획 (Super Harness Analysis & Improvement Plan)

작성일: 2026-04-08

## 1. 현재 구조: 하네스 시스템(Super Harness)으로의 접합 상황

Agent Factory는 단순한 "스크립트 실행기"나 "단일 에이전트 루프"를 넘어, 지속적인 운영과 품질 보장을 위한 **3계층의 슈퍼 하네스 구조(Control, Quality, Memory Plane)**로 접합 및 고도화되고 있습니다.

*   **Control Plane (운영 계층 - `core/project_pipeline.py`, `core/fsa_loop.py`, `core/ise_loop.py`)**
    *   **접합 상태**: 에이전트의 런타임 실행, 재시도, 스케줄링을 담당합니다. 여러 루틴(FSA, ISE 등)이 병렬 및 순차적으로 작동하면서 전체 워크플로우를 중단 없이(Interrupt-resilient) 수행하도록 제어하고 있습니다.
*   **Quality Plane (품질 계층 - `core/skill_eval_harness.py`, `core/parallel_critique.py`, `core/evaluator.py`)**
    *   **접합 상태**: 생성된 결과물이나 스킬의 무결성을 검증합니다. 특히 `SkillEvalHarness`는 스킬에 대해 **Contract, Hidden, Shadow 3단계 평가**를 수행하며, 신규 생성된 스킬의 등급(Draft -> Canary -> Candidate)을 승격시키는 게이트키퍼 역할을 강력하게 수행합니다.
*   **Memory Plane (기억 계층 - `core/memory.py`, `core/ast_memory_hub.py`, `core/skill_cache.py`)**
    *   **접합 상태**: 워크스페이스 상태 복원, 세션 간 연속성 유지(Continuity Rehydration), 스킬 사용 패턴 및 오류 이력(Feedback log)을 저장하여 다음 실행 시 컨텍스트에 주입되도록 시스템의 지능적 기반을 다지고 있습니다. 

#### [한계점 / 진단]
이 구조는 훌륭한 청사진을 가지고 있으나, 소스 코드 내 세부 구현에서는 아직 **완전 자동화나 매끄러운 유기성**이 약간 떨어집니다. 예를 들어, 스킬 매칭 방식의 한계(시맨틱 부재), 에러 발생 시 즉각적인 샌드박스 진화(Evolve) 루프의 연결성 부족 등이 발견됩니다.

---

## 2. 고도화를 위한 4단계 개선 로드맵 (Proposed Changes)

아키텍처 레이어별 주요 개선 기능 및 목표를 정의하며, Agent Factory를 궁극적으로 Claude Code Skills 이상의 지능형 공장으로 만듭니다.

### Phase 1: Semantic 기반 스킬 로드 도입 (Quality & Memory Plane)
현재 키워드 및 카테고리에 의존하는 하드코딩된 로딩 방식의 한계를 극복합니다. "버그 수정해줘"라는 입력 컨텍스트만으로도 관련 디버깅 스킬을 임베딩 유사도로 정확히 끌어올려야 합니다.
*   **작업 사항 (`core/skill_loader.py`)**
    *   로컬/경량 임베딩 모델(예: `sentence-transformers`) 또는 외부 Embedding API 연동.
    *   Task Input과 `skill.description` 간의 의미론적 거리(Cosine Similarity)를 구해 키워드(30%), 카테고리(30%), 시맨틱(40%) 비중으로 종합 스코어링.
*   **작업 사항 (`core/skill_metadata.py`)**
    *   스킬 메타데이터 캐싱 로직에 초기 생성 시점의 "Embedding Vector"를 저장하여 연산 속도를 보장.

### Phase 2: 자율 런타임 진화 완전 자동화 (Control Plane)
현행 `evolve_skill()`을 FSA 루프 내부 깊숙이 밀착 접합시켜, 사람의 명시적 개입 없이 오류 조치->코드 픽스->배포 루프가 스스로 돌게 만듭니다.
*   **작업 사항 (`core/fsa_loop.py` & `core/ise_loop.py`)**
    *   스킬 런타임 Exception 캐치 시 백그라운드 태스크로 `evolve_skill(error_log=...)` 자동 호출.
*   **작업 사항 (`core/skill_sandbox_tester.py` 신설)**
    *   롤백 및 기존 프로세스 오염을 막기 위한 도커/서브프로세스 기반의 독립 모의 실행 환경(Sandbox) 객체화. 통과된 코드만 핫 리로딩(Hot-reloading) 수행.

### Phase 3: 메모리-품질 계층 간 Knowledge Graph 통합 (Memory Plane)
실패했던 에피소드와 성공 패턴을 단순히 파일에 쌓는 것이 아니라 재활용 가능한 연결 지식으로 만듭니다.
*   **작업 사항 (`core/memory_system/knowledge_graph.py` 신설)**
    *   에러 원인, 문제, 해결 패턴 노드를 구축하고 Vector DB(Chroma 등) 연계를 통해 타 프로젝트 간(Cross-Project) 경험 재사용 및 호출.
*   **작업 사항 (`core/evaluator.py` & `core/skill_eval_harness.py`)**
    *   QA 및 Shadow Eval 결과를 단순히 단위 리포트로 유지하지 않고 `Knowledge Graph`의 벤치마크 노드로 영구 갱신.

### Phase 4: 오픈 생태계 확장 브릿지(MCP) 연동 (Control & Registry)
Claude 등 외부 표준 생태계와의 접합력을 높여야 합니다. 
*   **작업 사항 (`core/mcp_adapter.py` 신설)**
    *   외부 Model Context Protocol(MCP) 서버의 JSON/REST 명세서를 실시간 파싱하여 Agent Factory의 네이티브 `@skill_metadata` 객체로 즉시 맵핑 및 캐싱. 

---

## 3. 검증 계획 (Verification Plan)

### Automated Tests
1. **시맨틱 로딩 검증 (`test_skill_loader_semantic.py`)**: 의도적으로 우회적인 표현("뭔가 꼬였어")을 입력하여도 복구/디버그 관련 스킬이 우선 로드되는지 확인.
2. **샌드박스 진화 검증 (`test_sandbox_evolution.py`)**: 고의로 버그가 포함된 스킬을 실행시키고, 에이전트 팩토리가 독립 샌드박스에서 오류를 분석한 뒤 코드를 수정, 정답률 상승시키는 전체 자가치유 사이클 검증.
