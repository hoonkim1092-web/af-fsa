"""
core/agent_reservation.py
==========================
AgentReservationManager — lease 기반 에이전트 점유 관리.

ConversationManager와 DynamicOrchestrator가 동시에 같은 에이전트를
점유하는 충돌을 방지한다. lease는 TTL 만료 시 자동 해제된다.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentLease:
    lease_id: str
    agent_id: str
    reason: str          # "conversation", "orchestrator", "fsa", ...
    holder: str          # 점유 주체 식별자
    expires_at: float    # time.time() + duration
    acquired_at: float = field(default_factory=time.time)


class AgentReservationManager:
    """
    에이전트 점유 lease 관리자.

    사용 예시:
        lease_id = mgr.reserve("himari", duration=300, reason="conversation", holder="conv_room_123")
        if lease_id:
            try:
                # ... 에이전트 사용 ...
            finally:
                mgr.release(lease_id)
    """

    def __init__(self) -> None:
        self._leases: dict[str, AgentLease] = {}   # lease_id → AgentLease
        self._agent_lease: dict[str, str] = {}      # agent_id → lease_id
        self._lock = threading.Lock()

    # ── 공개 API ──────────────────────────────────────────────────────────

    def reserve(
        self,
        agent_id: str,
        *,
        duration: float = 300.0,
        reason: str = "unknown",
        holder: str = "",
    ) -> Optional[str]:
        """
        에이전트 점유 요청.

        Returns:
            lease_id (str) — 성공
            None            — 이미 다른 곳에서 점유 중
        """
        with self._lock:
            self._evict_expired()
            if agent_id in self._agent_lease:
                return None  # 이미 점유 중
            lease_id = uuid.uuid4().hex[:16]
            lease = AgentLease(
                lease_id=lease_id,
                agent_id=agent_id,
                reason=reason,
                holder=holder or reason,
                expires_at=time.time() + duration,
            )
            self._leases[lease_id] = lease
            self._agent_lease[agent_id] = lease_id
            return lease_id

    def reserve_all(
        self,
        agent_ids: list[str],
        *,
        duration: float = 300.0,
        reason: str = "unknown",
        holder: str = "",
    ) -> dict[str, Optional[str]]:
        """
        여러 에이전트를 한 번에 예약.

        Returns:
            {agent_id: lease_id | None}
            하나라도 실패하면 성공한 lease를 모두 롤백.
        """
        acquired: list[str] = []
        result: dict[str, Optional[str]] = {}
        with self._lock:
            self._evict_expired()
            # 사전 충돌 검사
            for agent_id in agent_ids:
                if agent_id in self._agent_lease:
                    # 롤백
                    for lid in acquired:
                        lease = self._leases.pop(lid, None)
                        if lease:
                            self._agent_lease.pop(lease.agent_id, None)
                    return {a: None for a in agent_ids}
            # 일괄 획득
            for agent_id in agent_ids:
                lease_id = uuid.uuid4().hex[:16]
                lease = AgentLease(
                    lease_id=lease_id,
                    agent_id=agent_id,
                    reason=reason,
                    holder=holder or reason,
                    expires_at=time.time() + duration,
                )
                self._leases[lease_id] = lease
                self._agent_lease[agent_id] = lease_id
                acquired.append(lease_id)
                result[agent_id] = lease_id
        return result

    def release(self, lease_id: str) -> bool:
        """lease 해제. Returns True if released."""
        with self._lock:
            lease = self._leases.pop(lease_id, None)
            if lease:
                self._agent_lease.pop(lease.agent_id, None)
                return True
            return False

    def release_all(self, lease_ids: list[str]) -> None:
        """여러 lease 일괄 해제."""
        with self._lock:
            for lease_id in lease_ids:
                lease = self._leases.pop(lease_id, None)
                if lease:
                    self._agent_lease.pop(lease.agent_id, None)

    def is_reserved(self, agent_id: str) -> bool:
        """에이전트가 현재 점유 중인지 확인."""
        with self._lock:
            self._evict_expired()
            return agent_id in self._agent_lease

    def get_lease(self, agent_id: str) -> Optional[AgentLease]:
        """에이전트의 현재 lease 정보 반환."""
        with self._lock:
            self._evict_expired()
            lease_id = self._agent_lease.get(agent_id)
            return self._leases.get(lease_id) if lease_id else None

    def extend(self, lease_id: str, extra_seconds: float) -> bool:
        """lease TTL 연장. Returns True if extended."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease:
                lease.expires_at += extra_seconds
                return True
            return False

    def status(self) -> dict[str, dict]:
        """전체 점유 현황 반환."""
        with self._lock:
            self._evict_expired()
            return {
                agent_id: {
                    "lease_id": lease_id,
                    "reason": self._leases[lease_id].reason,
                    "holder": self._leases[lease_id].holder,
                    "expires_in": round(self._leases[lease_id].expires_at - time.time(), 1),
                }
                for agent_id, lease_id in self._agent_lease.items()
            }

    # ── 내부 유틸 ─────────────────────────────────────────────────────────

    def _evict_expired(self) -> None:
        """만료된 lease 자동 해제 (lock 보유 상태에서 호출)."""
        now = time.time()
        expired = [
            lid for lid, lease in self._leases.items()
            if lease.expires_at <= now
        ]
        for lid in expired:
            lease = self._leases.pop(lid)
            self._agent_lease.pop(lease.agent_id, None)
