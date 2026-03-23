"""
core/mcp_adapter.py
===================
MCP (Model Context Protocol) 클라이언트 어댑터.

MCP 서버를 SkillRegistry에 스킬처럼 등록하여
Agent Factory 에이전트가 수백 개의 써드파티 MCP 서버를 즉시 활용할 수 있게 합니다.

MCP 프로토콜: JSON-RPC 2.0 over stdio (subprocess 방식)
참고: https://modelcontextprotocol.io/

설정 파일: {workspace}/mcp_servers.yaml (없으면 빈 설정)

mcp_servers.yaml 형식:
    servers:
      - name: filesystem
        command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/path"]
        timeout: 30
      - name: github
        command: ["npx", "-y", "@modelcontextprotocol/server-github"]
        env:
          GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
        timeout: 30

사용:
    adapter = MCPAdapter()
    await adapter.discover_all(workspace=".")
    tools = adapter.get_tool_functions()
    # tools를 AgentRunner의 tool_functions에 추가하면 됨
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import yaml


_MCP_CONFIG_FILE = "mcp_servers.yaml"
_DEFAULT_TIMEOUT = 30


def _log(msg: str) -> None:
    print(f"[MCPAdapter] {msg}")


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 헬퍼
# ---------------------------------------------------------------------------

def _make_request(method: str, params: Any = None, req_id: str | None = None) -> str:
    return json.dumps({
        "jsonrpc": "2.0",
        "id": req_id or uuid.uuid4().hex[:8],
        "method": method,
        "params": params or {},
    }, ensure_ascii=False)


def _make_notification(method: str, params: Any = None) -> str:
    return json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# MCPServerConnection — 단일 MCP 서버 stdio 연결
# ---------------------------------------------------------------------------

class MCPServerConnection:
    """stdio 기반 MCP 서버와 JSON-RPC 통신을 담당하는 클래스."""

    def __init__(
        self,
        name: str,
        command: list[str],
        env: dict[str, str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.name = name
        self.command = command
        self.env = env or {}
        self.timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._tools: list[dict] = []

    async def start(self) -> bool:
        """서버 프로세스를 시작하고 초기화 핸드셰이크를 수행한다."""
        try:
            merged_env = {**os.environ, **{
                k: os.path.expandvars(v) for k, v in self.env.items()
            }}
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
            )
        except FileNotFoundError as exc:
            _log(f"[{self.name}] 명령어를 찾을 수 없음: {exc}")
            return False
        except Exception as exc:
            _log(f"[{self.name}] 프로세스 시작 실패: {exc}")
            return False

        # 초기화 요청
        try:
            init_req = _make_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-factory", "version": "1.0"},
            })
            await self._send(init_req)
            resp = await self._recv()
            if resp.get("error"):
                _log(f"[{self.name}] initialize 에러: {resp['error']}")
                # Bug fix: 핸드셰이크 실패 시 이미 시작된 프로세스 정리 (좀비 방지)
                await self.stop()
                return False

            # initialized 알림
            await self._send(_make_notification("notifications/initialized"))
            _log(f"[{self.name}] 연결 성공")
            return True
        except (asyncio.TimeoutError, Exception) as exc:
            _log(f"[{self.name}] 핸드셰이크 실패: {exc}")
            # Bug fix: 예외 발생 시에도 프로세스 정리
            await self.stop()
            return False

    async def discover_tools(self) -> list[dict]:
        """tools/list RPC로 사용 가능한 도구 목록을 조회한다."""
        try:
            req = _make_request("tools/list")
            await self._send(req)
            resp = await asyncio.wait_for(self._recv(), timeout=self.timeout)
            raw_tools = resp.get("result", {}).get("tools", [])
            self._tools = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": t.get("inputSchema", {}),
                    "server": self.name,
                }
                for t in raw_tools
                if t.get("name")
            ]
            _log(f"[{self.name}] 발견된 도구: {[t['name'] for t in self._tools]}")
            return self._tools
        except Exception as exc:
            _log(f"[{self.name}] tools/list 실패: {exc}")
            return []

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """tools/call RPC로 도구를 실행하고 결과를 반환한다."""
        req_id = uuid.uuid4().hex[:8]
        req = _make_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, req_id=req_id)
        await self._send(req)
        try:
            resp = await asyncio.wait_for(self._recv(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return {"error": f"MCP tool timeout: {tool_name}"}

        if resp.get("error"):
            return {"error": resp["error"]}

        result = resp.get("result", {})
        content = result.get("content", [])
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            return "\n".join(texts) if texts else result
        return result

    async def stop(self) -> None:
        """서버 프로세스를 종료한다."""
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    # ── 내부 ──

    async def _send(self, data: str) -> None:
        if not self._proc or not self._proc.stdin:
            return
        line = (data + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    async def _recv(self) -> dict:
        if not self._proc or not self._proc.stdout:
            return {}
        try:
            line = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=self.timeout,
            )
            return json.loads(line.decode("utf-8").strip()) if line.strip() else {}
        except (asyncio.TimeoutError, json.JSONDecodeError):
            return {}


# ---------------------------------------------------------------------------
# MCPAdapter — 여러 서버 관리 + SkillRegistry 연동
# ---------------------------------------------------------------------------

class MCPAdapter:
    """MCP 서버 연결을 관리하고 Agent Factory에 도구를 노출하는 어댑터."""

    def __init__(self) -> None:
        self._connections: dict[str, MCPServerConnection] = {}
        self._tool_index: dict[str, tuple[str, dict]] = {}  # tool_name → (server_name, tool_meta)

    # ── 설정 로드 ──

    @staticmethod
    def load_config(workspace: str) -> list[dict]:
        """mcp_servers.yaml에서 서버 설정을 로드한다. 없으면 빈 리스트."""
        config_path = os.path.join(workspace, _MCP_CONFIG_FILE)
        if not os.path.isfile(config_path):
            return []
        try:
            with open(config_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            return data.get("servers", []) or []
        except Exception as exc:
            _log(f"설정 파일 로드 실패: {exc}")
            return []

    # ── 서버 시작 + 도구 발견 ──

    async def discover_all(self, workspace: str = ".") -> int:
        """모든 MCP 서버에 연결하고 도구를 발견한다. 발견된 도구 수 반환."""
        configs = self.load_config(workspace)
        if not configs:
            return 0

        total = 0
        for cfg in configs:
            name = str(cfg.get("name") or "").strip()
            command = cfg.get("command")
            if not name or not isinstance(command, list) or not command:
                _log(f"잘못된 서버 설정 스킵: {cfg}")
                continue

            conn = MCPServerConnection(
                name=name,
                command=command,
                env=cfg.get("env") or {},
                timeout=float(cfg.get("timeout") or _DEFAULT_TIMEOUT),
            )
            if not await conn.start():
                continue

            tools = await conn.discover_tools()
            self._connections[name] = conn
            for tool in tools:
                tool_name = f"mcp_{name}_{tool['name']}"
                if tool_name in self._tool_index:
                    _log(f"경고: 도구 이름 충돌 '{tool_name}' — 기존 항목 덮어씀")
                self._tool_index[tool_name] = (name, tool)
            total += len(tools)

        _log(f"총 {total}개 MCP 도구 등록 완료 ({len(self._connections)}개 서버)")
        return total

    # ── Tool 함수 생성 ──

    def get_tool_functions(self) -> list:
        """Agent Factory tool_functions 형식의 호출 가능한 함수 목록을 반환한다."""
        funcs = []
        for tool_name, (server_name, tool_meta) in self._tool_index.items():
            funcs.append(self._make_tool_func(tool_name, server_name, tool_meta))
        return funcs

    def _make_tool_func(self, tool_name: str, server_name: str, tool_meta: dict):
        """MCP tool을 Python callable로 래핑한다.

        Bug fix: asyncio.run()은 이미 실행 중인 이벤트루프에서 호출하면 RuntimeError.
        AgentRunner Gemini ReAct 루프는 동기 컨텍스트에서 도구를 호출하므로 asyncio.run() 사용.
        만약 이미 루프가 있으면 스레드 풀에서 새 루프를 생성해 실행.
        """
        conn = self._connections[server_name]
        original_name = tool_meta["name"]
        description = tool_meta.get("description", "")

        def _call(**kwargs) -> Any:
            """MCP 도구 호출 래퍼."""
            coro = conn.call_tool(original_name, kwargs)
            try:
                # 실행 중인 이벤트루프 확인
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is None:
                # 이벤트루프 없음 — 직접 asyncio.run()
                return asyncio.run(coro)
            else:
                # 이미 실행 중인 루프가 있음 — 별도 스레드에서 새 루프 실행
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, coro)
                    return future.result(timeout=conn.timeout)

        _call.__name__ = tool_name
        _call.__doc__ = f"[MCP:{server_name}] {description}"
        return _call

    # ── SkillRegistry 연동 ──

    def register_to_skill_registry(self, registry: Any) -> int:
        """발견된 MCP 도구를 SkillRegistry에 등록한다. 등록 수 반환."""
        registered = 0
        for tool_name, (server_name, tool_meta) in self._tool_index.items():
            try:
                registry.register_skill(
                    skill_id=tool_name,
                    skill_meta={
                        "name": tool_name,
                        "description": tool_meta.get("description", ""),
                        "type": "mcp",
                        "source": server_name,
                        "capabilities": [tool_meta["name"]],
                        "input_schema": tool_meta.get("input_schema", {}),
                    },
                )
                registered += 1
            except Exception as exc:
                _log(f"SkillRegistry 등록 실패 ({tool_name}): {exc}")
        return registered

    # ── 종료 ──

    async def shutdown(self) -> None:
        """모든 MCP 서버 연결을 종료한다."""
        for conn in self._connections.values():
            try:
                await conn.stop()
            except Exception:
                pass
        self._connections.clear()
        self._tool_index.clear()
        _log("모든 MCP 서버 연결 종료")


# ---------------------------------------------------------------------------
# 편의 함수
# ---------------------------------------------------------------------------

def load_mcp_tools_sync(workspace: str = ".") -> list:
    """동기 방식으로 MCP 도구를 로드한다. 이벤트루프가 없는 환경에서 사용."""
    adapter = MCPAdapter()
    configs = MCPAdapter.load_config(workspace)
    if not configs:
        return []
    try:
        asyncio.run(adapter.discover_all(workspace=workspace))
        return adapter.get_tool_functions()
    except RuntimeError:
        # 이미 실행 중인 이벤트루프가 있으면 스킵
        _log("이벤트루프 충돌로 MCP 도구 로드 스킵")
        return []
