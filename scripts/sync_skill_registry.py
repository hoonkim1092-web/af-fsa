import argparse
import os
from datetime import datetime

import yaml


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILLS_DIR = os.path.join(ROOT, "skills")
REGISTRY_PATH = os.path.join(SKILLS_DIR, "registry.yaml")

EXCLUDE_DIRS = {"_external_cache", "__pycache__", ".git"}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    out = []
    for ch in t:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    s = s.strip("_")
    return s or "skill"


def read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def write_yaml(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=True)


def to_portable(path_abs: str) -> str:
    rel = os.path.relpath(path_abs, ROOT)
    return rel.replace("\\", "/")


def load_meta(meta_path: str) -> dict:
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def discover_dir_skills() -> dict:
    found = {}
    for name in os.listdir(SKILLS_DIR):
        if name in EXCLUDE_DIRS:
            continue
        d = os.path.join(SKILLS_DIR, name)
        if not os.path.isdir(d):
            continue
        py_path = os.path.join(d, "skill.py")
        if not os.path.exists(py_path):
            continue

        sid = safe_id(name)
        meta_path = os.path.join(d, "meta.yaml")
        meta = load_meta(meta_path)

        found[sid] = {
            "id": sid,
            "name": str(meta.get("name") or name),
            "version": str(meta.get("version") or "1.0.0"),
            "status": str(meta.get("status") or "active"),
            "path": to_portable(py_path),
            "meta_path": to_portable(meta_path) if os.path.exists(meta_path) else "",
            "capabilities": meta.get("capabilities") if isinstance(meta.get("capabilities"), list) else [],
        }
    return found


def discover_forge_skills(existing: dict) -> dict:
    found = {}
    forge_dir = os.path.join(SKILLS_DIR, "forge")
    if not os.path.isdir(forge_dir):
        return found

    for name in os.listdir(forge_dir):
        if not name.endswith(".py"):
            continue
        sid = safe_id(name[:-3])
        if sid in existing:
            continue
        py_path = os.path.join(forge_dir, name)
        found[sid] = {
            "id": sid,
            "name": sid,
            "version": "1.0.0",
            "status": "active",
            "path": to_portable(py_path),
            "meta_path": "",
            "capabilities": [],
        }
    return found


def normalize_install_candidates(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        sid = safe_id(str(k))
        if not sid or not isinstance(v, dict):
            continue
        item = dict(v)
        path = str(item.get("path") or "").strip().replace("\\", "/")
        if path:
            if os.path.isabs(path):
                try:
                    rel = os.path.relpath(path, ROOT).replace("\\", "/")
                    if not rel.startswith(".."):
                        path = rel
                    else:
                        path = ""
                except Exception:
                    path = ""
            if path and (path.startswith("../") or path.startswith("..\\")):
                path = ""
        if path:
            item["path"] = path
        else:
            item.pop("path", None)
        out[sid] = item
    return out


def sync_registry(check_only: bool = False) -> int:
    current = read_yaml(REGISTRY_PATH)
    current_skills = current.get("skills") if isinstance(current.get("skills"), dict) else {}

    discovered = discover_dir_skills()
    discovered.update(discover_forge_skills(discovered))

    merged = {}
    for sid, item in discovered.items():
        prev = current_skills.get(sid) if isinstance(current_skills.get(sid), dict) else {}
        entry = dict(item)
        entry["last_test_ok"] = bool(prev.get("last_test_ok", True))
        entry["updated_at"] = str(prev.get("updated_at") or now_iso())
        if not entry.get("meta_path"):
            entry.pop("meta_path", None)
        merged[sid] = entry

    next_registry = {
        "skills": dict(sorted(merged.items(), key=lambda x: x[0])),
        "install_candidates": normalize_install_candidates(current.get("install_candidates", {})),
    }

    if check_only:
        print(f"[CHECK] discovered_skills={len(merged)} install_candidates={len(next_registry['install_candidates'])}")
        return 0

    write_yaml(REGISTRY_PATH, next_registry)
    print(f"[SYNC] registry updated: {REGISTRY_PATH}")
    print(f"[SYNC] skills={len(merged)} install_candidates={len(next_registry['install_candidates'])}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync skills/registry.yaml from local skill files.")
    parser.add_argument("--check", action="store_true", help="Check only; do not write file.")
    args = parser.parse_args()
    return sync_registry(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
