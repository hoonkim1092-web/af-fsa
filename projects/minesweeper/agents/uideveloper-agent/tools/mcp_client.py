import subprocess
import json
import argparse
import sys

def call_mcp_tool(server_command: str, tool_name: str, args_json: str) -> str:
    """
    MCP (Model Context Protocol) 확장 브릿지 클라이언트.
    외부 MCP 지원 서버(Exa, Grep.app 등)에 JSON-RPC over stdio 방식으로 통신하여 결과를 반환합니다.
    """
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": json.loads(args_json)
            }
        }
        
        proc = subprocess.Popen(
            server_command, 
            shell=True,
            stdin=subprocess.PIPE, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        req_str = json.dumps(request) + "\n"
        stdout, stderr = proc.communicate(input=req_str, timeout=30)
        
        if proc.returncode != 0:
            return f"[MCP Error] Server failed with code {proc.returncode}:\n{stderr}"
            
        return stdout.strip() if stdout.strip() else f"[MCP Warn] Empty response. Error log: {stderr}"
        
    except json.JSONDecodeError:
        return "[MCP Error] Invalid JSON arguments provided."
    except Exception as e:
        return f"[MCP Connection Error] {e}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent Bridge for remote MCP Servers")
    parser.add_argument("-c", "--command", required=True, help="Command to start MCP server (e.g., 'npx @exa/mcp-server')")
    parser.add_argument("-t", "--tool", required=True, help="Tool name exposed by MCP server")
    parser.add_argument("-a", "--args", required=True, help="JSON string of arguments")
    
    parsed = parser.parse_args()
    print(call_mcp_tool(parsed.command, parsed.tool, parsed.args))
