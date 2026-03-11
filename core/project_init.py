"""Project scaffold initialization helpers."""

from __future__ import annotations

import json
import os

import yaml

from core.documentation_policy import ensure_documentation_files


def _current_paths() -> dict[str, str]:
    from core import config_paths as cfg

    return {
        'POLICIES_PATH': cfg.POLICIES_PATH,
        'PROJECT_ID': cfg.PROJECT_ID,
        'PROJECT_ROOT': cfg.PROJECT_ROOT,
        'CONTEXT_SCHEMA_PATH': cfg.CONTEXT_SCHEMA_PATH,
        'SKILL_LOCK_PATH': cfg.SKILL_LOCK_PATH,
        'DASHBOARD_PATH': cfg.DASHBOARD_PATH,
        'PROJECT_WORKFLOW_PATH': cfg.PROJECT_WORKFLOW_PATH,
        'PROJECT_SETTINGS_PATH': cfg.PROJECT_SETTINGS_PATH,
    }


def ensure_project_files() -> None:
    """Create the current project's baseline files when they are missing."""

    paths = _current_paths()
    policies_path = paths['POLICIES_PATH']
    project_id = paths['PROJECT_ID']
    project_root = paths['PROJECT_ROOT']
    context_schema_path = paths['CONTEXT_SCHEMA_PATH']
    skill_lock_path = paths['SKILL_LOCK_PATH']
    dashboard_path = paths['DASHBOARD_PATH']
    workflow_path = paths['PROJECT_WORKFLOW_PATH']
    settings_path = paths['PROJECT_SETTINGS_PATH']

    if not os.path.exists(policies_path):
        os.makedirs(os.path.dirname(policies_path), exist_ok=True)
        with open(policies_path, 'w', encoding='utf-8') as handle:
            yaml.dump(
                {
                    'project_id': project_id,
                    'workflow': {'default_template': 'workflows/two_week_webapp_delivery.yaml', 'role_map': {}},
                    'quality_gate': {'default_stage_on_build': 'candidate', 'auto_promote_sequence': ['canary', 'active']},
                    'approval_policy': {'default_require_approval': False, 'require_skill_change_approval': False},
                    'external_skill_source_priority': ['claude_repo', 'codex_repo', 'registry', 'external_cache'],
                    'external_skill_sources': [],
                    'autonomy': {'max_stage_retries': 2, 'strict_quality_gate': True, 'stop_on_stage_failure': True},
                },
                handle,
                allow_unicode=True,
                default_flow_style=False,
            )

    if not os.path.exists(context_schema_path):
        os.makedirs(os.path.dirname(context_schema_path), exist_ok=True)
        with open(context_schema_path, 'w', encoding='utf-8') as handle:
            yaml.dump(
                {
                    'required_keys': ['agent', 'data_dir', 'artifacts_dir'],
                    'types': {'agent': 'dict', 'data_dir': 'str', 'artifacts_dir': 'str'},
                },
                handle,
                allow_unicode=True,
                default_flow_style=False,
            )

    if not os.path.exists(skill_lock_path):
        os.makedirs(os.path.dirname(skill_lock_path), exist_ok=True)
        with open(skill_lock_path, 'w', encoding='utf-8') as handle:
            yaml.dump({'skills': {}}, handle, allow_unicode=True, default_flow_style=False)

    if not os.path.exists(dashboard_path):
        os.makedirs(os.path.dirname(dashboard_path), exist_ok=True)
        legacy_dashboard_path = os.path.join(project_root, 'dashboard.json')
        if os.path.exists(legacy_dashboard_path):
            with open(legacy_dashboard_path, 'r', encoding='utf-8') as src:
                payload = json.load(src)
        else:
            payload = {'project_id': project_id, 'runs': []}
        with open(dashboard_path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    if not os.path.exists(workflow_path):
        os.makedirs(os.path.dirname(workflow_path), exist_ok=True)
        with open(workflow_path, 'w', encoding='utf-8') as handle:
            yaml.dump(
                {'owner_agent': 'General', 'stages': [{'id': 'MAIN', 'name': 'Main', 'objective': 'Default workflow stage'}]},
                handle,
                allow_unicode=True,
                default_flow_style=False,
            )

    if not os.path.exists(settings_path):
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, 'w', encoding='utf-8') as handle:
            yaml.dump(
                {
                    'agent_overrides': {},
                    'skill_overrides': {'prefer_project_skills': True},
                },
                handle,
                allow_unicode=True,
                default_flow_style=False,
            )

    ensure_documentation_files(project_root)