import os
import sys
import shutil

# 1. Setup Env for Project Root BEFORE importing agent_launcher
base_dir = os.path.dirname(os.path.abspath(__file__))
demo_proj_dir = os.path.join(base_dir, "demo_project_memory")

if os.path.exists(demo_proj_dir):
    shutil.rmtree(demo_proj_dir)
os.makedirs(demo_proj_dir, exist_ok=True)

os.environ["AGENT_PROJECT_ROOT"] = demo_proj_dir
print(f"📂 Setting Project Root to: {demo_proj_dir}")

# 2. Import AgentRunner
# We need to add factory dir to path
sys.path.append(base_dir)
from agent_launcher import AgentRunner, ModelRouter

def run_demo():
    print("🚀 [Demo] AgentRunner Memory Test")
    
    # 3. Mock Agent Config
    # We pretend this agent has 'core_memory' installed
    agent = {
        "name": "DemoAgent",
        "role": "Assistant",
        "system_ko": "당신은 도움이 되는 AI입니다. 한국어로 대답하세요.",
        "skills": ["core_memory"], # This must match folder name in skills/
        "signature_lines": ["안녕하세요!", "무엇을 도와드릴까요?"]
    }
    
    # 4. Initialize Runner
    runner = AgentRunner(ModelRouter())
    
    # 5. Run Task
    # We ask it to save something.
    # Note: 'core_memory' skill must be in d:\agent-factory\skills\core_memory
    # (Assuming we are running this inside d:\agent-factory)
    
    task = "프로젝트 코드명을 'Omega'라고 기억해줘. 카테고리는 'secret'으로 해줘."
    print(f"\n🗣️ User: {task}")
    
    runner.run(agent, task)
    
    # 6. Verify Result
    print("\n🔎 Verifying Result...")
    found_files = []
    for root, dirs, files in os.walk(demo_proj_dir):
        for file in files:
            full_path = os.path.join(root, file)
            found_files.append(full_path)
            print(f"   📄 Found: {full_path}")
            
    expected_file = os.path.join(demo_proj_dir, "data", "memory", "secret", "Omega.json")
    if os.path.exists(expected_file):
        print("✅ [Pass] Memory file created successfully!")
        with open(expected_file, "r", encoding="utf-8") as f:
            print(f"📄 Content: {f.read()}")
    else:
        print("❌ [Fail] Memory file not found.")
        print(f"All files found: {found_files}")

if __name__ == "__main__":
    run_demo()
