import json
from typing import Dict, Any

from core.llm_engine import LLMEngine
from core.utils import safe_json_load

class StrategyEvaluator:
    """
    Agent Factory V3: Evaluator & Strategy Pivot Engine
    Replaces dump retries (max_cycles=5) with intelligent analysis of failures.
    If an agent fails, the Evaluator breaks down the error log and decides if the 
    subtask needs a "Retry" (e.g., syntax typo) or a "Strategic Pivot" (e.g., wrong library,
    architectural mismatch).
    """
    def __init__(self, model_name: str = "gemini-1.5-pro-latest"):
        self.llm = LLMEngine(model_name=model_name)
        
    def evaluate_failure(self, role: str, instruction: str, error_log: str) -> Dict[str, Any]:
        """
        Analyzes a failure and returns the next step action.
        """
        prompt = f"""
        You are the Head Evaluator of Agent Factory V3.
        An agent ({role}) failed to complete a task.
        
        Task Instruction:
        {instruction}
        
        Error Log / Reason for Failure:
        {error_log}
        
        Analyze the failure and decide the next course of action.
        - "retry": It's a simple mistake like a typo, syntax error, or missing import that the agent can quickly fix.
        - "pivot": The strategy is fundamentally wrong (e.g., library doesn't exist, version conflict, approach is flawed). We must change the instructions or the goal entirely.
        - "abort": Catastrophic failure, cannot continue with this micro-task.
        
        Return JSON ONLY:
        {{
            "action": "retry|pivot|abort",
            "reasoning": "Explanation of why this action was chosen.",
            "new_instruction": "If pivot or retry, what should the agent be told to do now? (Leave blank if abort)"
        }}
        """
        
        try:
            res = self.llm.generate_json(prompt)
            data = res if isinstance(res, dict) else safe_json_load(res)
            return {
                "action": str(data.get("action", "abort")).strip().lower(),
                "reasoning": str(data.get("reasoning", "")),
                "new_instruction": str(data.get("new_instruction", ""))
            }
        except Exception as e:
            return {
                "action": "abort",
                "reasoning": f"Evaluator crashed during analysis: {e}",
                "new_instruction": ""
            }
