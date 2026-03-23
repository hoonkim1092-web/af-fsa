import os
import re

SENSITIVE_PATTERNS = [
    r"sk-[a-zA-Z0-9]{20,}", r"AIza[0-9A-Za-z-_]{35}", 
    r"ghp_[a-zA-Z0-9]{20,}", r"xoxb-[a-zA-Z0-9-]{10,}"
]

def security_scan(directory: str, logger=print) -> bool:
    """
    Scans a directory for sensitive patterns like API keys.
    Returns True if safe, False if sensitive info is found.
    """
    logger(f"[SECURITY] 🔒 Scanning directory for sensitive data: {directory}")
    is_safe = True
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith((".py", ".md", ".yaml", ".txt", ".json", ".sh")):
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        for pattern in SENSITIVE_PATTERNS:
                            if re.search(pattern, content):
                                logger(f"[SECURITY] ⚠️ Sensitive data found! File: {file}")
                                is_safe = False
                except Exception:
                    pass
    if not is_safe:
        logger("[SECURITY] 🛑 Security policy violation! Action aborted.")
        return False
    return True
