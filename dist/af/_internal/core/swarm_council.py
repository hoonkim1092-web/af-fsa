import json
import os
import subprocess
import sys
from datetime import datetime

from core.llm_engine import LLMEngine


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SwarmCouncil:
    def __init__(self, factory_dir: str, model_name: str = None):
        self.factory_dir = os.path.abspath(factory_dir)
        self.llm = LLMEngine(model_name=model_name)  # None 시 자동으로 최신 Flash 선택

    def _policy_text(self) -> str:
        policy_path = os.path.join(self.factory_dir, "projects", "default", "policies.yaml")
        if not os.path.exists(policy_path):
            return ""
        try:
            with open(policy_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def build_board(self, project_desc: str, roles: list[str]) -> dict:
        return {
            "project_description": project_desc,
            "roles": roles,
            "history": [],
            "current_status": "planning",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

    def save_board(self, board: dict, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(board, f, ensure_ascii=False, indent=2)

    def probing_questions(self, project_desc: str) -> list[str]:
        prompt = f"""
You are Lilith, PM.
Project: {project_desc}
Generate up to 3 short probing questions before execution.
Return JSON:
{{"questions":["...","..."]}}
""".strip()
        data = self.llm.generate_json(prompt)
        questions = data.get("questions", []) if isinstance(data, dict) else []
        return [str(q).strip() for q in questions if str(q).strip()][:3]

    def worker_proposal(self, role: str, project_desc: str, history: list[dict]) -> dict:
        recent = history[-6:] if len(history) > 6 else history
        prompt = f"""
Role: {role}
Project: {project_desc}
RecentHistory(JSON): {json.dumps(recent, ensure_ascii=False)}

Return JSON:
{{
  "summary":"one paragraph",
  "plan":["step1","step2"],
  "risks":["risk1","risk2"],
  "missing_skill":"optional_skill_or_empty"
}}
""".strip()
        data = self.llm.generate_json(prompt)
        if not isinstance(data, dict):
            return {"summary": "", "plan": [], "risks": [], "missing_skill": ""}
        data.setdefault("summary", "")
        data.setdefault("plan", [])
        data.setdefault("risks", [])
        data.setdefault("missing_skill", "")
        return data

    def lilith_review(self, role: str, proposal: dict, loop_count: int) -> dict:
        prompt = f"""
You are Lilith PM reviewing a worker proposal.
Role: {role}
Iteration: {loop_count}
Proposal(JSON): {json.dumps(proposal, ensure_ascii=False)}

Decision rules:
- If quality is acceptable: decision = "pass"
- Otherwise: decision = "reject" and provide missing_skill

Return JSON:
{{"critique":"...", "decision":"pass|reject", "missing_skill":"optional"}}
""".strip()
        data = self.llm.generate_json(prompt)
        if not isinstance(data, dict):
            return {"critique": "fallback reject", "decision": "reject", "missing_skill": ""}
        data["decision"] = str(data.get("decision", "reject")).strip().lower()
        if data["decision"] not in ("pass", "reject"):
            data["decision"] = "reject"
        data.setdefault("critique", "")
        data.setdefault("missing_skill", "")
        return data

    def defense_validate(self, draft: dict) -> dict:
        policy_text = self._policy_text()
        if not policy_text:
            return {
                "verified_critique": str(draft.get("critique", "")),
                "verified_decision": str(draft.get("decision", "reject")),
                "missing_skill": str(draft.get("missing_skill", "")),
            }
        prompt = f"""
Validate PM decision against policies.
Policies:
{policy_text}

Draft(JSON):
{json.dumps(draft, ensure_ascii=False)}

Return JSON:
{{"verified_critique":"...", "verified_decision":"pass|reject", "missing_skill":"optional"}}
""".strip()
        data = self.llm.generate_json(prompt)
        if not isinstance(data, dict):
            return {
                "verified_critique": str(draft.get("critique", "")),
                "verified_decision": str(draft.get("decision", "reject")),
                "missing_skill": str(draft.get("missing_skill", "")),
            }
        data.setdefault("verified_critique", str(draft.get("critique", "")))
        data.setdefault("verified_decision", str(draft.get("decision", "reject")))
        data.setdefault("missing_skill", str(draft.get("missing_skill", "")))
        return data

    def hot_upgrade(self, role: str, missing_skill: str, target_dir: str | None = None) -> bool:
        env = os.environ.copy()
        if target_dir:
            env["AGENT_PROJECT_ROOT"] = os.path.abspath(target_dir)
        cmd = [sys.executable, "-u", "factory_manager.py", role]
        try:
            p = subprocess.run(
                cmd,
                cwd=self.factory_dir,
                env=env,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=240,
            )
            return p.returncode == 0
        except Exception:
            return False

    def run(self, project_desc: str, roles: list[str], board_path: str, target_dir: str | None = None, max_loops: int = 3) -> dict:
        board = self.build_board(project_desc, roles)
        board["probing_questions"] = self.probing_questions(project_desc)
        approved = False
        role_order = list(roles)
        if not role_order:
            role_order = ["General"]

        for loop_idx in range(1, max_loops + 1):
            board["updated_at"] = now_iso()
            for role in role_order:
                proposal = self.worker_proposal(role, project_desc, board["history"])
                board["history"].append({"ts": now_iso(), "role": role, "type": "proposal", "content": proposal})

                draft = self.lilith_review(role, proposal, loop_idx)
                final = self.defense_validate(draft)
                board["history"].append({"ts": now_iso(), "role": "Lilith", "type": "decision", "content": final})

                decision = str(final.get("verified_decision", "reject")).strip().lower()
                if decision == "pass":
                    approved = True
                    break
                missing_skill = str(final.get("missing_skill", "")).strip()
                if missing_skill:
                    ok = self.hot_upgrade(role, missing_skill, target_dir=target_dir)
                    board["history"].append(
                        {
                            "ts": now_iso(),
                            "role": "Himari",
                            "type": "hot_upgrade",
                            "content": {"role": role, "missing_skill": missing_skill, "ok": ok},
                        }
                    )
            if approved:
                break

        board["current_status"] = "approved" if approved else "rejected"
        board["updated_at"] = now_iso()
        self.save_board(board, board_path)
        return board
