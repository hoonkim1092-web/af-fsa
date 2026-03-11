from __future__ import annotations

import argparse
import os
from typing import Any

import yaml

from core.external_skill_source_ids import normalize_external_source_id
from core.install_candidate_utils import normalize_install_candidate_collection
from core.utils import safe_id


SOURCE_CACHE_SEGMENTS = {
    "claude_repo": "claude",
    "codex_repo": "codex",
}


def _cache_root_for_source(cache_dir: str, source_id: str) -> str:
    segment = SOURCE_CACHE_SEGMENTS.get(source_id, source_id)
    return os.path.join(cache_dir, segment)


def _candidate_key(source_id: str, skill_id: str) -> str:
    return safe_id(f"{source_id}_{skill_id}")


def _portable_candidate_path(root_dir: str, path_text: str) -> str:
    abs_path = os.path.abspath(path_text)
    root_abs = os.path.abspath(root_dir)
    try:
        rel_path = os.path.relpath(abs_path, root_abs)
        if not rel_path.startswith(".."):
            return rel_path.replace("\\", "/")
    except ValueError:
        pass
    return abs_path.replace("\\", "/")


def _read_yaml_file(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return loaded if isinstance(loaded, dict) else {}


def _iter_repo_dirs(cache_dir: str, source_id: str) -> list[str]:
    source_root = _cache_root_for_source(cache_dir, source_id)
    if not os.path.isdir(source_root):
        return []
    out: list[str] = []
    for entry in sorted(os.listdir(source_root)):
        repo_dir = os.path.join(source_root, entry)
        if os.path.isdir(repo_dir):
            out.append(repo_dir)
    return out


def _candidate_from_manifest(
    *,
    root_dir: str,
    source_id: str,
    repo_dir: str,
    raw_key: str,
    raw_item: Any,
) -> dict[str, Any] | None:
    normalized = normalize_install_candidate_collection({raw_key: raw_item}, default_source=source_id)
    if not normalized:
        return None

    _canonical_key, item = next(iter(normalized.items()))
    skill_id = safe_id(str(item.get("id") or ""))
    rel_path = str(item.get("path") or "").strip().replace("\\", "/")
    if not skill_id or not rel_path:
        return None

    abs_path = os.path.normpath(os.path.join(repo_dir, rel_path))
    if not os.path.exists(abs_path):
        return None

    return {
        "id": skill_id,
        "name": str(item.get("name") or skill_id),
        "path": _portable_candidate_path(root_dir, abs_path),
        "source_id": source_id,
        "source_repo": os.path.basename(repo_dir),
        "source_url": str(item.get("source_url") or ""),
        "capabilities": [safe_id(str(value)) for value in (item.get("capabilities") or []) if safe_id(str(value))],
    }


def _scan_manifest_candidates(root_dir: str, cache_dir: str, source_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for repo_dir in _iter_repo_dirs(cache_dir, source_id):
        manifest_path = os.path.join(repo_dir, "skill_candidates.yaml")
        manifest = _read_yaml_file(manifest_path)
        raw_candidates = manifest.get("install_candidates", {})
        if not isinstance(raw_candidates, dict):
            continue
        for raw_key, raw_item in raw_candidates.items():
            candidate = _candidate_from_manifest(
                root_dir=root_dir,
                source_id=source_id,
                repo_dir=repo_dir,
                raw_key=str(raw_key),
                raw_item=raw_item,
            )
            if candidate:
                out.append(candidate)
    return out


def _python_candidate(root_dir: str, source_id: str, repo_dir: str, file_path: str) -> dict[str, Any] | None:
    _ = root_dir
    filename = os.path.basename(file_path)
    if not filename.lower().endswith(".py"):
        return None
    if filename.startswith("_"):
        return None
    skill_id = safe_id(os.path.splitext(filename)[0])
    if not skill_id:
        return None
    return {
        "id": skill_id,
        "name": skill_id,
        "path": _portable_candidate_path(root_dir, file_path),
        "source_id": source_id,
        "source_repo": os.path.basename(repo_dir),
        "source_url": "",
        "capabilities": [skill_id],
    }


def _markdown_candidate(root_dir: str, source_id: str, repo_dir: str, dir_path: str) -> dict[str, Any] | None:
    _ = root_dir
    skill_id = safe_id(os.path.basename(dir_path))
    if not skill_id:
        return None
    return {
        "id": skill_id,
        "name": skill_id,
        "path": _portable_candidate_path(root_dir, dir_path),
        "source_id": source_id,
        "source_repo": os.path.basename(repo_dir),
        "source_url": "",
        "capabilities": [skill_id],
    }


def _scan_fallback_candidates(root_dir: str, cache_dir: str, source_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for repo_dir in _iter_repo_dirs(cache_dir, source_id):
        for current_root, dirnames, filenames in os.walk(repo_dir):
            dirnames[:] = [name for name in dirnames if not name.startswith(".") and name != "__pycache__"]
            for filename in sorted(filenames):
                candidate = _python_candidate(
                    root_dir,
                    source_id,
                    repo_dir,
                    os.path.join(current_root, filename),
                )
                if candidate:
                    out.append(candidate)
            for dirname in sorted(dirnames):
                skill_dir = os.path.join(current_root, dirname)
                if any(
                    os.path.exists(os.path.join(skill_dir, marker))
                    for marker in ("SKILL.md", "skill.md")
                ):
                    candidate = _markdown_candidate(root_dir, source_id, repo_dir, skill_dir)
                    if candidate:
                        out.append(candidate)
    return out


def discover_external_candidates(
    *,
    root_dir: str,
    cache_dir: str,
    source_ids: list[str] | None = None,
    scan_python: bool = False,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    requested = list(source_ids or SOURCE_CACHE_SEGMENTS.keys())
    candidates: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []

    for raw_source_id in requested:
        source_id = normalize_external_source_id(raw_source_id, default="")
        if not source_id:
            continue
        discovered = _scan_fallback_candidates(root_dir, cache_dir, source_id) if scan_python else _scan_manifest_candidates(root_dir, cache_dir, source_id)
        for item in discovered:
            skill_id = safe_id(str(item.get("id") or ""))
            if not skill_id:
                continue
            key = _candidate_key(source_id, skill_id)
            if key in candidates:
                duplicates.append(
                    {
                        "candidate_key": key,
                        "kept_repo": str(candidates[key].get("source_repo") or ""),
                        "skipped_repo": str(item.get("source_repo") or ""),
                    }
                )
                continue
            candidates[key] = item
    return candidates, duplicates


def merge_install_candidates(
    *,
    registry_path: str,
    candidates: dict[str, dict[str, Any]],
    check_only: bool = False,
) -> dict[str, Any]:
    registry = _read_yaml_file(registry_path)
    registry.setdefault("skills", {})
    existing = normalize_install_candidate_collection(registry.get("install_candidates", {}), default_source="registry")

    merged = dict(existing)
    for raw_key, item in (candidates or {}).items():
        normalized = normalize_install_candidate_collection({raw_key: item}, default_source="registry")
        if not normalized:
            continue
        canonical_key, canonical_item = next(iter(normalized.items()))
        merged[canonical_key] = canonical_item

    registry["install_candidates"] = merged
    if not check_only:
        with open(registry_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(registry, handle, allow_unicode=True, sort_keys=False)
    return registry


def import_external_candidates(
    *,
    root_dir: str,
    registry_path: str,
    cache_dir: str,
    source_ids: list[str] | None = None,
    scan_python: bool = False,
    check_only: bool = False,
) -> dict[str, Any]:
    discovered, duplicates = discover_external_candidates(
        root_dir=root_dir,
        cache_dir=cache_dir,
        source_ids=source_ids,
        scan_python=scan_python,
    )
    merged = merge_install_candidates(
        registry_path=registry_path,
        candidates=discovered,
        check_only=check_only,
    )
    return {
        "candidate_count": len(discovered),
        "duplicate_count": len(duplicates),
        "duplicates": duplicates,
        "install_candidates": merged.get("install_candidates", {}),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import external skill candidates into the registry.")
    parser.add_argument("--root-dir", required=True)
    parser.add_argument("--registry-path", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--source-id", action="append", dest="source_ids")
    parser.add_argument("--scan-python", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    import_external_candidates(
        root_dir=args.root_dir,
        registry_path=args.registry_path,
        cache_dir=args.cache_dir,
        source_ids=args.source_ids,
        scan_python=bool(args.scan_python),
        check_only=bool(args.check_only),
    )
    return 0
