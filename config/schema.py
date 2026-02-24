import os
import sys
import yaml
from typing import Dict
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

class EngineConfig(BaseModel):
    tier: int
    description: str

class SkillConfig(BaseModel):
    risk: str
    engine_id: str

class PolicyConfig(BaseModel):
    engines: Dict[str, EngineConfig] = Field(default_factory=dict)
    skills: Dict[str, SkillConfig] = Field(default_factory=dict)

class AgentFactoryConfig(BaseSettings):
    google_api_key: str = Field(..., description="Google API Key")
    openai_api_key: str = Field(..., description="OpenAI API Key")
    supabase_url: str = Field(..., description="Supabase URL")
    supabase_key: str = Field(..., description="Supabase Key")
    pythonioencoding: str = Field("utf-8")
    
    policy: PolicyConfig = Field(default_factory=PolicyConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

def load_factory_config() -> AgentFactoryConfig:
    try:
        config = AgentFactoryConfig()
        
        # Load and validate policy.yaml
        factory_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        policy_path = os.path.join(factory_root, "policy.yaml")
        if os.path.exists(policy_path):
            with open(policy_path, "r", encoding="utf-8") as f:
                policy_data = yaml.safe_load(f) or {}
                config.policy = PolicyConfig(**policy_data)
                
        return config
    except ValidationError as e:
        print("\n[FATAL ERROR] Agent-Factory Configuration logic is invalid. Boot halted.\n")
        print(e)
        sys.exit(1)
    except Exception as e:
        print(f"\n[FATAL ERROR] Failed to load config files: {e}\n")
        sys.exit(1)

# Initialize globally to enforce fail-fast pattern upon import
factory_config = load_factory_config()
