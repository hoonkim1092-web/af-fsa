from core.hashline_editor import HashlineEditor

class HashlineEditSkill:
    """
    (P2) Hashline Edit Skill
    Replaces traditional file editing tools (like regex replaces or line numbers
    which suffer from stale context) with safe content hashing mutations.
    """
    __skill_id__ = "hashline_edit"
    
    def format_file(self, ctx: dict, file_path: str) -> str:
        """
        Reads a file and returns its content with hashline prefixes 
        so the LLM can see the 8-char hashes required for editing.
        """
        return HashlineEditor.format_file_with_hashes(file_path)

    def apply_edit(self, ctx: dict, file_path: str, target_hash: str, new_content: str, operation: str = "replace") -> dict:
        """
        Mutates a file safely using the unique line hash.
        Valid operations: replace, delete, insert_after, insert_before.
        """
        result = HashlineEditor.apply_hashline_edit(
            file_path=file_path, 
            target_hash=target_hash, 
            new_content=new_content, 
            operation=operation
        )
        if result.get("ok"):
            return {"ok": True, "message": result.get("message")}
        else:
            return {
                "ok": False, 
                "error": result.get("error"), 
                "hint_context": result.get("context", "")
            }
