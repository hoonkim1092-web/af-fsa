---
name: af-architecture
description: "Agent Factory 아키텍처 전체 구조. core/*.py 수정 시 자동 트리거. 서브시스템 의존 관계, 실행 흐름, 파일별 역할 참조."
---

<overview>
Agent Factory는 5-레이어 아키텍처의 멀티 에이전트 실행 시스템이다 (core/ 174파일, 46,000줄).
이 스킬은 전체 코드를 읽지 않고 구조를 파악하기 위한 참조 지식이다.
</overview>

<when-to-use>
- `core/*.py` 파일을 수정하기 전에
- 새 기능 추가 시 영향 범위 파악이 필요할 때
- 파일 간 의존 관계를 확인할 때
</when-to-use>

<architecture>

## 5-레이어 구조

```
Layer 5: UI              run_factory_cli.py, interactive_chat.py
Layer 4: 파이프라인        project_pipeline.py, fsa_loop.py, ise_loop.py
Layer 3: 멀티 에이전트     dynamic_orchestrator.py, agent_specializer.py
Layer 2: 단일 에이전트     agent_runner.py, providers/cli.py, skill_loader.py
Layer 1: 공유 인프라       memory_system/, message_broker.py, hooks/, model_router.py
```

## 핵심 실행 흐름

```
run_factory_cli.py → ControlPlaneIntake → MaintenancePipeline
  → RuntimeSupervisor → DynamicOrchestrator (asyncio 5-concurrent)
    → AgentRunner.run() × N (provider failover: claude→gemini→codex)
```

## 실행 모드

| 모드 | 진입 | 루프 |
|------|------|------|
| Interactive | interactive_chat.py | 사용자 입력 반복 |
| Approval | project_pipeline.py | Phase1→승인→Phase2 |
| FSA | fsa_loop.py | 최대 5사이클 |
| ISE | ise_loop.py | 무한 자가진화 |
| Worker | agent_worker.py | 단일 태스크 (PyInstaller) |

## 서브시스템 핵심 파일

**Runtime**: agent_runner.py(1,419줄), dynamic_orchestrator.py(887줄), model_router.py(262줄)
**Control Plane**: control/supervisor.py(550줄), control/maintenance_pipeline.py(456줄), control/run_ledger.py(310줄)
**Provider**: providers/registry.py(253줄), providers/cli.py(764줄), providers/session_adapter.py(642줄)
**Hook**: hooks/event_bus.py(155줄), hooks/context_fork.py(313줄), hooks/langsmith_tracing.py(360줄)
**Skill**: skill_creator.py(1,092줄), skill_procurer.py(876줄), skill_eval_harness.py(732줄), skill_registry.py(536줄)
**대화/연구**: interactive_chat.py(887줄), researcher.py(894줄), conversation_manager.py(822줄)
**교차검증**: cross_verification.py(760줄), parallel_critique.py(371줄), consensus_engine.py(364줄)

</architecture>

<rules>
## 코드 수정 시 필수 규칙

1. **수정 전**: Master_Blueprint.md의 해당 §섹션을 먼저 읽어 의존성과 영향 범위 파악
2. **수정 후**: 변경된 파일의 §섹션과 §12 변경 이력을 같은 커밋에서 업데이트
3. 새 `core/*.py` 파일 → `af.spec` hiddenimports에 추가
4. 전체 코드를 다시 읽지 않는다 — Blueprint가 최신이면 Blueprint만으로 판단

## Master_Blueprint.md 업데이트 트리거

| 이벤트 | 업데이트 대상 |
|--------|-------------|
| 새 .py 파일 생성 | §0 빠른 참조 테이블 |
| 클래스/메서드 변경 | §3 해당 서브시스템 |
| 버그 수정 | §11 에러 코드, §12 이력 |
| 배포(버전 bump) | §8 빌드, §12 이력 |
| 의존성 변경 | §10 Blast Radius |
</rules>
