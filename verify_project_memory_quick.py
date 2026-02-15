import os
import shutil
import sys

# agent_launcher.py가 있는 경로를 sys.path에 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# We need to import agent_launcher to trigger the DATA_DIR logic, 
# even if we don't use AgentFactory directly.
import agent_launcher 

def verify_project_memory_only():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_proj_dir = os.path.join(base_dir, "temp_project_quick")
    
    print(f"📁 Creating temp project dir: {temp_proj_dir}")
    if os.path.exists(temp_proj_dir):
        shutil.rmtree(temp_proj_dir)
    os.makedirs(temp_proj_dir, exist_ok=True)
    
    # Simulate running in the project dir
    print(f"🔄 Changing CWD to {temp_proj_dir}")
    original_cwd = os.getcwd()
    os.chdir(temp_proj_dir)
    
    try:
        # Force reload agent_launcher to pick up the new CWD
        # (This is tricky because module-level code runs once on import.
        # But we modified agent_launcher to check env var or CWD.
        # Since it's already imported, we might need to reload it or 
        # manually check the logic if we were running as a script.)
        
        # ACTUALLY, checking the source code of agent_launcher.py:
        # _proj = os.environ.get("AGENT_PROJECT_ROOT") or os.getcwd()
        # PROJECT_ROOT = os.path.abspath(_proj)
        # DATA_DIR = os.path.join(PROJECT_ROOT, "data")
        
        # This module-level code ran when we imported it above.
        # So DATA_DIR is already set to whatever CWD was when we imported it.
        # To test effectively, we should run this as a subprocess so it imports freshly.
        pass
    finally:
        os.chdir(original_cwd)
        # shutil.rmtree(temp_proj_dir)

if __name__ == "__main__":
    # verification is best done via subprocess to ensure clean module state
    import subprocess
    
    temp_proj_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_project_quick")
    os.makedirs(temp_proj_dir, exist_ok=True)
    
    verifier_code = """
import os
import sys

# Add factory path
factory_path = r"{factory_path}"
sys.path.append(factory_path)

# Verify CWD is correct
print(f"CWD: {os.getcwd()}")

# Import launcher to trigger path resolution
import agent_launcher
print(f"Resolved DATA_DIR: {agent_launcher.DATA_DIR}")

# Import memory skill
sys.path.append(os.path.join(factory_path, "skills", "core_memory"))
import skill as memory_skill

# Run test
ctx = {"data_dir": agent_launcher.DATA_DIR}
print(f"Testing with Context Data Dir: {ctx['data_dir']}")
res = memory_skill.test(ctx)
print(f"Test Result: {res}")
"""
    factory_path = os.path.dirname(os.path.abspath(__file__))
    verifier_script = os.path.join(temp_proj_dir, "run_quick_test.py")
    
    # Fix paths for string formatting
    code = verifier_code.replace("{factory_path}", factory_path.replace("\\", "/"))
    
    with open(verifier_script, "w", encoding="utf-8") as f:
        f.write(code)
        
    print("🚀 Running quick verification subprocess...")
    p = subprocess.run(
        [sys.executable, "run_quick_test.py"],
        cwd=temp_proj_dir,
        capture_output=True,
        text=True
    )
    
    print("--- Output ---")
    print(p.stdout)
    print("--- Error ---")
    print(p.stderr)
    
    # Check if data/memory was created in temp_proj_dir
    expected_memory = os.path.join(temp_proj_dir, "data", "memory")
    if os.path.exists(expected_memory):
        print(f"✅ [Pass] Local memory directory created: {expected_memory}")
    else:
        print(f"❌ [Fail] Local memory directory NOT found at {expected_memory}")

