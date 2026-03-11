# 스킬 울트라루프(FSALoop) 충돌 분석 및 통합 설계안

## 1. 개요 (Background)
Agent Factory에는 현재 `core/fsa_loop.py` 내부적으로 에러 복구를 전담하는 **FSALoop (Full Self Automation)** 기능이 탑재되어 있습니다. 해당 루프 안에는 하드코딩된 자체 `StrategyEvaluator`가 복구를 위한 재시도 지시를 내리고 있습니다.

최근 LangSmith의 핵심 스킬(`trace_execution`, `summarize_failure`, `generate_eval_dataset`)을 장착한 공식 **Evaluator 에이전트(`agents/evaluator.yaml`)** 가 신규 생성되었습니다. 
따라서 기존의 하드코딩된 `StrategyEvaluator`와 신규 `Evaluator` 에이전트 간에 **역할 충돌 및 오케스트레이션 중복(겉돌음 현상)** 이 예상됩니다.

## 2. 발생 가능한 충돌 요인 (Conflicts)
1. **역할 중복 (Redundant Evaluation):**
   - 기존의 `FSALoop.run_mission()`이 실패(fail)를 감지하면 신규 `Evaluator` 에이전트를 호출하지 않고, 내부의 `StrategyEvaluator` 클래스 메서드를 강제 호출합니다.
   - 방금 장착된 LangSmith 스킬 기반의 평가 및 테스트 코드 생성(`generate_eval_dataset`) 능력이 자동으로 가동되지 못합니다.
2. **파이프라인 분리 (Workflow Isoation):**
   - 개발/설계 에이전트(`Deadbyte`, `Iguro Obanai` 등)는 AgentRunner/DynamicOrchestrator를 타고 도는 반면, 오류 검증 파이프라인이 낡은 하드코딩 방식으로 고립되어 있습니다.

## 3. 해결 방안 (Integration Strategy)
강력한 `Evaluator` 에이전트를 팩토리 코어에 편입하여 "Agent-Improving-Agent" 루프를 완성하기 위한 로직 수정안입니다.

### [수정 대상] `core/fsa_loop.py`
기존의 `StrategyEvaluator` 의존성을 걷어내고, `AgentManager`를 통해 정식 **Evaluator 에이전트**를 실시간으로 소환하여 임무를 대신 수행하도록 배관을 연결해야 합니다.

**개념 증명(PoC) 흐름:**
1. Developer 에이전트 실패 감지 -> `git.rollback()` (기존 유지)
2. `AgentManager` 로 `Evaluator` 에이전트 로드
3. `AgentRunner`를 통해 Evaluator 에이전트에게 "run_id 와 발생한 에러 원인"을 태스크로 부여
4. Evaluator 에이전트는 장착된 `LangSmith` 스킬을 자율적으로 사용하여:
   - 에러의 맥락 파악 (`trace_execution`)
   - 원인 요약 (`summarize_failure`)
   - 방어 코드/테스트 케이스 생성 (`generate_eval_dataset`)
5. 생성된 피드백을 기반으로 다음 재시도(Cycle)를 재가동

## 4. 조치/검토 사항
* 이 변경은 Agent Factory의 핵심 자동화 동력(자가 복구 회로) 자체를 들어내는 작업이므로, 안정성 확보를 위해 코어 적용 전 샌드박싱 테스트가 필요할 수 있습니다.
* 적용(Apply) 승인이 떨어지면 `core/fsa_loop.py`의 `run_mission()` 메서드를 위 로직으로 리팩토링합니다.
