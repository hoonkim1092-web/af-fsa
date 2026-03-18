# 메모리 시스템 심층 분석 및 리뷰 결과 (Memory System Deep Dive)

현재 Agent Factory의 메모리 시스템 핵심 코드(`memory.py`, `ast_memory_hub.py`, `memory_consolidation.py`, `core_memory.py`)를 종합 분석했습니다. 구조적인 설계는 훌륭하나, **동시성(Concurrency), 성능(I/O), 그리고 '지능형 그래프 메모리' 비전 간의 괴리**에서 발생하는 치명적인 버그와 개선점이 다수 발견되었습니다.

이 문서는 코드를 직접 수정하지 않고, 발견된 버그와 구체적인 아키텍처 개선 단계를 명시적으로 기록하기 위해 작성되었습니다.

---

## 🚨 1. 발견된 핵심 버그 및 잠재적 취약점

### 1) 파일 시스템 기반 동시성 충돌 (Race Condition)
*   **위치:** `core/memory_system/adapters/core_memory.py` 내 `write()`
*   **문제:** 멀티 에이전트 환경에서 각 에이전트가 동시에 같은 `record_id.json`에 기록을 시도할 경우, **파일 락(File Lock) 제어가 전혀 없어 JSON 파일이 깨지거나(Corrupted) 데이터 유실**이 발생할 수 있습니다.

### 2) 비동기(Asyncio) 실행 컨텍스트의 불안정성
*   **위치:** `core/hooks/memory_consolidation.py` 내 `_run_record()`
*   **문제:** 동기식 훅(Hook) 환경 내에서 `asyncio.get_running_loop()`가 실패하면 `asyncio.run(coro)`를 강제로 호출하고 있습니다. 이는 런타임 환경(이미 닫힌 루프나 다른 스레드)에 따라 `Event loop is closed` 또는 데드락(Deadlock)과 같은 심각한 에러를 뿜을 수 있는 매우 불안정한 안티 패턴(Anti-pattern)입니다.

### 3) 이벤트 브로드캐스트 블로킹 (Blocking Pub/Sub)
*   **위치:** `core/ast_memory_hub.py` 내 `publish()`
*   **문제:** 여러 Subscriber(구독자)에게 이벤트를 뿌릴 때 `await cb(payload)`를 순차적인 for 루프로 돌고 있습니다. 만약 하나의 에이전트(Subscriber) 콜백이 지연되거나 무한 대기에 빠지면, 메인 AST 저장소 업데이트 자체가 완전히 멈춰버립니다(병목 발생).

### 4) 무거운 런타임 성능 저하 (O(N) File I/O)
*   **위치:** `core/memory.py` 내 `_iter_recent_json_files()` 및 `read_core_memory()`
*   **문제:** 메모리를 한 번 조회할 때마다 `os.walk()`로 수백 개의 파일 트리를 스캔하고, 매번 `os.path.getmtime(p)`을 호출하여 정렬합니다. 프로젝트가 길어져 메모리 파일이 수천 개로 쌓이면, **메모리 조회 속도가 급격히 느려지고 디스크 I/O에 엄청난 부하**가 걸립니다.

---

## 🛠 2. 아키텍처 관점의 개선점 (vs 지능형 메모리 비전)

`docs/intelligent_memory.md` 문서를 보면 궁극적인 지능형 메모리는 **단순 텍스트 검색을 넘어 '문제-원인-해결책'의 그래프(Graph-Vector Hybrid)**가 되어야 합니다. 하지만 현재 아키텍처는 다음과 같은 한계가 있습니다.

1.  **그래프 자료구조 부재**: 현재는 구조화 안 된 텍스트를 JSON 파일로 저장할 뿐, '노드(Node)'와 '엣지(Edge)'라는 관계성을 저장하는 물리적 실체(Graph DB 또는 최적화된 SQLite 등)가 구현되지 않았습니다.
2.  **AST 덮어쓰기 문제**: `ast_memory_hub.py`에서 `self.global_context[filepath] = ...` 방식으로 값을 덮어쓰기 때문에, 하나의 파일에서 여러 번 발생한 에러와 수정에 대한 "히스토리/인과관계"가 날아가고 '가장 최근 로그'만 남습니다.
3.  **메모리 통합 압축 단계 결여**: `MemoryConsolidationHook`은 단순히 실행된 `jsonl` 트레이스 전체를 뽑아내어 저장할 뿐, 마스터(Lilith)나 백그라운드 모델이 **핵심 노하우만 엑기스로 추출(Forging)하는 과정이 생략**되어 있습니다.

---

## 🚀 3. 명시적이고 상세한 아키텍처 개선 단계 (Step-by-Step)

향후 M3(지능형 메모리) 수준으로 역량을 진화시키기 위한 단계적 로드맵입니다.

### [Phase 1] 치명적 버그 수정 및 안정화 (Stability)
1.  **FileLock 도입**: `CoreMemoryAdapter`의 Read/Write 과정에 `filelock` 라이브러리를 도입하여 멀티 에이전트의 충돌(Race condition) 코러스를 방지합니다.
2.  **Pub/Sub 비동기 최적화**: `AstMemoryHub.publish()` 코드를 `asyncio.gather(*tasks)` 방식과 Timeout 처리로 변경해 브로드캐스트 병목을 제거합니다.
3.  **비동기 훅(Hook) 구조 개편**: `memory_consolidation.py`에서 강제로 `asyncio.run`을 호출하지 않고, 글로벌 백그라운드 `TaskQueue`나 데몬(Daemon) 스레드 워커에 메모리 압축 임무를 던지도록 비차단(Non-blocking) 방식으로 변경합니다.

### [Phase 2] 성능(I/O) 최적화 (Optimization)
1.  **SQLite 기반 메모리 허브 마이그레이션**: 단순 `.json` 수천 개를 읽는 방식을 버리고, 로컬 SQLite DB (예: `memory.db`) 하나로 통합합니다. 이를 통해 `read_core_memory`의 속도를 수백 배 이상 끌어올릴 수 있습니다.
2.  **AST 히스토리 누적 시스템 설계**: `AST_UPDATE`가 발생하면 단순히 Key를 덮어쓰지 않고 배열이나 시간 순서열(Timeline) 데이터로 누적해서 "이 파일이 어떤 과정을 거쳐 수정됐는지" 추적할 수 있도록 합니다.

### [Phase 3] 지능형 구조체(Graph-Vector) 구축 (Intelligence)
1.  **Extract-Transform-Load (ETL) 모듈 구축**: 에이전트의 유휴 시간에 `trace_xyz.jsonl` 로그를 읽고 백그라운드 LLM(예: Gemini Flash)을 동원해 **[태스크] - [에러] - [해결책] 세 개의 노드(Graph Entity)**로 파싱하는 지식 압축기를 만듭니다.
2.  **Graph 관계형 저장소 구현**: 추출된 노드와 엣지를 SQLite(관계형)나 NetworkX 같은 라이브러리를 써서, 이후 프롬프트 주입 시 "OO 에러가 발생하면 무조건 이 해결책을 제시해"라는 최적의 가드레일이 되도록 구조화합니다.
3.  **Semantic + Graph Hybrid 검색 도입**: 새로 시작하는 프롬프트(지시사항)과 가장 연관성 높은 그래프 '조각(Subgraph)'을 찾아내어, 컨텍스트 용량을 아끼면서도 핵심 노하우만 주입시키는 진정한 **지능형 라우터 기능**을 완성합니다.
