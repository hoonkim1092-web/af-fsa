import sys
import os
import yaml
import importlib.util

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(BASE_DIR, "skills")
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")

def load_skill_module(skill_id):
    skill_path = os.path.join(SKILLS_DIR, skill_id, "skill.py")
    if not os.path.exists(skill_path):
        print(f"❌ Skill not found: {skill_id}")
        return None
    
    spec = importlib.util.spec_from_file_location("skill", skill_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def run_tests():
    print("🧪 Verifying Saiba Midori's Skills...\n")
    
    skills = [
        "generate_image", 
        "create_design_system", 
        "perform_web_design_review", 
        "user_flow_optimization"
    ]
    
    ctx = {"artifacts_dir": ARTIFACTS_DIR}
    if not os.path.exists(ARTIFACTS_DIR):
        os.makedirs(ARTIFACTS_DIR)

    all_passed = True
    for skill_id in skills:
        print(f"Testing {skill_id}...")
        module = load_skill_module(skill_id)
        if not module:
            all_passed = False
            continue
            
        try:
            if hasattr(module, 'test'):
                result = module.test(ctx)
                if result.get("ok"):
                    print(f"✅ {skill_id}: OK")
                else:
                    print(f"❌ {skill_id}: Failed - {result}")
                    all_passed = False
            else:
                print(f"⚠️ {skill_id}: No test() function found")
        except Exception as e:
            print(f"❌ {skill_id}: Exception - {e}")
            all_passed = False
        print("-" * 30)

    if all_passed:
        print("\n🎉 All skills verified successfully!")
    else:
        print("\n💥 Some skills failed verification.")

if __name__ == "__main__":
    run_tests()
