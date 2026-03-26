"""
tests/test_conversation_collaboration.py
=========================================
에이전트 대화형 협업 시스템 단위 테스트.

커버리지:
  - AgentReservationManager: lease 획득/해제/TTL/동시 충돌
  - ConversationRoom / ConversationTurn / ConversationBudget: 데이터 모델
  - ConsensusEngine: 프로토콜별 합의 판정, 수렴 감지
  - ConversationToTaskAdapter: 합의 결과 → TaskBoard 변환
  - ConversationManager: 방 생성, lease 충돌 감지 (AgentRunner 없이)
"""
import time
import pytest

from core.agent_reservation import AgentReservationManager
from core.conversation_room import (
    ConversationBudget,
    ConversationRoom,
    ConversationTurn,
    ConsensusResult,
)
from core.consensus_engine import ConsensusEngine
from core.conversation_task_adapter import ConversationToTaskAdapter


# ── AgentReservationManager ───────────────────────────────────────────────

class TestAgentReservationManager:
    def test_reserve_and_release(self):
        mgr = AgentReservationManager()
        lid = mgr.reserve("himari", duration=60, reason="test")
        assert lid is not None
        assert mgr.is_reserved("himari")
        released = mgr.release(lid)
        assert released
        assert not mgr.is_reserved("himari")

    def test_double_reserve_fails(self):
        mgr = AgentReservationManager()
        lid1 = mgr.reserve("tanjiro", duration=60)
        lid2 = mgr.reserve("tanjiro", duration=60)
        assert lid1 is not None
        assert lid2 is None  # 이미 점유 중
        mgr.release(lid1)

    def test_reserve_all_rollback_on_conflict(self):
        mgr = AgentReservationManager()
        # tanjiro를 미리 점유
        existing = mgr.reserve("tanjiro", duration=60)
        # tanjiro 포함 일괄 예약 시도
        result = mgr.reserve_all(["himari", "tanjiro"], duration=60, reason="batch")
        assert all(v is None for v in result.values())
        # 롤백으로 himari도 해제됐는지 확인
        assert not mgr.is_reserved("himari")
        mgr.release(existing)

    def test_reserve_all_success(self):
        mgr = AgentReservationManager()
        result = mgr.reserve_all(["a", "b", "c"], duration=60, reason="test")
        assert all(v is not None for v in result.values())
        mgr.release_all(list(result.values()))
        assert not mgr.is_reserved("a")
        assert not mgr.is_reserved("b")

    def test_ttl_expiry(self):
        mgr = AgentReservationManager()
        mgr.reserve("deadbyte", duration=0.01)  # 10ms TTL
        time.sleep(0.05)
        # 만료 후 재예약 가능해야 함
        lid = mgr.reserve("deadbyte", duration=60)
        assert lid is not None
        mgr.release(lid)

    def test_extend_lease(self):
        mgr = AgentReservationManager()
        lid = mgr.reserve("iguro", duration=0.01)
        extended = mgr.extend(lid, extra_seconds=60)
        assert extended
        time.sleep(0.05)
        # 연장 후 만료되지 않아야 함
        assert mgr.is_reserved("iguro")
        mgr.release(lid)

    def test_status(self):
        mgr = AgentReservationManager()
        lid = mgr.reserve("saiba", duration=60, reason="conversation", holder="room_123")
        status = mgr.status()
        assert "saiba" in status
        assert status["saiba"]["reason"] == "conversation"
        mgr.release(lid)


# ── ConversationRoom / ConversationTurn / ConversationBudget ──────────────

class TestConversationRoom:
    def test_create_room(self):
        room = ConversationRoom.create(
            topic="REST vs GraphQL",
            protocol="debate",
            participants=["tanjiro", "himari"],
            project_id="test_proj",
            moderator="tanjiro",
        )
        assert room.room_id
        assert room.status == "open"
        assert room.protocol == "debate"
        assert room.max_rounds == 10

    def test_invalid_protocol(self):
        with pytest.raises(ValueError, match="Unknown protocol"):
            ConversationRoom.create(
                topic="test", protocol="invalid",
                participants=["a"], project_id="p"
            )

    def test_to_dict_roundtrip(self):
        room = ConversationRoom.create(
            topic="test", protocol="brainstorm",
            participants=["a", "b"], project_id="p"
        )
        d = room.to_dict()
        assert d["topic"] == "test"
        assert d["protocol"] == "brainstorm"
        assert d["status"] == "open"


class TestConversationTurn:
    def test_make_turn(self):
        turn = ConversationTurn.make(
            room_id="room1",
            speaker="himari",
            content="REST가 더 나은 선택입니다.",
            turn_type="proposal",
        )
        assert turn.turn_id
        assert turn.speaker == "himari"
        assert turn.turn_type == "proposal"

    def test_invalid_turn_type_defaults_to_statement(self):
        turn = ConversationTurn.make(
            room_id="r", speaker="s", content="c",
            turn_type="unknown_type",
        )
        assert turn.turn_type == "statement"

    def test_to_dict_from_dict_roundtrip(self):
        turn = ConversationTurn.make(
            room_id="r1", speaker="tanjiro",
            content="설계를 검토하겠습니다.",
            turn_type="statement",
            turn_references=["abc123"],
        )
        d = turn.to_dict()
        restored = ConversationTurn.from_dict(d)
        assert restored.turn_id == turn.turn_id
        assert restored.speaker == turn.speaker
        assert restored.turn_references == ["abc123"]

    def test_format_for_context(self):
        turn = ConversationTurn.make(
            room_id="r", speaker="deadbyte",
            content="GraphQL이 프론트에 유리합니다",
            turn_type="objection",
        )
        fmt = turn.format_for_context()
        assert "[deadbyte]" in fmt
        assert "(objection)" in fmt
        assert "GraphQL" in fmt


class TestConversationBudget:
    def test_can_proceed(self):
        budget = ConversationBudget(max_tokens_per_room=1000, reserve_tokens=100)
        assert budget.can_proceed(800)   # 0 + 800 + 100 = 900 ≤ 1000
        assert not budget.can_proceed(901)  # 0 + 901 + 100 = 1001 > 1000

    def test_record_accumulates(self):
        budget = ConversationBudget(max_tokens_per_room=10_000, reserve_tokens=500)
        budget.record("turn1", 300)
        budget.record("turn2", 200)
        assert budget.tokens_used == 500
        assert budget.remaining == 10_000 - 500 - 500  # max - used - reserve

    def test_can_proceed_after_usage(self):
        budget = ConversationBudget(max_tokens_per_room=500, reserve_tokens=50)
        budget.record("t1", 400)
        assert not budget.can_proceed(100)  # 400 + 100 + 50 = 550 > 500


# ── ConsensusEngine ───────────────────────────────────────────────────────

class TestConsensusEngine:
    def _room(self, protocol: str, participants=None, moderator=None) -> ConversationRoom:
        return ConversationRoom.create(
            topic="test", protocol=protocol,
            participants=participants or ["a", "b", "c"],
            project_id="test",
            moderator=moderator or "a",
        )

    def _turn(self, room_id, speaker, content, turn_type="statement"):
        return ConversationTurn.make(
            room_id=room_id, speaker=speaker,
            content=content, turn_type=turn_type,
        )

    def test_debate_majority_vote(self):
        engine = ConsensusEngine()
        room = self._room("debate", ["a", "b", "c"])
        room.current_round = 1
        proposal = self._turn(room.room_id, "a", "REST 채택 제안", "proposal")
        vote_a = self._turn(room.room_id, "a", "agree", "vote")
        vote_b = self._turn(room.room_id, "b", "agree", "vote")
        vote_c = self._turn(room.room_id, "c", "disagree", "vote")
        turns = [proposal, vote_a, vote_b, vote_c]
        result = engine.evaluate(room, turns)
        assert result is not None
        assert result.reached
        assert result.votes["a"] == "agree"
        assert result.votes["c"] == "disagree"

    def test_debate_no_majority(self):
        engine = ConsensusEngine()
        room = self._room("debate", ["a", "b", "c"])
        # 동점 (1 vs 1 vs abstain)
        turns = [
            self._turn(room.room_id, "a", "agree", "vote"),
            self._turn(room.room_id, "b", "disagree", "vote"),
        ]
        result = engine.evaluate(room, turns)
        assert result is None

    def test_debate_unanimous_agreement(self):
        engine = ConsensusEngine()
        room = self._room("debate", ["a", "b"])
        turns = [
            self._turn(room.room_id, "a", "동의합니다", "agreement"),
            self._turn(room.room_id, "b", "동의합니다", "agreement"),
        ]
        result = engine.evaluate(room, turns)
        assert result is not None
        assert result.reached

    def test_review_all_approved(self):
        engine = ConsensusEngine()
        room = self._room("review", ["moderator", "reviewer1", "reviewer2"], moderator="moderator")
        turns = [
            self._turn(room.room_id, "reviewer1", "승인", "agreement"),
            self._turn(room.room_id, "reviewer2", "승인", "agreement"),
        ]
        result = engine.evaluate(room, turns)
        assert result is not None
        assert result.reached

    def test_review_objection_blocks(self):
        engine = ConsensusEngine()
        room = self._room("review", ["mod", "r1", "r2"], moderator="mod")
        turns = [
            self._turn(room.room_id, "r1", "승인", "agreement"),
            self._turn(room.room_id, "r2", "수정 필요", "objection"),
        ]
        result = engine.evaluate(room, turns)
        assert result is None

    def test_brainstorm_triggers_on_max_rounds(self):
        engine = ConsensusEngine()
        room = self._room("brainstorm")
        room.max_rounds = 3
        room.current_round = 3
        turns = [
            self._turn(room.room_id, "a", "아이디어 1", "proposal"),
            self._turn(room.room_id, "b", "아이디어 2", "proposal"),
        ]
        result = engine.evaluate(room, turns)
        assert result is not None
        assert result.reached
        assert len(result.action_items) == 2

    def test_standup_all_spoke(self):
        engine = ConsensusEngine()
        room = self._room("standup", ["a", "b", "c"])
        turns = [
            self._turn(room.room_id, "a", "완료/진행중/없음", "statement"),
            self._turn(room.room_id, "b", "완료/진행중/없음", "statement"),
            self._turn(room.room_id, "c", "완료/진행중/없음", "statement"),
        ]
        result = engine.evaluate(room, turns)
        assert result is not None
        assert result.reached

    def test_standup_not_all_spoke(self):
        engine = ConsensusEngine()
        room = self._room("standup", ["a", "b", "c"])
        turns = [
            self._turn(room.room_id, "a", "보고", "statement"),
        ]
        result = engine.evaluate(room, turns)
        assert result is None

    def test_convergence_detection(self):
        engine = ConsensusEngine()
        room = self._room("debate")
        turns = [
            self._turn(room.room_id, "a", "REST가 낫다", "objection"),
            self._turn(room.room_id, "a", "REST가 낫다", "objection"),
            self._turn(room.room_id, "b", "아닙니다", "objection"),
            self._turn(room.room_id, "b", "아닙니다", "objection"),
        ]
        assert engine.check_convergence(turns, window=2)

    def test_no_convergence(self):
        engine = ConsensusEngine()
        room = self._room("debate")
        turns = [
            self._turn(room.room_id, "a", "제안1", "proposal"),
            self._turn(room.room_id, "a", "동의", "agreement"),
        ]
        assert not engine.check_convergence(turns, window=2)


# ── ConversationToTaskAdapter ─────────────────────────────────────────────

class TestConversationToTaskAdapter:
    def _make_room(self):
        return ConversationRoom.create(
            topic="아키텍처 결정",
            protocol="debate",
            participants=["tanjiro", "himari"],
            project_id="test_proj",
        )

    def _make_consensus(self, action_items=None):
        return ConsensusResult(
            reached=True,
            decision="REST API 채택",
            protocol="debate",
            votes={"tanjiro": "agree", "himari": "agree"},
            action_items=action_items or [
                {"title": "REST API 설계", "owner_role": "Backend Architect", "description": "API 스펙 작성"},
                {"title": "프론트엔드 연동", "owner_role": "Frontend Architect", "description": "React 훅 구현"},
            ],
        )

    def test_adapt_creates_tasks(self):
        adapter = ConversationToTaskAdapter()
        room = self._make_room()
        consensus = self._make_consensus()
        board = adapter.adapt(consensus, room, {})
        tasks = board.get("tasks", [])
        assert len(tasks) == 2
        assert tasks[0]["title"] == "REST API 설계"
        assert tasks[0]["owner_role"] == "Backend Architect"

    def test_adapt_adds_consensus_metadata(self):
        adapter = ConversationToTaskAdapter()
        room = self._make_room()
        consensus = self._make_consensus()
        board = adapter.adapt(consensus, room, {})
        task = board["tasks"][0]
        meta = task.get("consensus_metadata")
        assert meta is not None
        assert meta["conversation_room_id"] == room.room_id
        assert meta["consensus"]["reached"] is True
        assert meta["consensus"]["decision"] == "REST API 채택"

    def test_adapt_no_duplicate_tasks(self):
        adapter = ConversationToTaskAdapter()
        room = self._make_room()
        consensus = self._make_consensus()
        existing_board = {
            "tasks": [
                {"task_id": "existing", "title": "REST API 설계",
                 "instruction": "기존 태스크", "status": "pending", "notes": []}
            ]
        }
        board = adapter.adapt(consensus, room, existing_board)
        tasks = board["tasks"]
        titles = [t["title"] for t in tasks]
        # 중복 없이 기존 태스크 유지 + 새 태스크만 추가
        assert titles.count("REST API 설계") == 1

    def test_adapt_injects_metadata_to_existing(self):
        adapter = ConversationToTaskAdapter()
        room = self._make_room()
        consensus = self._make_consensus()
        existing_board = {
            "tasks": [
                {"task_id": "t1", "title": "REST API 설계",
                 "instruction": "기존", "status": "pending", "notes": []}
            ]
        }
        board = adapter.adapt(consensus, room, existing_board)
        task = board["tasks"][0]
        assert "consensus_metadata" in task

    def test_adapt_records_decision_in_board(self):
        adapter = ConversationToTaskAdapter()
        room = self._make_room()
        consensus = self._make_consensus()
        board = adapter.adapt(consensus, room, {})
        decisions = board.get("conversation_decisions", [])
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "REST API 채택"

    def test_adapt_no_action_items_still_records_decision(self):
        adapter = ConversationToTaskAdapter()
        room = self._make_room()
        consensus = ConsensusResult(
            reached=True, decision="GraphQL 보류", protocol="debate",
        )
        board = adapter.adapt(consensus, room, {})
        assert "conversation_decisions" in board
        assert not board.get("tasks")  # tasks 없음

    def test_adapt_none_consensus_returns_board_unchanged(self):
        """consensus=None 또는 reached=False이면 board를 변경하지 않아야 한다."""
        adapter = ConversationToTaskAdapter()
        room = self._make_room()
        original_board = {"tasks": [{"task_id": "t1", "title": "기존 태스크"}]}

        # reached=False
        not_reached = ConsensusResult(reached=False, decision="", protocol="debate")
        result = adapter.adapt(not_reached, room, dict(original_board))
        assert result.get("tasks") == original_board["tasks"]
        assert "conversation_decisions" not in result

    def test_adapt_preserves_existing_task_status(self):
        """adapt()가 기존 task의 status를 덮어쓰지 않아야 한다."""
        adapter = ConversationToTaskAdapter()
        room = self._make_room()
        consensus = self._make_consensus([
            {"title": "진행 중인 태스크", "owner_role": "Dev", "description": "이미 진행 중"}
        ])
        existing_board = {
            "tasks": [
                {"task_id": "t1", "title": "진행 중인 태스크",
                 "instruction": "이미 진행 중", "status": "in_progress", "notes": []}
            ]
        }
        result = adapter.adapt(consensus, room, existing_board)
        task = result["tasks"][0]
        # 기존 task의 status는 유지되어야 함 (adapt가 덮어쓰지 않음)
        assert task["status"] == "in_progress"


# ── ConversationManager (lease 충돌 단위 테스트) ──────────────────────────

class TestConversationManagerLeaseConflict:
    """
    AgentRunner 없이 lease 충돌 로직만 테스트.
    """

    def _make_manager(self):
        from unittest.mock import MagicMock
        import tempfile
        tmp = tempfile.mkdtemp()
        mgr = AgentReservationManager()
        conv = __import__("core.conversation_manager", fromlist=["ConversationManager"]).ConversationManager(
            project_id="test",
            workspace=tmp,
            broker=MagicMock(),
            agent_runner=MagicMock(),
            agent_mgr=MagicMock(),
            reservation_mgr=mgr,
        )
        return conv, mgr, tmp

    @staticmethod
    def _cleanup(tmp: str):
        """임시 디렉토리 정리."""
        import shutil
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    def test_create_room_reserves_agents(self):
        conv, mgr, tmp = self._make_manager()
        try:
            room = conv.create_room(
                topic="test", protocol="debate",
                participants=["a", "b"], moderator="a",
            )
            assert mgr.is_reserved("a")
            assert mgr.is_reserved("b")
            mgr.release_all(list(room.lease_ids.values()))
        finally:
            self._cleanup(tmp)

    def test_create_room_raises_on_conflict(self):
        conv, mgr, tmp = self._make_manager()
        lid = mgr.reserve("a", duration=60)
        try:
            with pytest.raises(ValueError, match="에이전트 점유 충돌"):
                conv.create_room(
                    topic="test", protocol="debate",
                    participants=["a", "b"],
                )
        finally:
            mgr.release(lid)
            self._cleanup(tmp)


# ── 할루시네이션 방어 테스트 ──────────────────────────────────────────────

from core.conversation_manager import infer_turn_type_from_text


class TestInferTurnTypeFromText:
    """자연어에서 turn_type 추론 테스트."""

    @pytest.mark.parametrize("text,expected", [
        ("저는 이 방안에 동의합니다", "agreement"),
        ("LGTM, let's go", "agreement"),
        ("찬성합니다!", "agreement"),
        ("+1 좋은 아이디어입니다", "agreement"),
        ("approve this change", "agreement"),
    ])
    def test_agreement_patterns(self, text, expected):
        assert infer_turn_type_from_text(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("반대합니다. 재검토가 필요합니다", "objection"),
        ("I disagree with this approach", "objection"),
        ("수정 필요한 부분이 있습니다", "objection"),
        ("동의할 수 없습니다", "objection"),
    ])
    def test_objection_patterns(self, text, expected):
        assert infer_turn_type_from_text(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("새로운 방안을 제안합니다", "proposal"),
        ("I propose we use a different approach", "proposal"),
        ("캐시를 사용하면 어떨까요?", "proposal"),
    ])
    def test_proposal_patterns(self, text, expected):
        assert infer_turn_type_from_text(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("이 부분은 어떻게 생각하시나요?", "question"),
        ("Why do we need this?", "question"),
        ("What if we change the architecture?", "question"),
    ])
    def test_question_patterns(self, text, expected):
        assert infer_turn_type_from_text(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("찬성에 한 표 던집니다", "vote"),
        ("반대에 투표합니다", "vote"),
    ])
    def test_vote_patterns(self, text, expected):
        assert infer_turn_type_from_text(text) == expected

    def test_fallback_to_statement(self):
        assert infer_turn_type_from_text("오늘 날씨가 좋네요") == "statement"
        assert infer_turn_type_from_text("") == "statement"


class TestConsensusEngineHallucinationDefense:
    """ConsensusEngine가 자연어 content에서도 투표/동의를 감지하는지 테스트."""

    def test_collect_votes_from_natural_language(self):
        from core.consensus_engine import ConsensusEngine
        engine = ConsensusEngine()
        turns = [
            ConversationTurn.make("r1", "agent_a", "저는 이 방안에 찬성합니다",
                                  turn_type="statement"),
            ConversationTurn.make("r1", "agent_b", "I disagree with this",
                                  turn_type="statement"),
        ]
        votes = engine._collect_votes(turns, ["agent_a", "agent_b"])
        assert votes["agent_a"] == "agree"
        assert votes["agent_b"] == "disagree"

    def test_collect_agreements_from_natural_language(self):
        from core.consensus_engine import ConsensusEngine
        engine = ConsensusEngine()
        turns = [
            ConversationTurn.make("r1", "agent_a", "동의합니다. 진행하죠",
                                  turn_type="statement"),
            ConversationTurn.make("r1", "agent_b", "LGTM, approve",
                                  turn_type="statement"),
            ConversationTurn.make("r1", "agent_c", "반대합니다",
                                  turn_type="statement"),
        ]
        agreements = engine._collect_agreements(turns, ["agent_a", "agent_b", "agent_c"])
        assert "agent_a" in agreements
        assert "agent_b" in agreements
        assert "agent_c" not in agreements

    def test_debate_consensus_with_natural_language_votes(self):
        """debate 프로토콜에서 자연어 투표로도 합의 도출 가능."""
        from core.consensus_engine import ConsensusEngine
        engine = ConsensusEngine()
        room = ConversationRoom(
            room_id="r1", topic="test", protocol="debate",
            participants=["a", "b", "c"], moderator="a",
            project_id="test_proj",
        )
        room.current_round = 2
        turns = [
            ConversationTurn.make("r1", "a", "이 방안을 제안합니다",
                                  turn_type="statement"),
            ConversationTurn.make("r1", "b", "좋습니다, 찬성합니다",
                                  turn_type="statement"),
            ConversationTurn.make("r1", "c", "동의합니다 +1",
                                  turn_type="statement"),
        ]
        result = engine.evaluate(room, turns)
        # 3명 모두 동의 → 합의 도출
        assert result is not None
        assert result.reached is True


class TestUnstructuredTurnHandling:
    """unstructured turn_type 처리 테스트."""

    def test_unstructured_in_turn_types(self):
        from core.conversation_room import TURN_TYPES
        assert "unstructured" in TURN_TYPES

    def test_unstructured_ratio_all_unstructured(self):
        from core.consensus_engine import ConsensusEngine
        engine = ConsensusEngine()
        turns = [
            ConversationTurn.make("r1", "a", "뭔가 말함", turn_type="unstructured"),
            ConversationTurn.make("r1", "b", "또 말함", turn_type="unstructured"),
        ]
        assert engine._unstructured_ratio(turns) == 1.0

    def test_unstructured_ratio_excludes_human_input(self):
        from core.consensus_engine import ConsensusEngine
        engine = ConsensusEngine()
        turns = [
            ConversationTurn.make("r1", "a", "찬성", turn_type="agreement"),
            ConversationTurn.make("r1", "human", "계속해", turn_type="human_input"),
            ConversationTurn.make("r1", "b", "뭔가", turn_type="unstructured"),
        ]
        # human_input 제외: agent 턴 2개 중 unstructured 1개 = 50%
        assert engine._unstructured_ratio(turns) == 0.5

    def test_consensus_blocked_when_too_many_unstructured(self):
        """unstructured 50% 이상이면 합의 판정 건너뜀."""
        import warnings
        from core.consensus_engine import ConsensusEngine
        engine = ConsensusEngine()
        room = ConversationRoom(
            room_id="r1", topic="test", protocol="debate",
            participants=["a", "b"], moderator="a",
            project_id="test_proj",
        )
        room.current_round = 2
        turns = [
            ConversationTurn.make("r1", "a", "동의합니다", turn_type="unstructured"),
            ConversationTurn.make("r1", "b", "찬성합니다", turn_type="unstructured"),
        ]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = engine.evaluate(room, turns)
        assert result is None
        assert any("unstructured" in str(warning.message) for warning in w)

    def test_consensus_proceeds_when_unstructured_below_threshold(self):
        """unstructured 비율이 낮으면 정상 합의 판정."""
        from core.consensus_engine import ConsensusEngine
        engine = ConsensusEngine()
        room = ConversationRoom(
            room_id="r1", topic="test", protocol="debate",
            participants=["a", "b", "c"], moderator="a",
            project_id="test_proj",
        )
        room.current_round = 2
        turns = [
            ConversationTurn.make("r1", "a", "제안합니다", turn_type="proposal"),
            ConversationTurn.make("r1", "b", "동의합니다", turn_type="agreement"),
            ConversationTurn.make("r1", "c", "알겠어요", turn_type="unstructured"),  # 33%
        ]
        # 33% < 50% → 합의 판정 진행 (c는 동의 집합에 안 들어가므로 합의 미도달)
        result = engine.evaluate(room, turns)
        assert result is None  # b만 agreement, c는 unstructured → 과반 미달

    def _make_minimal_manager(self, llm_engine=None):
        """_reclassify_turn_type 테스트용 최소 ConversationManager 인스턴스."""
        from core.conversation_manager import ConversationManager
        from unittest.mock import MagicMock
        mgr = object.__new__(ConversationManager)
        # _reclassify_turn_type이 참조하는 속성만 설정
        mgr._llm_engine = llm_engine
        return mgr

    def test_reclassify_without_llm_engine_returns_unstructured_for_ambiguous(self):
        """LLMEngine 없고 패턴 미매칭 → unstructured."""
        mgr = self._make_minimal_manager(llm_engine=None)
        result = mgr._reclassify_turn_type("오늘 날씨가 참 좋네요")
        assert result == "unstructured"

    def test_reclassify_without_llm_engine_matches_clear_pattern(self):
        """LLMEngine 없어도 명확한 패턴은 분류됨."""
        mgr = self._make_minimal_manager(llm_engine=None)
        assert mgr._reclassify_turn_type("동의합니다!") == "agreement"
        assert mgr._reclassify_turn_type("반대합니다") == "objection"
