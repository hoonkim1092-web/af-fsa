import os
import shutil

def main():
    with open('agent_launcher.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find boundaries
    def find_line(prefix):
        for i, l in enumerate(lines):
            if l.startswith(prefix):
                return i
        return -1

    config_start = find_line("global_project_root = os.getenv")
    config_end = find_line("for d in [PROJECTS_DIR, ") # line 108

    utils_start = find_line("def now_iso() -> str:") # line 183
    utils_end = find_line("class ModelRouter:") - 3 # line 419

    modelrouter_start = find_line("class ModelRouter:")
    modelrouter_end = find_line("BANNED_IMPORT_TOPS = {") - 3

    gitmgr_start = find_line("class GitManager:")
    agentrunner_end = find_line("class AgentFactory:") - 3

    if any(x < 0 for x in [config_start, config_end, utils_start, utils_end, modelrouter_start, modelrouter_end, gitmgr_start, agentrunner_end]):
        print(f"Could not find all boundaries {config_start}, {config_end}, {utils_start}, {utils_end}, {modelrouter_start}, {modelrouter_end}, {gitmgr_start}, {agentrunner_end}")
        return

    # 1. core/config_paths.py
    config_code = (
        "import os\n"
        "import re\n"
        "import google.generativeai as genai\n"
        "from config.schema import factory_config\n\n"
        "def _boot_safe_id(text: str) -> str:\n"
        "    t = (text or \"\").strip().lower()\n"
        "    t = re.sub(r\"[^a-z0-9_]+\", \"_\", t)\n"
        "    t = re.sub(r\"_+\", \"_\", t).strip(\"_\")\n"
        "    return t or \"default\"\n\n"
        + "".join(lines[config_start:config_end+2]).replace("os.path.dirname(os.path.abspath(__file__))", "os.path.dirname(os.path.dirname(os.path.abspath(__file__)))") # Include the for-loop of makedirs
    )
    with open('core/config_paths.py', 'w', encoding='utf-8') as f:
        f.write(config_code)

    # 2. core/utils.py
    utils_code = (
        "import os\n"
        "import re\n"
        "import json\n"
        "import yaml\n"
        "import hashlib\n"
        "from datetime import datetime\n"
        "from core.config_paths import *\n\n"
        + "".join(lines[utils_start:utils_end+1])
        + "\n__all__ = [name for name in dir() if not name.startswith('__')]\n"
    )
    with open('core/utils.py', 'w', encoding='utf-8') as f:
        f.write(utils_code)

    # 3. core/agent_runner.py
    runner_code = (
        "import os\n"
        "import time\n"
        "import json\n"
        "import subprocess\n"
        "import sys\n"
        "import google.generativeai as genai\n"
        "try:\n"
        "    from openai import OpenAI\n"
        "except Exception:\n"
        "    OpenAI = None\n\n"
        "from core.config_paths import *\n"
        "from core.utils import *\n"
        "from core.registry import ToolRegistry\n"
        "from core.tool_runtime import ToolRuntimeWrapper\n"
        "from core.policy_runtime import PolicyRuntime\n"
        "from core.hooks.event_bus import HookEventBus, IntentGateHook, TodoContinuationEnforcer, ToolOutputTruncator\n"
        "from core.llm_engine import get_best_model\n\n"
        + "".join(lines[modelrouter_start:modelrouter_end+1])
        + "\n"
        + "".join(lines[gitmgr_start:agentrunner_end+1])
    )
    with open('core/agent_runner.py', 'w', encoding='utf-8') as f:
        f.write(runner_code)

    # 4. Modify agent_launcher.py
    new_lines = []
    i = 0
    while i < len(lines):
        # Skip the _boot_safe_id def inside launcher because it's now in config_paths
        if lines[i].startswith("def _boot_safe_id("):
            while i < len(lines) and not lines[i].startswith("_proj_id_env ="):
                i += 1
            if i < len(lines):
                i += 0  # just to align mentally, it continues loop at next line? No, wait!
                # If we skip until '_proj_id_env =', this line itself needs to be processed.
                # So we DO NOT 'continue', we just let the logic below process `lines[i]` (which is _proj_id_env=)
                # Wait, what if _proj_id_env is part of config_start? Yes, config_start = 62, `def _boot_safe_id` = 78.
                # If config_start is already processed and removed, this is dead code as `i` jumps to `config_end+2`.
                pass

        if i == config_start:
            new_lines.append("from core.config_paths import *\n")
            i = config_end + 2
            continue
        if i == utils_start:
            new_lines.append("from core.utils import *\n")
            i = utils_end + 1
            continue
        if i == modelrouter_start:
            # Add import for AgentRunner, ModelRouter, GitManager here
            new_lines.append("from core.agent_runner import ModelRouter, AgentRunner, GitManager\n")
            i = modelrouter_end + 1
            continue
        if i == gitmgr_start:
            i = agentrunner_end + 1
            continue
        
        new_lines.append(lines[i])
        i += 1

    shutil.copy('agent_launcher.py', 'agent_launcher.py.bak')
    with open('agent_launcher.py', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    print(f"Partitioning complete! Lines: {len(new_lines)}")

if __name__ == '__main__':
    main()
