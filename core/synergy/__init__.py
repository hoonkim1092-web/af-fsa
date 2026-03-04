"""
core/synergy/__init__.py
========================
`core.synergy` 패키지 퍼블릭 API.

외부에서 다음 두 방식으로 동일하게 임포트할 수 있다:

    # 패키지 직접 임포트 (권장)
    from core.synergy import SynergyBridge, build_synergy_tools, JobStatus

    # 하위 모듈 직접 임포트 (세밀한 제어)
    from core.synergy.bridge import SynergyBridge, JobRegistry
    from core.synergy.job import JobStatus, OmoJob, TERMINAL_STATES
    from core.synergy.process import _kill_tree, OmoDetector
    from core.synergy.tools import build_synergy_tools, get_synergy_context
"""

from core.synergy.bridge import JobRegistry, SynergyBridge
from core.synergy.job import TERMINAL_STATES, JobStatus, OmoJob
from core.synergy.process import OmoDetector, _kill_tree, _truncate
from core.synergy.tools import build_synergy_tools, get_synergy_context

__all__ = [
    # 데이터 모델
    "JobStatus",
    "OmoJob",
    "TERMINAL_STATES",
    # 프로세스 유틸
    "_kill_tree",
    "_truncate",
    "OmoDetector",
    # 핵심 로직
    "JobRegistry",
    "SynergyBridge",
    # 에이전트 도구 팩토리
    "build_synergy_tools",
    "get_synergy_context",
]
