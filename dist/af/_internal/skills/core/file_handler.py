import os

def read_file(ctx, path: str) -> str:
    """
    [CRITICAL TOOL] Reads the content of a file. Use this to examine existing code.
    
    Args:
        path (str): The path to the file to read (relative to workspace).
    """
    workspace = ctx.get("workspace", ".")
    full_path = os.path.join(workspace, path)
    if not os.path.exists(full_path):
        return f"[Error] File not found: {path} (Resolved: {full_path})"
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"[Error] Failed to read file: {e}"

def write_file(ctx, path: str, content: str) -> str:
    """
    [CRITICAL TOOL] Writes content to a file. Use this to SAVE your progress, create index.html, style.css, script.js, etc.
    Always provide the FULL file content when writing.
    
    Args:
        path (str): The path to the file to write (relative to workspace).
        content (str): The complete content to write to the file.
    """
    workspace = ctx.get("workspace", ".")
    full_path = os.path.join(workspace, path)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(full_path)), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"[Success] File written to {path}"
    except Exception as e:
        return f"[Error] Failed to write file: {e}"

def list_files(ctx, path: str = ".") -> str:
    """
    Lists files in a directory to understand the project structure.
    
    Args:
        path (str): The directory to list (relative to workspace).
    """
    workspace = ctx.get("workspace", ".")
    full_path = os.path.join(workspace, path)
    if not os.path.exists(full_path):
        return f"[Error] Path not found: {path}"
    try:
        items = os.listdir(full_path)
        return "\n".join(items)
    except Exception as e:
        return f"[Error] Failed to list files: {e}"
