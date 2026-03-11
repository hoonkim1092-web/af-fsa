from unittest.mock import MagicMock
import os
import json
import traceback
import subprocess
from skills.eval.langsmith_eval import trace_execution, summarize_failure, generate_eval_dataset

def main():
    print("🚀 [Step 1] Developer Agent가 코드를 실행합니다 (Execute)")
    script_path = "test_fallback.py"
    output_log_path = ".system_generated/logs/trace_123.log"
    feedback_path = ".system_generated/feedback_loop/feedback.json"
    test_path = "tests/test_fallback_auto_gen.py"

    ctx = {"workspace": os.path.abspath(os.path.join(os.path.dirname(__file__)))}
    
    # Run the fallback script and capture output (simulate Developer Agent)
    process = subprocess.run(["python", script_path], capture_output=True, text=True)
    
    # Save the real err output to trace log manually for our demonstration script
    full_output_log_path = os.path.join(ctx["workspace"], output_log_path)
    os.makedirs(os.path.dirname(full_output_log_path), exist_ok=True)
    
    trace_content = f"STDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
    with open(full_output_log_path, "w", encoding="utf-8") as f:
        f.write(trace_content)

    print("📄 [Step 2] Evaluator Agent 깨어남: trace_execution 스킬 발동 (Trace)")
    print(f"[Trace Content Sample]:\n{trace_content[-200:]}...")

    if process.returncode != 0:
        print("\n💥 에러 감지됨!")
        print("💡 [Step 3] Evaluator Agent: summarize_failure 스킬 발동 (Summarize target)")
        
        # Real call to the skill we integrated
        # For simulation, since our mockup just dumps a static JSON, we inject a real reason:
        summary_result_msg = summarize_failure(ctx, output_log_path, feedback_path)
        
        # Manually alter the static json out to simulate actual analysis for the user viewing
        full_out_path = os.path.join(ctx["workspace"], feedback_path)
        summary_data = {
            "status": "failed",
            "reason": extract_reasoning(process.stderr),
            "suggested_fix": "Add a check to prevent division by zero in divide_numbers()."
        }
        with open(full_out_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
            
        print(summary_result_msg)
        print(f"[Feedback Generated]: {json.dumps(summary_data, indent=2, ensure_ascii=False)}")

        print("\n🛠️ [Step 4] Evaluator Agent: generate_eval_dataset 스킬 발동 (Datasets)")
        dataset_msg = generate_eval_dataset(ctx, feedback_path, test_path)
        print(dataset_msg)
        
        full_test_path = os.path.join(ctx["workspace"], test_path)
        with open(full_test_path, "r", encoding="utf-8") as f:
            print(f"[Generated Test Code]:\n{f.read()}")
            
        print("\n🔁 [Step 5] 재귀 (Reflect): 이제 Developer Agent가 이 피드백을 읽고 코드를 보완합니다!")

def extract_reasoning(stderr_str):
    for line in reversed(stderr_str.strip().split("\\n")):
        if line.strip():
            return line.strip()
    return "Unknown Error"

if __name__ == "__main__":
    main()
