from __future__ import annotations

from core.external_skill_source_ids import legacy_external_source_ids, normalize_external_source_id


def safe_id(text: str) -> str:
    value = (text or "").strip().lower()
    chars = []
    for ch in value:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            chars.append(ch)
        else:
            chars.append("_")
    normalized = "".join(chars)
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = normalized.strip("_")
    return normalized or "skill"


def infer_source_id_from_candidate_key(raw_key: str | None, skill_id: str | None) -> str:
    key = safe_id(str(raw_key or ""))
    sid = safe_id(str(skill_id or ""))
    if not key or not sid or key == sid:
        return ""

    suffix = f"_{sid}"
    if not key.endswith(suffix):
        return ""

    prefix = key[: -len(suffix)]
    return normalize_external_source_id(prefix, default="") if prefix else ""


def canonical_install_candidate_key(
    source_id: str | None,
    skill_id: str | None,
    raw_key: str | None = None,
) -> str:
    sid = safe_id(str(skill_id or ""))
    source = normalize_external_source_id(source_id, default="registry")
    if source == "registry":
        return safe_id(str(raw_key or sid))
    return safe_id(f"{source}__{sid}")


def legacy_install_candidate_keys(source_id: str | None, skill_id: str | None) -> list[str]:
    sid = safe_id(str(skill_id or ""))
    if not sid:
        return []

    keys: list[str] = []
    for legacy_source_id in legacy_external_source_ids(source_id):
        legacy_key = safe_id(f"{legacy_source_id}__{sid}")
        if legacy_key and legacy_key not in keys:
            keys.append(legacy_key)
    return keys


def normalize_install_candidate_item(
    raw_key: str,
    raw_value,
    *,
    default_source: str = "registry",
) -> tuple[str, dict] | None:
    if isinstance(raw_value, str):
        path = raw_value.strip()
        skill_id = safe_id(raw_key)
        if not path or not skill_id:
            return None
        source_id = infer_source_id_from_candidate_key(raw_key, skill_id) or default_source
        normalized_source_id = normalize_external_source_id(source_id, default=default_source)
        return (
            canonical_install_candidate_key(normalized_source_id, skill_id, raw_key),
            {"id": skill_id, "path": path, "source_id": normalized_source_id},
        )

    if not isinstance(raw_value, dict):
        return None

    item = dict(raw_value)
    skill_id = safe_id(
        str(item.get("id") or item.get("skill_id") or item.get("name") or raw_key)
    )
    if not skill_id:
        return None

    source_hint = (
        item.get("source_id")
        or item.get("source")
        or infer_source_id_from_candidate_key(raw_key, skill_id)
        or default_source
    )
    normalized_source_id = normalize_external_source_id(str(source_hint), default=default_source)
    item["id"] = skill_id
    item["source_id"] = normalized_source_id
    item.pop("source", None)
    return canonical_install_candidate_key(normalized_source_id, skill_id, raw_key), item


def normalize_install_candidate_collection(raw, *, default_source: str = "registry") -> dict:
    out: dict = {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = [
            (
                str(item.get("id") or item.get("skill_id") or item.get("name") or f"cand_{index}"),
                item,
            )
            for index, item in enumerate(raw)
            if isinstance(item, dict)
        ]
    else:
        return out

    for raw_key, raw_value in items:
        normalized = normalize_install_candidate_item(
            str(raw_key),
            raw_value,
            default_source=default_source,
        )
        if normalized:
            out[normalized[0]] = normalized[1]
    return out
