"""
core/consensus_engine.py
=========================
ConsensusEngine — 대화 내용을 분석해 합의 도달 여부를 판정한다.

두 가지 방법:
  1. 규칙 기반 (fast): turn_type 집계, 투표 카운트
  2. LLM 기반 (slow): 대화 전체를 LLM에게 판단 요청
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.conversation_room import ConversationRoom, ConversationTurn

from core.conversation_room import ConsensusResult


# ── ConsensusEngine ───────────────────────────────────────────────────────

class ConsensusEngine:
    """
    프로토콜별 합의 판정 엔진.

    빠른 규칙 기반 판정을 먼저 수행하고,
    불분명할 경우 선택적으로 LLM 판정을 사용한다.
    """

    def __init__(self, llm_engine=None) -> None:
        """
        Args:
            llm_engine: LLMEngine 인스턴스 (없으면 규칙 기반만 사용)
        """
        self._llm = llm_engine

    # unstructured 비율이 이 임계값 이상이면 합의 판정 자체를 중단
    UNSTRUCTURED_THRESHOLD = 0.5

    def evaluate(
        self,
        room: "ConversationRoom",
        turns: list["ConversationTurn"],
    ) -> Optional[ConsensusResult]:
        """
        합의 도달 여부 판정.

        unstructured 턴이 전체의 50% 이상이면 합의 판정을 건너뛴다.
        (파싱 품질이 너무 낮아 신뢰할 수 없는 합의가 도출될 위험)

        Returns:
            ConsensusResult if consensus reached, None otherwise.
        """
        if not turns:
            return None

        # unstructured 비율 확인 — 임계값 초과 시 합의 판정 중단
        unstructured_ratio = self._unstructured_ratio(turns)
        if unstructured_ratio >= self.UNSTRUCTURED_THRESHOLD:
            import warnings
            warnings.warn(
                f"[ConsensusEngine] unstructured 턴 비율 {unstructured_ratio:.0%} — "
                f"합의 판정 건너뜀 (임계값: {self.UNSTRUCTURED_THRESHOLD:.0%}). "
                "LLMEngine 재분류기 설정 또는 프롬프트 JSON 형식 강화를 권장합니다.",
                RuntimeWarning,
                stacklevel=2,
            )
            return None

        protocol = room.protocol

        if protocol == "debate":
            return self._eval_debate(room, turns)
        elif protocol == "review":
            return self._eval_review(room, turns)
        elif protocol == "brainstorm":
            return self._eval_brainstorm(room, turns)
        elif protocol == "standup":
            return self._eval_standup(room, turns)
        elif protocol == "handoff":
            return self._eval_handoff(room, turns)
        return None

    def _unstructured_ratio(self, turns: list["ConversationTurn"]) -> float:
        """전체 턴 중 unstructured 비율 (human_input 제외)."""
        agent_turns = [t for t in turns if t.turn_type != "human_input"]
        if not agent_turns:
            return 0.0
        unstructured = sum(1 for t in agent_turns if t.turn_type == "unstructured")
        return unstructured / len(agent_turns)

    def evaluate_with_llm(
        self,
        room: "ConversationRoom",
        turns: list["ConversationTurn"],
        mr=None,
    ) -> Optional[ConsensusResult]:
        """LLM을 사용해 합의 판정 (규칙 기반 보완용)."""
        from core.conversation_prompts import build_consensus_check_prompt

        if not mr:
            return self.evaluate(room, turns)

        prompt = build_consensus_check_prompt(room, turns)
        try:
            model_id = mr.pick("fast") if hasattr(mr, "pick") else None
            from core.llm_engine import LLMEngine
            llm = LLMEngine(model_name=model_id) if model_id else None
            if not llm:
                return self.evaluate(room, turns)

            response = llm.chat([{"role": "user", "content": prompt}])
            return self._parse_llm_consensus(response, room, turns)
        except Exception:
            return self.evaluate(room, turns)

    def check_convergence(
        self,
        turns: list["ConversationTurn"],
        window: int = 2,
    ) -> bool:
        """
        최근 window 라운드에서 동일 입장이 반복되면 수렴으로 판단.
        무한 루프 방지를 위해 사용.
        """
        if len(turns) < window * 2:
            return False
        recent = turns[-(window * 2):]
        # 같은 화자의 연속 발언이 동일한 turn_type인지 확인
        by_speaker: dict[str, list[str]] = {}
        for t in recent:
            by_speaker.setdefault(t.speaker, []).append(t.turn_type)
        for speaker, types in by_speaker.items():
            if len(types) >= window and len(set(types)) == 1:
                return True
        return False

    # ── 프로토콜별 판정 ────────────────────────────────────────────────────

    def _eval_debate(
        self,
        room: "ConversationRoom",
        turns: list["ConversationTurn"],
    ) -> Optional[ConsensusResult]:
        """과반수 투표 또는 만장일치 agreement 시 합의."""
        votes = self._collect_votes(turns, room.participants)
        agreements = self._collect_agreements(turns, room.participants)

        # 만장일치 agreement
        if len(agreements) >= len(room.participants):
            decision = self._extract_latest_proposal(turns)
            return ConsensusResult(
                reached=True,
                decision=decision or "만장일치 합의",
                protocol="debate",
                votes={a: "agree" for a in agreements},
                action_items=self._extract_action_items(turns),
                rounds_taken=room.current_round,
            )

        # 투표 집계
        if votes:
            agree_count = sum(1 for v in votes.values() if v == "agree")
            total_voters = len(room.participants)
            if agree_count > total_voters / 2:
                decision = self._extract_latest_proposal(turns)
                dissent = [
                    t.content for t in turns
                    if t.turn_type == "objection"
                ]
                return ConsensusResult(
                    reached=True,
                    decision=decision or f"투표로 결정 ({agree_count}/{total_voters})",
                    protocol="debate",
                    votes=votes,
                    dissent=dissent,
                    action_items=self._extract_action_items(turns),
                    rounds_taken=room.current_round,
                )
        return None

    def _eval_review(
        self,
        room: "ConversationRoom",
        turns: list["ConversationTurn"],
    ) -> Optional[ConsensusResult]:
        """모든 리뷰어 (moderator 제외) agreement 시 합의."""
        reviewers = [p for p in room.participants if p != room.moderator]
        agreements = self._collect_agreements(turns, reviewers)

        if len(agreements) >= len(reviewers) and reviewers:
            return ConsensusResult(
                reached=True,
                decision="리뷰 통과",
                protocol="review",
                votes={a: "agree" for a in agreements},
                action_items=self._extract_action_items(turns),
                rounds_taken=room.current_round,
            )
        return None

    def _eval_brainstorm(
        self,
        room: "ConversationRoom",
        turns: list["ConversationTurn"],
    ) -> Optional[ConsensusResult]:
        """brainstorm은 max_rounds 소진 시 항상 완료 (moderator가 정리)."""
        if room.current_round >= room.max_rounds:
            proposals = [t.content for t in turns if t.turn_type == "proposal"]
            return ConsensusResult(
                reached=True,
                decision=f"브레인스토밍 완료 ({len(proposals)}개 아이디어 수집)",
                protocol="brainstorm",
                action_items=[{"title": p, "owner_role": "", "description": ""} for p in proposals],
                rounds_taken=room.current_round,
            )
        return None

    def _eval_standup(
        self,
        room: "ConversationRoom",
        turns: list["ConversationTurn"],
    ) -> Optional[ConsensusResult]:
        """모든 참여자가 발언했으면 완료."""
        speakers = {t.speaker for t in turns if t.turn_type == "statement"}
        if speakers >= set(room.participants):
            return ConsensusResult(
                reached=True,
                decision="스탠드업 완료",
                protocol="standup",
                rounds_taken=room.current_round,
            )
        return None

    def _eval_handoff(
        self,
        room: "ConversationRoom",
        turns: list["ConversationTurn"],
    ) -> Optional[ConsensusResult]:
        """수신자가 agreement를 보내면 완료."""
        receivers = [p for p in room.participants
                     if p != (room.moderator or room.participants[0])]
        agreements = self._collect_agreements(turns, receivers)
        if agreements:
            return ConsensusResult(
                reached=True,
                decision="인수인계 완료",
                protocol="handoff",
                votes={a: "agree" for a in agreements},
                rounds_taken=room.current_round,
            )
        return None

    # ── 유틸 ──────────────────────────────────────────────────────────────

    def _collect_votes(
        self,
        turns: list["ConversationTurn"],
        participants: list[str],
    ) -> dict[str, str]:
        """참여자별 투표 수집 (turn_type + 자연어 content 기반).

        할루시네이션 방어: turn_type이 "vote"가 아니더라도 content에서
        명시적 찬성/반대 의사가 감지되면 투표로 간주한다.
        """
        _AGREE_KW = ("agree", "찬성", "동의", "lgtm", "+1", "approve")
        _DISAGREE_KW = ("disagree", "반대", "nack", "-1", "동의할 수 없")
        _ABSTAIN_KW = ("abstain", "기권")
        _VOTE_ELIGIBLE = ("vote", "agreement", "objection", "statement")

        result: dict[str, str] = {}
        for t in turns:
            if t.speaker not in participants:
                continue
            if t.turn_type not in _VOTE_ELIGIBLE:
                continue
            content_lower = t.content.lower()
            if any(k in content_lower for k in _DISAGREE_KW):
                result[t.speaker] = "disagree"
            elif any(k in content_lower for k in _ABSTAIN_KW):
                result[t.speaker] = "abstain"
            elif t.turn_type == "vote" or any(k in content_lower for k in _AGREE_KW):
                result[t.speaker] = "agree"
        return result

    def _collect_agreements(
        self,
        turns: list["ConversationTurn"],
        participants: list[str],
    ) -> set[str]:
        """동의 참여자 집합 (turn_type + 자연어 content 기반).

        할루시네이션 방어: turn_type이 "agreement"가 아니더라도
        content에서 동의 표현이 감지되면 동의로 간주한다.
        반대 표현이 감지되면 동의에서 제거한다.
        """
        _AGREE_KW = ("동의", "찬성", "좋습니다", "승인", "agree", "lgtm",
                      "looks good", "approve", "+1")
        _DISAGREE_KW = ("반대", "이의", "disagree", "object", "nack",
                         "-1", "수정 필요", "동의할 수 없")

        result: set[str] = set()
        for t in turns:
            if t.speaker not in participants:
                continue
            # unstructured 턴은 신뢰할 수 없으므로 합의 계산에서 제외
            # (_collect_votes와 동일한 정책)
            if t.turn_type == "unstructured":
                continue
            content_lower = t.content.lower()
            # 명시적 반대 → 제거
            if t.turn_type == "objection" or any(k in content_lower for k in _DISAGREE_KW):
                result.discard(t.speaker)
            # 명시적 동의 → 추가
            elif t.turn_type == "agreement" or any(k in content_lower for k in _AGREE_KW):
                result.add(t.speaker)
        return result

    def _extract_latest_proposal(self, turns: list["ConversationTurn"]) -> str:
        """가장 최근 proposal 내용 반환."""
        for t in reversed(turns):
            if t.turn_type in ("proposal", "final_decision"):
                return t.content
        return ""

    def _extract_action_items(
        self,
        turns: list["ConversationTurn"],
    ) -> list[dict[str, Any]]:
        """턴의 metadata에서 action_items 수집."""
        items: list[dict] = []
        for t in turns:
            ai = t.metadata.get("action_items", [])
            if isinstance(ai, list):
                items.extend(ai)
        return items

    def _parse_llm_consensus(
        self,
        response: str,
        room: "ConversationRoom",
        turns: list["ConversationTurn"],
    ) -> Optional[ConsensusResult]:
        """LLM 응답 JSON 파싱."""
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group())
            if not data.get("reached"):
                return None
            return ConsensusResult(
                reached=True,
                decision=data.get("decision", ""),
                protocol=room.protocol,
                votes=data.get("votes", {}),
                dissent=data.get("dissent", []),
                action_items=data.get("action_items", []),
                rounds_taken=room.current_round,
            )
        except (json.JSONDecodeError, KeyError):
            return None
