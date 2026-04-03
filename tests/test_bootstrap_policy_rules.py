"""
_build_policy_rules()가 granularity 및 모듈 태스크 수 제한을 LLM 프롬프트에 주입하는지 검증.
bugfix: docs/features/bugfix-policy-granularity-not-injected.md
"""
from unittest.mock import patch
import pytest

MOCK_POLICY = {
    "roles": {"min": 2, "max": 5, "prefer": "practical_implementation"},
    "modules": {"max_tasks_per_module": 8, "min_tasks_per_module": 2},
    "tasks": {"granularity": "small", "naming_convention": "english_snake_case"},
    "planning_steps": {"min": 3, "max": 5},
    "constraints": ["Split work into modules that can be implemented independently."],
}


def _get_rules(policy):
    with patch("core.bootstrap_roles._load_task_decomposition_policy", return_value=policy):
        from core.bootstrap_roles import _build_policy_rules
        return _build_policy_rules()


def test_granularity_injected():
    rules = _get_rules(MOCK_POLICY)
    assert "granularity" in rules
    assert "'small'" in rules


def test_module_task_count_injected():
    rules = _get_rules(MOCK_POLICY)
    assert "2 to 8 tasks" in rules


def test_only_max_tasks():
    policy = {**MOCK_POLICY, "modules": {"max_tasks_per_module": 6}}
    rules = _get_rules(policy)
    assert "at most 6 tasks" in rules


def test_no_granularity_no_injection():
    policy = {**MOCK_POLICY, "tasks": {"naming_convention": "english_snake_case"}}
    rules = _get_rules(policy)
    assert "granularity" not in rules


def test_no_modules_policy_no_injection():
    policy = {**MOCK_POLICY, "modules": {}}
    rules = _get_rules(policy)
    assert "module must have" not in rules


def test_constraints_still_injected():
    rules = _get_rules(MOCK_POLICY)
    assert "Split work into modules" in rules


def test_fallback_when_no_policy():
    rules = _get_rules(None)
    assert "small tasks" in rules
