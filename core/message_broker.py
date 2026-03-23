"""
core/message_broker.py
======================
에이전트 간 실시간 메시지 브로커.

두 가지 전송 모드를 지원한다:
  - thread 모드: asyncio.Queue 기반 (동일 프로세스 내 스레드 간)
  - subprocess 모드: TCP 루프백 소켓 (127.0.0.1, 동적 포트)

DynamicOrchestrator가 브로커를 생성하고,
각 에이전트(스레드 또는 서브프로세스)가 채널을 구독/발행한다.

채널 구조:
  - agent.{agent_id}.inbox  — 개별 에이전트 수신
  - broadcast               — 전체 브로드캐스트
  - skill.request            — 스킬 요청/응답
  - task.status              — 태스크 상태 변경 알림
"""
from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
import uuid
from typing import Any


def _log(step: str, msg: str) -> None:
    print(f"[MessageBroker:{step}] {msg}")


# ---------------------------------------------------------------------------
# Message envelope
# ---------------------------------------------------------------------------

def make_message(
    channel: str,
    body: Any,
    *,
    sender: str = "",
    msg_type: str = "event",
    correlation_id: str = "",
) -> dict[str, Any]:
    """표준 메시지 envelope 생성."""
    return {
        "id": uuid.uuid4().hex[:12],
        "channel": channel,
        "sender": sender,
        "type": msg_type,
        "body": body,
        "correlation_id": correlation_id or "",
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# MessageBroker (thread 모드: in-process pub/sub)
# ---------------------------------------------------------------------------

class MessageBroker:
    """에이전트 간 실시간 메시지 브로커 (스레드 모드)."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, dict[str, asyncio.Queue]] = {}
        self._lock = threading.Lock()
        # request-response를 위한 pending futures
        self._pending_requests: dict[str, asyncio.Future] = {}
        # TCP 서버 (subprocess 모드)
        self._tcp_server: asyncio.AbstractServer | None = None
        self._tcp_port: int = 0
        self._tcp_clients: dict[str, asyncio.StreamWriter] = {}

    # ── Pub/Sub (thread 모드) ──

    def subscribe(self, channel: str, subscriber_id: str = "") -> asyncio.Queue:
        """채널을 구독하고 메시지 수신용 Queue를 반환한다."""
        sub_id = subscriber_id or uuid.uuid4().hex[:8]
        with self._lock:
            if channel not in self._subscriptions:
                self._subscriptions[channel] = {}
            q: asyncio.Queue = asyncio.Queue()
            self._subscriptions[channel][sub_id] = q
        return q

    def unsubscribe(self, channel: str, subscriber_id: str) -> None:
        """채널 구독 해제."""
        with self._lock:
            subs = self._subscriptions.get(channel, {})
            subs.pop(subscriber_id, None)

    async def publish(self, channel: str, message: dict[str, Any]) -> int:
        """채널에 메시지를 발행하고 수신자 수를 반환한다."""
        with self._lock:
            subs = dict(self._subscriptions.get(channel, {}))
        count = 0
        for q in subs.values():
            await q.put(message)
            count += 1

        # broadcast 채널로도 전달 (채널이 broadcast가 아닌 경우)
        if channel != "broadcast":
            with self._lock:
                broadcast_subs = dict(self._subscriptions.get("broadcast", {}))
            for q in broadcast_subs.values():
                await q.put(message)

        # TCP 클라이언트에도 전달
        await self._relay_to_tcp_clients(channel, message)

        # request-response correlation 처리
        corr_id = message.get("correlation_id", "")
        if corr_id and message.get("type") == "response":
            fut = self._pending_requests.pop(corr_id, None)
            if fut and not fut.done():
                fut.set_result(message)

        return count

    async def request(
        self,
        channel: str,
        body: Any,
        *,
        sender: str = "",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """동기식 요청-응답. correlation_id로 응답을 매칭한다."""
        corr_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_requests[corr_id] = fut

        msg = make_message(
            channel, body, sender=sender, msg_type="request", correlation_id=corr_id,
        )
        await self.publish(channel, msg)

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(corr_id, None)
            return make_message(channel, {"error": "timeout"}, msg_type="error", correlation_id=corr_id)

    # ── TCP 서버 (subprocess 모드) ──

    async def start_tcp_server(self) -> int:
        """TCP 루프백 서버를 시작하고 포트를 반환한다."""
        if self._tcp_server:
            return self._tcp_port

        self._tcp_server = await asyncio.start_server(
            self._handle_tcp_client,
            host="127.0.0.1",
            port=0,  # OS가 동적 포트 할당
        )
        sock = self._tcp_server.sockets[0]
        self._tcp_port = sock.getsockname()[1]
        _log("TCP", f"서버 시작: 127.0.0.1:{self._tcp_port}")
        return self._tcp_port

    def get_broker_address(self) -> str:
        """서브프로세스가 연결할 주소 (host:port) 반환."""
        if self._tcp_port:
            return f"127.0.0.1:{self._tcp_port}"
        return ""

    async def _handle_tcp_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        """TCP 클라이언트 연결 핸들러."""
        client_id = ""
        recv_task = None
        send_task = None
        try:
            # 첫 메시지: 핸드셰이크 (agent_id 등록)
            handshake = await self._tcp_read_msg(reader)
            client_id = handshake.get("sender", uuid.uuid4().hex[:8])
            self._tcp_clients[client_id] = writer
            _log("TCP", f"클라이언트 연결: {client_id}")

            # agent inbox 자동 구독
            inbox_channel = f"agent.{client_id}.inbox"
            inbox_q = self.subscribe(inbox_channel, client_id)

            # 수신 루프 (클라이언트 → 브로커)
            recv_task = asyncio.create_task(self._tcp_recv_loop(reader, client_id))
            # 송신 루프 (브로커 → 클라이언트)
            send_task = asyncio.create_task(self._tcp_send_loop(inbox_q, writer))

            # recv가 끝나면 (클라이언트 종료) send도 취소
            done, pending = await asyncio.wait(
                [recv_task, send_task], return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
        except (ConnectionResetError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        finally:
            for t in [recv_task, send_task]:
                if t and not t.done():
                    t.cancel()
            self._tcp_clients.pop(client_id, None)
            if client_id:
                self.unsubscribe(f"agent.{client_id}.inbox", client_id)
                _log("TCP", f"클라이언트 해제: {client_id}")
            writer.close()

    async def _tcp_recv_loop(self, reader: asyncio.StreamReader, client_id: str) -> None:
        """TCP 클라이언트로부터 메시지 수신 → 브로커에 publish."""
        while True:
            msg = await self._tcp_read_msg(reader)
            if not msg:
                break
            msg["sender"] = client_id
            channel = msg.get("channel", "broadcast")
            await self.publish(channel, msg)

    async def _tcp_send_loop(self, queue: asyncio.Queue, writer: asyncio.StreamWriter) -> None:
        """브로커 큐의 메시지를 TCP 클라이언트로 전송."""
        while True:
            msg = await queue.get()
            try:
                await self._tcp_write_msg(writer, msg)
            except (ConnectionResetError, BrokenPipeError):
                break

    async def _relay_to_tcp_clients(self, channel: str, message: dict) -> None:
        """구독 채널에 매칭되는 TCP 클라이언트에 메시지 전달."""
        # agent.{id}.inbox 채널이면 해당 클라이언트에 직접 전달
        if channel.startswith("agent.") and channel.endswith(".inbox"):
            target_id = channel.split(".")[1]
            writer = self._tcp_clients.get(target_id)
            if writer:
                try:
                    await self._tcp_write_msg(writer, message)
                except (ConnectionResetError, BrokenPipeError):
                    self._tcp_clients.pop(target_id, None)

    # ── TCP 프레이밍: 4바이트 길이 헤더 + JSON ──

    @staticmethod
    async def _tcp_read_msg(reader: asyncio.StreamReader) -> dict:
        header = await reader.readexactly(4)
        length = struct.unpack("!I", header)[0]
        if length > 10 * 1024 * 1024:  # 10MB 제한
            return {}
        data = await reader.readexactly(length)
        return json.loads(data.decode("utf-8"))

    @staticmethod
    async def _tcp_write_msg(writer: asyncio.StreamWriter, msg: dict) -> None:
        payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        writer.write(struct.pack("!I", len(payload)) + payload)
        await writer.drain()

    # ── Shutdown ──

    async def shutdown(self) -> None:
        """브로커를 종료한다."""
        if self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
            self._tcp_server = None
        for writer in self._tcp_clients.values():
            writer.close()
        self._tcp_clients.clear()
        self._subscriptions.clear()
        self._pending_requests.clear()
        _log("SHUTDOWN", "브로커 종료 완료")
