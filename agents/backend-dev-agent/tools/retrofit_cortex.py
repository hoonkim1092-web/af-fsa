
import os
import shutil
import glob

# Constants
FACTORY_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) # d:\agent-factory
AGENTS_DIR = os.path.join(FACTORY_ROOT, "agents")
CORTEX_SKILL = os.path.join(FACTORY_ROOT, "skills", "core", "cortex.py")
PROTOCOL_FILE = os.path.join(FACTORY_ROOT, "skills", "core", "learning_protocol.md")

def retrofit_agents():
    print(f"🔧 Retrofitting Agents in {AGENTS_DIR}...")
    
    # Read Protocol
    protocol_content = ""
    if os.path.exists(PROTOCOL_FILE):
        with open(PROTOCOL_FILE, 'r', encoding='utf-8') as f:
            protocol_content = f.read()
    else:
        print("❌ Protocol file not found!")
        return

    # Iterate Agents
    agents = [d for d in os.listdir(AGENTS_DIR) if os.path.isdir(os.path.join(AGENTS_DIR, d))]
    
    for agent_name in agents:
        agent_path = os.path.join(AGENTS_DIR, agent_name)
        tools_dir = os.path.join(agent_path, "tools")
        profile_path = os.path.join(agent_path, "profile.md")
        
        print(f"\nProcessing: {agent_name}")
        
        # 1. Inject Cortex Skill
        if not os.path.exists(tools_dir):
            os.makedirs(tools_dir)
            print("  - Created tools directory.")
            
        shutil.copy2(CORTEX_SKILL, os.path.join(tools_dir, "cortex.py"))
        print("  - Copied cortex.py")
        
        # 2. Inject Protocol into Profile
        if os.path.exists(profile_path):
            with open(profile_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if "Rule 0: Signature First (Absolute)" not in content:
                # If protocol exists but old version, we might want to replace it.
                # For now, let's just append the new protocol (it might duplicate the header, but ensures the rule is there).
                # Better: let's replace the whole protocol section if we can identify it.
                # Actually, simplest is to just append the Rule if it's missing.
                
                if "Cortex Learning Protocol" in content:
                     print("  - Old Protocol found. Updating...")
                     # Split and replace or just append the new rule?
                     # Let's just append the new protocol at the end for now to be safe.
                     with open(profile_path, 'a', encoding='utf-8') as f:
                        f.write("\n\n" + protocol_content)
                     print("  - Appended New Protocol (Signature Rule).")
                else: 
                     with open(profile_path, 'a', encoding='utf-8') as f:
                        f.write("\n\n" + protocol_content)
                     print("  - Injected Learning Protocol.")
            else:
                print("  - Signature Rule already present. Skipping.")
        else:
            # Create minimal profile if missing
            with open(profile_path, 'w', encoding='utf-8') as f:
                f.write(f"# Agent: {agent_name}\n\n{protocol_content}")
            print("  - Created new profile.md with Protocol")

    print("\n✅ Retrofit Complete.")

if __name__ == "__main__":
    retrofit_agents()
