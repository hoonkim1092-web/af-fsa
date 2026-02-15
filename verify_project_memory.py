import os
import shutil
import subprocess
import sys

def verify_project_memory():
    # 1. Create a dummy project directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    temp_proj_dir = os.path.join(base_dir, "temp_project_v1")
    
    print(f"📁 Creating temp project dir: {temp_proj_dir}")
    if os.path.exists(temp_proj_dir):
        shutil.rmtree(temp_proj_dir)
    os.makedirs(temp_proj_dir, exist_ok=True)
    
    # 2. Run run_verify_agent.py VIA SUBPROCESS with CWD set to temp_proj_dir
    # This ensures agent_launcher.py treats temp_proj_dir as the project root
    verify_script = os.path.join(base_dir, "run_verify_agent.py")
    
    print("🚀 Running verification subprocess...")
    env = os.environ.copy()
    # Ensure PYTHONPATH includes agent-factory so it can import agent_launcher
    env["PYTHONPATH"] = base_dir + os.pathsep + env.get("PYTHONPATH", "")
    
    # We only care about the Core Memory test part of verify_agent, 
    # but run_verify_agent runs everything. That's fine.
    
    cmd = [sys.executable, verify_script]
    
    try:
        # Use simple run, capturing output to avoid pollution but printing it if needed
        p = subprocess.run(
            cmd,
            cwd=temp_proj_dir, # <--- KEY: Running FROM the temp project dir
            env=env,
            capture_output=True,
            text=True,
            timeout=60
        )
        print("--- Subprocess Output ---")
        print(p.stdout)
        print("-----------------------")
        if p.stderr:
            print("--- Subprocess Error ---")
            print(p.stderr)
            print("------------------------")
            
    except Exception as e:
        print(f"⚠️ Subprocess failed: {e}")
        return

    # 3. Check if data/memory exists in temp_proj_dir
    expected_memory_dir = os.path.join(temp_proj_dir, "data", "memory")
    print(f"🔎 Checking for memory at: {expected_memory_dir}")
    
    if os.path.exists(expected_memory_dir):
        print("✅ [Pass] Project-specific memory directory FOUND!")
        
        # Check if any json file is inside (Core Memory test creates one)
        # Verify_agent runs memory_skill.test() -> store()
        # It stores "test_key_123" -> category "test_cat"
        
        expected_file = os.path.join(expected_memory_dir, "test_cat", "test_key_123.json")
        if os.path.exists(expected_file):
             print(f"✅ [Pass] Memory file created: {expected_file}")
        else:
             print(f"❌ [Fail] Memory directory exists but file not found at {expected_file}")
             # List what IS there
             for root, dirs, files in os.walk(expected_memory_dir):
                 print(f"   Found: {root} -> {files}")
    else:
        print("❌ [Fail] Project-specific memory directory NOT found.")
        print(f"   (Did it go to default? Check {os.path.join(base_dir, 'data', 'memory')}?)")

    # Cleanup
    # shutil.rmtree(temp_proj_dir) 

if __name__ == "__main__":
    verify_project_memory()
