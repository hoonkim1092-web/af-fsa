"""
core/synergy/job.py
===================
OmO 비동기 병렬 작업의 데이터 모델 (상태머신 + 레코드).

단일 책임:
  - JobStatus  → 상태머신 Enum
  - OmoJob     → 단일 작업 레코드 (dataclass)
  - TERMINAL_STATES → 종결 상태 집합
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# =============================================================================
# 상태머신
# 전이: queued → running → succeeded | failed | timeout | cancelled
#        queued ─────────────────────────────────────────────────→ cancelled
# =============================================================================

class JobStatus(str, Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    SUCCEEDED = "succeeded"
    FAILED    = "failed"
    TIMEOUT   = "timeout"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[JobStatus] = frozenset({
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.TIMEOUT,
    JobStatus.CANCELLED,
})


# =============================================================================
# 단일 OmO 하청 작업 레코드
# =============================================================================

@dataclass
class OmoJob:
    """단일 OmO 위임 작업 레코드.

    Attributes:
        job_id:       고유 식별자 (예: "omo_a1b2c3d4")
        task:         원본 작업 설명 문자열
        process:      관리 중인 Popen 인스턴스
        started_at:   time.time() 기준 시작 타임스탬프
        timeout_sec:  soft terminate 기준 최대 실행 시간(초)
        group_id:     CircuitBreaker 그룹 식별자
        status:       현재 JobStatus
        returncode:   프로세스 종료 코드 (미종료 시 None)
        attempt:      실행 시도 횟수 (재시도 확장용)
        _stdout_path: stdout 로그 파일 경로
        _stderr_path: stderr 로그 파일 경로
        _stdout_fh:   stdout 파일 핸들 (쓰기용, 종료 후 닫힘)
        _stderr_fh:   stderr 파일 핸들 (쓰기용, 종료 후 닫힘)
    """
    job_id: str
    task: str
    process: subprocess.Popen
    started_at: float
    timeout_sec: int
    group_id: str = "omo_synergy"
    status: JobStatus = JobStatus.QUEUED
    returncode: int | None = None
    attempt: int = 1
    _stdout_path: Path | None = None
    _stderr_path: Path | None = None
    _stdout_fh: object = field(default=None, repr=False)
    _stderr_fh: object = field(default=None, repr=False)

    def elapsed_ms(self) -> int:
        """시작 후 경과 시간 (밀리초)."""
        return int((time.time() - self.started_at) * 1000)

    def pid(self) -> int | None:
        """Popen 프로세스 PID. 프로세스 없으면 None."""
        try:
            return self.process.pid
        except Exception:
            return None

    def is_terminal(self) -> bool:
        """현재 상태가 종결 상태인지 여부."""
        return self.status in TERMINAL_STATES

    def to_status_dict(self) -> dict:
        """check_status API 반환값 형식으로 직렬화."""
        return {
            "ok": True,
            "job_id": self.job_id,
            "status": self.status.value,
            "pid": self.pid(),
            "elapsed_ms": self.elapsed_ms(),
            "attempt": self.attempt,
            "returncode": self.returncode,
            "is_terminal": self.is_terminal(),
        }
