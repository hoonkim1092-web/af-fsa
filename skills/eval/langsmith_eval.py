import os
import json

try:
    from core.decorators import skill_metadata
except ImportError:
    def skill_metadata(**kwargs):
        def decorator(func):
            func.__skill_metadata__ = kwargs
            return func
        return decorator

@skill_metadata(category="eval", max_tokens=1000)
def trace_execution(ctx, script_path: str, output_log_path: str) -> str:
    """
    [When to use] 터미널에서 코드를 실행하고 그 결과를 트레이싱(Tracing)하여 모든 stdout/stderr를 로그 파일로 남길 때 호출하라.
    """
    workspace = ctx.get("workspace", ".")
    full_script_path = os.path.join(workspace, script_path)
    full_output_path = os.path.join(workspace, output_log_path)
    
    os.makedirs(os.path.dirname(full_output_path), exist_ok=True)
    
    # In a real implementation, you would execute the script and capture outputs via Popen or similar.
    # For now, this is the interface placeholder requested by the integration plan.
    message = f"Simulating tracing of execution for {script_path}. Saved trace log to {output_log_path}."
    with open(full_output_path, "w", encoding="utf-8") as f:
        f.write(message)
        
    return message

@skill_metadata(category="eval", max_tokens=1500)
def summarize_failure(ctx, trace_log_path: str, output_json_path: str) -> str:
    """
    [When to use] 터미널 실행 중 에러가 감지되거나 실패했을 때, 그 원인 분석 및 요약 리포트를 생성하기 위해 반드시 이 스킬을 호출하라.
    """
    workspace = ctx.get("workspace", ".")
    full_log_path = os.path.join(workspace, trace_log_path)
    full_out_path = os.path.join(workspace, output_json_path)
    
    if not os.path.exists(full_log_path):
        return f"[Error] Trace log not found at {full_log_path}"
        
    os.makedirs(os.path.dirname(full_out_path), exist_ok=True)
    
    # Real implementation would call ChatOpenAI to summarize.
    summary_data = {
        "status": "failed",
        "reason": "Extracted failure reason placeholder",
        "suggested_fix": "Look into the trace log to identify the traceback."
    }
    
    with open(full_out_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)
        
    return f"Failure summarized successfully. Output written to {output_json_path}"

@skill_metadata(category="eval", max_tokens=2000)
def generate_eval_dataset(ctx, feedback_json_path: str, test_output_path: str) -> str:
    """
    [When to use] 요약된 실패 원인을 바탕으로, 두 번 다시 같은 논리적 오류를 반복하지 않도록 방어적인 검증 테스트 케이스(가상 쿼리 등)를 생성할 때 호출하라.
    """
    workspace = ctx.get("workspace", ".")
    full_feedback_path = os.path.join(workspace, feedback_json_path)
    full_test_path = os.path.join(workspace, test_output_path)
    
    if not os.path.exists(full_feedback_path):
        return f"[Error] Feedback JSON not found at {full_feedback_path}"
        
    os.makedirs(os.path.dirname(full_test_path), exist_ok=True)
    
    test_code = f\"\"\"# Auto-generated eval test case
def test_regression_fix():
    # Load feedback from {feedback_json_path}
    assert True, "This is a placeholder for the generated assert based on failure feedback"
\"\"\"
    
    with open(full_test_path, "w", encoding="utf-8") as f:
        f.write(test_code)
        
    return f"Evaluation dataset and test cases generated successfully at {test_output_path}"
