import os
import sys
import shutil

base_dir = os.path.dirname(os.path.abspath(__file__))
demo_proj_dir = os.path.join(base_dir, "demo_project_memory")

if os.path.exists(demo_proj_dir):
    shutil.rmtree(demo_proj_dir)
os.makedirs(demo_proj_dir, exist_ok=True)

os.environ["AGENT_PROJECT_ROOT"] = demo_proj_dir
print(f"?뱛 Setting Project Root to: {demo_proj_dir}")

sys.path.append(base_dir)
from agent_launcher import AgentRunner, ModelRouter

def run_demo():
    print("?? [Demo] AgentRunner Memory Test")
    
    agent = {
        "name": "DemoAgent",
        "role": "Assistant",
        "system_ko": "?뱀떊? ?꾩????섎뒗 AI?낅땲?? ?쒓뎅?대줈 ??듯븯?몄슂.",
        "skills": ["core_memory"], # This must match folder name in skills/
        "signature_lines": ["?덈뀞?섏꽭??", "臾댁뾿???꾩??쒕┫源뚯슂?"]
    }
    
    runner = AgentRunner(ModelRouter())
    
    
    task = "?꾨줈?앺듃 肄붾뱶紐낆쓣 'Omega'?쇨퀬 湲곗뼲?댁쨾. 移댄뀒怨좊━??'secret'?쇰줈 ?댁쨾."
    print(f"\n?뿣截?User: {task}")
    
    runner.run(agent, task)
    
    print("\n?뵊 Verifying Result...")
    found_files = []
    for root, dirs, files in os.walk(demo_proj_dir):
        for file in files:
            full_path = os.path.join(root, file)
            found_files.append(full_path)
            print(f"   ?뱞 Found: {full_path}")
            
    expected_file = os.path.join(demo_proj_dir, "data", "memory", "secret", "Omega.json")
    if os.path.exists(expected_file):
        print("??[Pass] Memory file created successfully!")
        with open(expected_file, "r", encoding="utf-8") as f:
            print(f"?뱞 Content: {f.read()}")
    else:
        print("??[Fail] Memory file not found.")
        print(f"All files found: {found_files}")

if __name__ == "__main__":
    run_demo()
