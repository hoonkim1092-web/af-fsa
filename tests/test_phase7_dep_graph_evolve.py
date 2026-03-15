"""
Phase 7: 의존성 그래프 위상 정렬 + FSALoop 스킬 자동 진화 테스트 (20개)

테스트 대상:
- SkillDependencyGraph: DAG 구축, 순환 감지, 위상 정렬 (10개)
- DynamicSkillLoader 의존성 통합: 자동 주입, 12-Cap (4개)
- FSALoop 스킬 진화: 스킬 감지, 검증, 롤백, 핫리로딩 (6개)
"""
import os

import pytest

from core.skill_metadata import SkillMetadata, SkillCategory


# ===========================================================================
# SkillDependencyGraph 테스트 (10개)
# ===========================================================================

class TestSkillDependencyGraph:
    """의존성 그래프 위상 정렬 단위 테스트"""

    def _make_skill(self, skill_id, deps=None, incompatible=None):
        return SkillMetadata(
            skill_id=skill_id,
            name=skill_id,
            description=f"Test skill {skill_id}",
            category=SkillCategory.CODING,
            dependencies=deps or [],
            incompatible_with=incompatible or [],
        )

    def test_empty_graph(self):
        """스킬 없으면 빈 결과"""
        from core.skill_loader import SkillDependencyGraph
        graph = SkillDependencyGraph({})
        sorted_ids, excluded = graph.topological_sort([])
        assert sorted_ids == []
        assert excluded == []

    def test_no_dependencies(self):
        """의존성 없는 스킬들은 원래 순서 유지"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a"),
            "b": self._make_skill("b"),
            "c": self._make_skill("c"),
        }
        graph = SkillDependencyGraph(skills)
        sorted_ids, excluded = graph.topological_sort(["a", "b", "c"])
        assert set(sorted_ids) == {"a", "b", "c"}
        assert excluded == []

    def test_linear_dependency(self):
        """A→B→C 선형 의존성: C가 먼저 실행"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a", deps=["b"]),
            "b": self._make_skill("b", deps=["c"]),
            "c": self._make_skill("c"),
        }
        graph = SkillDependencyGraph(skills)
        sorted_ids, excluded = graph.topological_sort(["a", "b", "c"])
        assert excluded == []
        # c는 b보다 먼저, b는 a보다 먼저
        assert sorted_ids.index("c") < sorted_ids.index("b")
        assert sorted_ids.index("b") < sorted_ids.index("a")

    def test_diamond_dependency(self):
        """다이아몬드 의존성: A→B, A→C, B→D, C→D"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a", deps=["b", "c"]),
            "b": self._make_skill("b", deps=["d"]),
            "c": self._make_skill("c", deps=["d"]),
            "d": self._make_skill("d"),
        }
        graph = SkillDependencyGraph(skills)
        sorted_ids, excluded = graph.topological_sort(["a", "b", "c", "d"])
        assert excluded == []
        assert sorted_ids.index("d") < sorted_ids.index("b")
        assert sorted_ids.index("d") < sorted_ids.index("c")
        assert sorted_ids.index("b") < sorted_ids.index("a")
        assert sorted_ids.index("c") < sorted_ids.index("a")

    def test_cycle_detection_simple(self):
        """A→B→A 순환 감지"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a", deps=["b"]),
            "b": self._make_skill("b", deps=["a"]),
        }
        graph = SkillDependencyGraph(skills)
        cycles = graph.detect_cycles()
        assert len(cycles) > 0

    def test_cycle_detection_triangle(self):
        """A→B→C→A 삼각 순환 감지"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a", deps=["b"]),
            "b": self._make_skill("b", deps=["c"]),
            "c": self._make_skill("c", deps=["a"]),
        }
        graph = SkillDependencyGraph(skills)
        cycles = graph.detect_cycles()
        assert len(cycles) > 0

    def test_cycle_excluded_from_sort(self):
        """순환 스킬은 위상 정렬에서 제외"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a", deps=["b"]),
            "b": self._make_skill("b", deps=["a"]),
            "c": self._make_skill("c"),  # 독립
        }
        graph = SkillDependencyGraph(skills)
        sorted_ids, excluded = graph.topological_sort(["a", "b", "c"])
        assert "c" in sorted_ids
        assert set(excluded) == {"a", "b"}

    def test_get_required_deps(self):
        """누락된 의존 스킬 반환"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a", deps=["b"]),
            "b": self._make_skill("b"),
            "c": self._make_skill("c"),
        }
        graph = SkillDependencyGraph(skills)
        missing = graph.get_required_deps(["a"])  # a는 b가 필요
        assert "b" in missing

    def test_no_missing_deps(self):
        """모든 의존성 포함 시 빈 목록"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a", deps=["b"]),
            "b": self._make_skill("b"),
        }
        graph = SkillDependencyGraph(skills)
        missing = graph.get_required_deps(["a", "b"])
        assert missing == []

    def test_partial_graph_sort(self):
        """전체 그래프 중 일부 스킬만 정렬"""
        from core.skill_loader import SkillDependencyGraph
        skills = {
            "a": self._make_skill("a", deps=["b"]),
            "b": self._make_skill("b", deps=["c"]),
            "c": self._make_skill("c"),
            "d": self._make_skill("d"),
        }
        graph = SkillDependencyGraph(skills)
        sorted_ids, excluded = graph.topological_sort(["a", "b"])
        assert excluded == []
        assert sorted_ids.index("b") < sorted_ids.index("a")


# ===========================================================================
# DynamicSkillLoader 의존성 통합 테스트 (4개)
# ===========================================================================

class TestDynamicSkillLoaderDeps:
    """DynamicSkillLoader 의존성 자동 주입 테스트"""

    def _register_skills(self, skills_dict):
        from core.skill_registry import get_global_registry
        registry = get_global_registry()
        registry.clear()
        for sid, meta in skills_dict.items():
            registry.register(meta)

    def _make_skill(self, skill_id, deps=None, keywords=None, category=SkillCategory.CODING):
        return SkillMetadata(
            skill_id=skill_id,
            name=skill_id,
            description=f"Test skill {skill_id}",
            category=category,
            dependencies=deps or [],
            when_to_use_keywords=keywords or [skill_id],
        )

    def test_auto_inject_deps(self):
        """의존 스킬이 자동으로 포함됨"""
        from core.skill_loader import DynamicSkillLoader
        skills = {
            "coder": self._make_skill("coder", deps=["formatter"], keywords=["코드"]),
            "formatter": self._make_skill("formatter", keywords=["포맷"]),
            "searcher": self._make_skill("searcher", keywords=["검색"]),
        }
        self._register_skills(skills)
        loader = DynamicSkillLoader()
        selected, scores = loader.load_skills_for_task("코드 작성해줘")
        selected_ids = [s.skill_id for s in selected]
        # coder가 선택되면 formatter도 자동 포함
        if "coder" in selected_ids:
            assert "formatter" in selected_ids

    def test_12cap_with_deps(self):
        """의존성 추가해도 12개 초과하지 않음"""
        from core.skill_loader import DynamicSkillLoader
        skills = {}
        for i in range(15):
            skills[f"s{i}"] = self._make_skill(f"s{i}", keywords=[f"kw{i}"])
        self._register_skills(skills)
        loader = DynamicSkillLoader()
        # 모든 키워드를 포함하는 입력
        task = " ".join(f"kw{i}" for i in range(15))
        selected, _ = loader.load_skills_for_task(task)
        assert len(selected) <= 12

    def test_invalidate_dep_graph(self):
        """dep graph 캐시 무효화"""
        from core.skill_loader import DynamicSkillLoader
        skills = {"a": self._make_skill("a")}
        self._register_skills(skills)
        loader = DynamicSkillLoader()
        assert loader._dep_graph is None
        loader.load_skills_for_task("a")
        assert loader._dep_graph is not None
        loader.invalidate_dep_graph()
        assert loader._dep_graph is None

    def test_cycle_in_loader_graceful(self):
        """순환 의존성이 있어도 로더가 정상 동작"""
        from core.skill_loader import DynamicSkillLoader
        skills = {
            "x": self._make_skill("x", deps=["y"], keywords=["엑스"]),
            "y": self._make_skill("y", deps=["x"], keywords=["와이"]),
            "z": self._make_skill("z", keywords=["제트"]),
        }
        self._register_skills(skills)
        loader = DynamicSkillLoader()
        selected, scores = loader.load_skills_for_task("엑스 와이 제트")
        selected_ids = [s.skill_id for s in selected]
        # 순환 스킬은 제외되고, z는 포함
        assert "z" in selected_ids


# ===========================================================================
# FSALoop 스킬 자동 진화 테스트 (6개)
# ===========================================================================

class TestFSALoopSkillEvolve:
    """FSALoop 스킬 진화 관련 단위 테스트.

    FSALoop는 AgentRunner 등 무거운 의존성이 있으므로,
    sys.modules를 mock하여 경량 import 후 테스트합니다.
    """

    @pytest.fixture(autouse=True)
    def _setup_fsa_module(self):
        """FSALoop를 경량 mock 의존성과 함께 import."""
        import sys
        from unittest.mock import MagicMock

        # 무거운 모듈을 가짜로 대체 (아직 로드 안 된 경우만)
        stubs = {}
        for mod_name in ("core.agent_runner", "core.git_manager", "core.evaluator"):
            if mod_name not in sys.modules:
                fake = type(sys)("fake_" + mod_name)
                stubs[mod_name] = fake
                sys.modules[mod_name] = fake

        # 필수 클래스 스텁
        sys.modules["core.agent_runner"].AgentRunner = MagicMock
        sys.modules["core.git_manager"].GitManager = MagicMock
        mock_evaluator = MagicMock()
        sys.modules["core.evaluator"].StrategyEvaluator = lambda **kw: mock_evaluator

        # FSALoop import (또는 이미 있으면 reload)
        if "core.fsa_loop" in sys.modules:
            import importlib
            importlib.reload(sys.modules["core.fsa_loop"])

        from core.fsa_loop import FSALoop
        runner = MagicMock()
        runner.mr = MagicMock()
        runner.mr.pick = MagicMock(return_value="test-model")
        self.fsa = FSALoop(runner)

        yield

        # 정리: 스텁만 제거
        for mod_name in stubs:
            sys.modules.pop(mod_name, None)
        sys.modules.pop("core.fsa_loop", None)

    def test_detect_failed_skill_dir_found(self, tmp_path):
        """에러 메시지에서 스킬 디렉토리를 올바르게 감지"""
        skill_dir = tmp_path / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text("# test")

        import core.fsa_loop as fsa_module
        original_project = fsa_module.PROJECT_SKILLS_DIR
        fsa_module.PROJECT_SKILLS_DIR = str(tmp_path)
        try:
            result = self.fsa._detect_failed_skill_dir("test_skill에서 에러 발생", "")
            assert result is not None
            assert "test_skill" in result
        finally:
            fsa_module.PROJECT_SKILLS_DIR = original_project

    def test_detect_failed_skill_dir_not_found(self, tmp_path):
        """에러 메시지에 스킬 이름이 없으면 None 반환"""
        import core.fsa_loop as fsa_module
        original_project = fsa_module.PROJECT_SKILLS_DIR
        fsa_module.PROJECT_SKILLS_DIR = str(tmp_path)
        try:
            result = self.fsa._detect_failed_skill_dir("알 수 없는 에러", "")
            assert result is None
        finally:
            fsa_module.PROJECT_SKILLS_DIR = original_project

    def test_verify_evolved_skill_safe_code(self, tmp_path):
        """안전한 코드는 보안 검사 통과"""
        skill_py = tmp_path / "skill.py"
        skill_py.write_text('def test(ctx):\n    return {"ok": True}\n')

        from core.security_guard import quick_guard
        with open(str(skill_py), "r") as f:
            code = f.read()
        safe, violations = quick_guard(code)
        assert safe is True
        assert violations == []

    def test_verify_evolved_skill_unsafe_code(self, tmp_path):
        """위험한 코드는 보안 검사 실패"""
        skill_py = tmp_path / "skill.py"
        skill_py.write_text('import os\nos.system("rm -rf /")\n')

        from core.security_guard import quick_guard
        with open(str(skill_py), "r") as f:
            code = f.read()
        safe, violations = quick_guard(code)
        assert safe is False
        assert len(violations) > 0

    def test_rollback_skill(self, tmp_path):
        """.bak 파일로 롤백 동작 확인"""
        skill_dir = tmp_path / "my_skill"
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text("# evolved version")
        (skill_dir / "skill.py.bak").write_text("# original version")

        self.fsa._rollback_skill(str(skill_dir), "my_skill")

        content = (skill_dir / "skill.py").read_text()
        assert content == "# original version"
        assert not (skill_dir / "skill.py.bak").exists()

    def test_hot_reload_registry(self):
        """핫리로딩이 예외 없이 동작"""
        from unittest.mock import patch, MagicMock

        mock_registry = MagicMock()
        mock_registry.auto_load_from_directories.return_value = 5
        mock_registry.count.return_value = 5

        with patch("core.skill_registry.get_global_registry", return_value=mock_registry):
            self.fsa._hot_reload_registry("test_skill")
            mock_registry.auto_load_from_directories.assert_called_once_with(force=True)
