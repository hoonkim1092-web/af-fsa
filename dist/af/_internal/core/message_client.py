"""
core/message_client.py
======================
서브프로세스(agent_worker)용 MessageBroker TCP 클라이언트.

agent_worker.py에서 생성하여 브로커와 실시간 통신한다.

사용:
    client = MessageClient("127.0.0.1:12345", agent_id="engineer_01")
    await client.connect()
    await client.send("task.status", {"status": "started"})
    msg = await client.receive(timeout=5.0)
    await client.close()
"""
from __future__ import annotations

import asyncio
import json
import struct
import time
import uuid
from typing import Any

from core.message_broker import make_message


def _log(step: str, msg: str) -> None:
    print(f"[MessageClient:{step}] {msg}")


class MessageClient:
    """서브프로세스용 브로커 TCP 클라이언트."""

    def __init__(self, broker_address: str, agent_id: str = "") -> None:
        self.agent_id = agent_id or uuid.uuid4().hex[:8]
        host_port = broker_address.split(":")
        self._host = host_port[0] if host_port else "127.0.0.1"
        self._port = int(host_port[1]) if len(host_port) > 1 else 0
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._connected = False
        # request_skill()용 pending response 추적
        # correlation_id → Future
        self._pending_responses: dict[str, asyncio.Future] = {}

    async def connect(self) -> bool:
        """브로커에 TCP 연결하고 핸드셰이크를 수행한다."""
        if not self._port:
            _log("CONNECT", "브로커 주소가 없습니다")
            return False
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port,
            )
            # 핸드셰이크: agent_id 전송
            handshake = make_message(
                "handshake", {"agent_id": self.agent_id},
                sender=self.agent_id, msg_type="handshake",
            )
            await self._write_msg(handshake)
            self._connected = True
            # 백그라운드 수신 루프 시작
            self._recv_task = asyncio.create_task(self._recv_loop())
            _log("CONNECT", f"브로커 연결 성공: {self._host}:{self._port}")
            return True
        except (OSError, ConnectionRefusedError) as exc:
            _log("CONNECT", f"브로커 연결 실패: {exc}")
            return False

    async def send(self, channel: str, body: Any, *, msg_type: str = "event") -> None:
        """채널에 메시지를 전송한다."""
        if not self._connected or not self._writer:
            _log("SEND", "연결되지 않은 상태에서 전송 시도")
            return
        msg = make_message(channel, body, sender=self.agent_id, msg_type=msg_type)
        await self._write_msg(msg)

    async def receive(self, timeout: float | None = None) -> dict[str, Any] | None:
        """수신 큐에서 메시지를 가져온다."""
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._inbox.get(), timeout=timeout)
            return await self._inbox.get()
        except asyncio.TimeoutError:
            return None

    async def request_skill(
        self,
        skill_name: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """스킬 요청을 보내고 응답을 기다린다.
        _pending_responses 딕셔너리로 correlation_id를 추적하여
        다른 수신 메시지를 inbox에서 소비하지 않는다."""
        if not self._connected or not self._writer:
            return {"error": "not_connected"}

        corr_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_responses[corr_id] = fut

        msg = make_message(
            "skill.request",
            {"skill_name": skill_name, "params": params or {}},
            sender=self.agent_id,
            msg_type="request",
            correlation_id=corr_id,
        )
        await self._write_msg(msg)

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_responses.pop(corr_id, None)
            return {"error": "timeout", "correlation_id": corr_id}

    async def send_status(self, status: str, detail: str = "") -> None:
        """태스크 상태 변경을 브로커에 알린다."""
        await self.send("task.status", {
            "agent_id": self.agent_id,
            "status": status,
            "detail": detail,
            "timestamp": time.time(),
        })

    async def close(self) -> None:
        """연결을 종료한다."""
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        # pending futures 취소
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.cancel()
        self._pending_responses.clear()
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
        self._reader = None

    # ── 내부 ──

    async def _recv_loop(self) -> None:
        """백그라운드에서 브로커 메시지를 수신한다.
        correlation_id가 pending request와 매칭되면 Future를 resolve하고,
        그렇지 않으면 inbox에 넣는다."""
        while self._connected and self._reader:
            try:
                msg = await self._read_msg()
                if not msg:
                    continue
                # pending request 응답 처리
                corr_id = msg.get("correlation_id", "")
                if corr_id and msg.get("type") == "response":
                    fut = self._pending_responses.pop(corr_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                        continue
                await self._inbox.put(msg)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                self._connected = False
                break
            except asyncio.CancelledError:
                break

    async def _read_msg(self) -> dict:
        if not self._reader:
            return {}
        header = await self._reader.readexactly(4)
        length = struct.unpack("!I", header)[0]
        if length > 10 * 1024 * 1024:
            return {}
        data = await self._reader.readexactly(length)
        return json.loads(data.decode("utf-8"))

    async def _write_msg(self, msg: dict) -> None:
        if not self._writer:
            return
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        self._writer.write(struct.pack("!I", len(payload)) + payload)
        await self._writer.drain()
