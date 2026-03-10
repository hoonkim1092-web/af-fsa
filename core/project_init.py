"""
core/project_init.py
====================
프로젝트 디렉토리 초기화 전담 모듈.
agent_launcher.py 에서 추출.

최초 실행 시 필요한 정책/워크플로/대시보드/설정 파일을 생성합니다.
"""

import os
import json
import yaml

from core.config_paths import (
    POLICIES_PATH, PROJECT_ID, PROJECT_ROOT, CONTEXT_SCHEMA_PATH,
    SKILL_LOCK_PATH, DASHBOARD_PATH, PROJECT_WORKFLOW_PATH,
    PROJECT_SETTINGS_PATH,
)
from core.documentation_policy import ensure_documentation_files


def ensure_project_files():
    """프로젝트에 필요한 기본 파일들이 없으면 생성합니다."""

    if not os.path.exists(POLICIES_PATH):
        os.makedirs(os.path.dirname(POLICIES_PATH), exist_ok=True)
        with open(POLICIES_PATH, "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "project_id": PROJECT_ID,
                    "workflow": {"default_template": "workflows/two_week_webapp_delivery.yaml", "role_map": {}},
                    "quality_gate": {"default_stage_on_build": "candidate", "auto_promote_sequence": ["canary", "active"]},
                    "approval_policy": {"default_require_approval": False, "require_skill_change_approval": False},
                    "external_skill_source_priority": ["claude_repo", "codex_repo", "registry", "external_cache"],
                    "external_skill_sources": [],
                    "autonomy": {"max_stage_retries": 2, "strict_quality_gate": True, "stop_on_stage_failure": True},
                },
                f,
                allow_unicode=True,
                default_flow_style=False,
            )

    if not os.path.exists(CONTEXT_SCHEMA_PATH):
        os.makedirs(os.path.dirname(CONTEXT_SCHEMA_PATH), exist_ok=True)
        with open(CONTEXT_SCHEMA_PATH, "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "required_keys": ["agent", "data_dir", "artifacts_dir"],
                    "types": {"agent": "dict", "data_dir": "str", "artifacts_dir": "str"},
                },
                f,
                allow_unicode=True,
                default_flow_style=False,
            )

    if not os.path.exists(SKILL_LOCK_PATH):
        os.makedirs(os.path.dirname(SKILL_LOCK_PATH), exist_ok=True)
        with open(SKILL_LOCK_PATH, "w", encoding="utf-8") as f:
            yaml.dump({"skills": {}}, f, allow_unicode=True, default_flow_style=False)

    if not os.path.exists(DASHBOARD_PATH):
        os.makedirs(os.path.dirname(DASHBOARD_PATH), exist_ok=True)
        legacy_dashboard_path = os.path.join(PROJECT_ROOT, "dashboard.json")
        if os.path.exists(legacy_dashboard_path):
            with open(legacy_dashboard_path, "r", encoding="utf-8") as src:
                payload = json.load(src)
        else:
            payload = {"project_id": PROJECT_ID, "runs": []}
        with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    if not os.path.exists(PROJECT_WORKFLOW_PATH):
        os.makedirs(os.path.dirname(PROJECT_WORKFLOW_PATH), exist_ok=True)
        with open(PROJECT_WORKFLOW_PATH, "w", encoding="utf-8") as f:
            yaml.dump(
                {"owner_agent": "General", "stages": [{"id": "MAIN", "name": "Main", "objective": "기본 워크플로우"}]},
                f,
                allow_unicode=True,
                default_flow_style=False,
            )

    if not os.path.exists(PROJECT_SETTINGS_PATH):
        os.makedirs(os.path.dirname(PROJECT_SETTINGS_PATH), exist_ok=True)
        with open(PROJECT_SETTINGS_PATH, "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "agent_overrides": {},
                    "skill_overrides": {"prefer_project_skills": True},
                },
                f,
                allow_unicode=True,
                default_flow_style=False,
            )

    ensure_documentation_files(PROJECT_ROOT)
