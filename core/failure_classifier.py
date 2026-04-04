"""
core/failure_classifier.py
==========================
태스크 실패 reason 문자열을 INFRA / IMPLEMENTATION으로 분류한다.

INFRA 실패는 retry가 무의미(쿼터, 인증, 키 누락 등)하므로
StrategyEvaluator를 호출하지 않고 즉시 failed 처리한다.
"""
from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    INFRA = "infra"
    IMPLEMENTATION = "impl"


_INFRA_PATTERNS: tuple[str, ...] = (
    "missing_api_key",
    "missing_google_api_key",
    "no_callable_backend",
    "sdk_init_failed",
    "cli_timeout",
    "cli_auth_required",
    "cli_permission_denied",
    "cli_command_not_found",
    "cli_auto_install_failed",
    "worker_timeout",
    "worker_exited_code",
    "quota",
    "rate_limit",
    "429",
    "503",
    "auth_required",
    "not_logged_in",
)


def classify_failure(reason: str) -> FailureCategory:
    """reason 문자열을 분석하여 INFRA 또는 IMPLEMENTATION을 반환한다."""
    lowered = str(reason or "").lower()
    for pattern in _INFRA_PATTERNS:
        if pattern in lowered:
            return FailureCategory.INFRA
    return FailureCategory.IMPLEMENTATION
