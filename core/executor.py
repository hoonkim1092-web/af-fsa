import json
import os
import subprocess
import sys
import tempfile
import time

from core.policy import resolve_runtime_mode
from core.providers.registry import strip_engine_api_keys


def run_skill_safely(
    role: str,
    skill_path: str,
    args: list,
    timeout: int = 30,
    workdir: str | None = None,
    runtime_mode: str | None = None,
) -> dict:
    """Execute a skill script with timeout, isolated mode, and structured output."""
    start_time = time.time()
    mode = resolve_runtime_mode(runtime_mode or "safe")
    isolated = mode in ("safe", "strict")

    skill_abspath = os.path.abspath(skill_path)
    cmd = [sys.executable] + (["-I"] if isolated else []) + [skill_abspath] + list(args)

    print(f"\n[EXECUTOR] Sandboxing execution for Role: [{role}] | Skill: [{skill_abspath}]")
    print(f"[EXECUTOR] Payload args: {args}")

    result = {
        "status": "failed",
        "stdout": "",
        "stderr": "",
        "execution_time_ms": 0,
        "error": "",
    }

    allowlist = {
        "PATH",
        "SYSTEMROOT",
        "USERPROFILE",
        "TEMP",
        "TMP",
        "PYTHONUTF8",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "PYTHONPATH",
    }
    child_env = {k: v for k, v in os.environ.items() if k in allowlist} if isolated else dict(os.environ)
    child_env = strip_engine_api_keys(child_env)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_run_dir = os.path.join(base_dir, "runs")
    target_cwd = workdir or default_run_dir
    fallback_cwd = os.path.join(tempfile.gettempdir(), "agent-factory-runs")

    try:
        os.makedirs(target_cwd, exist_ok=True)
        probe = os.path.join(target_cwd, ".executor_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except Exception:
        os.makedirs(fallback_cwd, exist_ok=True)
        target_cwd = fallback_cwd

    try:
        completed = subprocess.run(
            cmd,
            env=child_env,
            cwd=target_cwd,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
        )

        result["stdout"] = (completed.stdout or "").strip()
        result["stderr"] = (completed.stderr or "").strip()

        if completed.returncode == 0:
            result["status"] = "success"
            print("[EXECUTOR] Execution successful. Exit code 0.")
        else:
            result["error"] = f"Process exited with code {completed.returncode}"
            print(f"[EXECUTOR] Execution failed. Exit code {completed.returncode}.")

    except subprocess.TimeoutExpired as exc:
        if isinstance(exc.stdout, str):
            result["stdout"] = exc.stdout.strip()
        if isinstance(exc.stderr, str):
            result["stderr"] = exc.stderr.strip()
        result["error"] = f"Timeout constraint ({timeout}s) exceeded. Process terminated."
        print("[EXECUTOR] TIMEOUT EXCEEDED. Process killed.")

    except Exception as exc:
        result["error"] = str(exc)
        print(f"[EXECUTOR] FATAL ERROR: {exc}")

    finally:
        end_time = time.time()
        result["execution_time_ms"] = int((end_time - start_time) * 1000)
        print(f"[EXECUTOR STATS] Elapsed: {result['execution_time_ms']}ms | Status: {result['status']}")

    return result


if __name__ == "__main__":
    if len(sys.argv) > 2:
        test_role = sys.argv[1]
        test_skill = sys.argv[2]
        test_args = sys.argv[3:]
        out = run_skill_safely(test_role, test_skill, test_args)
        print("\n--- FINAL OUTPUT ---")
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("Usage: python executor.py <role> <skill_path> [args...]")
