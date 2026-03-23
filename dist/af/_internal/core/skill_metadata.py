from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class SkillCategory(str, Enum):
    """High-level categories used by the dynamic loader and registry."""

    CODING = "coding"
    RESEARCH = "research"
    IO = "io"
    TESTING = "testing"
    EVAL = "eval"
    PLAN = "plan"
    REVIEW = "review"
    DEBUG = "debug"


class SkillType(str, Enum):
    """Supported skill package kinds."""

    ACTION = "action"
    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    HYBRID = "hybrid"
    BUNDLE = "bundle"


@dataclass
class SkillMetadata:
    """Normalized metadata consumed by the registry, loader, and runner."""

    skill_id: str
    name: str = ""
    version: str = "0.1.0"

    description: str = ""
    when_to_use: str = ""
    when_NOT_to_use: str = ""
    use_case_examples: list[str] = field(default_factory=list)

    category: SkillCategory = SkillCategory.CODING
    skill_type: SkillType = SkillType.ACTION

    when_to_use_keywords: list[str] = field(default_factory=list)
    semantic_tags: list[str] = field(default_factory=list)

    max_tokens: int = 4000
    timeout_sec: int = 30
    dependencies: list[str] = field(default_factory=list)
    incompatible_with: list[str] = field(default_factory=list)

    requires_auth: bool = False
    network_required: bool = False
    stateful: bool = False

    author: str = "agent-factory"
    tags: list[str] = field(default_factory=list)
    experimental: bool = False

    # Claude/Codex style invocation and policy controls.
    auto_invocable: bool = True
    user_invocable: bool = True
    planner_invocable: bool = True
    context_mode: str = "inline"
    preferred_model: str = ""
    preferred_agent: str = ""
    argument_hint: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    approval_required_tools: list[str] = field(default_factory=list)
    hooks: Any = field(default_factory=dict)

    # Lifecycle and observability metadata.
    lifecycle_stage: str = "active"
    has_spec: bool = False
    spec_path: str = ""
    has_evals: bool = False
    evals_path: str = ""
    source_path: str = ""
    distribution_source: str = ""

    @property
    def can_run_isolated(self) -> bool:
        """Whether the skill can be executed through an isolated runtime path."""

        return self.skill_type in {SkillType.ACTION, SkillType.TOOL, SkillType.HYBRID} or self.context_mode in {
            "fork",
            "isolated",
        }

    @property
    def is_auto_selectable(self) -> bool:
        """Whether the loader may auto-select this skill without an explicit request."""

        return self.auto_invocable and self.lifecycle_stage not in {"archived", "retired", "disabled"}

    def to_dict(self) -> dict:
        return asdict(self)



def skill_metadata(metadata: SkillMetadata):
    """Attach normalized metadata to a function or class."""

    def decorator(func_or_class):
        func_or_class.__skill_metadata__ = metadata
        return func_or_class

    return decorator



def get_skill_metadata(func_or_class) -> Optional[SkillMetadata]:
    return getattr(func_or_class, "__skill_metadata__", None)



def has_skill_metadata(func_or_class) -> bool:
    return hasattr(func_or_class, "__skill_metadata__")



def require_skill_metadata(func_or_class) -> SkillMetadata:
    metadata = get_skill_metadata(func_or_class)
    if metadata is None:
        raise ValueError(
            f"{func_or_class} is missing @skill_metadata. Dynamic skill loading requires normalized metadata."
        )
    return metadata
