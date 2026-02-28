"""
core/lsp_bridge.py
==================
LSP (Language Server Protocol) 브릿지 — Agent Factory 코어 내장 모듈.

에이전트가 코드의 *구조*를 이해할 수 있게 합니다:
  - find_definition(file, line, col)  → 심볼 정의 위치
  - find_references(file, line, col)  → 사용처 목록
  - get_diagnostics(file)             → 에러/경고
  - get_hover(file, line, col)        → 타입 정보

subprocess로 pyright를 구동하고 JSON-RPC 2.0으로 통신합니다.
"""

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any


class LSPBridge:
    """Lightweight LSP client that communicates with a language server via JSON-RPC."""

    def __init__(self, server_cmd: list[str] | None = None, workspace: str | None = None):
        """
        Args:
            server_cmd: Language server command, e.g. ["pyright-langserver", "--stdio"].
                        Defaults to pyright.
            workspace:  Project root directory. Defaults to cwd.
        """
        if server_cmd is None:
            server_cmd = [sys.executable, "-m", "pyright", "--langserver", "--stdio"]
        self.workspace = os.path.abspath(workspace or os.getcwd())
        self._server_cmd = server_cmd
        self._proc: subprocess.Popen | None = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._responses: dict[int, Any] = {}
        self._reader_thread: threading.Thread | None = None
        self._initialized = False
        self._diagnostics: dict[str, list[dict]] = {}

    # =========================================================================
    # Lifecycle
    # =========================================================================
    def start(self) -> bool:
        """Start the language server process and initialize LSP handshake."""
        if self._proc is not None:
            return True
        try:
            self._proc = subprocess.Popen(
                self._server_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError:
            print("[LSP] Language server not found. Install with: pip install pyright")
            return False

        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        # LSP initialize
        result = self._request("initialize", {
            "processId": os.getpid(),
            "rootUri": self._path_to_uri(self.workspace),
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "hover": {"dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": True},
                }
            },
            "workspaceFolders": [
                {"uri": self._path_to_uri(self.workspace), "name": os.path.basename(self.workspace)}
            ],
        })
        if result is None:
            self.stop()
            return False

        self._notify("initialized", {})
        self._initialized = True
        return True

    def stop(self):
        """Shutdown the language server."""
        if self._proc is None:
            return
        try:
            self._request("shutdown", None, timeout=5)
            self._notify("exit", None)
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        self._initialized = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    # =========================================================================
    # Public API
    # =========================================================================
    def open_file(self, file_path: str) -> bool:
        """Notify the server that a file has been opened."""
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return False
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return False

        self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": self._path_to_uri(abs_path),
                "languageId": self._detect_lang(abs_path),
                "version": 1,
                "text": text,
            }
        })
        time.sleep(0.5)  # Allow server to process
        return True

    def find_definition(self, file_path: str, line: int, col: int) -> list[dict]:
        """
        Find the definition of the symbol at the given position.
        Returns list of {"file": str, "line": int, "col": int}.
        Lines and columns are 0-indexed.
        """
        abs_path = os.path.abspath(file_path)
        self.open_file(abs_path)
        result = self._request("textDocument/definition", {
            "textDocument": {"uri": self._path_to_uri(abs_path)},
            "position": {"line": line, "character": col},
        })
        return self._parse_locations(result)

    def find_references(self, file_path: str, line: int, col: int, include_declaration: bool = True) -> list[dict]:
        """
        Find all references to the symbol at the given position.
        Returns list of {"file": str, "line": int, "col": int}.
        """
        abs_path = os.path.abspath(file_path)
        self.open_file(abs_path)
        result = self._request("textDocument/references", {
            "textDocument": {"uri": self._path_to_uri(abs_path)},
            "position": {"line": line, "character": col},
            "context": {"includeDeclaration": include_declaration},
        })
        return self._parse_locations(result)

    def get_hover(self, file_path: str, line: int, col: int) -> str:
        """
        Get type/documentation info for the symbol at the given position.
        Returns markdown string.
        """
        abs_path = os.path.abspath(file_path)
        self.open_file(abs_path)
        result = self._request("textDocument/hover", {
            "textDocument": {"uri": self._path_to_uri(abs_path)},
            "position": {"line": line, "character": col},
        })
        if not result:
            return ""
        contents = result.get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", "")
        if isinstance(contents, str):
            return contents
        if isinstance(contents, list):
            return "\n".join(c.get("value", str(c)) if isinstance(c, dict) else str(c) for c in contents)
        return str(contents)

    def get_diagnostics(self, file_path: str) -> list[dict]:
        """
        Get diagnostics (errors/warnings) for a file.
        Returns list of {"line": int, "col": int, "severity": str, "message": str}.
        """
        abs_path = os.path.abspath(file_path)
        self.open_file(abs_path)
        time.sleep(1)  # Allow pyright to analyze
        uri = self._path_to_uri(abs_path)
        raw = self._diagnostics.get(uri, [])
        out = []
        severity_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}
        for d in raw:
            rng = d.get("range", {}).get("start", {})
            out.append({
                "line": rng.get("line", 0),
                "col": rng.get("character", 0),
                "severity": severity_map.get(d.get("severity", 3), "info"),
                "message": d.get("message", ""),
            })
        return out

    # =========================================================================
    # JSON-RPC Transport
    # =========================================================================
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send(self, msg: dict):
        if self._proc is None or self._proc.stdin is None:
            return
        body = json.dumps(msg, ensure_ascii=False)
        header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
        try:
            self._proc.stdin.write(header.encode("utf-8"))
            self._proc.stdin.write(body.encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _request(self, method: str, params: Any, timeout: float = 15) -> Any:
        req_id = self._next_id()
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if req_id in self._responses:
                    resp = self._responses.pop(req_id)
                    if "error" in resp:
                        return None
                    return resp.get("result")
            time.sleep(0.05)
        return None

    def _notify(self, method: str, params: Any):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _read_loop(self):
        """Background thread: reads JSON-RPC messages from the server."""
        if self._proc is None or self._proc.stdout is None:
            return
        stdout = self._proc.stdout
        while True:
            try:
                # Read headers
                content_length = 0
                while True:
                    line = stdout.readline()
                    if not line:
                        return  # Server closed
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        break
                    if line_str.lower().startswith("content-length:"):
                        content_length = int(line_str.split(":")[1].strip())
                if content_length == 0:
                    continue
                body = stdout.read(content_length)
                if not body:
                    return
                msg = json.loads(body.decode("utf-8", errors="replace"))

                # Response to a request
                if "id" in msg and ("result" in msg or "error" in msg):
                    with self._lock:
                        self._responses[msg["id"]] = msg

                # Server notification (e.g., diagnostics)
                if "method" in msg and msg["method"] == "textDocument/publishDiagnostics":
                    params = msg.get("params", {})
                    uri = params.get("uri", "")
                    diags = params.get("diagnostics", [])
                    self._diagnostics[uri] = diags

            except Exception:
                return

    # =========================================================================
    # Helpers
    # =========================================================================
    @staticmethod
    def _path_to_uri(path: str) -> str:
        abs_path = os.path.abspath(path).replace("\\", "/")
        if not abs_path.startswith("/"):
            abs_path = "/" + abs_path
        return f"file://{abs_path}"

    @staticmethod
    def _uri_to_path(uri: str) -> str:
        if uri.startswith("file:///"):
            path = uri[8:]  # Windows: file:///C:/...
        elif uri.startswith("file://"):
            path = uri[7:]
        else:
            path = uri
        return path.replace("/", os.sep)

    @staticmethod
    def _detect_lang(path: str) -> str:
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".jsx": "javascriptreact", ".tsx": "typescriptreact",
            ".go": "go", ".rs": "rust", ".java": "java",
        }
        _, ext = os.path.splitext(path.lower())
        return ext_map.get(ext, "plaintext")

    def _parse_locations(self, result: Any) -> list[dict]:
        if result is None:
            return []
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            return []
        out = []
        for loc in result:
            uri = loc.get("uri", loc.get("targetUri", ""))
            rng = loc.get("range", loc.get("targetRange", {}))
            start = rng.get("start", {})
            out.append({
                "file": self._uri_to_path(uri),
                "line": start.get("line", 0),
                "col": start.get("character", 0),
            })
        return out
