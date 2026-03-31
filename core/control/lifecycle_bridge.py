"""
core/control/lifecycle_bridge.py
==================================
CanonicalLifecycleBridge — provider-specific hook을 canonical event로 변환.

provider native 이름은 보존, canonical 정규화는 bridge 내부에서만 수행.
session_adapter.handle_hook_event()를 교체하지 않음.
bridge는 session_adapter 호출 후 추가 처리만 수행.

canonical event 종류:
  "session_start"  — 세션 시작
  "prompt_submit"  — 사용자 입력 제출
  "pre_compact"    — 컨텍스트 압축 직전
  "session_end"    — 세션 종료
  "after_agent"    — 에이전트 완료 후 (Gemini only)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict


# Provider native name → canonical name 매핑
CANONICAL_EVENTS: dict[str, str] = {
    # Claude / Claude Code
    "SessionStart":       "session_start",
    "UserPromptSubmit":   "prompt_submit",
    "PreCompact":         "pre_compact",
    "Stop":               "session_end",
    # Gemini equivalents
    "BeforeAgent":        "session_start",
    "PreCompress":        "pre_compact",
    "SessionEnd":         "session_end",
    "AfterAgent":         "after_agent",
    # 추가 provider 확장 지점
}

# 알려진 canonical event 이름 집합
_CANONICAL_NAMES: frozenset[str] = frozenset(CANONICAL_EVENTS.values())


@dataclass
class CanonicalEvent:
    """provider-specific hook을 canonical 형식으로 정규화한 이벤트."""
    canonical_name: str     # "session_start" | "prompt_submit" | "pre_compact" | "session_end" | "after_agent"
    provider_id: str        # 원본 프로바이더 식별자
    native_name: str        # 원본 hook 이름 (보존)
    timestamp: str
    workspace: str
    run_id: str
    payload: dict = field(default_factory=dict)  # provider-specific 데이터

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CanonicalEvent":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def is_known(self) -> bool:
        """canonical_name이 알려진 이벤트인지 확인한다."""
        return self.canonical_name in _CANONICAL_NAMES


class CanonicalLifecycleBridge:
    """
    provider-specific hook을 canonical event로 변환하고 lifecycle 처리를 수행.

    사용 패턴:
      bridge = CanonicalLifecycleBridge(workspace, run_id)
      event = bridge.normalize_event(provider_id, native_name, payload)
      bridge.on_canonical_event(event)
    """

    def __init__(self, workspace: str, run_id: str):
        self._workspace = workspace
        self._run_id = run_id

    def normalize_event(
        self,
        provider_id: str,
        native_name: str,
        payload: dict,
        workspace: str = "",
        run_id: str = "",
    ) -> CanonicalEvent:
        """
        native hook 이름을 canonical로 변환.
        미등록 이름은 그대로 pass-through (lowercase 변환).
        """
        from core.utils import now_iso

        ws = workspace or self._workspace
        rid = run_id or self._run_id

        # 등록된 매핑 먼저 확인
        canonical = CANONICAL_EVENTS.get(native_name)
        if canonical is None:
            # pass-through: lowercase 정규화만
            canonical = native_name.lower()

        return CanonicalEvent(
            canonical_name=canonical,
            provider_id=provider_id,
            native_name=native_name,
            timestamp=now_iso(),
            workspace=ws,
            run_id=rid,
            payload=payload,
        )

    def on_canonical_event(self, event: CanonicalEvent) -> None:
        """
        canonical event 처리:
          - session_start: ContinuitySnapshot 갱신
          - pre_compact: 현재 진행 상태 checkpoint 저장
          - session_end: RunLedger 갱신 트리거
        """
        if event.canonical_name == "session_start":
            self._handle_session_start(event)
        elif event.canonical_name == "pre_compact":
            self._handle_pre_compact(event)
        elif event.canonical_name == "session_end":
            self._handle_session_end(event)
        elif event.canonical_name == "after_agent":
            self._handle_after_agent(event)
        # prompt_submit 및 미등록 이벤트는 처리 없음 (log only)

        self._log_event(event)

    def dispatch(
        self,
        provider_id: str,
        native_name: str,
        payload: dict,
        workspace: str = "",
        run_id: str = "",
    ) -> CanonicalEvent:
        """normalize_event + on_canonical_event를 한 번에 수행한다."""
        event = self.normalize_event(provider_id, native_name, payload, workspace, run_id)
        self.on_canonical_event(event)
        return event

    # ── 이벤트 핸들러 ──

    def _handle_session_start(self, event: CanonicalEvent) -> None:
        """세션 시작: ContinuitySnapshot 갱신."""
        try:
            from core.control.continuity_snapshot import ContinuitySnapshotBuilder
            builder = ContinuitySnapshotBuilder()
            builder.build(event.workspace)
        except Exception as exc:
            print(f"[LifecycleBridge] session_start snapshot failed: {exc}")

    def _handle_pre_compact(self, event: CanonicalEvent) -> None:
        """컨텍스트 압축 전: 진행 상태를 checkpoint에 저장한다."""
        try:
            checkpoints_dir = os.path.join(
                event.workspace, ".af_runtime", "control", "checkpoints"
            )
            os.makedirs(checkpoints_dir, exist_ok=True)
            checkpoint_path = os.path.join(checkpoints_dir, f"{event.run_id}.json")
            tmp = checkpoint_path + ".tmp"
            checkpoint_data = {
                "run_id": event.run_id,
                "provider_id": event.provider_id,
                "timestamp": event.timestamp,
                "workspace": event.workspace,
                "payload": event.payload,
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, checkpoint_path)
        except Exception as exc:
            print(f"[LifecycleBridge] pre_compact checkpoint failed: {exc}")

    def _handle_session_end(self, event: CanonicalEvent) -> None:
        """세션 종료: RunLedger에 종료 신호를 남긴다."""
        try:
            from core.control.run_ledger import RunLedger
            ledger = RunLedger(event.workspace)
            # 이미 닫혀있을 수 있으므로 update만 시도 (실패해도 무시)
            ledger.update_run(
                run_id=event.run_id,
                state="session_ended",
                metadata={"session_end_at": event.timestamp, "provider": event.provider_id},
            )
        except Exception as exc:
            print(f"[LifecycleBridge] session_end ledger update failed: {exc}")

    def _handle_after_agent(self, event: CanonicalEvent) -> None:
        """에이전트 완료 후 처리 (Gemini only). ContinuitySnapshot 갱신."""
        try:
            from core.control.continuity_snapshot import ContinuitySnapshotBuilder
            builder = ContinuitySnapshotBuilder()
            builder.build(event.workspace)
        except Exception as exc:
            print(f"[LifecycleBridge] after_agent snapshot failed: {exc}")

    # 로그 로테이션 설정
    _LOG_MAX_BYTES = 1 * 1024 * 1024   # 1 MB
    _LOG_KEEP_LINES = 500              # 로테이션 후 보존할 최신 줄 수

    def _log_event(self, event: CanonicalEvent) -> None:
        """이벤트를 lifecycle_events.jsonl에 기록한다. 1 MB 초과 시 로테이션."""
        try:
            log_dir = os.path.join(event.workspace, ".af_runtime", "control")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "lifecycle_events.jsonl")

            # 로테이션 체크: 파일이 임계값을 초과하면 최신 N줄만 남김
            if os.path.isfile(log_path) and os.path.getsize(log_path) > self._LOG_MAX_BYTES:
                self._rotate_log(log_path)

            line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass  # 로그 실패는 silent

    def _rotate_log(self, log_path: str) -> None:
        """로그 파일에서 최신 _LOG_KEEP_LINES 줄만 남기고 truncate한다."""
        try:
            with open(log_path, encoding="utf-8") as f:
                lines = f.readlines()
            keep = lines[-self._LOG_KEEP_LINES:]
            tmp = log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, log_path)
        except Exception:
            pass  # 로테이션 실패는 silent (다음 기회에 재시도)


__all__ = ["CanonicalEvent", "CanonicalLifecycleBridge", "CANONICAL_EVENTS"]
