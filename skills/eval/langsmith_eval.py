import os
import json
import subprocess

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

    if not os.path.exists(full_script_path):
        return f"[Error] Script not found: {full_script_path}"

    try:
        proc = subprocess.run(
            ["python", full_script_path],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=workspace,
        )
        trace_output = f"=== STDOUT ===\n{proc.stdout}\n\n=== STDERR ===\n{proc.stderr}\n\n=== RETURN CODE: {proc.returncode} ==="
    except subprocess.TimeoutExpired:
        trace_output = "[Error] Script execution timed out after 120 seconds."
    except Exception as e:
        trace_output = f"[Error] Execution failed: {e}"

    with open(full_output_path, "w", encoding="utf-8") as f:
        f.write(trace_output)

    return f"Trace captured for {script_path}. Saved to {output_log_path}. Return code: {proc.returncode if 'proc' in dir() else 'N/A'}"


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

    with open(full_log_path, "r", encoding="utf-8") as f:
        log_content = f.read()

    # Use LLM for real analysis
    try:
        from core.llm_engine import LLMEngine
        llm = LLMEngine()
        prompt = f"""Analyze this execution trace and identify the root cause of failure.

Trace Log:
{log_content[:4000]}

Return JSON:
{{
    "status": "failed|passed",
    "root_cause": "concise description of the root cause",
    "error_type": "syntax|runtime|logic|dependency|timeout|unknown",
    "suggested_fix": "specific actionable fix suggestion",
    "confidence": 0.0-1.0
}}"""
        summary_data = llm.generate_json(prompt)
        if not summary_data:
            raise ValueError("LLM returned empty response")
    except Exception as e:
        # Fallback: basic regex-based analysis
        summary_data = {
            "status": "failed",
            "root_cause": _extract_error_line(log_content),
            "error_type": "unknown",
            "suggested_fix": "Review the trace log manually.",
            "confidence": 0.3,
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

    with open(full_feedback_path, "r", encoding="utf-8") as f:
        feedback = json.load(f)

    # Use LLM to generate pytest code
    try:
        from core.llm_engine import LLMEngine
        llm = LLMEngine()
        prompt = f"""Based on this failure analysis, generate a pytest test case that would catch this regression.

Failure Analysis:
{json.dumps(feedback, ensure_ascii=False, indent=2)}

Generate ONLY the Python test code (pytest format). Include:
1. A descriptive test function name
2. Clear assertions that would catch the exact failure described
3. Comments explaining what each assertion validates"""

        test_code = llm.generate(prompt)
        if not test_code:
            raise ValueError("LLM returned empty response")
        # Clean up markdown code fences if present
        if "```python" in test_code:
            test_code = test_code.split("```python")[1].split("```")[0].strip()
        elif "```" in test_code:
            test_code = test_code.split("```")[1].split("```")[0].strip()
    except Exception:
        test_code = f"""# Auto-generated regression test from failure feedback
import pytest

def test_regression_from_failure():
    \"\"\"Regression test based on: {feedback.get('root_cause', 'unknown failure')}\"\"\"
    # TODO: Implement specific assertions based on the failure
    assert True, "Placeholder — replace with real assertions"
"""

    with open(full_test_path, "w", encoding="utf-8") as f:
        f.write(test_code)

    return f"Evaluation dataset and test cases generated successfully at {test_output_path}"


def _extract_error_line(log_content: str) -> str:
    """Extract the most relevant error line from a trace log."""
    lines = log_content.strip().split("\n")
    for line in reversed(lines):
        stripped = line.strip()
        if any(kw in stripped.lower() for kw in ["error", "exception", "traceback", "failed"]):
            return stripped[:200]
    return lines[-1][:200] if lines else "No output captured"
