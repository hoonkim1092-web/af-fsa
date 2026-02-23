import os
import time
import pytest
from core.executor import run_skill_safely

def test_executor_success(tmp_path):
    # Create a simple python script that prints "hello world"
    script = tmp_path / "test_success_skill.py"
    script.write_text("print('hello world')")
    
    result = run_skill_safely("TestRole", str(script), [])
    assert result["status"] == "success"
    assert "hello world" in result["stdout"]
    assert result["execution_time_ms"] > 0

def test_executor_timeout(tmp_path):
    # Create a script that sleeps for 3 seconds
    script = tmp_path / "test_timeout_skill.py"
    script.write_text("import time\ntime.sleep(3)")
    
    # Run with a 1 second timeout
    result = run_skill_safely("TestRole", str(script), [], timeout=1)
    
    # Should be killed due to timeout
    assert result["status"] == "failed"
    assert "Timeout constraint" in result["error"]

def test_executor_failure(tmp_path):
    # Create a script that raises an error
    script = tmp_path / "test_fail_skill.py"
    script.write_text("raise ValueError('Intentional Error')")
    
    result = run_skill_safely("TestRole", str(script), [])
    assert result["status"] == "failed"
    assert "Process exited with code 1" in result["error"]
    assert "Intentional Error" in result["stderr"]
