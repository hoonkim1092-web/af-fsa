import hashlib
import os

class HashlineEditor:
    """
    (P2) Hashline Editor Core
    Employs content-hashing per line to ensure mutations are applied
    exactly where intended, preventing the classic 'stale line number' problem
    inherent in multi-agent parallel workflows.
    """
    
    @staticmethod
    def _compute_line_hash(line: str) -> str:
        """Computes a stable 8-char hex hash for a single line of text."""
        # Normalize line endings and whitespace for stability
        normalized = line.rstrip()
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:8]

    @classmethod
    def format_file_with_hashes(cls, file_path: str) -> str:
        """
        Reads a file and returns its content with hashline prefixes.
        Format: LINE_NUM#HASH | content
        """
        if not os.path.exists(file_path):
            return f"Error: File {file_path} not found."
            
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        formatted_lines = []
        for i, line in enumerate(lines, start=1):
            line_hash = cls._compute_line_hash(line)
            # Remove the actual newline from the string for neat display
            clean_str = line.rstrip('\n')
            formatted_lines.append(f"{i}#{line_hash} | {clean_str}")
            
        return "\n".join(formatted_lines)

    def apply_hashline_edit(cls, file_path: str, target_hash: str, new_content: str, operation: str = "replace") -> dict:
        """
        Applies an edit targeting a specific line hash.
        operation: 'replace', 'insert_before', 'insert_after', 'delete'
        Returns a dictionary with 'ok', 'error', and optional 'context' for agent recovery.
        """
        if not os.path.exists(file_path):
            err = f"[HashlineEditor] Error: File {file_path} not found."
            print(err)
            return {"ok": False, "error": err}
            
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # Find the target line
        target_idx = -1
        for i, line in enumerate(lines):
            if cls._compute_line_hash(line) == target_hash:
                target_idx = i
                break
                
        if target_idx == -1:
            err = f"[HashlineEditor] Error: Hash {target_hash} not found in {file_path}."
            print(err)
            # Provide surrounding context hint if possible. Since we don't know where the hash was,
            # we provide a quick snippet of the current file with hashes so the agent can quickly remap.
            context_hint = cls.format_file_with_hashes(file_path)
            # Truncate to first 30 lines if too long to save tokens
            lines_hint = context_hint.split("\n")
            if len(lines_hint) > 30:
                hint_str = "\n".join(lines_hint[:15] + ["... (truncated) ..."] + lines_hint[-15:])
            else:
                hint_str = context_hint
                
            return {
                "ok": False, 
                "error": f"{err} The file may have been modified by another process. Please check the current file context and try again with the new correct hash.",
                "context": hint_str
            }
            
        # Ensure new_content ends with a newline if it's not a deletion
        if new_content and not new_content.endswith('\n'):
            new_content += '\n'
            
        if operation == "replace":
            lines[target_idx] = new_content
        elif operation == "delete":
            del lines[target_idx]
        elif operation == "insert_before":
            lines.insert(target_idx, new_content)
        elif operation == "insert_after":
            lines.insert(target_idx + 1, new_content)
        else:
            err = f"[HashlineEditor] Unknown operation: {operation}"
            print(err)
            return {"ok": False, "error": err}
            
        # Write back atomically
        temp_path = file_path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            
        os.replace(temp_path, file_path)
        msg = f"[HashlineEditor] Successfully applied '{operation}' via Hash {target_hash}."
        print(msg)
        return {"ok": True, "message": msg}
