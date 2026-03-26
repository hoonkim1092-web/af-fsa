"""
core/conversation_task_adapter.py
==================================
ConversationToTaskAdapter — 대화 합의 결과를 TaskBoard task로 변환.

consensus.action_items → TaskBoard tasks로 매핑하고,
기존 board와 merge (중복 제거 + consensus_metadata 삽입).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.conversation_room import ConsensusResult, ConversationRoom

from core.utils import now_iso


# ── ConversationToTaskAdapter ─────────────────────────────────────────────

class ConversationToTaskAdapter:
    """합의 결과를 TaskBoard 형식으로 변환하고 기존 board와 merge."""

    def adapt(
        self,
        consensus: "ConsensusResult",
        room: "ConversationRoom",
        existing_board: dict[str, Any],
    ) -> dict[str, Any]:
        """
        consensus.action_items → task 목록 생성 후 existing_board에 merge.

        - 기존 task와 title이 같으면 consensus_metadata만 주입 (중복 생성 방지)
        - 새 task는 board.tasks에 추가
        - 기존 consensus_metadata는 보존
        """
        if not consensus or not consensus.reached:
            return existing_board

        action_items = consensus.action_items or []
        new_tasks = [
            self._action_item_to_task(item, room, consensus)
            for item in action_items
            if isinstance(item, dict) and item.get("title")
        ]

        if not new_tasks:
            # action_items 없어도 consensus_metadata를 보드 summary에 기록
            existing_board.setdefault("conversation_decisions", [])
            existing_board["conversation_decisions"].append({
                "room_id": room.room_id,
                "topic": room.topic,
                "protocol": room.protocol,
                "decision": consensus.decision,
                "rounds_taken": consensus.rounds_taken,
            })
            return existing_board

        # 기존 task 목록과 merge
        existing_tasks: list[dict] = existing_board.get("tasks", [])
        existing_titles = {
            _normalize(t.get("title") or t.get("instruction") or "")
            for t in existing_tasks
        }

        for task in new_tasks:
            normalized = _normalize(task["title"])
            if normalized in existing_titles:
                # 기존 task에 consensus_metadata만 주입
                for et in existing_tasks:
                    et_key = _normalize(et.get("title") or et.get("instruction") or "")
                    if et_key == normalized:
                        et["consensus_metadata"] = task["consensus_metadata"]
                        break
            else:
                existing_tasks.append(task)
                existing_titles.add(normalized)

        existing_board["tasks"] = existing_tasks
        existing_board.setdefault("conversation_decisions", [])
        existing_board["conversation_decisions"].append({
            "room_id": room.room_id,
            "topic": room.topic,
            "protocol": room.protocol,
            "decision": consensus.decision,
            "rounds_taken": consensus.rounds_taken,
        })
        return existing_board

    def _action_item_to_task(
        self,
        item: dict[str, Any],
        room: "ConversationRoom",
        consensus: "ConsensusResult",
    ) -> dict[str, Any]:
        """action_item dict → TaskBoard task dict."""
        title = str(item.get("title") or "")
        owner_role = str(item.get("owner_role") or "")
        description = str(item.get("description") or item.get("instruction") or title)
        depends_on_raw = item.get("depends_on", [])
        depends_on = [str(d) for d in depends_on_raw] if isinstance(depends_on_raw, list) else []

        # task_id: title → snake_case
        task_id = _to_snake(title)[:40]

        return {
            "task_id": task_id,
            "title": title,
            "instruction": description,
            "owner_role": owner_role,
            "module_id": f"conversation_{room.room_id}",
            "phase": "build",
            "depends_on": depends_on,
            "acceptance": [],
            "artifacts": [],
            "status": "pending",
            "notes": [],
            "updated_at": now_iso(),
            "consensus_metadata": {
                "conversation_room_id": room.room_id,
                "topic": room.topic,
                "protocol": room.protocol,
                "consensus": {
                    "reached": consensus.reached,
                    "decision": consensus.decision,
                    "votes": consensus.votes,
                },
                "consensus_immutable": False,
            },
        }


# ── 유틸 ──────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip().lower())


def _to_snake(text: str) -> str:
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', '_', text.strip().lower())
