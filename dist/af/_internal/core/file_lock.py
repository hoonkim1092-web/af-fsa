"""
Thread-safe + process-safe file lock utility.

에이전트들은 asyncio.to_thread()로 실행되거나 (같은 프로세스 내 스레드),
별도 subprocess로 실행될 수 있다 (에이전트별 터미널 모드).

두 경우를 모두 보호하기 위해:
  - 스레드 간: threading.Lock
  - 프로세스 간: .lock 파일 원자적 생성 (O_CREAT | O_EXCL)

스탈 락 방어: 일정 시간(기본 10초)이 지난 .lock 파일은 자동 제거한다.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager

# 파일 경로 → 스레드 락 매핑 (모듈 레벨 싱글톤)
_thread_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()

_STALE_LOCK_SECONDS: float = 10.0   # 이 시간이 지난 .lock 파일은 스탈로 간주
_ACQUIRE_TIMEOUT: float = 15.0      # 락 획득 최대 대기 시간(초)
_POLL_INTERVAL: float = 0.02        # 폴링 간격(초)


def _get_thread_lock(path: str) -> threading.Lock:
    """파일 경로에 대한 스레드 락을 가져오거나 새로 생성한다."""
    with _registry_lock:
        if path not in _thread_locks:
            _thread_locks[path] = threading.Lock()
        return _thread_locks[path]


@contextmanager
def locked_file(path: str, timeout: float = _ACQUIRE_TIMEOUT):
    """
    path 기준의 스레드+프로세스 안전 락을 획득한 컨텍스트를 반환한다.

    사용법:
        with locked_file(messages_path):
            messages = load_mailbox_messages(workspace)
            messages.append(new_msg)
            _write_messages(workspace, messages)

    Args:
        path: 보호할 파일 경로 (락 파일은 path + ".lock" 으로 생성)
        timeout: 락 획득 최대 대기 시간(초). 초과 시 TimeoutError.
    """
    thread_lock = _get_thread_lock(path)
    lock_path = path + ".lock"
    deadline = time.monotonic() + timeout

    thread_lock.acquire()
    file_acquired = False
    try:
        # 프로세스 간 파일 락: O_CREAT | O_EXCL 원자적 생성
        # .lock 파일의 상위 디렉터리가 없으면 미리 생성한다
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        while time.monotonic() < deadline:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                file_acquired = True
                break
            except FileExistsError:
                # 스탈 락 감지: mtime이 오래된 .lock 파일은 제거
                try:
                    if time.time() - os.path.getmtime(lock_path) > _STALE_LOCK_SECONDS:
                        os.remove(lock_path)
                        continue
                except OSError:
                    pass
                time.sleep(_POLL_INTERVAL)

        if not file_acquired:
            raise TimeoutError(
                f"locked_file: could not acquire lock for {path!r} within {timeout}s"
            )

        yield

    finally:
        if file_acquired:
            try:
                os.remove(lock_path)
            except OSError:
                pass
        thread_lock.release()
