"""
core/synergy_runner.py
======================
[하위 호환 shim] — 실제 로직은 core.synergy 패키지로 이전되었습니다.

이 파일은 기존 코드베이스의 임포트를 깨지 않기 위해 유지됩니다.
신규 코드에서는 아래 패키지를 직접 사용하세요:

    from core.synergy import SynergyBridge, build_synergy_tools
    from core.synergy import JobStatus, OmoJob, TERMINAL_STATES
"""

from core.synergy import *  # noqa: F401, F403
from core.synergy import (  # noqa: F401 (explicit re-export for type checkers)
    TERMINAL_STATES,
    JobRegistry,
    JobStatus,
    OmoDetector,
    OmoJob,
    SynergyBridge,
    _kill_tree,
    _truncate,
    build_synergy_tools,
    get_synergy_context,
)
