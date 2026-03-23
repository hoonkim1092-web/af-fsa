import os
import subprocess
import re
import time

class GitManager:
    """
    (V22.5) Git Orchestrator
    Handles commits and rollbacks to provide a safety net for autonomous execution.
    """
    def __init__(self, directory: str = None):
        # Default to factory root if not provided
        self.directory = directory or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def commit(self, message: str) -> bool:
        """Saves current state."""
        try:
            subprocess.run(["git", "add", "."], check=True, cwd=self.directory)
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=self.directory)
            if not status.stdout.strip():
                return True
            subprocess.run(["git", "commit", "-m", message], check=True, cwd=self.directory)
            return True
        except Exception as e:
            print(f"[GitManager] Commit failed: {e}")
            return False

    def rollback(self) -> bool:
        """Rolls back local changes with a safe default strategy.

        Default: stash all tracked/untracked changes for recovery.
        Optional destructive mode can be enabled via AGENT_DESTRUCTIVE_ROLLBACK=1.
        """
        try:
            print(f"[GitManager] Rolling back changes in {self.directory}...")
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=self.directory,
                check=False,
            )
            if not str(status.stdout or "").strip():
                return True

            destructive = str(os.getenv("AGENT_DESTRUCTIVE_ROLLBACK", "")).strip().lower() in ("1", "true", "yes", "on")
            if destructive:
                subprocess.run(["git", "reset", "--hard", "HEAD"], check=True, cwd=self.directory)
                subprocess.run(["git", "clean", "-fd"], check=True, cwd=self.directory)
            else:
                # Safe rollback: stash current (failed) changes instead of deleting
                ts = int(time.time())
                subprocess.run(["git", "stash", "push", "-u", "-m", f"FSALoop_Safe_Rollback_{ts}"], check=True, cwd=self.directory)
            return True
        except Exception as e:
            print(f"[GitManager] Rollback failed: {e}")
            return False

    def push(self) -> bool:
        """Pushes current branch to origin."""
        try:
            subprocess.run(["git", "push"], check=True, cwd=self.directory)
            return True
        except Exception as e:
            print(f"[GitManager] Push failed: {e}")
            return False

def git_configure_and_push(directory: str, target_dir: str, agent_name: str, model_name: str, logger=print) -> bool:
    """Legacy function for push integration."""
    mgr = GitManager(directory)
    msg = f"feat: Factory generated/updated {agent_name} using {model_name}"
    if mgr.commit(msg):
        return mgr.push()
    return False
