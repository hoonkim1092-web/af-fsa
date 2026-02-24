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

    @classmethod
    def apply_hashline_edit(cls, file_path: str, target_hash: str, new_content: str, operation: str = "replace") -> bool:
        """
        Applies an edit targeting a specific line hash.
        operation: 'replace', 'insert_before', 'insert_after', 'delete'
        """
        if not os.path.exists(file_path):
            print(f"[HashlineEditor] Error: File {file_path} not found.")
            return False
            
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # Find the target line
        target_idx = -1
        for i, line in enumerate(lines):
            if cls._compute_line_hash(line) == target_hash:
                target_idx = i
                break
                
        if target_idx == -1:
            print(f"[HashlineEditor] Error: Hash {target_hash} not found in {file_path}. The file may have been modified.")
            return False
            
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
            print(f"[HashlineEditor] Unknown operation: {operation}")
            return False
            
        # Write back atomically
        temp_path = file_path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            
        os.replace(temp_path, file_path)
        print(f"[HashlineEditor] Successfully applied '{operation}' via Hash {target_hash}.")
        return True
