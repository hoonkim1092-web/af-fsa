"""
core/synergy/bridge.py
======================
SynergyBridge — OmO 비동기 병렬 위임 핵심 로직 (V2.1 hardened).

[V2.1 수정 사항 — 5개 결함 대응]
  FIX-1: 워치독 submit 반환값 검증 + fallback 스레드 (영구 정체 방지)
  FIX-2: cancel ↔ watchdog 상태 경합 Race Condition 방지 (Lock 기반)
  FIX-3: dispatch timeout_sec=None → config 기본값 우선 적용
  FIX-4: meta.json 디스크 영속 + 프로세스 재시작 시 복구 (JobRegistry.recover)
  FIX-5: omo_max_concurrent_jobs Semaphore 제한 연결 (자원 폭주 방지)

단일 책임:
  - JobRegistry   → thread-safe 작업 레지스트리 (영속 포함)
  - SynergyBridge → dispatch/status/collect/cancel + 레거시 2종
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from core.config_paths import BASE_DIR, PROJECT_ROOT
from core.synergy.job import TERMINAL_STATES, JobStatus, OmoJob
from core.synergy.process import OmoDetector, _kill_tree, _truncate

logger = logging.getLogger(__name__)


# =============================================================================
# JobRegistry — thread-safe 작업 레지스트리 (+ 디스크 영속)
# =============================================================================

class JobRegistry:
    """OmoJob 인스턴스를 thread-safe하게 관리하는 레지스트리.

    [FIX-4] meta.json을 디스크에 영속하여 프로세스 재시작 후에도
    상태 조회(status) 및 결과 수거(collect)가 가능하도록 한다.
    """

    def __init__(self, jobs_dir: Path | None = None) -> None:
        self._jobs: dict[str, OmoJob] = {}
        self._lock = threading.Lock()
        self._jobs_dir = jobs_dir or (Path(BASE_DIR) / "jobs")

    def put(self, job: OmoJob) -> None:
        """job을 등록하고 meta.json을 디스크에 영속한다."""
        with self._lock:
            self._jobs[job.job_id] = job
        self._persist_meta(job)

    def get(self, job_id: str) -> OmoJob | None:
        """job_id로 OmoJob을 반환한다. 없으면 None."""
        with self._lock:
            return self._jobs.get(job_id)

    def pop(self, job_id: str) -> OmoJob | None:
        """job을 제거하고 반환한다. 없으면 None."""
        with self._lock:
            return self._jobs.pop(job_id, None)

    def list_all(self) -> list[dict]:
        """전체 job 요약 목록을 반환한다."""
        with self._lock:
            return [j.to_status_dict() for j in self._jobs.values()]

    def count(self) -> int:
        """현재 등록된 job 수를 반환한다."""
        with self._lock:
            return len(self._jobs)

    # -- [FIX-4] 디스크 영속 --

    def _persist_meta(self, job: OmoJob) -> None:
        """job 메타데이터를 meta.json에 저장한다."""
        try:
            job_dir = self._jobs_dir / job.job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            meta = {
                "job_id": job.job_id,
                "task": job.task,
                "status": job.status.value,
                "returncode": job.returncode,
                "started_at": job.started_at,
                "timeout_sec": job.timeout_sec,
                "attempt": job.attempt,
                "group_id": job.group_id,
                "pid": job.pid(),
            }
            meta_path = job_dir / "meta.json"
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[_persist_meta] {job.job_id} 저장 실패: {e}")

    def update_status(self, job: OmoJob) -> None:
        """job 상태만 meta.json에 업데이트한다."""
        self._persist_meta(job)

    def recover_from_disk(self) -> list[dict]:
        """프로세스 재시작 후 디스크에 남은 job의 메타 정보를 복구한다.

        ⚠️ Popen 프로세스는 복구 불가하므로, 미종결 상태는 'timeout'으로 마킹한다.
        반환: 복구된 meta 목록.
        """
        recovered: list[dict] = []
        if not self._jobs_dir.exists():
            return recovered
        for job_dir in self._jobs_dir.iterdir():
            if not job_dir.is_dir():
                continue
            meta_path = job_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                status = meta.get("status", "")
                # 미종결 상태 → timeout으로 마킹 (프로세스 이미 죽었으므로)
                if status not in ("succeeded", "failed", "timeout", "cancelled"):
                    meta["status"] = "timeout"
                    meta["note"] = "recovered_after_restart"
                    meta_path.write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                recovered.append(meta)
            except Exception as e:
                logger.warning(f"[recover_from_disk] {job_dir.name} 복구 실패: {e}")
        return recovered


# =============================================================================
# SynergyBridge — 핵심 비동기 로직 (V2.1 hardened)
# =============================================================================

class SynergyBridge:
    """Agent Factory ↔ OmO 런타임 비동기 연동 브릿지 (V2.1).

    [수정 이력]
      V2.0: 기본 4-API 패턴 (dispatch/status/collect/cancel)
      V2.1: 5개 결함 대응 (워치독 검증, 상태 경합, config timeout, 영속, 동시성 제한)
    """

    JOBS_DIR: Path = Path(BASE_DIR) / "jobs"

    def __init__(self) -> None:
        self._detector = OmoDetector(base_dir=BASE_DIR)
        self.omo_path: str | None = self._detector.detect_path()
        self._cmd: dict | None = (
            self._detector.resolve_cmd(self.omo_path) if self.omo_path else None
        )
        self._registry = JobRegistry(jobs_dir=self.JOBS_DIR)

        # [FIX-5] OmO 전용 동시성 세마포어 (BoundedSemaphore: 중복 release 즉시 탐지)
        self._omo_semaphore = threading.BoundedSemaphore(self._get_max_concurrent_jobs())

        # [FIX-2] 상태 전이 보호 락 — cancel ↔ watchdog 경합 방지
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 설정 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def _get_max_concurrent_jobs() -> int:
        """schema에서 omo_max_concurrent_jobs를 읽는다."""
        try:
            from config.schema import factory_config
            return factory_config.background_tasks.omo_max_concurrent_jobs
        except Exception:
            return int(os.getenv("OMO_MAX_CONCURRENT_JOBS", "3"))

    @staticmethod
    def _get_default_timeout() -> int:
        """schema에서 omo_dispatch_timeout_sec를 읽는다."""
        try:
            from config.schema import factory_config
            return factory_config.background_tasks.omo_dispatch_timeout_sec
        except Exception:
            return int(os.getenv("OMO_DISPATCH_TIMEOUT_SEC", "300"))

    def _grace_sec(self) -> int:
        """설정에서 grace period를 읽는다."""
        try:
            from config.schema import factory_config
            return factory_config.background_tasks.omo_grace_period_sec
        except Exception:
            return int(os.getenv("OMO_GRACE_PERIOD_SEC", "10"))

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _log_state(self, job: OmoJob, prev_status: str) -> None:
        """상태 전이를 구조화 JSON으로 로깅한다 (lazy evaluate)."""
        if not logger.isEnabledFor(logging.INFO):
            return
        payload = {
            "event": "omo_job_state_change",
            "job_id": job.job_id,
            "pid": job.pid(),
            "group_id": job.group_id,
            "status": job.status.value,
            "prev_status": prev_status,
            "started_at": job.started_at,
            "elapsed_ms": job.elapsed_ms(),
            "attempt": job.attempt,
            "task_preview": job.task[:80],
        }
        logger.info(json.dumps(payload, ensure_ascii=False))

    def _finalize_job(self, job: OmoJob) -> None:
        """stdout/stderr 파일 핸들을 닫는다 (Guardrail 5 - 종료 훅)."""
        for fh_attr in ("_stdout_fh", "_stderr_fh"):
            fh = getattr(job, fh_attr, None)
            if fh is not None:
                try:
                    if not fh.closed:
                        fh.close()
                except Exception as e:
                    logger.warning(f"[_finalize_job] {job.job_id} 파일 핸들 닫기 실패: {e}")
        # [FIX-4] 상태 영속
        self._registry.update_status(job)

    def _cleanup_job_dir(self, job_id: str) -> None:
        """job 전용 디렉토리를 삭제한다 (디스크 누수 방지)."""
        try:
            job_dir = self.JOBS_DIR / job_id
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"[_cleanup_job_dir] {job_id} 삭제 실패: {e}")

    def _soft_terminate(self, job: OmoJob) -> None:
        """Soft terminate → grace period → hard kill 2단계 종료."""
        grace = self._grace_sec()
        try:
            job.process.terminate()
        except Exception:
            pass
        try:
            job.process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pid = job.pid()
            if pid:
                logger.error(
                    f"[_soft_terminate] {job.job_id} grace {grace}s 초과 → _kill_tree(pid={pid})"
                )
                _kill_tree(pid)

    def _transition(self, job: OmoJob, new_status: JobStatus) -> bool:
        """[FIX-2] thread-safe 상태 전이. CANCELLED 상태는 덮어쓸 수 없다.

        Returns:
            True이면 전이 성공, False이면 CANCELLED에 의해 거부됨.
        """
        with self._state_lock:
            if job.status == JobStatus.CANCELLED:
                return False  # cancel이 먼저 찍었으면 watchdog이 덮어쓸 수 없음
            prev = job.status.value
            job.status = new_status
        self._log_state(job, prev)
        # [FIX-4+] 전이마다 즉시 flush — 크래시 시 중간 상태 유실 방지
        self._registry.update_status(job)
        return True

    # ------------------------------------------------------------------
    # 워치독
    # ------------------------------------------------------------------

    def _watchdog(self, job_id: str, heartbeat_cb: Callable | None = None) -> None:
        """Popen 프로세스를 감시하며 타임아웃/정상 종료를 처리한다.

        [FIX-2] 상태 전이 시 _transition()을 사용하여
                cancel에 의한 CANCELLED가 watchdog에 의해 덮어써지는 것을 방지.
        [FIX-5] 세마포어를 finally에서 반드시 release.
        """
        job = self._registry.get(job_id)
        if not job:
            self._omo_semaphore.release()
            return

        try:
            if not self._transition(job, JobStatus.RUNNING):
                return  # 이미 취소됨

            while job.process.poll() is None:
                # [FIX-2] cancel에 의해 이미 CANCELLED 상태면 워치독 종료
                if job.status == JobStatus.CANCELLED:
                    return

                if time.time() - job.started_at > job.timeout_sec:
                    logger.warning(
                        f"[watchdog] {job_id} timeout ({job.timeout_sec}s) → soft terminate"
                    )
                    self._soft_terminate(job)
                    self._transition(job, JobStatus.TIMEOUT)
                    self._finalize_job(job)
                    return

                if heartbeat_cb:
                    heartbeat_cb()
                time.sleep(2)

            # 프로세스 정상 종료
            job.returncode = job.process.returncode
            new_status = (
                JobStatus.SUCCEEDED if job.returncode == 0 else JobStatus.FAILED
            )
            if self._transition(job, new_status):
                self._finalize_job(job)
            # else: CANCELLED 상태 유지

        finally:
            # [FIX-5] 세마포어 반환 보장
            self._omo_semaphore.release()

    # ------------------------------------------------------------------
    # [API 1] dispatch — Fire-and-Forget
    # ------------------------------------------------------------------

    def dispatch(self, task: str, timeout_sec: int | None = None) -> dict:
        """OmO에 작업을 비동기로 발사한다. job_id를 즉시 반환한다.

        [FIX-3] timeout_sec=None이면 config 기본값 사용.
        [FIX-5] omo_max_concurrent_jobs 초과 시 즉시 에러 반환 (non-blocking).

        Args:
            task: OmO ultrawork에 위임할 작업 설명
            timeout_sec: 최대 대기 시간 (None이면 config 기본값)
        """
        if not self.omo_path:
            return {"ok": False, "error": "omo_not_detected"}
        if not self._cmd:
            return {"ok": False, "error": "ultrawork_command_not_resolved", "omo_path": self.omo_path}
        text = str(task or "").strip()
        if not text:
            return {"ok": False, "error": "missing_task"}

        # [FIX-5] 동시성 제한 — non-blocking acquire
        if not self._omo_semaphore.acquire(blocking=False):
            max_jobs = self._get_max_concurrent_jobs()
            return {
                "ok": False,
                "error": "omo_concurrent_limit_reached",
                "max_concurrent_jobs": max_jobs,
                "active_jobs": self._registry.count(),
            }

        # [FIX-3] config 기본값 우선
        effective_timeout = timeout_sec if timeout_sec is not None else self._get_default_timeout()

        try:
            argv, env_overrides = OmoDetector.build_argv(self._cmd, text)
        except Exception as e:
            self._omo_semaphore.release()
            return {"ok": False, "error": f"argv_build_failed:{e}"}

        job_id = f"omo_{uuid.uuid4().hex[:8]}"
        job_dir = self.JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        try:
            stdout_path = job_dir / "stdout.log"
            stderr_path = job_dir / "stderr.log"
            stdout_fh = open(stdout_path, "w", encoding="utf-8")
            stderr_fh = open(stderr_path, "w", encoding="utf-8")

            popen_kwargs = {
                "cwd": self.omo_path,
                "stdout": stdout_fh,
                "stderr": stderr_fh,
                "env": {**os.environ, **env_overrides},
                **OmoDetector.popen_platform_kwargs(),
            }
            proc = subprocess.Popen(argv, **popen_kwargs)

        except Exception as e:
            self._omo_semaphore.release()
            self._cleanup_job_dir(job_id)
            return {"ok": False, "error": f"popen_failed:{e}"}

        job = OmoJob(
            job_id=job_id,
            task=text,
            process=proc,
            started_at=time.time(),
            timeout_sec=int(effective_timeout),
            status=JobStatus.QUEUED,
            _stdout_path=stdout_path,
            _stderr_path=stderr_path,
            _stdout_fh=stdout_fh,
            _stderr_fh=stderr_fh,
        )
        self._registry.put(job)

        # [FIX-1] BackgroundTaskManager submit 반환값 검증
        watchdog_ok = False
        try:
            from core.concurrency import background_manager
            result = background_manager.submit(
                group_id="omo_synergy",
                func=self._watchdog,
                job_id=job_id,
            )
            # submit이 "ERROR: ..." 문자열을 반환하면 실패
            if isinstance(result, str) and result.startswith("ERROR"):
                logger.warning(f"[dispatch] BackgroundManager 거부: {result}")
            else:
                watchdog_ok = True
        except Exception as e:
            logger.warning(f"[dispatch] BackgroundManager 예외: {e}")

        # [FIX-1] fallback: 직접 데몬 스레드로 워치독 실행
        if not watchdog_ok:
            logger.info(f"[dispatch] {job_id} → fallback 워치독 스레드 직접 기동")
            t = threading.Thread(
                target=self._watchdog, args=(job_id,), daemon=True,
                name=f"omo-watchdog-{job_id}",
            )
            t.start()

        self._log_state(job, "created")
        return {
            "ok": True,
            "job_id": job_id,
            "status": job.status.value,
            "pid": proc.pid,
            "watchdog": "background_manager" if watchdog_ok else "fallback_thread",
        }

    # ------------------------------------------------------------------
    # [API 2] check_status
    # ------------------------------------------------------------------

    def check_status(self, job_id: str) -> dict:
        """dispatched job의 현재 상태를 반환한다.

        [FIX-4] 메모리에 없으면 디스크 meta.json에서 복구 시도.
        """
        job = self._registry.get(str(job_id or ""))
        if job:
            return job.to_status_dict()

        # [FIX-4] 메모리에 없으면 디스크에서 fallback
        meta_path = self.JOBS_DIR / str(job_id or "") / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return {
                    "ok": True,
                    "job_id": meta.get("job_id", job_id),
                    "status": meta.get("status", "unknown"),
                    "elapsed_ms": None,
                    "source": "disk_recovery",
                    "is_terminal": meta.get("status") in ("succeeded", "failed", "timeout", "cancelled"),
                }
            except Exception:
                pass

        return {"ok": False, "error": "job_not_found", "job_id": job_id}

    # ------------------------------------------------------------------
    # [API 3] collect_result
    # ------------------------------------------------------------------

    def collect_result(self, job_id: str) -> dict:
        """완료된 job의 결과를 수거한다.

        ⚠️ 종결 상태에서만 성공.
        [FIX-4] 메모리에 없어도 디스크에 결과가 남아있으면 수거 가능.
        """
        job = self._registry.get(str(job_id or ""))

        if job:
            if not job.is_terminal():
                return {
                    "ok": False,
                    "error": "job_not_finished",
                    "status": job.status.value,
                    "elapsed_ms": job.elapsed_ms(),
                }
            stdout_text, stderr_text = self._read_output_files(job_id, job._stdout_path, job._stderr_path)
            result = {
                "ok": job.status == JobStatus.SUCCEEDED,
                "job_id": job.job_id,
                "status": job.status.value,
                "returncode": job.returncode,
                "elapsed_ms": job.elapsed_ms(),
                "stdout": stdout_text,
                "stderr": stderr_text,
                "mode": "omo_ultrawork_async",
            }
            self._registry.pop(job_id)
            self._cleanup_job_dir(job_id)
            return result

        # [FIX-4] 메모리에 없지만 디스크에 남아있는 경우 (재시작 후)
        job_dir = self.JOBS_DIR / str(job_id or "")
        meta_path = job_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                status = meta.get("status", "")
                if status not in ("succeeded", "failed", "timeout", "cancelled"):
                    return {"ok": False, "error": "job_not_finished", "status": status}

                stdout_text, stderr_text = self._read_output_files(
                    str(job_id), job_dir / "stdout.log", job_dir / "stderr.log",
                )
                result = {
                    "ok": status == "succeeded",
                    "job_id": meta.get("job_id", job_id),
                    "status": status,
                    "returncode": meta.get("returncode"),
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "mode": "omo_ultrawork_async",
                    "source": "disk_recovery",
                }
                self._cleanup_job_dir(str(job_id))
                return result
            except Exception:
                pass

        return {"ok": False, "error": "job_not_found", "job_id": job_id}

    def _read_output_files(
        self, job_id: str, stdout_path: Path | None, stderr_path: Path | None
    ) -> tuple[str, str]:
        """stdout/stderr 로그 파일을 읽어 truncate된 문자열을 반환한다."""
        stdout_text = ""
        stderr_text = ""
        try:
            if stdout_path and stdout_path.exists():
                stdout_text = _truncate(
                    stdout_path.read_text(encoding="utf-8", errors="replace"), 2400
                )
            if stderr_path and stderr_path.exists():
                stderr_text = _truncate(
                    stderr_path.read_text(encoding="utf-8", errors="replace"), 1200
                )
        except Exception as e:
            logger.warning(f"[collect] {job_id} 파일 읽기 실패: {e}")
        return stdout_text, stderr_text

    # ------------------------------------------------------------------
    # [API 4] cancel
    # ------------------------------------------------------------------

    def cancel(self, job_id: str) -> dict:
        """실행 중 또는 대기 중인 job을 강제 회수한다.

        [FIX-2] CAS 패턴 완성: is_terminal 체크 + 상태 전이를 단일 락 안에서 수행.
                TOCTOU 갭 제거 — watchdog 끼어들기 불가.
        """
        job = self._registry.get(str(job_id or ""))
        if not job:
            return {"ok": False, "error": "job_not_found", "job_id": job_id}

        # [FIX-2+] CAS: 상태 확인과 전이를 원자적으로 수행
        with self._state_lock:
            prev_status = job.status.value
            if job.is_terminal():
                return {
                    "ok": True,
                    "job_id": job_id,
                    "prev_status": prev_status,
                    "new_status": prev_status,
                    "note": "already_terminal",
                }
            job.status = JobStatus.CANCELLED

        # 락 해제 후 프로세스 종료 (I/O 블로킹을 락 밖에서 처리)
        self._soft_terminate(job)
        self._finalize_job(job)
        self._log_state(job, prev_status)

        return {
            "ok": True,
            "job_id": job_id,
            "prev_status": prev_status,
            "new_status": job.status.value,
        }

    # ------------------------------------------------------------------
    # list_jobs — 전체 job 요약 조회
    # ------------------------------------------------------------------

    def list_jobs(self) -> dict:
        """현재 관리 중인 모든 job의 상태 요약을 반환한다."""
        return {
            "ok": True,
            "count": self._registry.count(),
            "jobs": self._registry.list_all(),
        }

    # ------------------------------------------------------------------
    # [레거시] run_ultrawork — 동기식 (하위 호환)
    # ------------------------------------------------------------------

    def run_ultrawork(self, task: str, timeout_sec: int = 120) -> dict:
        """[레거시 동기식] OmO ultrawork 실행. 단순/긴급 작업용."""
        if not self.omo_path:
            return {"ok": False, "error": "omo_not_detected"}
        if not self._cmd:
            return {"ok": False, "error": "ultrawork_command_not_resolved", "omo_path": self.omo_path}
        text = str(task or "").strip()
        if not text:
            return {"ok": False, "error": "missing_task"}
        try:
            argv, env_overrides = OmoDetector.build_argv(self._cmd, text)
            proc = subprocess.run(
                argv,
                cwd=self.omo_path,
                env={**os.environ, **env_overrides},
                capture_output=True, text=True,
                timeout=max(5, int(timeout_sec)),
                check=False,
            )
            return {
                "ok": proc.returncode == 0,
                "mode": "omo_ultrawork",
                "returncode": int(proc.returncode),
                "stdout": _truncate(proc.stdout, 2400),
                "stderr": _truncate(proc.stderr, 1200),
                "omo_path": self.omo_path,
            }
        except Exception as e:
            return {"ok": False, "mode": "omo_ultrawork", "error": str(e), "omo_path": self.omo_path}

    # ------------------------------------------------------------------
    # [레거시] run_hash_edit — Hash-Anchored 파일 편집 (하위 호환)
    # ------------------------------------------------------------------

    def run_hash_edit(self, path: str, find: str, replace: str, count: int = 1, expected_sha256: str = "") -> dict:
        """[레거시] SHA-256 선택적 가드를 포함한 결정론적 파일 편집."""
        rel = str(path or "").strip()
        if not rel:
            return {"ok": False, "error": "missing_path"}
        if str(find or "") == "":
            return {"ok": False, "error": "missing_find"}

        project_root = Path(PROJECT_ROOT).resolve()
        target = Path(rel)
        if not target.is_absolute():
            target = (project_root / target).resolve()
        else:
            target = target.resolve()

        if project_root not in [target, *target.parents]:
            return {"ok": False, "error": "path_outside_project", "path": str(target)}
        if not target.exists() or not target.is_file():
            return {"ok": False, "error": "file_not_found", "path": str(target)}

        try:
            before = target.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"read_failed:{e}", "path": str(target)}

        before_hash = hashlib.sha256(before.encode("utf-8")).hexdigest()
        expected = str(expected_sha256 or "").strip().lower()
        if expected and expected != before_hash:
            return {
                "ok": False, "error": "sha256_mismatch",
                "expected_sha256": expected, "actual_sha256": before_hash,
                "path": str(target),
            }

        n = before.count(find)
        if n == 0:
            return {"ok": False, "error": "find_not_found", "path": str(target)}

        reps = max(1, int(count or 1))
        after = before.replace(find, str(replace or ""), reps)
        after_hash = hashlib.sha256(after.encode("utf-8")).hexdigest()
        try:
            target.write_text(after, encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"write_failed:{e}", "path": str(target)}

        return {
            "ok": True, "mode": "omo_hash_edit", "path": str(target),
            "replacements": min(reps, n),
            "sha256_before": before_hash, "sha256_after": after_hash,
        }
