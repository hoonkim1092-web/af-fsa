import sys
import argparse

def prune_text(text: str, max_lines: int = 50) -> str:
    lines = text.splitlines(True)
    if len(lines) <= max_lines:
        return text
    
    half = max_lines // 2
    head = "".join(lines[:half])
    tail = "".join(lines[-half:])
    
    omitted = len(lines) - max_lines
    return head + f"\n\n... [System Truncated: {omitted} lines omitted for context compression] ...\n\n" + tail

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Memory Pruner for large text outputs")
    parser.add_argument("--max-lines", type=int, default=50, help="Maximum lines to keep (head/tail)")
    args = parser.parse_args()
    
    data = sys.stdin.read()
    sys.stdout.write(prune_text(data, args.max_lines))
