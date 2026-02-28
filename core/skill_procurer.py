"""
core/skill_procurer.py
======================
스킬 조달 + 제작(포징) 전담 모듈.
factory_manager.py 에서 추출.

주요 함수:
  - procure_skill(skill_name, role): 기존 스킬 검색 → 없으면 제작
  - forge_new_skill(skill_name, role): LLM으로 새 스킬 코드 생성
  - sync_warehouse(): 코어 스킬 저장소 Git 동기화
"""

import os
import re
import glob
import shutil
import datetime
import subprocess

from model_utils import get_best_model, resolve_dynamic_model
from core.llm_engine import LLMEngine
from core.skill_registry import check_skill_exists, register_skill


def log(step, msg):
    print(f"[{step}] {msg}")


FACTORY_ROOT = os.getcwd()
AGENT_PROJECT_ROOT = os.getenv("AGENT_PROJECT_ROOT")

if AGENT_PROJECT_ROOT:
    AGENTS_DIR = os.path.join(AGENT_PROJECT_ROOT, "agents")
    FORGE_DIR = os.path.join(AGENT_PROJECT_ROOT, "skills", "forge")
else:
    AGENTS_DIR = os.path.join(FACTORY_ROOT, "agents")
    FORGE_DIR = os.path.join(FACTORY_ROOT, "skills", "forge")

WAREHOUSE_DIR = os.path.join(FACTORY_ROOT, "skills", "warehouse")
ANTIGRAVITY_REPO_URL = "https://github.com/guanyang/antigravity-skills.git"


# =============================================================================
# Warehouse Sync
# =============================================================================
def sync_warehouse():
    log("WAREHOUSE", "Syncing...")
    if not os.path.exists(WAREHOUSE_DIR):
        try:
            subprocess.run(["git", "clone", ANTIGRAVITY_REPO_URL, WAREHOUSE_DIR], check=True)
            log("WAREHOUSE", "Download complete")
        except Exception as e:
            log("WAREHOUSE", f"Download failed: {e}")
    else:
        try:
            subprocess.run(["git", "-C", WAREHOUSE_DIR, "pull"], check=True)
            log("WAREHOUSE", "Update complete")
        except Exception as e:
            log("WAREHOUSE", f"Update failed (local mode): {e}")


def snapshot_registry():
    registry_path = os.path.join(FACTORY_ROOT, "registry.yaml")
    if os.path.exists(registry_path):
        backup_dir = os.path.join(FACTORY_ROOT, "backup_registry")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"registry_{timestamp}.yaml")
        try:
            shutil.copy2(registry_path, backup_path)
            log("BACKUP", f"Registry snapshot created: {backup_path}")
        except Exception as e:
            log("BACKUP", f"Snapshot failed: {e}")


# =============================================================================
# Skill ID Helpers
# =============================================================================
def normalize_skill_id(value):
    base = os.path.splitext(os.path.basename(str(value)))[0].strip().lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return base


def get_installed_skill_ids(agent_name):
    tools_dir = os.path.join(AGENTS_DIR, agent_name, "tools")
    if not os.path.exists(tools_dir):
        return set()
    installed = set()
    for path in glob.glob(os.path.join(tools_dir, "*.py")):
        sid = normalize_skill_id(path)
        if sid:
            installed.add(sid)
    return installed


def get_missing_skills(agent_name, required_skills):
    normalized_required = []
    for raw in required_skills:
        sid = normalize_skill_id(raw)
        if sid and sid not in normalized_required:
            normalized_required.append(sid)

    installed = get_installed_skill_ids(agent_name)
    missing = [sid for sid in normalized_required if sid not in installed]

    log("CHECK", f"required={normalized_required}")
    log("CHECK", f"installed={sorted(installed)}")
    log("CHECK", f"missing={missing}")
    return missing


# =============================================================================
# Procurement & Forging
# =============================================================================
def procure_skill(skill_name, role):
    """기존 스킬 검색 → warehouse → forge 순서로 스킬 조달"""
    purpose_desc = f"Skill intended for {role} to handle {skill_name}"
    existing_skill_path = check_skill_exists(skill_name, purpose_desc)

    if existing_skill_path and os.path.exists(existing_skill_path):
        log("REGISTRY", f"Reusing existing skill: {existing_skill_path}")
        return existing_skill_path

    found = glob.glob(os.path.join(WAREHOUSE_DIR, "**", f"{skill_name}.py"), recursive=True)
    if found:
        register_skill(skill_name, purpose_desc, found[0])
        return found[0]

    forge_path = os.path.join(FORGE_DIR, f"{skill_name}.py")
    if os.path.exists(forge_path):
        register_skill(skill_name, purpose_desc, forge_path)
        return forge_path

    return forge_new_skill(skill_name, role)


def forge_new_skill(skill_name, role, coding_engine=None):
    """LLM으로 새 스킬 코드를 생성(포징)"""
    if coding_engine is None:
        coding_engine = resolve_dynamic_model("codex")

    log("FORGE", f"Forging new skill: '{skill_name}' (Engine: {coding_engine})")
    os.makedirs(FORGE_DIR, exist_ok=True)
    output_path = os.path.join(FORGE_DIR, f"{skill_name}.py")

    llm = LLMEngine(model_name=get_best_model([coding_engine]))

    prompt = f"Write a professional Python CLI tool '{skill_name}.py' for the role '{role}'. Use argparse. Provide clean, robust code only. Code docstrings and user output MUST be in Korean. Return ONLY the python code."
    try:
        code = llm.generate(prompt)
        code = code.replace("```python", "").replace("```", "").strip()
        with open(output_path, "w", encoding="utf-8") as f: f.write(code)
        log("FORGE", f"Forge complete: {output_path}")
        register_skill(skill_name, f"Dynamically forged skill for {role}", output_path)
        return output_path
    except Exception as e:
        log("FORGE", f"Forge failed: {e}")
        return None
