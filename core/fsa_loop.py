import os
import json
import time
from core.agent_runner import AgentRunner
from core.git_manager import GitManager
from core.utils import now_iso, print_agent_msg, safe_json_load
from core.evaluator import StrategyEvaluator


def parse_evaluator_response(result: dict) -> dict:
    """Parse an evaluator agent's run result into action/reasoning/new_instruction."""
    if not result.get("ok"):
        return {"action": "abort", "reasoning": f"Evaluator agent failed: {result.get('reason', '')}", "new_instruction": ""}

    # Try to extract JSON from the agent's output
    output = result.get("output", "") or result.get("reason", "")
    if isinstance(output, dict):
        return {
            "action": str(output.get("action", "abort")).strip().lower(),
            "reasoning": str(output.get("reasoning", "")),
            "new_instruction": str(output.get("new_instruction", "")),
        }

    # Try JSON extraction from text
    data = safe_json_load(output) if isinstance(output, str) else {}
    if data and "action" in data:
        return {
            "action": str(data.get("action", "abort")).strip().lower(),
            "reasoning": str(data.get("reasoning", "")),
            "new_instruction": str(data.get("new_instruction", "")),
        }

    return {"action": "abort", "reasoning": "Could not parse evaluator response", "new_instruction": ""}


class FSALoop:
    """
    (V23) Full Self Automation (FSA) Loop Orchestrator with Git Safety.
    Implements EXECUTE -> TRACE -> EVAL -> SUMMARIZE -> DATASETS -> REFLECT cycle.
    """
    def __init__(self, runner: AgentRunner, agent_mgr=None):
        self.runner = runner
        self.git = GitManager()
        self.agent_mgr = agent_mgr
        # Fallback evaluator (used when agent_mgr is unavailable or evaluator agent fails)
        self.evaluator = StrategyEvaluator(
            model_name=runner.mr.pick('evaluator') if hasattr(runner.mr, 'pick') else 'gemini-1.5-pro-latest'
        )
        self.max_cycles = 5

    def run_mission(self, agent: dict, task_input: str, run_id: str, workspace: str | None = None):
        print(f"\n🌀 [FSALoop] 풀 셀프 자동화 모드(FSA) 시작: {run_id}")

        current_task = task_input
        for cycle in range(1, self.max_cycles + 1):
            print(f"\n🔄 [Cycle {cycle}/{self.max_cycles}] 실행 및 자동 커밋 준비...")

            # ── Step 1: Pre-Commit for safety ──
            commit_msg = f"AEE Auto-Save: {run_id} Cycle {cycle}"
            self.git.commit(commit_msg)

            # ── Step 2: EXECUTE ──
            result = self.runner.run(
                agent,
                current_task,
                run_id=f"{run_id}_c{cycle}",
                auto_approve=True,
                workspace=workspace,
            )

            # Step 2b: TRACE — LangSmithTracingHook auto-collects (Phase 1, no-op if disabled)

            if result.get("ok"):
                print(f"✅ [Cycle {cycle}] 성공적으로 완료됨.")
                return result

            # ── Step 3: Failure & Rollback ──
            print(f"⚠️ [Cycle {cycle}] 실패 감지: {result.get('reason')}")
            print(f"⏪ [FSALoop] 안전을 위해 Git Rollback을 수행합니다.")

            try:
                self.git.rollback()
            except Exception as e:
                print_agent_msg("Critical", f"Rollback 실패: {e}", "🛑")

            # ── Step 4: EVAL — Evaluator 에이전트 또는 fallback ──
            eval_res = self._run_evaluator(agent, current_task, result, run_id, cycle)

            action = eval_res.get("action", "abort")
            if action == "abort":
                print_agent_msg("Evaluator", f"Catastrophic failure. Aborting sequence. Reason: {eval_res.get('reasoning')}", "🛑")
                return {"ok": False, "reason": "Evaluator aborted task."}

            # ── Step 5: SUMMARIZE + DATASETS — handled by evaluator agent's skills ──
            # (trace_execution, summarize_failure, generate_eval_dataset are in evaluator's skill set)

            # ── Step 6: REFLECT — inject feedback into next cycle's task ──
            print_agent_msg("Evaluator", f"Decision: {action.upper()} | Reasoning: {eval_res.get('reasoning')}", "💡")
            current_task = f"[EVALUATOR {action.upper()} ADVICE]\n{eval_res.get('new_instruction')}\n\n[Original Task]\n{task_input}"

        return {"ok": False, "reason": "최대 재시도 횟수(5회) 초과로 중단되었습니다."}

    def _run_evaluator(self, agent: dict, current_task: str, result: dict, run_id: str, cycle: int) -> dict:
        """Try evaluator agent first, fall back to StrategyEvaluator."""
        if self.agent_mgr is not None:
            try:
                evaluator_agent = self.agent_mgr.get_or_create("evaluator")
                eval_task = (
                    f"[EVAL REQUEST] run_id={run_id}\n"
                    f"Error: {result.get('reason')}\n"
                    f"Original Task: {current_task}"
                )
                eval_result = self.runner.run(
                    evaluator_agent,
                    eval_task,
                    run_id=f"{run_id}_eval_c{cycle}",
                    auto_approve=True,
                )
                parsed = parse_evaluator_response(eval_result)
                if parsed.get("action") != "abort" or "Could not parse" not in parsed.get("reasoning", ""):
                    return parsed
                # If parsing failed, fall through to legacy evaluator
            except Exception as e:
                print_agent_msg("FSALoop", f"Evaluator 에이전트 호출 실패, fallback 사용: {e}", "⚠️")

        # Fallback: legacy StrategyEvaluator
        return self.evaluator.evaluate_failure(
            role=agent.get("role", "General"),
            instruction=current_task,
            error_log=result.get("reason", "Unknown error"),
        )
