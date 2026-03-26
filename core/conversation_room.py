"""
core/conversation_room.py
==========================
ConversationRoom, ConversationTurn, ConversationBudget, ConsensusResult 데이터 모델.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ── 대화 프로토콜 ──────────────────────────────────────────────────────────

PROTOCOLS = {"debate", "review", "handoff", "brainstorm", "standup"}

TURN_TYPES = {
    "statement",      # 일반 발언
    "question",       # 질문
    "proposal",       # 제안
    "vote",           # 투표
    "objection",      # 반대
    "agreement",      # 동의
    "summary",        # 요약 (moderator)
    "human_input",    # 사용자 개입
    "final_decision", # 최종 결정 (moderator)
    "unstructured",   # JSON 파싱 실패 — 합의 계산 제외, raw text 보존
}

ROOM_STATUSES = {"open", "in_progress", "consensus_reached", "closed", "aborted"}


# ── ConversationBudget ────────────────────────────────────────────────────

@dataclass
class ConversationBudget:
    """대화방 토큰/라운드 예산 관리."""
    max_tokens_per_room: int = 50_000
    max_tokens_per_turn: int = 2_000
    reserve_tokens: int = 5_000
    tokens_used: int = 0
    turn_tokens: dict[str, int] = field(default_factory=dict)  # turn_id → tokens

    def can_proceed(self, estimated_tokens: int = 0) -> bool:
        return (self.tokens_used + estimated_tokens + self.reserve_tokens
                <= self.max_tokens_per_room)

    def record(self, turn_id: str, tokens: int) -> None:
        self.tokens_used += tokens
        self.turn_tokens[turn_id] = tokens

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens_per_room - self.tokens_used - self.reserve_tokens)


# ── ConversationTurn ──────────────────────────────────────────────────────

@dataclass
class ConversationTurn:
    """에이전트의 단일 발언."""
    turn_id: str
    room_id: str
    speaker: str                        # 에이전트 ID
    content: str                        # 발언 내용
    turn_type: str                      # TURN_TYPES 중 하나
    turn_references: list[str] = field(default_factory=list)  # 참조 turn_id 목록
    metadata: dict[str, Any] = field(default_factory=dict)    # 코드, 파일 경로 등
    tokens_used: int = 0
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def make(
        cls,
        room_id: str,
        speaker: str,
        content: str,
        turn_type: str = "statement",
        turn_references: list[str] | None = None,
        metadata: dict | None = None,
        tokens_used: int = 0,
    ) -> "ConversationTurn":
        return cls(
            turn_id=uuid.uuid4().hex[:12],
            room_id=room_id,
            speaker=speaker,
            content=content,
            turn_type=turn_type if turn_type in TURN_TYPES else "statement",
            turn_references=turn_references or [],
            metadata=metadata or {},
            tokens_used=tokens_used,
        )

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "room_id": self.room_id,
            "speaker": self.speaker,
            "content": self.content,
            "turn_type": self.turn_type,
            "turn_references": self.turn_references,
            "metadata": self.metadata,
            "tokens_used": self.tokens_used,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConversationTurn":
        return cls(
            turn_id=d["turn_id"],
            room_id=d["room_id"],
            speaker=d["speaker"],
            content=d["content"],
            turn_type=d.get("turn_type", "statement"),
            turn_references=d.get("turn_references", []),
            metadata=d.get("metadata", {}),
            tokens_used=d.get("tokens_used", 0),
            timestamp=d.get("timestamp", time.time()),
        )

    def format_for_context(self) -> str:
        """대화 맥락에 표시할 문자열."""
        return f"[{self.speaker}] ({self.turn_type}) {self.content}"


# ── ConsensusResult ───────────────────────────────────────────────────────

@dataclass
class ConsensusResult:
    """합의 도출 결과."""
    reached: bool
    decision: str                           # 결정 사항 요약
    protocol: str                           # 어떤 프로토콜로 도달했는지
    votes: dict[str, str] = field(default_factory=dict)        # {agent_id: "agree"|"disagree"|"abstain"}
    dissent: list[str] = field(default_factory=list)           # 반대 의견 목록
    action_items: list[dict[str, Any]] = field(default_factory=list)  # 후속 작업
    forced_by_moderator: bool = False       # moderator가 강제 종결했는지
    rounds_taken: int = 0

    def to_dict(self) -> dict:
        return {
            "reached": self.reached,
            "decision": self.decision,
            "protocol": self.protocol,
            "votes": self.votes,
            "dissent": self.dissent,
            "action_items": self.action_items,
            "forced_by_moderator": self.forced_by_moderator,
            "rounds_taken": self.rounds_taken,
        }


# ── ConversationRoom ──────────────────────────────────────────────────────

@dataclass
class ConversationRoom:
    """에이전트들이 모여 대화하는 방."""
    room_id: str
    topic: str
    protocol: str                           # PROTOCOLS 중 하나
    participants: list[str]                 # 에이전트 ID 목록
    project_id: str
    moderator: Optional[str] = None
    status: str = "open"                    # ROOM_STATUSES 중 하나
    max_rounds: int = 10
    current_round: int = 0
    consensus: Optional[ConsensusResult] = None
    budget: ConversationBudget = field(default_factory=ConversationBudget)
    lease_ids: dict[str, str] = field(default_factory=dict)  # agent_id → lease_id
    created_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None

    @classmethod
    def create(
        cls,
        topic: str,
        protocol: str,
        participants: list[str],
        project_id: str,
        moderator: Optional[str] = None,
        max_rounds: int = 10,
        budget: Optional[ConversationBudget] = None,
    ) -> "ConversationRoom":
        if protocol not in PROTOCOLS:
            raise ValueError(f"Unknown protocol '{protocol}'. Must be one of {PROTOCOLS}")
        return cls(
            room_id=uuid.uuid4().hex[:12],
            topic=topic,
            protocol=protocol,
            participants=list(participants),
            project_id=project_id,
            moderator=moderator,
            max_rounds=max_rounds,
            budget=budget or ConversationBudget(),
        )

    def to_dict(self) -> dict:
        return {
            "room_id": self.room_id,
            "topic": self.topic,
            "protocol": self.protocol,
            "participants": self.participants,
            "project_id": self.project_id,
            "moderator": self.moderator,
            "status": self.status,
            "max_rounds": self.max_rounds,
            "current_round": self.current_round,
            "consensus": self.consensus.to_dict() if self.consensus else None,
            "budget_tokens_used": self.budget.tokens_used,
            "budget_max_tokens": self.budget.max_tokens_per_room,
            "lease_ids": self.lease_ids,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
        }
