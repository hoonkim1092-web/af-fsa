"""
core/control/rollback.py
=========================
RollbackManager — 유지보수 실패 시 안전한 롤백을 수행한다.

전략:
  git_revert        — git revert --no-commit으로 커밋 취소
  checkpoint_restore — .checkpoint/{run_id}.json에서 이전 상태 복원
  manual            — rollback_instructions를 출력 (자동화 불가 시)

저장 경로: {workspace}/.af_runtime/control/rollback_plan.json
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field, asdict


@dataclass
class RollbackPlan:
    """롤백 지점 기록."""
    run_id: str
    workspace: str
    strategy: str                           # "git_revert" | "checkpoint_restore" | "manual"
    git_ref_before: str                     # 실행 시작 전 git HEAD
    checkpoint_path: str = ""              # .checkpoint/{run_id}.json 경로
    affected_files: list[str] = field(default_factory=list)
    rollback_instructions: list[str] = field(default_factory=list)
    created_at: str = ""
    executed_at: str = ""
    status: str = "pending"                 # "pending" | "executed" | "failed" | "skipped"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RollbackPlan":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def is_executable(self) -> bool:
        return self.status == "pending" and self.git_ref_before != ""


class RollbackManager:
    """유지보수 실패 시 안전한 롤백을 수행한다."""

    def __init__(self, workspace: str):
        self._workspace = workspace
        self._control_dir = os.path.join(workspace, ".af_runtime", "control")

    # ── Public API ──

    def create_rollback_point(
        self,
        run_id: str,
        affected_files: list[str] | None = None,
    ) -> RollbackPlan:
        """
        실행 시작 전 git ref + checkpoint를 기록한다.
        전략은 git이 사용 가능하면 git_revert, 아니면 checkpoint_restore.
        """
        from core.utils import now_iso

        git_ref = self._get_git_head()
        strategy = "git_revert" if git_ref else "checkpoint_restore"

        checkpoint_path = os.path.join(
            self._control_dir, "checkpoints", f"{run_id}.json"
        )

        instructions = self._build_instructions(strategy, git_ref, affected_files or [])

        plan = RollbackPlan(
            run_id=run_id,
            workspace=self._workspace,
            strategy=strategy,
            git_ref_before=git_ref,
            checkpoint_path=checkpoint_path,
            affected_files=affected_files or [],
            rollback_instructions=instructions,
            created_at=now_iso(),
            status="pending",
        )

        self._save(plan)
        return plan

    def execute_rollback(self, plan: RollbackPlan) -> dict:
        """
        전략별 롤백을 수행한다.

        Returns:
          {"success": bool, "strategy": str, "message": str}
        """
        from core.utils import now_iso

        if not plan.is_executable:
            return {
                "success": False,
                "strategy": plan.strategy,
                "message": f"plan not executable (status={plan.status}, ref={plan.git_ref_before!r})",
            }

        if plan.strategy == "git_revert":
            result = self._execute_git_revert(plan)
        elif plan.strategy == "checkpoint_restore":
            result = self._execute_checkpoint_restore(plan)
        else:
            result = self._execute_manual(plan)

        # 상태 업데이트
        plan.executed_at = now_iso()
        plan.status = "executed" if result["success"] else "failed"
        self._save(plan)

        return result

    def load_plan(self, run_id: str) -> RollbackPlan | None:
        """저장된 롤백 플랜을 불러온다."""
        path = self._plan_path()
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("run_id") == run_id:
                return RollbackPlan.from_dict(data)
        except Exception:
            pass
        return None

    # ── 전략별 실행 ──

    def _execute_git_revert(self, plan: RollbackPlan) -> dict:
        """git revert --no-commit으로 롤백한다."""
        try:
            # git rev-list로 롤백할 커밋 목록 수집
            result = subprocess.run(
                ["git", "rev-list", f"{plan.git_ref_before}..HEAD"],
                capture_output=True, text=True,
                cwd=self._workspace, timeout=30,
            )
            if result.returncode != 0:
                return {
                    "success": False,
                    "strategy": "git_revert",
                    "message": f"git rev-list failed: {result.stderr.strip()}",
                }

            commits = [c.strip() for c in result.stdout.splitlines() if c.strip()]
            if not commits:
                return {
                    "success": True,
                    "strategy": "git_revert",
                    "message": "no commits to revert (already at rollback point)",
                }

            # 최신 커밋부터 순서대로 revert
            for commit in commits:
                revert = subprocess.run(
                    ["git", "revert", "--no-commit", commit],
                    capture_output=True, text=True,
                    cwd=self._workspace, timeout=30,
                )
                if revert.returncode != 0:
                    return {
                        "success": False,
                        "strategy": "git_revert",
                        "message": f"git revert failed at {commit[:8]}: {revert.stderr.strip()}",
                    }

            return {
                "success": True,
                "strategy": "git_revert",
                "message": f"reverted {len(commits)} commit(s) (staged, not committed)",
            }
        except Exception as exc:
            return {
                "success": False,
                "strategy": "git_revert",
                "message": f"git_revert exception: {exc}",
            }

    def _execute_checkpoint_restore(self, plan: RollbackPlan) -> dict:
        """checkpoint 파일에서 이전 상태를 복원한다."""
        if not plan.checkpoint_path or not os.path.isfile(plan.checkpoint_path):
            return {
                "success": False,
                "strategy": "checkpoint_restore",
                "message": f"checkpoint not found: {plan.checkpoint_path}",
            }
        try:
            with open(plan.checkpoint_path, encoding="utf-8") as f:
                checkpoint = json.load(f)
            # checkpoint에 저장된 파일 상태를 복원 (JSON 형식 의존)
            restored_files = checkpoint.get("files", {})
            for rel_path, content in restored_files.items():
                full_path = os.path.join(self._workspace, rel_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
            return {
                "success": True,
                "strategy": "checkpoint_restore",
                "message": f"restored {len(restored_files)} file(s) from checkpoint",
            }
        except Exception as exc:
            return {
                "success": False,
                "strategy": "checkpoint_restore",
                "message": f"checkpoint_restore exception: {exc}",
            }

    def _execute_manual(self, plan: RollbackPlan) -> dict:
        """자동화 불가: rollback_instructions를 사용자에게 제시."""
        instructions = "\n".join(
            f"  {i+1}. {step}"
            for i, step in enumerate(plan.rollback_instructions)
        )
        print(f"[RollbackManager] Manual rollback required:\n{instructions}")
        return {
            "success": False,  # 수동 확인이 필요하므로 false
            "strategy": "manual",
            "message": f"manual rollback steps provided ({len(plan.rollback_instructions)} steps)",
        }

    # ── 내부 유틸 ──

    def _get_git_head(self) -> str:
        """현재 git HEAD ref를 반환한다. git 미사용 시 빈 문자열."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True,
                cwd=self._workspace, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    def _build_instructions(
        self,
        strategy: str,
        git_ref: str,
        affected_files: list[str],
    ) -> list[str]:
        """사람이 읽을 수 있는 rollback_instructions를 생성한다."""
        if strategy == "git_revert":
            return [
                f"git revert --no-commit {git_ref}..HEAD",
                "git commit -m 'revert: rollback maintenance run'",
            ]
        elif strategy == "checkpoint_restore":
            return [
                "checkpoint 파일에서 이전 상태를 수동 복원하세요",
                f"영향받은 파일: {', '.join(affected_files) if affected_files else '(unknown)'}",
            ]
        return ["수동으로 변경 사항을 검토하고 되돌리세요"]

    def _plan_path(self) -> str:
        return os.path.join(self._control_dir, "rollback_plan.json")

    def _save(self, plan: RollbackPlan) -> None:
        os.makedirs(self._control_dir, exist_ok=True)
        path = self._plan_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[RollbackManager] save failed: {exc}")


__all__ = ["RollbackPlan", "RollbackManager"]
