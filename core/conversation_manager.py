"""
core/conversation_manager.py
=============================
ConversationManager — 에이전트 간 대화형 협업 관리자.

대화방 생성, 턴 실행, 합의 도출, 대화 기록 저장을 담당한다.
AgentReservationManager를 통해 DynamicOrchestrator와 에이전트 점유 충돌을 방지한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from core.agent_reservation import AgentReservationManager
from core.consensus_engine import ConsensusEngine
from core.context_window_manager import estimate_tokens
from core.conversation_prompts import (
    build_conversation_prompt,
    build_moderator_decision_prompt,
)
from core.conversation_room import (
    PROTOCOLS,
    ConversationBudget,
    ConversationRoom,
    ConversationTurn,
    ConsensusResult,
)


# ── 할루시네이션 방어: 자연어 → turn_type 추론 ─────────────────────────────

_TURN_PATTERNS: list[tuple[str, re.Pattern]] = [
    # vote (투표 의사 표현) — 최우선 매칭
    ("vote", re.compile(
        r"(투표|vote\b|한\s*표|찬성에?\s*(한\s*표|투표)|반대에?\s*(한\s*표|투표))",
        re.IGNORECASE,
    )),
    # objection (반대/이의) — agreement보다 먼저 검사 (disagree가 agree에 걸리지 않도록)
    ("objection", re.compile(
        r"(반대합니다|반대해요|반대입니다|이의\s*있습니다|disagree\b|object\b|nack\b|-1\b"
        r"|수정\s*필요|재검토\s*필요|동의할\s*수\s*없)",
        re.IGNORECASE,
    )),
    # agreement (동의/승인)
    ("agreement", re.compile(
        r"(동의합니다|동의해요|찬성합니다|찬성이요|찬성해요|좋습니다|승인합니다"
        r"|(?<!dis)agree\b|lgtm\b|looks\s+good|approve[ds]?\b|\+1\b|네\s*맞습니다)",
        re.IGNORECASE,
    )),
    # proposal (제안)
    ("proposal", re.compile(
        r"(제안합니다|제안드립니다|제안이요|propose\b|suggest\b"
        r"|하면\s*어떨까|어떨까요|것을?\s*제안|방안을?\s*제시)",
        re.IGNORECASE,
    )),
    # question (질문)
    ("question", re.compile(
        r"(\?\s*$|질문이\s*있|궁금한\s*것|어떻게\s*생각|왜\s+|어떤\s+이유"
        r"|how\s+(about|do|should)|why\s+|what\s+if)",
        re.IGNORECASE | re.MULTILINE,
    )),
    # summary (요약)
    ("summary", re.compile(
        r"(요약하면|정리하면|종합하면|summariz|to\s+sum\s+up|in\s+summary)",
        re.IGNORECASE,
    )),
]


def infer_turn_type_from_text(content: str) -> str:
    """자연어 content에서 turn_type을 추론한다.

    JSON 응답을 하지 않는 LLM 할루시네이션에 대한 방어 레이어.
    매칭되는 패턴이 없으면 기본값 ``"statement"``를 반환한다.
    """
    if not content:
        return "statement"
    for turn_type, pattern in _TURN_PATTERNS:
        if pattern.search(content):
            return turn_type
    return "statement"


# ── ConversationResult ────────────────────────────────────────────────────

@dataclass
class ConversationResult:
    room_id: str
    topic: str
    protocol: str
    consensus: Optional[ConsensusResult]
    turns: list[ConversationTurn]
    aborted: bool = False
    abort_reason: str = ""


# ── TranscriptStore ───────────────────────────────────────────────────────

class TranscriptStore:
    """대화 기록 파일 저장/조회 (append-only JSONL + 파일 락)."""

    def __init__(self, workspace: str) -> None:
        self._base = os.path.join(workspace, "data", "conversations")
        os.makedirs(self._base, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._index_lock = threading.Lock()
        self._index_path = os.path.join(self._base, "index.json")

    def _room_dir(self, room_id: str) -> str:
        d = os.path.join(self._base, f"room_{room_id}")
        os.makedirs(d, exist_ok=True)
        return d

    def _file_lock(self, room_id: str) -> threading.Lock:
        if room_id not in self._locks:
            self._locks[room_id] = threading.Lock()
        return self._locks[room_id]

    def save_room(self, room: ConversationRoom) -> None:
        d = self._room_dir(room.room_id)
        path = os.path.join(d, "metadata.json")
        with self._file_lock(room.room_id):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(room.to_dict(), f, ensure_ascii=False, indent=2)
        self._update_index(room)

    def append_turn(self, turn: ConversationTurn) -> None:
        d = self._room_dir(turn.room_id)
        path = os.path.join(d, "transcript.jsonl")
        with self._file_lock(turn.room_id):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(turn.to_dict(), ensure_ascii=False) + "\n")

    def save_consensus(self, room_id: str, consensus: ConsensusResult) -> None:
        d = self._room_dir(room_id)
        path = os.path.join(d, "consensus.json")
        with self._file_lock(room_id):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(consensus.to_dict(), f, ensure_ascii=False, indent=2)

    def load_transcript(self, room_id: str) -> list[ConversationTurn]:
        d = self._room_dir(room_id)
        path = os.path.join(d, "transcript.jsonl")
        turns: list[ConversationTurn] = []
        if not os.path.exists(path):
            return turns
        with self._file_lock(room_id):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            turns.append(ConversationTurn.from_dict(json.loads(line)))
                        except (json.JSONDecodeError, KeyError):
                            pass
        return turns

    def _update_index(self, room: ConversationRoom) -> None:
        with self._index_lock:
            index: dict[str, Any] = {}
            if os.path.exists(self._index_path):
                try:
                    with open(self._index_path, encoding="utf-8") as f:
                        index = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass
            index[room.room_id] = {
                "topic": room.topic,
                "protocol": room.protocol,
                "status": room.status,
                "participants": room.participants,
                "created_at": room.created_at,
            }
            with open(self._index_path, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)


# ── ConversationManager ───────────────────────────────────────────────────

class ConversationManager:
    """
    에이전트 간 대화형 협업 관리자.

    사용 예시 (ProjectPipeline 내):
        mgr = ConversationManager(
            project_id=project_id,
            workspace=workspace,
            broker=shared_broker,
            agent_runner=runner,
            agent_mgr=agent_mgr,
            reservation_mgr=reservation_mgr,
        )
        room = mgr.create_room(
            topic="REST vs GraphQL 결정",
            protocol="debate",
            participants=["tanjiro", "himari", "deadbyte"],
            moderator="tanjiro",
        )
        result = asyncio.run(mgr.run_conversation(room.room_id))
    """

    def __init__(
        self,
        project_id: str,
        workspace: str,
        broker,                             # MessageBroker (싱글톤 주입)
        agent_runner,                       # AgentRunner
        agent_mgr,                          # AgentManager
        reservation_mgr: Optional[AgentReservationManager] = None,
        mr=None,                            # ModelRouter (LLM 기반 합의 판정용)
        llm_engine=None,                    # LLMEngine (turn_type 재분류용, optional)
        visualizer=None,                    # TerminalVisualizer (optional)
    ) -> None:
        self.project_id = project_id
        self.workspace = workspace
        self.broker = broker
        self.runner = agent_runner
        self.agent_mgr = agent_mgr
        self.reservation_mgr = reservation_mgr or AgentReservationManager()
        self.mr = mr
        self._llm_engine = llm_engine  # None이면 정규식 fallback → unstructured
        self._visualizer = visualizer   # TerminalVisualizer (None이면 기존 print)

        self._rooms: dict[str, ConversationRoom] = {}
        self._turns: dict[str, list[ConversationTurn]] = {}
        self._store = TranscriptStore(workspace)
        self._engine = ConsensusEngine()

    # ── 공개 API ──────────────────────────────────────────────────────────

    def create_room(
        self,
        topic: str,
        protocol: str,
        participants: list[str],
        moderator: Optional[str] = None,
        max_rounds: int = 10,
        budget: Optional[ConversationBudget] = None,
    ) -> ConversationRoom:
        """
        대화방 생성 + 참여 에이전트 lease 예약.

        Raises:
            ValueError: protocol 오류 또는 에이전트 점유 충돌
        """
        if protocol not in PROTOCOLS:
            raise ValueError(f"Unknown protocol '{protocol}'. Valid: {PROTOCOLS}")

        # lease 예약 (충돌 방지)
        lease_duration = max_rounds * 60.0  # 라운드당 최대 60초 가정
        leases = self.reservation_mgr.reserve_all(
            participants,
            duration=lease_duration,
            reason="conversation",
            holder=f"room_{topic[:20]}",
        )
        failed = [a for a, lid in leases.items() if lid is None]
        if failed:
            # 예약 실패한 에이전트의 현재 점유 정보 수집
            occupied = []
            for agent_id in failed:
                lease = self.reservation_mgr.get_lease(agent_id)
                if lease:
                    occupied.append(f"{agent_id}({lease.reason}:{lease.holder})")
                else:
                    occupied.append(agent_id)
            raise ValueError(
                f"에이전트 점유 충돌 — 다음 에이전트가 이미 사용 중: {', '.join(occupied)}"
            )

        room = ConversationRoom.create(
            topic=topic,
            protocol=protocol,
            participants=participants,
            project_id=self.project_id,
            moderator=moderator,
            max_rounds=max_rounds,
            budget=budget,
        )
        room.lease_ids = {a: lid for a, lid in leases.items() if lid}

        self._rooms[room.room_id] = room
        self._turns[room.room_id] = []
        self._store.save_room(room)

        if self._visualizer:
            self._visualizer.on_conversation_start(room)
        else:
            print(f"[ConversationManager] 대화방 생성: '{topic}' (protocol={protocol}, "
                  f"participants={participants}, room_id={room.room_id})")
        return room

    def run_conversation(
        self,
        room_id: str,
        initial_context: Optional[dict] = None,
    ) -> ConversationResult:
        """
        대화 실행 (동기 래퍼 — asyncio.run 또는 이미 실행 중인 루프에서 호출).

        Returns:
            ConversationResult
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 이미 실행 중인 이벤트 루프 (테스트 등)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run,
                        self._run_conversation_async(room_id, initial_context)
                    )
                    return future.result()
            else:
                return loop.run_until_complete(
                    self._run_conversation_async(room_id, initial_context)
                )
        except RuntimeError:
            return asyncio.run(self._run_conversation_async(room_id, initial_context))

    async def _run_conversation_async(
        self,
        room_id: str,
        initial_context: Optional[dict] = None,
    ) -> ConversationResult:
        """비동기 대화 실행 루프."""
        room = self._rooms.get(room_id)
        if not room:
            return ConversationResult(
                room_id=room_id, topic="", protocol="",
                consensus=None, turns=[], aborted=True,
                abort_reason="room not found",
            )

        room.status = "in_progress"
        self._store.save_room(room)
        turns = self._turns[room_id]

        # HITL: 대화 시작 전 사용자 개입 포인트
        hitl_override = self._hitl_check_start(room)
        if hitl_override == "abort":
            room.status = "aborted"
            self._store.save_room(room)
            return ConversationResult(
                room_id=room_id, topic=room.topic, protocol=room.protocol,
                consensus=None, turns=turns, aborted=True,
                abort_reason="user aborted before start",
            )

        try:
            for round_num in range(1, room.max_rounds + 1):
                room.current_round = round_num

                # 예산 확인 (estimate_tokens로 다음 라운드 비용 추정)
                estimated_round_cost = self._estimate_round_cost(room, turns)
                if not room.budget.can_proceed(estimated_round_cost):
                    print(f"[ConversationManager] 토큰 예산 소진 "
                          f"(used={room.budget.tokens_used:,}, "
                          f"estimated={estimated_round_cost:,}, "
                          f"remaining={room.budget.remaining:,}) — 대화 종료")
                    break

                print(f"[ConversationManager] 라운드 {round_num}/{room.max_rounds} "
                      f"(토큰 사용: {room.budget.tokens_used:,}/{room.budget.max_tokens_per_room:,})")

                # 각 참여자 순차 발언
                for agent_id in room.participants:
                    turn = await self._get_agent_response(
                        agent_id, room, turns, initial_context
                    )
                    # tokens_used는 _parse_agent_response에서 estimate_tokens로 이미 계산됨
                    turns.append(turn)
                    self._store.append_turn(turn)
                    room.budget.record(turn.turn_id, turn.tokens_used)

                    if self._visualizer:
                        self._visualizer.on_conversation_turn(turn, room)
                    else:
                        print(f"  [{agent_id}] ({turn.turn_type}) {turn.content[:100]}")

                # HITL: 매 라운드 후 사용자 개입 확인 (비대화식 환경에서는 skip)
                hitl_action = self._hitl_check_round(room, turns)
                if hitl_action == "stop":
                    print(f"[ConversationManager] 사용자 요청으로 대화 중단")
                    break
                if hitl_action and hitl_action not in ("", "continue"):
                    # 사용자 발언 삽입
                    human_turn = ConversationTurn.make(
                        room_id=room_id,
                        speaker="human",
                        content=hitl_action,
                        turn_type="human_input",
                        tokens_used=estimate_tokens(hitl_action),
                    )
                    turns.append(human_turn)
                    self._store.append_turn(human_turn)
                    room.budget.record(human_turn.turn_id, human_turn.tokens_used)

                # unstructured 비율 경고 + HITL 트리거
                unstructured_ratio = self._engine._unstructured_ratio(turns)
                if unstructured_ratio >= 0.5:
                    print(
                        f"[ConversationManager] ⚠ unstructured 턴 {unstructured_ratio:.0%} — "
                        "에이전트가 JSON 형식을 따르지 않습니다. 개입이 필요할 수 있습니다."
                    )
                    if self._is_interactive():
                        answer = input("  계속 진행하시겠습니까? (y/N): ").strip().lower()
                        if answer != "y":
                            break

                # 합의 확인
                consensus = self._engine.evaluate(room, turns)
                if consensus:
                    room.consensus = consensus
                    room.status = "consensus_reached"
                    self._store.save_room(room)
                    self._store.save_consensus(room_id, consensus)
                    if self._visualizer:
                        self._visualizer.on_consensus_reached(room, consensus.decision[:80])
                    else:
                        print(f"[ConversationManager] 합의 도달: {consensus.decision[:80]}")

                    # HITL: 합의 후 사용자 승인
                    if not self._hitl_approve_consensus(room, consensus):
                        print(f"[ConversationManager] 사용자가 합의 거부 — 재토론")
                        room.consensus = None
                        room.status = "in_progress"
                        continue  # 다음 라운드 진행

                    return ConversationResult(
                        room_id=room_id,
                        topic=room.topic,
                        protocol=room.protocol,
                        consensus=consensus,
                        turns=turns,
                    )

                # 수렴 감지 (무한 루프 방지)
                if self._engine.check_convergence(turns):
                    if not self._visualizer:
                        print(f"[ConversationManager] 대화 수렴 감지 — moderator 결정 요청")
                    break

            # max_rounds 소진 또는 수렴 → moderator 최종 결정
            consensus = await self._moderator_decision(room, turns)
            room.consensus = consensus
            room.status = "consensus_reached"
            self._store.save_room(room)
            if consensus:
                self._store.save_consensus(room_id, consensus)
                if self._visualizer:
                    self._visualizer.on_consensus_reached(room, consensus.decision[:80])
            else:
                if self._visualizer:
                    self._visualizer.on_conversation_end(room)

            return ConversationResult(
                room_id=room_id,
                topic=room.topic,
                protocol=room.protocol,
                consensus=consensus,
                turns=turns,
            )

        except Exception as exc:
            room.status = "aborted"
            self._store.save_room(room)
            if self._visualizer:
                self._visualizer.on_conversation_end(room)
            return ConversationResult(
                room_id=room_id, topic=room.topic, protocol=room.protocol,
                consensus=None, turns=turns, aborted=True,
                abort_reason=str(exc),
            )
        finally:
            # 항상 lease 해제
            self._release_leases(room)
            room.closed_at = time.time()

    async def _get_agent_response(
        self,
        agent_id: str,
        room: ConversationRoom,
        context: list[ConversationTurn],
        initial_context: Optional[dict],
    ) -> ConversationTurn:
        """에이전트에게 대화 컨텍스트를 주고 순차로 응답 받기."""
        agent = self._load_agent(agent_id)
        role_description = agent.get("role", "") if agent else ""

        prompt = build_conversation_prompt(
            agent_name=agent_id,
            role_description=role_description,
            room=room,
            context_turns=context,
            initial_context=initial_context,
        )

        # AgentRunner.run()을 동기 스레드에서 실행 (이벤트 루프 블로킹 방지)
        result = await asyncio.to_thread(
            self._run_agent_turn, agent or {"name": agent_id}, prompt, room
        )

        return self._parse_agent_response(result, agent_id, room.room_id, context)

    async def _moderator_decision(
        self,
        room: ConversationRoom,
        turns: list[ConversationTurn],
    ) -> Optional[ConsensusResult]:
        """moderator 에이전트에게 최종 결정 요청."""
        moderator_id = room.moderator or (room.participants[0] if room.participants else None)
        if not moderator_id:
            return ConsensusResult(
                reached=True,
                decision="(moderator 없음 — 대화 종료)",
                protocol=room.protocol,
                forced_by_moderator=True,
                rounds_taken=room.current_round,
            )

        agent = self._load_agent(moderator_id)
        role_description = agent.get("role", "") if agent else ""
        prompt = build_moderator_decision_prompt(
            agent_name=moderator_id,
            role_description=role_description,
            room=room,
            context_turns=turns,
        )

        result = await asyncio.to_thread(
            self._run_agent_turn, agent or {"name": moderator_id}, prompt, room
        )
        turn = self._parse_agent_response(result, moderator_id, room.room_id, turns)
        turns.append(turn)
        self._store.append_turn(turn)

        # final_decision 턴에서 합의 추출
        if turn.turn_type == "final_decision":
            action_items = turn.metadata.get("action_items", [])
            return ConsensusResult(
                reached=True,
                decision=turn.content,
                protocol=room.protocol,
                action_items=action_items,
                forced_by_moderator=True,
                rounds_taken=room.current_round,
            )
        return ConsensusResult(
            reached=True,
            decision=turn.content,
            protocol=room.protocol,
            forced_by_moderator=True,
            rounds_taken=room.current_round,
        )

    def _run_agent_turn(
        self,
        agent: dict,
        prompt: str,
        room: ConversationRoom,
    ) -> dict:
        """AgentRunner.run() 호출 — 대화 1턴."""
        run_id = f"conv_{room.room_id}_{uuid.uuid4().hex[:8]}"
        try:
            return self.runner.run(
                agent=agent,
                task_input=prompt,
                run_id=run_id,
                auto_approve=True,
                workspace=self.workspace,
            )
        except Exception as exc:
            return {"output": f"(에러: {exc})", "status": "error"}

    def _parse_agent_response(
        self,
        run_result: dict,
        agent_id: str,
        room_id: str,
        context: list[ConversationTurn],
    ) -> ConversationTurn:
        """AgentRunner 결과에서 ConversationTurn 파싱.

        파싱 전략 (3단계):
        1. JSON 추출 성공 → turn_type/content/references/action_items 직접 사용
        2. JSON 실패 + LLMEngine 있음 → 경량 재분류 호출
        3. JSON 실패 + LLMEngine 없음 → turn_type="unstructured" 로 저장
           (합의 계산에서 자동 제외, raw text 보존)
        """
        raw_output = ""
        if isinstance(run_result, dict):
            raw_output = (
                run_result.get("output")
                or run_result.get("result")
                or run_result.get("response")
                or ""
            )

        content = str(raw_output).strip()
        references: list[str] = []
        action_items: list[dict] = []
        parse_succeeded = False

        # ── 1단계: JSON 추출 ───────────────────────────────────────────────
        json_match = re.search(r'\{[^{}]*"turn_type"[^{}]*\}', raw_output, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                turn_type = data.get("turn_type", "statement")
                content = data.get("content", content)
                references = data.get("references", [])
                action_items = data.get("action_items", [])
                parse_succeeded = True
            except json.JSONDecodeError:
                pass

        if not parse_succeeded:
            # ── 2단계: LLM 재분류 ─────────────────────────────────────────
            turn_type = self._reclassify_turn_type(content)

        tokens_used = estimate_tokens(content)
        metadata: dict = {}
        if action_items:
            metadata["action_items"] = action_items
        if not parse_succeeded:
            metadata["parse_failed"] = True  # 감사 추적용

        return ConversationTurn.make(
            room_id=room_id,
            speaker=agent_id,
            content=content,
            turn_type=turn_type,
            turn_references=references,
            metadata=metadata,
            tokens_used=tokens_used,
        )

    def _reclassify_turn_type(self, content: str) -> str:
        """JSON 파싱 실패 시 turn_type 재분류.

        LLMEngine이 있으면 경량 분류 호출,
        없으면 정규식 추론,
        정규식도 실패하면 "unstructured" 반환.
        """
        if not content:
            return "unstructured"

        if self._llm_engine is not None:
            try:
                prompt = (
                    "Classify the following agent response into exactly one turn_type.\n"
                    "Valid types: statement, question, proposal, vote, objection, agreement, summary\n"
                    "Rules:\n"
                    "- vote: explicit yes/no vote expression\n"
                    "- objection: disagreement or request for change\n"
                    "- agreement: approval or acceptance\n"
                    "- proposal: suggesting a new approach\n"
                    "- question: asking for information\n"
                    "- summary: summarizing the discussion\n"
                    "- statement: anything else\n\n"
                    f"Response: {content[:500]}\n\n"
                    'Reply with JSON only: {"turn_type": "<type>"}'
                )
                result = self._llm_engine.generate_json(prompt)
                classified = result.get("turn_type", "")
                from core.conversation_room import TURN_TYPES
                if classified in TURN_TYPES and classified != "unstructured":
                    return classified
            except Exception:
                pass  # 재분류 실패 시 정규식으로 fallback

        # 정규식 추론 (키워드 명확성 높은 경우만)
        inferred = infer_turn_type_from_text(content)
        if inferred != "statement":
            # 명확한 패턴 매칭 성공
            return inferred

        # 아무것도 확신할 수 없음 → unstructured
        return "unstructured"

    def _load_agent(self, agent_id: str) -> Optional[dict]:
        """AgentManager에서 에이전트 정보 로드."""
        try:
            # AgentManager.get_or_create() 사용 (get_agent()는 존재하지 않음)
            return self.agent_mgr.get_or_create(agent_id, workspace=self.workspace)
        except Exception:
            return {"name": agent_id, "role": agent_id}

    def _release_leases(self, room: ConversationRoom) -> None:
        """방의 모든 에이전트 lease 해제."""
        lease_ids = list(room.lease_ids.values())
        if lease_ids:
            self.reservation_mgr.release_all(lease_ids)
            room.lease_ids = {}

    def _estimate_round_cost(
        self,
        room: ConversationRoom,
        turns: list[ConversationTurn],
    ) -> int:
        """
        다음 라운드 예상 토큰 비용 추정.
        컨텍스트 길이 + 참여자 수 기반 heuristic.
        """
        context_text = " ".join(t.content for t in turns[-10:])
        context_tokens = estimate_tokens(context_text)
        # 참여자 1인당 max_tokens_per_turn + 현재 컨텍스트의 10%
        per_participant = room.budget.max_tokens_per_turn + (context_tokens // 10)
        return per_participant * len(room.participants)

    # ── HITL (Human-in-the-Loop) ───────────────────────────────────────────

    def _hitl_check_start(self, room: ConversationRoom) -> str:
        """
        대화 시작 전 HITL 포인트.
        대화형 터미널 환경에서만 프롬프트 표시, 비대화식 환경(CI/파이프라인)에서는 skip.

        Returns:
            "abort" — 사용자가 취소
            ""      — 계속 진행
        """
        if not self._is_interactive():
            return ""
        print(f"\n[HITL] 대화 시작 확인")
        print(f"  주제: {room.topic}")
        print(f"  프로토콜: {room.protocol}")
        print(f"  참여자: {', '.join(room.participants)}")
        print(f"  최대 라운드: {room.max_rounds}")
        try:
            ans = input("  진행하시겠습니까? [Enter=진행 / q=취소]: ").strip().lower()
            return "abort" if ans in ("q", "quit", "n", "no") else ""
        except (EOFError, KeyboardInterrupt):
            return ""

    def _hitl_check_round(
        self,
        room: ConversationRoom,
        turns: list[ConversationTurn],
    ) -> str:
        """
        매 라운드 후 HITL 포인트.
        비대화식 환경에서는 skip (빈 문자열 반환).

        Returns:
            ""          — 계속 진행
            "stop"      — 대화 중단
            "<발언>"    — 사용자 발언 삽입
        """
        if not self._is_interactive():
            return ""
        # 매 라운드마다 묻지 않고 교착 상태(수렴)에서만 묻기
        if not self._engine.check_convergence(turns, window=1):
            return ""
        print(f"\n[HITL] 대화가 교착 상태입니다. 개입하시겠습니까?")
        print(f"  [Enter] 계속  |  [s] 중단  |  또는 발언을 입력하세요")
        try:
            ans = input("  > ").strip()
            if ans.lower() in ("s", "stop"):
                return "stop"
            return ans  # 발언 삽입 (빈 문자열이면 계속)
        except (EOFError, KeyboardInterrupt):
            return ""

    def _hitl_approve_consensus(
        self,
        room: ConversationRoom,
        consensus: "ConsensusResult",
    ) -> bool:
        """
        합의 도달 후 사용자 승인 HITL 포인트.
        비대화식 환경에서는 자동 승인.

        Returns:
            True  — 승인
            False — 거부 (재토론)
        """
        if not self._is_interactive():
            return True
        print(f"\n[HITL] 합의 도달 — 승인하시겠습니까?")
        print(f"  결정: {consensus.decision}")
        if consensus.action_items:
            print(f"  후속 작업: {len(consensus.action_items)}개")
        try:
            ans = input("  [Enter=승인 / r=재토론]: ").strip().lower()
            return ans not in ("r", "retry", "n", "no")
        except (EOFError, KeyboardInterrupt):
            return True

    @staticmethod
    def _is_interactive() -> bool:
        """터미널 대화식 환경인지 확인."""
        import sys
        return sys.stdin.isatty() and sys.stdout.isatty()

    # ── 조회 API ──────────────────────────────────────────────────────────

    def get_transcript(self, room_id: str) -> list[ConversationTurn]:
        """대화 기록 조회 (메모리 우선, 없으면 파일)."""
        if room_id in self._turns:
            return list(self._turns[room_id])
        return self._store.load_transcript(room_id)

    def get_consensus(self, room_id: str) -> Optional[ConsensusResult]:
        """합의 결과 조회."""
        room = self._rooms.get(room_id)
        return room.consensus if room else None

    def get_room(self, room_id: str) -> Optional[ConversationRoom]:
        return self._rooms.get(room_id)

    def reservation_status(self) -> dict:
        """현재 에이전트 점유 현황."""
        return self.reservation_mgr.status()
