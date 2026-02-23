import os
import subprocess
import re


def git_configure_and_push(directory: str, target_dir: str, agent_name: str, model_name: str, logger=print) -> bool:
    """
    Adds, commits, and pushes changes in a git repository.
    """
    logger(f"[GIT] 📤 Preparing to push changes for '{agent_name}'...")
    try:
        # We assume the script is executed where the root git directory is accessible, 
        # or we run it from the target directory if it's a separate repo.
        # usually FACTORY_ROOT is the repo.
        subprocess.run(["git", "add", "agents/", "skills/", "core/", "tests/"], check=True, cwd=directory)
        
        # Check if there are actual changes before committing
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=directory)
        if not status.stdout.strip():
            logger("[GIT] ℹ️ No changes detected to commit.")
            return True
            
        subprocess.run(["git", "commit", "-m", f"feat: Factory generated/updated {agent_name} using {model_name}"], check=True, cwd=directory)
        subprocess.run(["git", "push"], check=True, cwd=directory)
        logger("[GIT] ✅ Push successful!")
        return True
    except subprocess.CalledProcessError as e:
        logger(f"[GIT Error] ❌ Git command failed with error code {e.returncode}: {e.stderr or e.stdout}")
        return False
    except FileNotFoundError:
        logger("[GIT Error] ❌ Git executable not found on the system.")
        return False
    except Exception as e:
        logger(f"[GIT Error] ❌ Unexpected Error during push: {e}")
        return False
