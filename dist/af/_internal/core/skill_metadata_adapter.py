from __future__ import annotations

import os
import re
from typing import Any, Optional

import yaml

from core.file_io import read_yaml
from core.skill_metadata import SkillCategory, SkillMetadata, SkillType


EVAL_FILENAMES = ("evals.yml", "evals.yaml")
PACKAGE_SPEC_FILENAMES = ("skill.yaml", "skill.yml")
GENERATED_SPEC_FILENAMES = ("skill-spec.yaml", "skill-spec.yml")


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None



def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default



def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for part in (_coerce_str(item) for item in value) if part)
    return str(value).strip()



def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[\r\n,]", value) if part.strip()]
    text = str(value).strip()
    return [text] if text else []



def _coerce_argument_hint(value: Any) -> str:
    if isinstance(value, list):
        items = [item for item in _coerce_list(value) if item]
        if not items:
            return ""
        return f"[{', '.join(items)}]"
    return _coerce_str(value)



def _merge_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    for value in values:
        for item in _coerce_list(value):
            if item not in merged:
                merged.append(item)
    return merged



def _normalize_skill_id(raw_value: Any, fallback: str = "") -> str:
    text = _coerce_str(raw_value) or _coerce_str(fallback)
    text = text.replace(" ", "-")
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-_").lower()



def _default_display_name(skill_id: str) -> str:
    return skill_id.replace("-", " ").replace("_", " ").title()



def _parse_category(category_value: Any, default: SkillCategory = SkillCategory.CODING) -> SkillCategory:
    text = _coerce_str(category_value).lower()
    if not text:
        return default
    try:
        return SkillCategory(text)
    except ValueError:
        return default



def _parse_skill_type(type_value: Any, default: SkillType = SkillType.ACTION) -> SkillType:
    text = _coerce_str(type_value).lower()
    if not text:
        return default
    try:
        return SkillType(text)
    except ValueError:
        return default



def _default_category_for_type(skill_type: SkillType) -> SkillCategory:
    return SkillCategory.PLAN if skill_type == SkillType.KNOWLEDGE else SkillCategory.CODING



def _detect_eval_manifest(skill_dir: str) -> tuple[bool, str]:
    if not skill_dir:
        return False, ""
    for filename in EVAL_FILENAMES:
        candidate = os.path.join(skill_dir, filename)
        if os.path.exists(candidate):
            return True, candidate
    return False, ""



def _detect_generated_spec(skill_dir: str) -> tuple[bool, str]:
    if not skill_dir:
        return False, ""
    for filename in GENERATED_SPEC_FILENAMES:
        candidate = os.path.join(skill_dir, filename)
        if os.path.exists(candidate):
            return True, candidate
    return False, ""



def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n)?(.*)$", content, re.DOTALL)
    if not match:
        return {}, content
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return frontmatter, match.group(2)



def _extract_markdown_description(markdown_body: str) -> str:
    for block in re.split(r"\r?\n\s*\r?\n", markdown_body or ""):
        cleaned = block.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        return re.sub(r"\s+", " ", cleaned)
    return ""



def _resolve_context_mode(*values: Any) -> str:
    text = _coerce_str(_first_non_empty(*values), "inline").lower()
    if text not in {"inline", "fork", "isolated"}:
        return "inline"
    return text



def _eval_manifest_from_fields(skill_dir: str, raw: dict[str, Any], metadata_block: dict[str, Any]) -> tuple[bool, str]:
    has_evals, evals_path = _detect_eval_manifest(skill_dir)
    declared_path = _coerce_str(
        _first_non_empty(
            raw.get("evals_path"),
            raw.get("evals"),
            raw.get("benchmark"),
            metadata_block.get("evals_path"),
        )
    )
    if not declared_path:
        return has_evals, evals_path
    resolved_path = declared_path
    if skill_dir and not os.path.isabs(declared_path):
        resolved_path = os.path.join(skill_dir, declared_path)
    return True, resolved_path



def _generated_spec_from_fields(skill_dir: str, raw: dict[str, Any], metadata_block: dict[str, Any]) -> tuple[bool, str]:
    has_spec, spec_path = _detect_generated_spec(skill_dir)
    declared_path = _coerce_str(
        _first_non_empty(
            raw.get("spec_path"),
            raw.get("skill_spec"),
            raw.get("skill-spec"),
            metadata_block.get("spec_path"),
        )
    )
    if not declared_path:
        return has_spec, spec_path
    resolved_path = declared_path
    if skill_dir and not os.path.isabs(declared_path):
        resolved_path = os.path.join(skill_dir, declared_path)
    return True, resolved_path



def _mapping_to_metadata(
    raw: dict[str, Any],
    *,
    default_skill_id: str = "",
    default_skill_type: SkillType = SkillType.ACTION,
    default_category: SkillCategory | None = None,
    skill_dir: str = "",
    source_path: str = "",
) -> SkillMetadata:
    invocation = raw.get("invocation") if isinstance(raw.get("invocation"), dict) else {}
    policy = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    distribution = raw.get("distribution") if isinstance(raw.get("distribution"), dict) else {}
    metadata_block = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}

    raw_name = _coerce_str(_first_non_empty(raw.get("name"), default_skill_id))
    skill_id = _normalize_skill_id(
        _first_non_empty(raw.get("skill_id"), raw.get("id"), raw_name, default_skill_id),
        default_skill_id,
    )
    display_name = raw_name or _default_display_name(skill_id)

    skill_type = _parse_skill_type(
        _first_non_empty(raw.get("kind"), raw.get("type"), metadata_block.get("type")),
        default_skill_type,
    )
    category = _parse_category(
        _first_non_empty(raw.get("category"), metadata_block.get("category")),
        default_category or _default_category_for_type(skill_type),
    )

    disable_model_invocation = _first_non_empty(
        raw.get("disable-model-invocation"),
        raw.get("disable_model_invocation"),
    )
    invocation_auto = invocation.get("auto")
    model_invocable = _first_non_empty(raw.get("model_invocable"), raw.get("model-invocable"))

    if disable_model_invocation is not None:
        auto_invocable = not _coerce_bool(disable_model_invocation, default=False)
    elif model_invocable is not None:
        auto_invocable = _coerce_bool(model_invocable, default=True)
    elif invocation_auto is not None:
        auto_invocable = _coerce_bool(invocation_auto, default=True)
    else:
        auto_invocable = True

    has_spec, spec_path = _generated_spec_from_fields(skill_dir, raw, metadata_block)
    has_evals, evals_path = _eval_manifest_from_fields(skill_dir, raw, metadata_block)

    return SkillMetadata(
        skill_id=skill_id,
        name=display_name,
        version=_coerce_str(raw.get("version"), "0.1.0") or "0.1.0",
        description=_coerce_str(raw.get("description")),
        when_to_use=_coerce_str(raw.get("when_to_use")),
        when_NOT_to_use=_coerce_str(_first_non_empty(raw.get("when_NOT_to_use"), raw.get("when_not_to_use"))),
        use_case_examples=_merge_lists(raw.get("use_case_examples"), raw.get("examples")),
        category=category,
        skill_type=skill_type,
        when_to_use_keywords=_merge_lists(raw.get("when_to_use_keywords"), raw.get("keywords")),
        semantic_tags=_merge_lists(raw.get("semantic_tags"), metadata_block.get("semantic_tags")),
        max_tokens=_coerce_int(raw.get("max_tokens"), 4000),
        timeout_sec=_coerce_int(raw.get("timeout_sec"), 30),
        dependencies=_coerce_list(raw.get("dependencies")),
        incompatible_with=_coerce_list(raw.get("incompatible_with")),
        requires_auth=_coerce_bool(raw.get("requires_auth"), default=False),
        network_required=_coerce_bool(raw.get("network_required"), default=False),
        stateful=_coerce_bool(raw.get("stateful"), default=False),
        author=_coerce_str(raw.get("author"), "agent-factory") or "agent-factory",
        tags=_merge_lists(raw.get("tags"), raw.get("capabilities"), metadata_block.get("tags")),
        experimental=_coerce_bool(raw.get("experimental"), default=False),
        auto_invocable=auto_invocable,
        user_invocable=_coerce_bool(
            _first_non_empty(raw.get("user-invocable"), raw.get("user_invocable"), invocation.get("user_invocable")),
            default=True,
        ),
        planner_invocable=_coerce_bool(
            _first_non_empty(
                raw.get("planner-invocable"),
                raw.get("planner_invocable"),
                invocation.get("planner_invocable"),
            ),
            default=True,
        ),
        context_mode=_resolve_context_mode(raw.get("context"), raw.get("context_mode"), invocation.get("context_mode")),
        preferred_model=_coerce_str(_first_non_empty(raw.get("model"), raw.get("preferred_model"), invocation.get("model"))),
        preferred_agent=_coerce_str(_first_non_empty(raw.get("agent"), raw.get("preferred_agent"), invocation.get("agent"))),
        argument_hint=_coerce_argument_hint(
            _first_non_empty(raw.get("argument-hint"), raw.get("argument_hint"), invocation.get("argument_hint"))
        ),
        allowed_tools=_merge_lists(
            raw.get("allowed-tools"),
            raw.get("allowed_tools"),
            raw.get("allow_tools"),
            policy.get("allowed_tools"),
        ),
        approval_required_tools=_merge_lists(
            raw.get("approval-required-tools"),
            raw.get("approval_required_tools"),
            policy.get("approval_required_tools"),
        ),
        hooks=_first_non_empty(raw.get("hooks"), raw.get("hook"), {}) or {},
        lifecycle_stage=_coerce_str(
            _first_non_empty(raw.get("lifecycle_stage"), raw.get("status"), distribution.get("maturity")),
            "active",
        ).lower()
        or "active",
        has_spec=has_spec,
        spec_path=spec_path,
        has_evals=has_evals,
        evals_path=evals_path,
        source_path=source_path,
        distribution_source=_coerce_str(_first_non_empty(raw.get("source"), distribution.get("source"))),
    )



def convert_skill_yaml_to_metadata(spec_path: str) -> Optional[SkillMetadata]:
    if not os.path.exists(spec_path):
        return None
    try:
        spec = read_yaml(spec_path)
        if not isinstance(spec, dict):
            return None
        skill_dir = os.path.dirname(spec_path)
        default_skill_id = os.path.basename(skill_dir)
        return _mapping_to_metadata(
            spec,
            default_skill_id=default_skill_id,
            default_skill_type=SkillType.ACTION,
            skill_dir=skill_dir,
            source_path=spec_path,
        )
    except Exception:
        return None



def convert_meta_yaml_to_metadata(meta_path: str) -> Optional[SkillMetadata]:
    if not os.path.exists(meta_path):
        return None
    try:
        meta = read_yaml(meta_path)
        if not isinstance(meta, dict):
            return None
        skill_dir = os.path.dirname(meta_path)
        default_skill_id = os.path.basename(skill_dir)
        return _mapping_to_metadata(
            meta,
            default_skill_id=default_skill_id,
            default_skill_type=SkillType.ACTION,
            skill_dir=skill_dir,
            source_path=meta_path,
        )
    except Exception:
        return None



def convert_skill_md_to_metadata(md_path: str, skill_id: str) -> Optional[SkillMetadata]:
    if not os.path.exists(md_path):
        return None
    try:
        with open(md_path, "r", encoding="utf-8") as handle:
            content = handle.read()

        frontmatter, markdown_body = _split_frontmatter(content)
        frontmatter.setdefault("name", skill_id)
        frontmatter.setdefault("description", _extract_markdown_description(markdown_body))
        frontmatter.setdefault("when_to_use", frontmatter.get("description", ""))

        return _mapping_to_metadata(
            frontmatter,
            default_skill_id=skill_id,
            default_skill_type=SkillType.KNOWLEDGE,
            default_category=SkillCategory.PLAN,
            skill_dir=os.path.dirname(md_path),
            source_path=md_path,
        )
    except Exception:
        return None



def convert_yaml_config_to_metadata(skill_config: dict) -> Optional[SkillMetadata]:
    if not isinstance(skill_config, dict):
        return None
    try:
        default_skill_id = _coerce_str(_first_non_empty(skill_config.get("skill_id"), skill_config.get("id"), skill_config.get("name")))
        return _mapping_to_metadata(
            skill_config,
            default_skill_id=default_skill_id,
            default_skill_type=SkillType.ACTION,
            source_path="",
        )
    except Exception:
        return None



def auto_detect_and_convert(skill_dir: str, skill_id: str) -> Optional[SkillMetadata]:
    for spec_name in PACKAGE_SPEC_FILENAMES:
        spec_path = os.path.join(skill_dir, spec_name)
        metadata = convert_skill_yaml_to_metadata(spec_path)
        if metadata:
            return metadata

    meta_path = os.path.join(skill_dir, "meta.yaml")
    metadata = convert_meta_yaml_to_metadata(meta_path)
    if metadata:
        return metadata

    for md_name in ("SKILL.md", "skill.md"):
        md_path = os.path.join(skill_dir, md_name)
        metadata = convert_skill_md_to_metadata(md_path, skill_id)
        if metadata:
            return metadata

    return None
