import subprocess
import argparse
import sys
import shutil

def run_ast_grep(pattern: str, lang: str, path: str = ".") -> str:
    if not shutil.which("sg"):
        return "[AST-Grep Error] 'sg' command not found. Please install via: npm install -g @ast-grep/cli"
        
    cmd = ["sg", "-p", pattern, "-l", lang, path]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return res.stdout if res.stdout else "No matches found."
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:
            return "No matches found."
        return f"[AST-Grep Error] Execution failed: {e.stderr}"
    except Exception as e:
        return f"[AST-Grep Error] Unknown fatal error: {e}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AST-Grep (Structural Search) wrapper for Agents")
    parser.add_argument("-p", "--pattern", required=True, help="Structural pattern to search")
    parser.add_argument("-l", "--lang", required=True, help="Language (e.g., python, typescript)")
    parser.add_argument("--path", default=".", help="Target directory or file")
    args = parser.parse_args()
    
    output = run_ast_grep(args.pattern, args.lang, args.path)
    print(output)
