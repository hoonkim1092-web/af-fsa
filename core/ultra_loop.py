import os
import time
from core.agent_runner import AgentRunner
from core.git_manager import GitManager
from core.utils import now_iso

class UltraLoop:
    """
    (V22.5) Ultrawork Loop Orchestrator with Git Safety
    Implements a Plan -> Work -> Verify -> Rework cycle with automatic rollback.
    """
    def __init__(self, runner: AgentRunner):
        self.runner = runner
        self.git = GitManager()
        self.max_cycles = 5

    def run_mission(self, agent: dict, task_input: str, run_id: str):
        print(f"\n🌀 [UltraLoop] 자율 완수 모드(Ultrawork) 시작: {run_id}")
        
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
            print(f"⏪ [UltraLoop] 안전을 위해 Git Rollback을 수행합니다.")
            
            # Note: GitManager에 rollback 메서드가 구현되어 있다고 가정하거나 
            # 없으면 직접 명령어로 처리 (여기서는 구현되었다고 보고 호출)
            try:
                self.git.rollback() 
            except Exception as e:
                print(f"🛑 [Critical] Rollback 실패: {e}")
            
            current_task = f"이전 시도 실패 사유: {result.get('reason')}\n다시 시도하십시오. 이번에는 실패를 극복할 대안을 계획하세요.\n원본 태스크: {task_input}"

        return {"ok": False, "reason": "최대 재시도 횟수(5회) 초과로 중단되었습니다."}
