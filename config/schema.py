import os
import sys
import yaml
from typing import Dict
from pydantic import BaseModel, Field, ValidationError

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:
    # Fallback for environments where pydantic-settings is not installed
    class BaseSettings(BaseModel):
        pass
    class SettingsConfigDict(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

class EngineConfig(BaseModel):
    tier: int
    description: str

class SkillConfig(BaseModel):
    risk: str
    engine_id: str

class ExperimentalConfig(BaseModel):
    hashline_hints_enabled: bool = Field(default=True)
    template_input_enforced: bool = Field(default=True)

class BackgroundTasksConfig(BaseModel):
    max_concurrent_agents: int = Field(default=5)
    heartbeat_timeout_sec: int = Field(default=300)
    circuit_breaker_max_fails: int = Field(default=3)

class HooksConfig(BaseModel):
    event_bus_enabled: bool = Field(default=True)
    truncation_max_length: int = Field(default=16000)

class PolicyConfig(BaseModel):
    engines: Dict[str, EngineConfig] = Field(default_factory=dict)
    skills: Dict[str, SkillConfig] = Field(default_factory=dict)

from typing import Optional, Dict

class AgentFactoryConfig(BaseSettings):
    google_api_key: Optional[str] = Field(None, description="Google API Key")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API Key")
    supabase_url: Optional[str] = Field(None, description="Supabase URL")
    supabase_key: Optional[str] = Field(None, description="Supabase Key")
    pythonioencoding: str = Field("utf-8")
    
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    experimental: ExperimentalConfig = Field(default_factory=ExperimentalConfig)
    background_tasks: BackgroundTasksConfig = Field(default_factory=BackgroundTasksConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

def load_factory_config() -> AgentFactoryConfig:
    try:
        factory_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(factory_root, ".env"))
        except ImportError:
            pass
            
        kwargs = {
            "google_api_key": os.getenv("GOOGLE_API_KEY"),
            "openai_api_key": os.getenv("OPENAI_API_KEY"),
            "supabase_url": os.getenv("SUPABASE_URL"),
            "supabase_key": os.getenv("SUPABASE_KEY")
        }
        kwargs = {k: v for k, v in kwargs.items() if v}
        
        config = AgentFactoryConfig(**kwargs)
        
        # Load and validate policy.yaml
        policy_path = os.path.join(factory_root, "policy.yaml")
        if os.path.exists(policy_path):
            with open(policy_path, "r", encoding="utf-8") as f:
                policy_data = yaml.safe_load(f) or {}
                config.policy = PolicyConfig(**policy_data)
                
        return config
    except ValidationError as e:
        print(f"\n[WARNING] Config validation failed: {e}. Using partial default config.\n")
        # Return a partially populated config to allow utilities to function
        return AgentFactoryConfig.model_construct(
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            supabase_url=os.getenv("SUPABASE_URL"),
            supabase_key=os.getenv("SUPABASE_KEY")
        )
    except Exception as e:
        print(f"\n[WARNING] Failed to load config files: {e}\n")
        return AgentFactoryConfig.model_construct(
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )

# Initialize globally to enforce fail-fast pattern upon import
factory_config = load_factory_config()
