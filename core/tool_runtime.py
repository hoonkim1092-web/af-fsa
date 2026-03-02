import inspect
import functools
import re
from typing import Callable, List, Dict
from core.registry import ToolRegistry

def safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return (t[:60] if t else "skill")

class ToolRuntimeWrapper:
    """
    Extracts the legacy `_build_tool_functions` logic from AgentFactory
    to convert raw Python module methods (Skills) into standard tool
    functions bindable to the ToolRegistry and LLM executor.
    """
    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def build_tool_functions(self, module_list: List, ctx: Dict, policy: Dict, is_allowed_fn: Callable = None) -> List[Callable]:
        funcs = []
        for m in module_list:
            sid = getattr(m, "__skill_id__", "unknown")
            methods = inspect.getmembers(m, predicate=inspect.isfunction)
            if not methods:
                classes = inspect.getmembers(m, predicate=inspect.isclass)
                for cname, cls in classes:
                    if cname.endswith("Skill") or cname == "Skill":
                        inst = cls()
                        methods = inspect.getmembers(inst, predicate=inspect.ismethod)
                        break

            for fname, fn in methods:
                if fname.startswith("_"):
                    continue
                if is_allowed_fn and not is_allowed_fn(policy, sid, fname):
                    continue
                wrapped = self._wrap_tool(sid, fname, fn, ctx)
                funcs.append(wrapped)
        return funcs

    def _wrap_tool(self, module_name: str, func_name: str, fn: Callable, ctx: dict) -> Callable:
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        needs_ctx = False
        if params and params[0].name == "ctx":
            needs_ctx = True
            params = params[1:]

        tool_name = safe_id(f"{module_name}_{func_name}")

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if needs_ctx:
                merged_ctx = dict(ctx)
                merged_ctx.update(kwargs)
                return fn(merged_ctx, *args)
            return fn(*args, **kwargs)

        wrapper.__name__ = tool_name
        wrapper._skill_id = module_name
        wrapper.__doc__ = fn.__doc__ or f"Tool: {tool_name}"
        new_sig = sig.replace(parameters=params)
        wrapper.__signature__ = new_sig
        return wrapper

    def build_registry(self, module_list: List, ctx: Dict, policy: Dict, is_allowed_fn: Callable = None) -> ToolRegistry:
        """Adapts legacy modules into the precise V2 Tool Registry"""
        agent_name = ctx.get("agent", {}).get("name", "unknown")
        registry = ToolRegistry(agent_name=agent_name, factory_root=self.base_dir)
        
        legacy_tool_funcs = self.build_tool_functions(module_list, ctx, policy, is_allowed_fn)
        
        # [SMART NAMING] Check for collisions to allow shorter tool names
        # If we only have one 'write_file' across all skills, we don't need 'file_handler_write_file'
        name_counts = {}
        for fn in legacy_tool_funcs:
            real_fname = fn.__name__.split("_", 1)[-1] if "_" in fn.__name__ else fn.__name__
            name_counts[real_fname] = name_counts.get(real_fname, 0) + 1
            
        for fn in legacy_tool_funcs:
            real_fname = fn.__name__.split("_", 1)[-1] if "_" in fn.__name__ else fn.__name__
            # Only use short name if NO COLLISION and name is descriptive
            if name_counts.get(real_fname) == 1 and len(real_fname) > 3:
                registry.mount_tool(real_fname, fn)
            else:
                registry.mount_tool(fn.__name__, fn)
            
        return registry
