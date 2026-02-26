import os
import re

agents_dir = r"c:\Project\agent-factory\agents"
yaml_files = [f for f in os.listdir(agents_dir) if f.endswith(".yaml")]

def clean_yaml(content):
    # Replace the messy ' \n\n\n ... ' structure with a clean scalar block or just remove extra lines
    # This regex targets the system_prompt/system_ko block with many empty lines
    content = re.sub(r"system_prompt: '.*?'", lambda m: m.group(0).replace("\n\n", "\n").replace("' \n", "'\n"), content, flags=re.DOTALL)
    content = re.sub(r"system_ko: '.*?'", lambda m: m.group(0).replace("\n\n", "\n").replace("' \n", "'\n"), content, flags=re.DOTALL)
    # More aggressively remove multiple newlines
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content

for filename in yaml_files:
    path = os.path.join(agents_dir, filename)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    cleaned = clean_yaml(content)
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(cleaned)
    print(f"Cleaned {filename}")
