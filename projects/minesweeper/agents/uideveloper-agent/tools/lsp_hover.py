import os
import re
import argparse
import sys

def lsp_goto_definition(symbol: str, target_dir: str = ".") -> str:
    """
    경량화된 LSP Goto Definition 시뮬레이터.
    실제 LSP 데몬 없이 정규식 및 단순 스캔을 통해 파이썬/JS 함수 및 클래스 정의부를 찾습니다.
    """
    results = []
    
    # 파이썬 및 JS/TS 정의부 정규식
    py_pattern = re.compile(rf"^\s*(def|class)\s+{symbol}\b")
    js_pattern = re.compile(rf"^\s*(function|class|const|let|var)\s+{symbol}\b")
    
    for root, dirs, files in os.walk(target_dir):
        # Ignore common artifacts
        if any(ignore in root for ignore in [".git", "node_modules", "__pycache__", "venv"]):
            continue
            
        for file in files:
            if not file.endswith((".py", ".js", ".ts", ".jsx", ".tsx")):
                continue
                
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, 1):
                        if py_pattern.search(line) or js_pattern.search(line):
                            results.append(f"{file_path}:{line_num}\n  {line.strip()}")
            except Exception:
                pass
                
    if not results:
        return f"[LSP Simulate] No definition found for '{symbol}' in '{target_dir}'"
        
    return "[LSP Simulate Definition Results]\n" + "\n".join(results)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lightweight LSP Goto Definition Simulator")
    parser.add_argument("-s", "--symbol", required=True, help="Function or class name to find definition for")
    parser.add_argument("-d", "--dir", default=".", help="Target directory to scan")
    args = parser.parse_args()
    
    print(lsp_goto_definition(args.symbol, args.dir))
