import argparse
import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core.skill_registry as sr


def _read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate skills registry to unified map schema.")
    parser.add_argument("--path", default=os.path.join("skills", "registry.yaml"), help="Registry file path")
    parser.add_argument("--dry-run", action="store_true", help="Show migration summary only")
    parser.add_argument("--apply", action="store_true", help="Apply migration and write file")
    args = parser.parse_args()

    path = os.path.abspath(args.path)
    old = _read_yaml(path)
    normalized = sr._normalize_registry_data(old)

    old_skills = old.get("skills", {}) if isinstance(old, dict) else {}
    old_count = len(old_skills) if isinstance(old_skills, (list, dict)) else 0
    new_count = len(normalized.get("skills", {}))

    print(f"[MIGRATE] path={path}")
    print(f"[MIGRATE] skills: {old_count} -> {new_count}")
    print(f"[MIGRATE] install_candidates: {len(normalized.get('install_candidates', {}))}")

    if args.dry_run and not args.apply:
        print("[MIGRATE] dry-run complete (no writes)")
        return 0

    if not args.apply:
        print("[MIGRATE] no action requested; use --dry-run or --apply")
        return 1

    backup = path + ".bak"
    if os.path.exists(path):
        with open(path, "rb") as src, open(backup, "wb") as dst:
            dst.write(src.read())
        print(f"[MIGRATE] backup created: {backup}")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(normalized, f, allow_unicode=True, sort_keys=False)
    print("[MIGRATE] migration applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
