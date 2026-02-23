import sys
import os
import subprocess
import json

def _is_auth_error(stderr_text: str) -> bool:
    s = (stderr_text or "").lower()
    flags = [
        "authentication expired",
        "rpc error 16",
        "clientauthenticationerror",
        "run 'nlm login'",
    ]
    return any(f in s for f in flags)

def _reauth_notebooklm() -> bool:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        print("[RESEARCH] Attempting NotebookLM CLI re-authentication...")
        p = subprocess.run(
            [sys.executable, "-m", "notebooklm_tools.cli.main", "login"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=180,
        )
        return p.returncode == 0
    except Exception:
        return False

def query_notebooklm(query: str, notebook_id: str = "eaa34a54-a898-46a0-835a-cdb6024887f0") -> str:
    """
    Query NotebookLM via CLI, auto re-auth once if token expired.
    """
    try:
        cmd = [
            sys.executable, "-m", "notebooklm_tools.cli.main",
            "query", "notebook",
            notebook_id,
            query
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)

        # Retry logic for auth errors
        if p.returncode != 0 and _is_auth_error(p.stderr or ""):
            if _reauth_notebooklm():
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)

        if p.returncode != 0:
            print(f"[RESEARCH Error] NotebookLM query failed: {(p.stderr or '').strip()}")
            return ""
            
        return p.stdout.strip()
    except Exception as e:
        print(f"[RESEARCH Error] NotebookLM Connection failed: {e}")
        return ""

def generate_deep_research_prompt(role: str) -> str:
    """
    Generates a Domain Deep-Dive template for Himari.
    Ensures research yields specific seasonal context, pricing, tools, and executable recipes.
    """
    return f"""
    Target Role/Domain: {role}
    
    WARNING: Do NOT provide generic encyclopedia answers. 
    Act as a hyper-specialized domain expert. Break down the exact operational realities for '{role}'.
    
    You MUST provide detailed, actionable data addressing these 4 pillars:
    
    1. [Current Context (Season/Time)]: What is critical RIGHT NOW? (e.g., Seasonal ingredients like 방어 in Winter, current market trends, time-sensitive risks).
    2. [Business/FinOps (Cost/Margin)]: What are the exact cost drivers? What is the target cost percentage (e.g., 'Target food cost 35%')? How does this role maximize profit margins and defend ROI?
    3. [Resources/Tools]: What specific, professional-grade tools/equipment/software/prerequisites are absolutely mandatory? (e.g., 야나기바, specific POS, specific machinery).
    4. [Execution (Recipe/Playbook)]: Provide a step-by-step, actionable 'Recipe' or 'Playbook' that this role executes on the floor. Be concrete, not abstract.

    Return the insights structured clearly around these 4 pillars.
    """.strip()
