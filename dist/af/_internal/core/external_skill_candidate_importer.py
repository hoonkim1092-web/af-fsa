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
MANIFEST_PATHS = (
    "skill_candidates.yaml",
    "skill_candidates.yml",
    "registry.yaml",
    os.path.join("skills", "registry.yaml"),
)
SKILL_MARKDOWN_FILENAMES = ("SKILL.md", "skill.md")
MANIFEST_METADATA_KEYS = {"source_url", "name", "description", "version"}


def _default_root_dir() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def cache_root_for_source(cache_dir: str, source_id: str) -> str:
    normalized_source_id = normalize_external_source_id(source_id, default=safe_id(str(source_id or "")))
    segment = SOURCE_CACHE_SEGMENTS.get(normalized_source_id, normalized_source_id)
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


def _cache_roots_for_source(cache_dir: str, source_id: str) -> list[str]:
    normalized_source_id = normalize_external_source_id(source_id, default="")
    if not normalized_source_id:
        return []
    roots: list[str] = []
    for segment in (
        SOURCE_CACHE_SEGMENTS.get(normalized_source_id, normalized_source_id),
        normalized_source_id,
    ):
        if not segment:
            continue
        root_dir = os.path.join(cache_dir, segment)
        if root_dir not in roots:
            roots.append(root_dir)
    return roots


def _discover_source_ids(cache_dir: str, source_ids: list[str] | None) -> list[str]:
    requested: list[str] = []
    for raw_source_id in source_ids or []:
        source_id = normalize_external_source_id(raw_source_id, default="")
        if source_id and source_id not in requested:
            requested.append(source_id)
    if requested:
        return requested

    discovered: list[str] = []
    if not os.path.isdir(cache_dir):
        return discovered
    for entry in sorted(os.listdir(cache_dir)):
        source_root = os.path.join(cache_dir, entry)
        if not os.path.isdir(source_root):
            continue
        source_id = normalize_external_source_id(entry, default="")
        if source_id and source_id not in discovered:
            discovered.append(source_id)
    return discovered


def _iter_repo_dirs(cache_dir: str, source_id: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for source_root in _cache_roots_for_source(cache_dir, source_id):
        if not os.path.isdir(source_root):
            continue
        for entry in sorted(os.listdir(source_root)):
            repo_dir = os.path.join(source_root, entry)
            repo_key = os.path.normcase(os.path.abspath(repo_dir))
            if os.path.isdir(repo_dir) and repo_key not in seen:
                seen.add(repo_key)
                out.append(repo_dir)
    return out


def _candidate_from_manifest(
    *,
    root_dir: str,
    source_id: str,
    repo_dir: str,
    raw_key: str,
    raw_item: Any,
    source_url: str = "",
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
        "source_url": str(item.get("source_url") or source_url or ""),
        "capabilities": [safe_id(str(value)) for value in (item.get("capabilities") or []) if safe_id(str(value))],
    }


def _manifest_candidate_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("install_candidates", "skills"):
        mapping = payload.get(key)
        if isinstance(mapping, dict):
            return mapping
    fallback: dict[str, Any] = {}
    for raw_key, raw_value in payload.items():
        key_text = str(raw_key or "").strip()
        if not key_text or key_text in MANIFEST_METADATA_KEYS:
            continue
        if isinstance(raw_value, (dict, str)):
            fallback[key_text] = raw_value
    return fallback


def _scan_repo_manifest_candidates(root_dir: str, source_id: str, repo_dir: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rel_manifest in MANIFEST_PATHS:
        manifest_path = os.path.join(repo_dir, rel_manifest)
        manifest = _read_yaml_file(manifest_path)
        if not manifest:
            continue
        raw_candidates = _manifest_candidate_mapping(manifest)
        if not raw_candidates:
            continue
        source_url = str(manifest.get("source_url") or "")
        for raw_key, raw_item in raw_candidates.items():
            candidate = _candidate_from_manifest(
                root_dir=root_dir,
                source_id=source_id,
                repo_dir=repo_dir,
                raw_key=str(raw_key),
                raw_item=raw_item,
                source_url=source_url,
            )
            if candidate:
                out.append(candidate)
    return out


def _python_candidate(root_dir: str, source_id: str, repo_dir: str, file_path: str) -> dict[str, Any] | None:
    filename = os.path.basename(file_path)
    if not filename.lower().endswith(".py"):
        return None
    if filename.startswith("_") or filename.startswith("test_") or filename == "conftest.py":
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


def _scan_repo_fallback_candidates(root_dir: str, source_id: str, repo_dir: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
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
            if any(os.path.exists(os.path.join(skill_dir, marker)) for marker in SKILL_MARKDOWN_FILENAMES):
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
    candidates: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []

    for source_id in _discover_source_ids(cache_dir, source_ids):
        for repo_dir in _iter_repo_dirs(cache_dir, source_id):
            discovered = _scan_repo_manifest_candidates(root_dir, source_id, repo_dir)
            if not discovered and scan_python:
                discovered = _scan_repo_fallback_candidates(root_dir, source_id, repo_dir)
            for item in discovered:
                skill_id = safe_id(str(item.get("id") or ""))
                if not skill_id:
                    continue
                key = _candidate_key(source_id, skill_id)
                if key in candidates:
                    duplicates.append(
                        {
                            "candidate_key": key,
                            "source_id": source_id,
                            "skill_id": skill_id,
                            "kept_repo": str(candidates[key].get("source_repo") or ""),
                            "skipped_repo": str(item.get("source_repo") or ""),
                            "kept_path": str(candidates[key].get("path") or ""),
                            "skipped_path": str(item.get("path") or ""),
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

    registry["install_candidates"] = dict(sorted(merged.items(), key=lambda item: item[0]))
    if not check_only:
        os.makedirs(os.path.dirname(registry_path), exist_ok=True)
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
        "install_candidate_count": len(merged.get("install_candidates", {})),
        "install_candidates": merged.get("install_candidates", {}),
        "registry_path": registry_path,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import external skill candidates into the registry.")
    parser.add_argument("--root-dir", "--root", dest="root_dir", default=_default_root_dir())
    parser.add_argument("--registry-path", "--registry", dest="registry_path", default="")
    parser.add_argument("--cache-dir", dest="cache_dir", default="")
    parser.add_argument("--source-id", "--source", action="append", dest="source_ids")
    parser.add_argument("--scan-python", action="store_true")
    parser.add_argument("--check-only", "--check", action="store_true", dest="check_only")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    root_dir = os.path.abspath(str(args.root_dir or _default_root_dir()))
    registry_path = os.path.abspath(args.registry_path or os.path.join(root_dir, "skills", "registry.yaml"))
    cache_dir = os.path.abspath(args.cache_dir or os.path.join(root_dir, "skills", "_external_cache"))
    result = import_external_candidates(
        root_dir=root_dir,
        registry_path=registry_path,
        cache_dir=cache_dir,
        source_ids=args.source_ids,
        scan_python=bool(args.scan_python),
        check_only=bool(args.check_only),
    )
    mode = "CHECK" if args.check_only else "IMPORT"
    print(
        f"[{mode}] external_candidates={result['candidate_count']} "
        f"duplicates_skipped={result['duplicate_count']} "
        f"install_candidates={result['install_candidate_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
