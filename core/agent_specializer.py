"""
AgentSpecializer — 1 Agent per 1 Task 특화 시스템.

역할(role) 수준의 base_agent를 받아, 단일 작업(task)에 특화된
임시(in-memory) 에이전트 dict를 생성한다.

사용법:
    specializer = AgentSpecializer()
    specialized = specializer.specialize(base_agent, task_meta, workspace)
    # specialized는 새 dict (base_agent 불변)
"""

from __future__ import annotations

import copy
from typing import Any

from core.project_mailbox import mailbox_prompt_digest
from core.project_task_board import load_project_board
from core.utils import safe_id


class AgentSpecializer:
    """역할 기반 에이전트를 단일 작업에 특화시키는 중간 레이어."""

    def specialize(
        self,
        base_agent: dict[str, Any],
        task_meta: dict[str, Any],
        workspace: str,
    ) -> dict[str, Any]:
        """base_agent를 변경하지 않고 작업 특화 에이전트 dict를 반환한다.

        Args:
            base_agent: AgentManager.get_or_create()로 얻은 역할 수준 에이전트.
            task_meta: project_board의 task dict
                       (task_id, title, instruction, owner_role, phase,
                        depends_on, acceptance, artifacts, module_id, notes).
            workspace: 프로젝트 워크스페이스 경로.

        Returns:
            새 dict. base_agent는 변경되지 않는다.
        """
        # 비동기 병렬 실행 시 base_agent의 중첩 dict가 공유되지 않도록 deep copy
        agent = copy.deepcopy(base_agent)
        agent["_specialized"] = True
        agent["_task_id"] = safe_id(
            task_meta.get("task_id") or task_meta.get("id", "")
        )
        agent["_task_meta"] = task_meta

        # 1. 작업 특화 시스템 프롬프트 조립
        agent["system_ko"] = self._build_task_prompt(base_agent, task_meta, workspace)

        # 2. 작업 관련 스킬만 필터링
        agent["skills"] = self._select_task_skills(base_agent, task_meta)

        return agent

    # ------------------------------------------------------------------
    # 시스템 프롬프트 조립
    # ------------------------------------------------------------------

    def _build_task_prompt(
        self,
        base_agent: dict[str, Any],
        task_meta: dict[str, Any],
        workspace: str,
    ) -> str:
        sections: list[str] = []

        # ── Section 1: 축약된 역할 페르소나 ──
        role_name = base_agent.get("role") or base_agent.get("name") or "Agent"
        original_prompt = str(base_agent.get("system_ko", "")).strip()
        abbreviated = self._abbreviate_role_prompt(original_prompt)
        sections.append(f"[역할] {role_name}\n{abbreviated}")

        # ── Section 2: 현재 작업 정보 ──
        task_id = safe_id(task_meta.get("task_id") or task_meta.get("id", ""))
        title = str(task_meta.get("title", "")).strip()
        instruction = str(task_meta.get("instruction", "")).strip()
        phase = str(task_meta.get("phase", "")).strip()
        acceptance = task_meta.get("acceptance") or []
        artifacts = task_meta.get("artifacts") or []

        task_lines = [f"[현재 작업] task_id={task_id}"]
        if title:
            task_lines.append(f"제목: {title}")
        if phase:
            task_lines.append(f"단계: {phase}")
        task_lines.append(f"지시사항: {instruction}")

        if acceptance:
            criteria = "\n".join(f"  - {c}" for c in acceptance if str(c).strip())
            task_lines.append(f"완료 기준:\n{criteria}")

        if artifacts:
            artifact_list = "\n".join(f"  - {a}" for a in artifacts if str(a).strip())
            task_lines.append(f"기대 산출물:\n{artifact_list}")

        sections.append("\n".join(task_lines))

        # ── Section 3: 선행 작업 결과 ──
        depends_on = task_meta.get("depends_on") or []
        if depends_on:
            dep_context = self._gather_dependency_context(depends_on, workspace)
            if dep_context:
                sections.append(f"[선행 작업 결과]\n{dep_context}")

        # ── Section 4: 수신 메일박스 메시지 ──
        owner_role = safe_id(
            task_meta.get("owner_role", "")
        ) or safe_id(
            str(base_agent.get("role", ""))
        )
        try:
            mailbox = mailbox_prompt_digest(
                workspace, role=owner_role, task_id=task_id, limit=6
            )
        except Exception:
            mailbox = ""
        if mailbox and "no mailbox" not in mailbox.lower():
            sections.append(f"[수신 메시지]\n{mailbox}")

        # ── Section 5: 범위 제한 ──
        sections.append(
            "[범위 제한]\n"
            "이 작업 외의 범위는 수행하지 마라. "
            "위에 명시된 지시사항과 완료 기준에만 집중하라. "
            "다른 모듈이나 역할의 작업에 개입하지 마라."
        )

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # 역할 프롬프트 축약
    # ------------------------------------------------------------------

    @staticmethod
    def _abbreviate_role_prompt(original: str, max_chars: int = 200) -> str:
        """역할 프롬프트를 max_chars 이내로 축약한다."""
        if not original:
            return ""
        if len(original) <= max_chars:
            return original
        cutoff = original[:max_chars]
        last_period = cutoff.rfind(".")
        if last_period > max_chars // 2:
            return cutoff[: last_period + 1] + " ..."
        return cutoff.rstrip() + " ..."

    # ------------------------------------------------------------------
    # 작업 맞춤 스킬 선택
    # ------------------------------------------------------------------

    @staticmethod
    def _select_task_skills(
        base_agent: dict[str, Any],
        task_meta: dict[str, Any],
    ) -> list[str]:
        """작업에 관련된 스킬만 필터링한다.

        전략:
        1. base_agent에 선언된 스킬 목록을 가져온다.
        2. task_meta.instruction + acceptance 텍스트에서 키워드 매칭.
        3. 매칭되는 스킬 우선, 나머지 순서대로. 최대 8개.
        """
        declared = [
            safe_id(str(s))
            for s in (base_agent.get("skills") or [])
            if str(s).strip()
        ]
        if not declared:
            return []

        instruction = str(task_meta.get("instruction", "")).lower()
        acceptance_text = " ".join(
            str(a) for a in (task_meta.get("acceptance") or [])
        ).lower()
        artifacts_text = " ".join(
            str(a) for a in (task_meta.get("artifacts") or [])
        ).lower()
        task_text = f"{instruction} {acceptance_text} {artifacts_text}"

        # 스킬 ID 토큰이 작업 텍스트에 등장하면 관련 스킬로 판단
        matched: list[str] = []
        unmatched: list[str] = []
        for skill_id in declared:
            tokens = skill_id.replace("_", " ").split()
            if any(token in task_text for token in tokens if len(token) > 2):
                matched.append(skill_id)
            else:
                unmatched.append(skill_id)

        # 매칭 스킬 우선 + 나머지 순서 유지, 최대 8개
        result = matched + unmatched
        return result[:8]

    # ------------------------------------------------------------------
    # 선행 작업 컨텍스트 수집
    # ------------------------------------------------------------------

    @staticmethod
    def _gather_dependency_context(
        depends_on: list[str], workspace: str
    ) -> str:
        """선행 작업의 상태/결과를 보드에서 조회한다."""
        board = load_project_board(workspace)
        if not board:
            return ""

        task_map: dict[str, dict] = {}
        for task in board.get("tasks") or []:
            tid = safe_id(str(task.get("task_id", "")))
            if tid:
                task_map[tid] = task

        lines: list[str] = []
        for dep_id in depends_on:
            dep_key = safe_id(dep_id)
            dep_task = task_map.get(dep_key)
            if not dep_task:
                lines.append(f"- {dep_id}: (정보 없음)")
                continue
            status = dep_task.get("status", "unknown")
            notes = dep_task.get("notes") or []
            last_note = str(notes[-1]) if notes else ""
            dep_artifacts = ", ".join(
                str(a) for a in (dep_task.get("artifacts") or [])
            ) or "-"
            lines.append(
                f"- {dep_id} [{status}]: "
                f"{dep_task.get('title', '')} | 산출물={dep_artifacts}"
            )
            if last_note:
                lines.append(f"  최근 메모: {last_note}")

        return "\n".join(lines)
