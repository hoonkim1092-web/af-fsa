# Feature: Claude Code Skills 2.0 통합

## 목적
LangChain Skills 2.0을 agent-factory 코어에 통합. 기존 커스텀 오케스트레이션(AgentRunner, FSALoop) 유지하면서 LangChain을 도구/유틸리티로 활용.

## 설계 요약
- **Phase 0**: LangChain Adapter Layer (graceful degradation)
- **Phase 1**: LangSmith Tracing Hook
- **Phase 2**: Dynamic Skill Loading (12-Cap)
- **Phase 3**: FSALoop Evaluator 에이전트 교체
- **Phase 4**: Structured Output (Pydantic)
- **Phase 5**: Self-Improvement Loop
- **Phase 6**: Human-in-the-Loop + Checkpoint Persistence

## 영향 범위
- `core/` 디렉토리 전반
- `agent_launcher.py`
- `agents/evaluator.yaml`
- `skills/eval/langsmith_eval.py`

## 체크리스트
- [ ] Phase 0: langchain_adapter.py + requirements.txt
- [ ] Phase 1: langsmith_tracing.py + event_bus 수정
- [ ] Phase 2: 12-cap skill loading
- [ ] Phase 3: FSALoop evaluator agent 교체
- [ ] Phase 4: Structured output
- [ ] Phase 5: Self-improvement loop
- [ ] Phase 6: HitL + Checkpoint hooks
