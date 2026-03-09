# [Agent Factory] M4 넥스트 제너레이션 아키텍처 설계: OpenSwarm & Oh-My-Opencode 통합 분석

## 1. 개요

현재 설계된 Agent Factory의 코어 아키텍처(동시 다발적 병렬 실행 + FSA 안전망)를 기반으로 하여, 최근 떠오르는 자율형 코딩 시스템인 `OpenSwarm` 및 `Oh-my-opencode`의 핵심 방법론을 어떻게 수용하고 이식할지에 대한 전면적인 코드 구조 분석 및 진화 마일스톤(To-Be)을 정의합니다.

## 2. 현재(As-Is) Agent Factory 코드 구조 및 작동 방식 파악

현재 팩토리는 **"다중 엔진의 동시 다발적 병렬 실행(ThreadPool/Ghost-Pilot)"**을 코어로 삼고 있습니다.

* `core/fsa_loop.py` & `evaluator.py`: 에이전트가 코딩 후 실패 시 전략을 수정해 재시도(Git 롤백 지원)하는 로컬 생존망.
* `agent_launcher.py`: 사용자 지시를 쪼개어 여러 에이전트에게 백그라운드에서 동시에 살포(Scatter)하고 결과를 취합(Gather)하는 속도전 엔진.
* `core/synergy/`: Gemini, Claude, GPT 등 각 엔진별 특성을 극대화하여 맵핑하는 오토반(Autobahn) 라우터.

> **현재의 한계점:** 막강한 화력(병렬 속도와 멀티 모델)을 가졌지만, 사용자가 항상 터미널에서 지시해야 하고(수동 트리거), 세션이 끝나면 기억이 날아가며(장기 기억 부재), PR/CI 배포 등 실제 운영망까지는 뻗어 있지 않은 상황입니다.

---

## 3. 3대 아키텍처 비교 (Agent Factory vs Oh-My-Opencode vs OpenSwarm)

| 기능 스펙 | 🏭 Agent Factory (현재 우리) | ⚡ Oh My Opencode (비교군 1) | 🚀 OpenSwarm (비교군 2) |
| :--- | :--- | :--- | :--- |
| **운영 철학** | **"다중 지능 협업 공장"**<br>빠른 속도의 병렬 실행과 다중 모델(Claude+Gemini 등) 교차 검증의 극한 시너지. | **"똑똑한 1:1 보좌관"**<br>토큰을 아끼는 압축 기술(Compaction)과 `@멘션`을 통한 직관적 인터랙티브 제어. | **"무인 자율 배포팀"**<br>이슈 수집부터 CI 통과(PR 초록불)까지 전 과정을 릴레이로 무한 수행하는 좀비 루틴. |
| **프로세스 형태** | **Scatter-Gather (병렬)**<br>동시 다발적으로 여러 에이전트가 여러 파일을 수정. | **Interactive / Linear (선형)**<br>사용자와 대화하며 하나씩 순차 수행. | **Sequential Relay (직렬 릴레이)**<br>Worker ➡️ Reviewer ➡️ Tester ➡️ PR. |
| **기억 (Memory)** | **단기 (Session & Text Files)**<br>`progress.md` 등 세션 내 텍스트 파일 공유 | **단기/중기 (동적 압축 및 룰 주입)**<br>로그 용량을 줄이고 디렉토리 단위 규칙 주입 | **장기 (Vector DB + AST 의존성 RAG)**<br>LanceDB를 통한 영구적 지식 그래프 보존 |
| **에러 극복 (Loop)** | 코딩 실패 시 5회 제한 궤도 수정 + Git 롤백 방어선 (로컬 생존) | 사용자에게 반문하는 것을 금지하고 강제 결말 도출 (Todo Enforcer) | 배포망(GitHub CI) 및 PR 충돌 해결 시까지 무한 롤백/수정 (좀비 루프) |

---

## 4. 통합 이식 방안 (How to Integrate)

Agent Factory의 가장 강력한 무기인 **"가로(병렬) 전개 능력"**을 훼손하지 않으면서, 그 위에 Oh-My-Opencode의 **"마이크로 제어력"**과 OpenSwarm의 **"세로(직렬) 파이프라인"**을 십자(Cross)로 엮어 궁극의 아키텍처를 완성합니다.

1. **가로(병렬) + 세로(직렬)의 하이브리드 파이프라인 구축:**
    * 티켓이 들어오면 `Worker` 단위에서 Agent Factory 주특기인 `병렬 다중 에이전트 실행기(agent_launcher)`가 투입되어 엄청난 속도로 코드를 깎아냅니다.
    * 그 후 `Reviewer(Gemini)` ➡️ `Tester(GPT)` 단위로 결과물을 엄격하게 검증하며 넘기는 OpenSwarm식 세로 릴레이 라인을 태웁니다.
2. **컨텍스트 스펙트럼 확장 (마이크로 튜닝 적용):**
    * Oh-My-Opencode의 `Context Compaction(터미널 동적 요약)`과 트리망 룰 주입(`.factory_rules`)을 도입해, 에이전트가 길을 잃거나 토큰 폭발이 나는 것을 원천 차단합니다.
3. **지식 그래프 및 좀비 루프 적용:**
    * LanceDB 기반 코딩 히스토리 RAG를 팩토리에 탑재하여 "이 함수를 고치면 어디서 에러가 나는지" 스캔하고, 외부 CI에서 에러가 나면 통과할 때까지 무한 반복하는 생존망을 붙입니다.

---

## 5. 적용 전후 (As-Is vs To-Be) 비교

이식 전후의 극적인 차이는 **"인간 개입의 최소화"**와 **"품질의 절대적 보장"**입니다.

| 컴포넌트 | As-Is (통합 적용 전) | To-Be (하이브리드 아키텍처 통합 적용 시) |
| :--- | :--- | :--- |
| **작업 시작** | 보스(User)가 출근해서 프롬프트 입력 대기 | 티켓(Linear/GitHub)만 있으면 주말에도 혼자 자동으로 공장 가동 |
| **비용 및 맥락 제어** | 대화/로그가 길어지면 에이전트가 바보가 되고 토큰 과금 급증 | Oh-My-Opencode식 **동적 압축**으로 핵심 정보만 남겨 토큰 방어 및 성능 유지 |
| **부작용 (Side Effect)** | A파일을 고치다가 의존성이 묶인 B파일이 터지는 걸 모름 | LanceDB **임베딩(장기 기억)** 스캔으로 코드의 폭발 반경(Impact Graph) 사전 차단 |
| **에러 대응** | 5회 복구 루프 실패 시 사용자에게 보고 후 작업 중단 | 원격 CI 로그를 긁어와서 PR 상태가 초록불(`Merge Ready`)이 될 때까지 무한 해결 |
| **최종 결과물** | 로컬에서 컴파일/실행이 되는 코드 덩어리 묶음 | **테스트 커버리지와 리뷰 승인이 완료된 Merge Ready 상태의 배포망 PR** |

---

## 6. 명시적이고 상세한 파이프라인 개발 마일스톤 (5단계 로드맵)

1. **Phase 1: 컨텍스트 지능화 및 멘션 제어 (Oh-My-Opencode 스펙 이식)**
    * (a) **텍스트 압축기(Compaction 필터) 구현:** 터미널 명령어 실행 결과나 `cat` 결과가 1000줄이 나오면, `LLM` 필터를 거쳐 핵심 20줄로 요약해 메인 프롬프트에 넘기는 필터망 구축.
    * (b) **디렉토리 룰 주입기:** 작업 런타임 시 특정 폴더의 `.factory_rules` 파일을 자동 색인하여 에이전트 System Prompt에 병합 주입.
2. **Phase 2: 장기 지식 스캐너 부착 (OpenSwarm RAG 스펙 이식)**
    * (a) 로컬 환경에 LanceDB 구동 및 전체 프로젝트 Vector Embedding (e5-multilingual 등) 초기 적재.
    * (b) 에이전트가 코드를 건드리기 전 "이 함수가 어디서 불리는가(AST 의존성 망)"를 무조건 DB에서 조회하도록 내부 Skill(도구) 제작 및 강제화.
3. **Phase 3: 무인 티켓 폴러(Poller) 획득망 개발**
    * 외부 Linear / GitHub API를 연동하여 'To-Do' 이슈를 주워와 `task_plan.md` (팩토리 작업 지시서)로 자동 파싱하는 트리거(Trigger) 데몬 구현.
4. **Phase 4: 병렬-릴레이 하이브리드 파이프라인 (Orchestrator) 재설계**
    * 기존 `agent_launcher.py`(다중 병렬 로직) 코어를 떼어내어 신규 `pipeline_orchestrator.py`의 **`1단계 Worker` 노드** 안으로 편입시킴 (병렬 속도 유지).
    * 위 결과물을 받아 `Reviewer(Gemini 검수) ➡️ Tester(테스트 작성)` 형태로 강제 인수인계하는 릴레이 컨베이어 벨트 클래스 신규 개발.
5. **Phase 5: 워치타워 & 좀비 루프 개발 (CI/CD 최종 연동)**
    * GitHub Actions Webhook을 리스닝하다가 승인/실패 상태를 포착하는 포트 리스너 구축.
    * 에러 발생(Failure) 또는 Merge Conflict(충돌 마커) 인지 시, 파이프라인을 1단계부터 원격 에러 로그와 함께 강제 재구동(Auto-Fix Loop).
    * Discord 연동 및 대시보드(포트 3847 등)를 통해 DB 삭제나 결제 API 배포 등 고위험 작업 시 보스의 인가(Approval O/X)를 대기하는 Human-in-the-loop 결재망 부착.
