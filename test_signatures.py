
import os
import sys

# Add the factory root to path if needed
sys.path.append(os.getcwd())

import agent_launcher
import factory_manager

def test_signatures():
    print("=== Testing Agent Signature Lines ===\n")
    
    # Test agents to verify
    agent_names = ["himari", "tanjiro_logimind_planning_director", "saiba_midori", "deadbyte", "lilith"]
    
    for name in agent_names:
        yaml_path = f"agents/{name}.yaml"
        if not os.path.exists(yaml_path):
            print(f"Skipping {name} (YAML not found)")
            continue
            
        print(f"--- Agent: {name} ---")
        try:
            import yaml
            with open(yaml_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            
            # Use the launcher's utility
            sig = agent_launcher.get_random_signature(config)
            if sig:
                print(f"Random Signature: \"{sig}\"")
                # Also test the printer
                agent_launcher.print_agent_msg(config.get("name", name), "작업을 시작합니다.", sig)
            else:
                print("No signature lines found for this agent.")
        except Exception as e:
            print(f"Error testing {name}: {e}")
        print("\n")

if __name__ == "__main__":
    # Standardize encoding
    sys.stdout.reconfigure(encoding='utf-8')
    test_signatures()
