"""
core/conversation_prompts.py
=============================
프로토콜별 대화 프롬프트 템플릿.
에이전트에게 대화 컨텍스트를 제공할 때 사용한다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.conversation_room import ConversationRoom, ConversationTurn


# ── 프로토콜별 규칙 ───────────────────────────────────────────────────────

_PROTOCOL_RULES: dict[str, str] = {
    "debate": (
        "자유 토론 방식입니다. 근거를 들어 의견을 주장하거나 반박할 수 있습니다.\n"
        "투표 시 'vote' turn_type을 사용하세요 (agree/disagree/abstain).\n"
        "과반수 동의 또는 moderator 최종 결정으로 합의가 도출됩니다."
    ),
    "review": (
        "리뷰 방식입니다. 발표자의 내용을 검토하고 피드백을 제공하세요.\n"
        "승인 시 'agreement', 수정 요청 시 'objection' turn_type을 사용하세요.\n"
        "모든 리뷰어가 승인해야 종료됩니다."
    ),
    "brainstorm": (
        "브레인스토밍 방식입니다. 비판 없이 아이디어를 자유롭게 제안하세요.\n"
        "'proposal' turn_type을 주로 사용하세요.\n"
        "max_rounds 소진 후 moderator가 아이디어를 정리합니다."
    ),
    "standup": (
        "스탠드업 방식입니다. 담당 작업의 진행 상황을 간결하게 보고하세요.\n"
        "형식: [완료한 것] / [진행 중인 것] / [차단 요소]\n"
        "모든 참여자 보고 완료 시 종료됩니다."
    ),
    "handoff": (
        "인수인계 방식입니다. 작업 결과와 다음 담당자에게 필요한 컨텍스트를 전달하세요.\n"
        "'statement' 또는 'proposal' turn_type을 사용하세요.\n"
        "수신자 확인 후 종료됩니다."
    ),
}

# ── 응답 JSON 스키마 안내 ──────────────────────────────────────────────────

_RESPONSE_SCHEMA = """응답은 반드시 아래 JSON 형식으로 작성하세요:
{"turn_type": "<type>", "content": "<발언 내용>", "references": ["<참조 turn_id>", ...]}

turn_type 선택지: statement | question | proposal | vote | objection | agreement | summary | final_decision
vote 사용 시 content에 "agree", "disagree", "abstain" 중 하나를 포함하세요.
references는 참조하는 이전 발언의 turn_id 목록입니다 (없으면 빈 배열).
"""


# ── 프롬프트 빌더 ──────────────────────────────────────────────────────────

def build_conversation_prompt(
    agent_name: str,
    role_description: str,
    room: "ConversationRoom",
    context_turns: list["ConversationTurn"],
    initial_context: dict | None = None,
) -> str:
    """에이전트에게 전달할 대화 참여 프롬프트 생성."""
    protocol_rule = _PROTOCOL_RULES.get(room.protocol, "")

    # 이전 대화 포맷
    history_lines: list[str] = []
    for turn in context_turns[-30:]:  # 최근 30개 턴으로 제한
        history_lines.append(turn.format_for_context())
    history_text = "\n".join(history_lines) if history_lines else "(아직 대화 없음)"

    # 초기 컨텍스트 (이전 단계 합의 등)
    initial_ctx_text = ""
    if initial_context:
        initial_ctx_text = f"\n[이전 단계 합의 결과]\n{_format_initial_context(initial_context)}\n"

    lines = [
        f"당신은 {agent_name}입니다. {role_description}",
        "",
        "[대화 설정]",
        f"- 프로토콜: {room.protocol}",
        f"- 주제: {room.topic}",
        f"- 참여자: {', '.join(room.participants)}",
        f"- 진행자(moderator): {room.moderator or '없음'}",
        f"- 현재 라운드: {room.current_round}/{room.max_rounds}",
        f"- 남은 토큰 예산: {room.budget.remaining:,}",
        "",
        "[프로토콜 규칙]",
        protocol_rule,
        initial_ctx_text,
        "[이전 대화]",
        history_text,
        "",
        "[당신의 차례입니다]",
        _RESPONSE_SCHEMA,
    ]
    return "\n".join(lines)


def build_moderator_decision_prompt(
    agent_name: str,
    role_description: str,
    room: "ConversationRoom",
    context_turns: list["ConversationTurn"],
) -> str:
    """max_rounds 소진 시 moderator의 최종 결정 프롬프트."""
    history_lines = [t.format_for_context() for t in context_turns[-30:]]
    history_text = "\n".join(history_lines) if history_lines else "(대화 없음)"

    lines = [
        f"당신은 {agent_name}입니다. {role_description}",
        "",
        f"[최종 결정 요청]",
        f"주제 '{room.topic}'에 대한 대화가 {room.max_rounds}라운드를 소진했습니다.",
        "진행자(moderator)로서 최종 결정을 내려주세요.",
        "",
        "[전체 대화]",
        history_text,
        "",
        "아래 JSON 형식으로 최종 결정을 반환하세요:",
        '{"turn_type": "final_decision", "content": "<결정 내용>", '
        '"action_items": [{"title": "...", "owner_role": "...", "description": "..."}], '
        '"references": []}',
    ]
    return "\n".join(lines)


def build_consensus_check_prompt(
    room: "ConversationRoom",
    recent_turns: list["ConversationTurn"],
) -> str:
    """합의 도달 여부를 판단하는 내부 프롬프트."""
    turns_text = "\n".join(t.format_for_context() for t in recent_turns[-20:])
    participants_str = ", ".join(room.participants)

    return (
        f"다음 대화에서 '{room.topic}' 주제에 대한 합의가 도달했는지 판단하세요.\n"
        f"프로토콜: {room.protocol}\n"
        f"참여자: {participants_str}\n\n"
        f"[최근 대화]\n{turns_text}\n\n"
        f"아래 JSON 형식으로만 응답하세요:\n"
        '{"reached": true|false, "decision": "<결정 요약>", '
        '"votes": {"<agent_id>": "agree|disagree|abstain"}, '
        '"dissent": ["<반대 의견>"], '
        '"action_items": [{"title": "...", "owner_role": "...", "description": "..."}]}'
    )


# ── 내부 유틸 ──────────────────────────────────────────────────────────────

def _format_initial_context(ctx: dict) -> str:
    if isinstance(ctx, dict):
        decision = ctx.get("decision", "")
        action_items = ctx.get("action_items", [])
        lines = []
        if decision:
            lines.append(f"결정: {decision}")
        if action_items:
            lines.append("후속 작업:")
            for item in action_items:
                title = item.get("title", "") if isinstance(item, dict) else str(item)
                lines.append(f"  - {title}")
        return "\n".join(lines) if lines else str(ctx)
    return str(ctx)
