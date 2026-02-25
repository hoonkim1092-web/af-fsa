import os
import glob
from typing import List, Dict, Callable
from config.schema import factory_config

# Dummy type hint representing a callable tool
ToolFunction = Callable

class ToolRegistry:
    """
    V2.0 Dynamic Tool Registry
    Replaces static/flat tool loading with contextual mounting.
    """
    def __init__(self, agent_name: str, factory_root: str):
        self.agent_name = agent_name
        self.factory_root = factory_root
        self.agent_tools_dir = os.path.join(self.factory_root, "agents", agent_name, "tools")
        self._mounted_tools: Dict[str, ToolFunction] = {}

    def discover_local_skills(self) -> List[str]:
        """Scans the agent's tool directory for available python skills."""
        if not os.path.exists(self.agent_tools_dir):
            return []
        
        skills = []
        for file in glob.glob(os.path.join(self.agent_tools_dir, "*.py")):
            base_name = os.path.splitext(os.path.basename(file))[0]
            if base_name != "__init__":
                skills.append(base_name)
        return skills

    def mount_tool(self, tool_name: str, tool_func: ToolFunction):
        """Dynamically loads a skill function into the LLM context."""
        self._mounted_tools[tool_name] = tool_func

    def get_active_tools(self) -> List[ToolFunction]:
        """Returns the list of currently mounted tools for the LLM."""
        return list(self._mounted_tools.values())
