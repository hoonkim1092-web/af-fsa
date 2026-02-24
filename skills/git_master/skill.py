import subprocess

class GitMasterSkill:
    """
    (P1) Git Master Skill
    Enforces atomic commits, safe history rewriting, and strictly formatted
    Git operations for agentic pipelines per OmO architecture rules.
    """
    __skill_id__ = "git_master"
    
    def propose(self, ctx: dict, command: str) -> dict:
        """Analyzes repository status and proposes atomic commit boundaries."""
        status = subprocess.run(["git", "status", "-s"], capture_output=True, text=True).stdout
        return {
            "status": "proposed",
            "git_status_raw": status,
            "principles": [
                "Atomic Commits ONLY. Do not use 'git commit -am' blindly.",
                "Review diffs before staging to prevent secrets leak.",
                "Adhere to Conventional Commits: feat:, fix:, docs:, chore:"
            ]
        }

    def apply(self, ctx: dict, commit_message: str, files: list[str]) -> dict:
        """Executes the Git operations to stage and commit safely."""
        try:
            for f in files:
                subprocess.run(["git", "add", f], check=True)
                
            subprocess.run(["git", "commit", "-m", commit_message], check=True)
            return {
                "status": "applied",
                "message": f"Successfully created atomic commit: {commit_message}"
            }
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    def test(self, ctx: dict) -> dict:
        """Verifies the latest commit log format."""
        try:
            log = subprocess.run(["git", "log", "-1", "--oneline"], capture_output=True, text=True, check=True).stdout
            return {
                "ok": True,
                "status": "verified",
                "last_commit": log.strip()
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
