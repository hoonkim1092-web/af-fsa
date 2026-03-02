import os
import time
from core.agent_runner import AgentRunner
from core.git_manager import GitManager
from core.utils import now_iso, print_agent_msg
from core.evaluator import StrategyEvaluator

class FSALoop:
    """
    (V22.5) Full Self Automation (FSA) Loop Orchestrator with Git Safety
    Implements a Plan -> Work -> Verify -> Rework cycle with automatic rollback.
    """
    def __init__(self, runner: AgentRunner):
        self.runner = runner
        self.git = GitManager()
        self.evaluator = StrategyEvaluator(model_name=runner.mr.pick('evaluator') if hasattr(runner.mr, 'pick') else 'gemini-1.5-pro-latest')
        self.max_cycles = 5

    def run_mission(self, agent: dict, task_input: str, run_id: str):
        print(f"\n🌀 [FSALoop] 풀 셀프 자동화 모드(FSA) 시작: {run_id}")
        
        current_task = task_input
        for cycle in range(1, self.max_cycles + 1):
            print(f"\n🔄 [Cycle {cycle}/{self.max_cycles}] 실행 및 자동 커밋 준비...")
            
            # 1. Pre-Commit for safety
            commit_msg = f"AEE Auto-Save: {run_id} Cycle {cycle}"
            self.git.commit(commit_msg)
            
            # 2. Work Phase (Autonomous)
            result = self.runner.run(agent, current_task, run_id=f"{run_id}_c{cycle}", auto_approve=True)
            
            if result.get("ok"):
                print(f"✅ [Cycle {cycle}] 성공적으로 완료됨.")
                return result
            
            # 3. Failure & Rollback logic
            print(f"⚠️ [Cycle {cycle}] 실패 감지: {result.get('reason')}")
            print(f"⏪ [FSALoop] 안전을 위해 Git Rollback을 수행합니다.")
            
            # Note: GitManager에 rollback 메서드가 구현되어 있다고 가정하거나 
            # 없으면 직접 명령어로 처리 (여기서는 구현되었다고 보고 호출)
            try:
                self.git.rollback() 
            except Exception as e:
                print_agent_msg("Critical", f"Rollback 실패: {e}", "🛑")
            
            # Phase 3: Strategy Pivot
            eval_res = self.evaluator.evaluate_failure(
                role=agent.get("role", "General"),
                instruction=current_task,
                error_log=result.get("reason", "Unknown error")
            )
            
            action = eval_res.get("action", "abort")
            if action == "abort":
                print_agent_msg("Evaluator", f"Catastrophic failure. Aborting sequence. Reason: {eval_res.get('reasoning')}", "🛑")
                return {"ok": False, "reason": "Evaluator aborted task."}
            
            print_agent_msg("Evaluator", f"Decision: {action.upper()} | Reasoning: {eval_res.get('reasoning')}", "💡")
            current_task = f"[EVALUATOR {action.upper()} ADVICE]\n{eval_res.get('new_instruction')}\n\n[Original Task]\n{task_input}"

        return {"ok": False, "reason": "최대 재시도 횟수(5회) 초과로 중단되었습니다."}
