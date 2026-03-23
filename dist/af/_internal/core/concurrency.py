import time
import threading
import uuid
import logging

from typing import Callable, Any, Dict

logger = logging.getLogger("ConcurrencyControl")

class TaskCircuitBreaker:
    """
    (Guardrail 4) 백오프(Backoff) / 회로 차단
    동일 태스크 반복 실패 시 즉시 차단.
    """
    def __init__(self, max_failures: int = 3, reset_timeout_sec: int = 60):
        self.max_failures = max_failures
        self.reset_timeout_sec = reset_timeout_sec
        self.failures = 0
        self.last_failure_time = 0.0

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()

    def is_open(self) -> bool:
        if self.failures >= self.max_failures:
            # Check if reset timeout elapsed
            if time.time() - self.last_failure_time > self.reset_timeout_sec:
                self.failures = 0 # reset
                return False
            return True
        return False

class BackgroundTask:
    def __init__(self, task_id: str, func: Callable, args: tuple, kwargs: dict):
        self.task_id = task_id
        self.func = func
        self.args = args
        self.kwargs = kwargs
        
        # (Guardrail 2) 하트비트 상태
        self.last_heartbeat = time.time()
        
        self.is_running = False
        self.is_cancelled = False
        self.result = None
        self.error = None
        self._completion_event = threading.Event()
        self._semaphore_released = False  # 중복 release 방지 플래그

    def heartbeat(self):
        """Update heartbeat timestamp locally from within the task."""
        self.last_heartbeat = time.time()

    def check_status(self):
        return {
            "id": self.task_id,
            "running": self.is_running,
            "last_heartbeat": self.last_heartbeat,
            "cancelled": self.is_cancelled
        }

class BackgroundTaskManager:
    """
    (Guardrail 1-6) 안전한 강제 종료 6대 가드레일을 적용한 백그라운드 태스크 제어기
    """
    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        # BoundedSemaphore: 중복 release 시 ValueError 즉시 발생 → 버그 탐지
        self.semaphore = threading.BoundedSemaphore(self.max_concurrent)
        self.tasks: Dict[str, BackgroundTask] = {}
        self.circuit_breakers: Dict[str, TaskCircuitBreaker] = {}
        self.lock = threading.Lock()

    def submit(self, group_id: str, func: Callable, *args, **kwargs) -> str:
        """스레드를 활용해 백그라운드 태스크를 안전하게 스케줄링합니다."""
        with self.lock:
            # (Guardrail 4) Circuit Breaker 체크
            if group_id not in self.circuit_breakers:
                self.circuit_breakers[group_id] = TaskCircuitBreaker()
            
            cb = self.circuit_breakers[group_id]
            if cb.is_open():
                err = f"Circuit is OPEN for group {group_id}. Too many aborts."
                logger.warning(err)
                return f"ERROR: {err}"

            task_id = str(uuid.uuid4())
            task = BackgroundTask(task_id, func, args, kwargs)
            self.tasks[task_id] = task

        thread = threading.Thread(target=self._run_task_wrapper, args=(task, group_id), daemon=True)
        thread.start()
        return task_id

    def _run_task_wrapper(self, task: BackgroundTask, group_id: str):
        # Concurrency Queue Wait
        self.semaphore.acquire()
        task.is_running = True
        logger.info(f"Task {task.task_id} started.")
        
        try:
            # (Guardrail 2) 하트비트가 가능하도록 태스크에 self(task 객체)를 넘겨주거나 외부에서 호출
            # In a real environment, we'd inject a heartbeat callback to the func.
            # Here we inject 'heartbeat_cb' into kwargs if the function accepts it.
            if 'heartbeat_cb' in task.kwargs:
                task.kwargs['heartbeat_cb'] = task.heartbeat
                
            task.result = task.func(*task.args, **task.kwargs)
            
            # Reset circuit breaker on success
            with self.lock:
                if group_id in self.circuit_breakers:
                    self.circuit_breakers[group_id].failures = 0

        except Exception as e:
            task.error = e
            logger.error(f"(Guardrail 6) Task {task.task_id} Exception: {e}")
            with self.lock:
                if group_id in self.circuit_breakers:
                    self.circuit_breakers[group_id].record_failure()
        finally:
            self._cleanup_task_resources(task)
            # 중복 release 방지: abort_task()에서 이미 release했으면 스킵
            if not task._semaphore_released:
                task._semaphore_released = True
                self.semaphore.release()

    def _cleanup_task_resources(self, task: BackgroundTask):
        """(Guardrail 5) 종료 훅(Cleanup Hook) - 로컬 세션 및 임시 파일 등 정리 보장"""
        task.is_running = False
        task._completion_event.set()
        
        # 실제 환경에서는 ThreadLocal 변수, DB Connection, Temp File 등을 여기서 삭제해야 함.
        # 예: cleanup_temp_files(task.task_id)
        logger.info(f"Cleanup Hook executed for task {task.task_id}.")

    def abort_task(self, task_id: str, timeout_sec: int = 10) -> bool:
        """
        (Guardrail 1) 2단계 종료 (Soft Cancel -> Hard Kill)
        파이썬 쓰레드의 한계로 직접 Kill은 어려우나, 
        플래그를 통해 Soft Cancel 후 타임아웃 시 고아 스레드로 버리고 세마포어를 회수한다.
        Popen 프로세스가 연결된 경우(_popen_ref) 트리 전체를 종료한다.
        """
        with self.lock:
            task = self.tasks.get(task_id)
        
        if not task or not task.is_running:
            return False

        # 1단계: Soft Cancel Signal
        logger.info(f"Sending Soft Cancel signal to task {task_id}...")
        task.is_cancelled = True

        # (Guardrail 1+) Popen 프로세스가 연결된 경우 트리 전체 종료
        popen_ref = getattr(task, '_popen_ref', None)
        if popen_ref is not None:
            try:
                from core.synergy_runner import _kill_tree
                pid = getattr(popen_ref, 'pid', None)
                if pid:
                    popen_ref.terminate()
                    try:
                        popen_ref.wait(timeout=5)
                    except Exception:
                        _kill_tree(pid)
                    logger.info(f"(Guardrail 1+) Popen tree killed for task {task_id}, pid={pid}")
            except Exception as e:
                logger.warning(f"(Guardrail 1+) Popen kill failed for {task_id}: {e}")
        
        # 2단계: 대기
        if task._completion_event.wait(timeout=timeout_sec):
            logger.info(f"Task {task_id} gracefully aborted.")
            return True
            
        # 3단계: 시간 초과 시 Hard Kill (파이썬에서는 리소스 강제 회수 로직으로 대체)
        # (Guardrail 6) 관측성
        logger.error(f"(Guardrail 1/6) Task {task_id} failed to graceful stop within {timeout_sec}s. Hard Kill / Orphaned.")
        
        # 세마포어를 강제로 돌려주어 전체 시스템이 막히지 않게 함 (Hard Abort Effect)
        # 중복 release 방지: 플래그로 _run_task_wrapper의 finally와 충돌 차단
        if not task._semaphore_released:
            task._semaphore_released = True
            self.semaphore.release()
        
        # (Guardrail 5) 정리 훅 강제 실행
        self._cleanup_task_resources(task)
        
        return False

    def monitor_heartbeats(self, timeout_sec: int = 300):
        """
        (Guardrail 2) 데몬 스레드로 돌며 일정 시간 이상 하트비트가 없는 태스크를 찾아 취소.
        """
        now = time.time()
        stale_tasks = []
        with self.lock:
            for tid, t in self.tasks.items():
                if t.is_running and (now - t.last_heartbeat > timeout_sec):
                    stale_tasks.append(tid)

        for tid in stale_tasks:
            logger.warning(f"Task {tid} STALE (No Heartbeat for {timeout_sec}s). Aborting.")
            self.abort_task(tid, timeout_sec=5)

# Singleton manager instance
background_manager = BackgroundTaskManager(max_concurrent=10)
