import os
import re
import json
import glob
from datetime import datetime
from typing import Dict, List, Optional

import yaml

from core.llm_engine import LLMEngine

REGISTRY_FILE = os.path.join(os.getcwd(), "skills", "registry.yaml")
DOCS_FILE = os.path.join(os.getcwd(), "skills", "skill_docs.md")


def _safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "skill"


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _normalize_skill_entry(raw_key: str, raw_value: dict) -> tuple[str, dict]:
    src = raw_value if isinstance(raw_value, dict) else {}
    sid = _safe_id(
        str(src.get("id") or src.get("skill_id") or src.get("skill_name") or src.get("name") or raw_key)
    )
    entry = {
        "id": sid,
        "skill_id": sid,  # backward compatibility
        "name": str(src.get("name") or src.get("skill_name") or sid),
        "skill_name": str(src.get("skill_name") or src.get("name") or sid),
        "type": str(src.get("type", "action")).strip().lower(),  # 'action' or 'knowledge'
        "purpose": str(src.get("purpose") or src.get("description") or ""),
        "path": str(src.get("path") or "").strip(),
        "meta_path": str(src.get("meta_path") or "").strip(),
        "version": str(src.get("version") or "1.0.0"),
        "status": str(src.get("status") or "active"),
        "capabilities": [_safe_id(str(x)) for x in _as_list(src.get("capabilities")) if str(x).strip()],
        "dependencies": [str(x) for x in _as_list(src.get("dependencies")) if str(x).strip()],
        "updated_at": str(src.get("updated_at") or datetime.now().isoformat(timespec="seconds")),
        "last_test_ok": bool(src.get("last_test_ok", True)),
    }
    return sid, entry


def _normalize_registry_data(data: dict) -> dict:
    base = data if isinstance(data, dict) else {}
    raw_skills = base.get("skills", {})
    skills: dict = {}

    if isinstance(raw_skills, list):
        for i, item in enumerate(raw_skills):
            sid, entry = _normalize_skill_entry(f"legacy_{i}", item if isinstance(item, dict) else {})
            skills[sid] = entry
    elif isinstance(raw_skills, dict):
        for k, v in raw_skills.items():
            sid, entry = _normalize_skill_entry(str(k), v if isinstance(v, dict) else {})
            skills[sid] = entry

    raw_candidates = base.get("install_candidates", {})
    install_candidates: dict = {}
    if isinstance(raw_candidates, dict):
        for k, v in raw_candidates.items():
            cid = _safe_id(str(k))
            if isinstance(v, dict):
                item = dict(v)
                item["id"] = _safe_id(str(item.get("id") or cid))
                install_candidates[cid] = item
            elif isinstance(v, str) and v.strip():
                install_candidates[cid] = {"id": cid, "path": v.strip()}
    elif isinstance(raw_candidates, list):
        for i, item in enumerate(raw_candidates):
            if not isinstance(item, dict):
                continue
            cid = _safe_id(str(item.get("id") or f"cand_{i}"))
            n = dict(item)
            n["id"] = cid
            install_candidates[cid] = n

    return {"skills": skills, "install_candidates": install_candidates}


def _load_registry() -> Dict:
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return _normalize_registry_data(data)
        except Exception:
            print("[WARN] Registry file corrupted, initializing new.")
    return {"skills": {}, "install_candidates": {}}


def _save_registry(data: Dict):
    normalized = _normalize_registry_data(data if isinstance(data, dict) else {})
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(normalized, f, allow_unicode=True, sort_keys=False)
    _generate_docs(normalized)


def _generate_docs(registry_data: Dict):
    skills = registry_data.get("skills", {}) if isinstance(registry_data, dict) else {}
    docs_content = [
        "# Agent Skill Registry Dashboard",
        f"**Last Updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Registered Skills Overview",
        "",
    ]

    if not skills:
        docs_content.append("No registered skills.")
    else:
        docs_content.append("| Skill Name | Purpose | Dependencies | Skill ID |")
        docs_content.append("|---|---|---|---|")
        sorted_skills = sorted(skills.values(), key=lambda x: x.get("skill_name", x.get("name", "")))
        for s in sorted_skills:
            name = s.get("skill_name", s.get("name", "Unknown"))
            purpose = s.get("purpose", "").replace("\n", " ").strip() or "N/A"
            deps = ", ".join(s.get("dependencies", [])) or "None"
            sid = s.get("id", "")
            docs_content.append(f"| **{name}** | {purpose} | `{deps}` | `{sid}` |")

    with open(DOCS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(docs_content))


def check_skill_exists(skill_name: str, purpose_description: str) -> Optional[str]:
    registry_data = _load_registry()
    skills = registry_data.get("skills", {})
    if not skills:
        return None

    llm = LLMEngine()  # 모델 자동 선택 (하드코딩 배제)
    prompt = f"""
You are a skill registry reviewer.
Requested skill: '{skill_name}'
Requested purpose: '{purpose_description}'
Existing skills(JSON map): {json.dumps(skills, ensure_ascii=False)}

Return strict JSON:
{{
  "match_found": true/false,
  "matched_skill_id": "id or empty",
  "reason": "brief reason"
}}
""".strip()

    try:
        decision = llm.generate_json(prompt)
        matched_id = _safe_id(str(decision.get("matched_skill_id", "")))
        if decision.get("match_found") and matched_id and matched_id in skills:
            return str(skills[matched_id].get("path") or "").strip() or None
    except Exception as e:
        print(f"[REGISTRY Error] Semantic match failed: {e}")
    return None


def register_skill(
    skill_name: str,
    purpose: str,
    path: str,
    dependencies: List[str] = None,
    capabilities: List[str] = None,
    status: str = "active",
    version: str = "1.0.0",
) -> str:
    registry_data = _load_registry()
    skills = registry_data.get("skills", {})
    sid = _safe_id(skill_name)
    prev = skills.get(sid, {}) if isinstance(skills.get(sid), dict) else {}
    entry = {
        "id": sid,
        "skill_id": sid,
        "name": str(prev.get("name") or sid),
        "skill_name": str(prev.get("skill_name") or skill_name),
        "purpose": str(purpose or prev.get("purpose") or ""),
        "path": str(path or prev.get("path") or ""),
        "meta_path": str(prev.get("meta_path") or ""),
        "version": str(version or prev.get("version") or "1.0.0"),
        "status": str(status or prev.get("status") or "active"),
        "capabilities": [_safe_id(str(x)) for x in (capabilities or prev.get("capabilities") or [sid]) if str(x).strip()],
        "dependencies": [str(x) for x in (dependencies or prev.get("dependencies") or []) if str(x).strip()],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "last_test_ok": bool(prev.get("last_test_ok", True)),
    }
    skills[sid] = entry
    registry_data["skills"] = skills
    _save_registry(registry_data)
    print(f"[REGISTRY] Skill '{sid}' registered/updated.")
    return sid


def rebuild_registry_from_disk(forge_dir: str, warehouse_dir: str):
    registry_data = _load_registry()
    skills_map = registry_data.get("skills", {})
    existing_paths = {str(s.get("path") or "") for s in skills_map.values() if isinstance(s, dict)}

    for base_dir in [forge_dir, warehouse_dir]:
        if not os.path.exists(base_dir):
            continue
        for filepath in glob.glob(os.path.join(base_dir, "**", "*.py"), recursive=True):
            if filepath in existing_paths:
                continue
            name = os.path.basename(filepath).replace(".py", "")
            register_skill(name, "Legacy auto-registered skill.", filepath, capabilities=[name])
