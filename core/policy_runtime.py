import os
from config.schema import factory_config

def resolve_quality_gate_policy(target_skill: str, config=None) -> dict:
    """
    Returns the resolved quality gate policy for a given skill.
    Uses centralized schema definition as defaults.
    """
    if config is None:
        config = factory_config
        
    cfg_dict = config.dict() if hasattr(config, 'dict') else config
    global_qg = cfg_dict.get("quality_gate", {})
    fallback = global_qg.get("fallback", "manual")
    timeout = global_qg.get("timeout_sec", 60)
    
    return {
        "fallback": fallback,
        "timeout_sec": timeout,
        "resolved_for": target_skill
    }

class PolicyRuntime:
    """
    Extracts policy evaluation logic out of AgentFactory.
    Manages resolving overrides, runtime rules, and quality gates.
    """
    def __init__(self, base_dir: str):
        self.base_dir = base_dir

    def resolve_agent_policy(self, agent: dict) -> dict:
        """
        Merge global factory constraints with agent-specific runtime rules.
        """
        runtime_rules = agent.get("runtime_rules", {})
        cfg_dict = factory_config.dict() if hasattr(factory_config, 'dict') else factory_config
        merged = dict(cfg_dict.get("global_policy", {}))
        merged.update(runtime_rules)
        return merged
        
    def evaluate_quality_gate(self, result: dict) -> bool:
        """
        Evaluate tool execution results against strict quality gates.
        """
        return result.get("ok", False) and result.get("exit_code", 0) == 0
