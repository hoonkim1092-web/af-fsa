import argparse
import sys
import os
import json

# 현재 디렉토리 기준 mcp_client 임포트
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from mcp_client import call_mcp_tool
except ImportError:
    print("[Error] mcp_client.py not found in the core skills directory.")
    sys.exit(1)

def exa_search(query: str, num_results: int = 3) -> str:
    # Exa MCP 서버 실행 커맨드 (npx 환경)
    server_cmd = "npx -y @modelcontextprotocol/server-exa"
    
    args_dict = {
        "query": query,
        "numResults": num_results
    }
    args_json = json.dumps(args_dict)
    
    print(f"📡 [MCP Server] Exa 서버로 '{query}' 검색을 요청합니다...")
    result = call_mcp_tool(server_cmd, "search", args_json)
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Exa Search Wrapper")
    parser.add_argument("-q", "--query", required=True, help="Search query")
    parser.add_argument("-n", "--num", type=int, default=3, help="Number of results")
    args = parser.parse_args()
    
    if not os.getenv("EXA_API_KEY"):
        print("[MCP Warning] EXA_API_KEY environment variable is missing. Exa MCP might fail if key is required by the server.")
        
    print(exa_search(args.query, args.num))
