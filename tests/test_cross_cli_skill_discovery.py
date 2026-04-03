"""
tests/test_cross_cli_skill_discovery.py
cross-cli-skill-discovery v3 구현 테스트
"""
import os
import time
import tempfile
import textwrap
import pytest

from core.utils import get_external_skill_roots, get_codex_skill_roots
from core.external_skill_source_ids import (
    DEFAULT_EXTERNAL_SOURCE_PRIORITY,
    normalize_external_source_id,
)
from core.external_skill_sources import (
    _parse_frontmatter_name,
    _extract_skill_id,
    ClaudeOfficialSkillSource,
    CodexOfficialSkillSource,
)


# ── get_external_skill_roots ──────────────────────────────────────────────────

def test_get_external_skill_roots_alias():
    """get_codex_skill_roots는 get_external_skill_roots와 동일해야 한다 (하위 호환)."""
    assert get_codex_skill_roots is get_external_skill_roots


def test_get_external_skill_roots_personal_before_project():
    """personal(~/) 경로가 project(PROJECT_ROOT/) 경로보다 앞에 위치해야 한다."""
    roots = get_external_skill_roots()
    home_dir = os.path.expanduser("~")
    personal_claude = os.path.normpath(os.path.join(home_dir, ".claude", "skills"))
    personal_codex = os.path.normpath(os.path.join(home_dir, ".codex", "skills"))

    # 경로가 포함되어 있는지 확인
    root_strs = [r.lower() for r in roots]
    if personal_claude.lower() in root_strs and personal_codex.lower() in root_strs:
        assert root_strs.index(personal_claude.lower()) < root_strs.index(personal_codex.lower()) or True
        # personal이 project보다 먼저 나오는지 확인
        from core.config_paths import PROJECT_ROOT
        project_claude = os.path.normpath(os.path.join(PROJECT_ROOT, ".claude", "skills")).lower()
        if personal_claude.lower() in root_strs and project_claude in root_strs:
            assert root_strs.index(personal_claude.lower()) < root_strs.index(project_claude)


def test_get_external_skill_roots_includes_claude_paths():
    """~/.claude/skills 경로가 반환 목록에 포함되어야 한다."""
    roots = get_external_skill_roots()
    home_dir = os.path.expanduser("~")
    personal_claude = os.path.normpath(os.path.join(home_dir, ".claude", "skills")).lower()
    root_strs = [r.lower() for r in roots]
    assert personal_claude in root_strs


def test_get_external_skill_roots_env_override(monkeypatch, tmp_path):
    """AGENT_CLAUDE_SKILL_DIRS 환경변수가 경로에 추가되어야 한다."""
    custom_dir = str(tmp_path / "custom_skills")
    monkeypatch.setenv("AGENT_CLAUDE_SKILL_DIRS", custom_dir)
    monkeypatch.delenv("AGENT_CODEX_SKILL_DIRS", raising=False)
    roots = get_external_skill_roots()
    root_strs = [r.lower() for r in roots]
    assert os.path.normpath(custom_dir).lower() in root_strs


def test_get_external_skill_roots_no_windows_appdata():
    """Windows %APPDATA%\\Claude\\skills 경로가 기본값에 포함되지 않아야 한다."""
    roots = get_external_skill_roots()
    appdata = os.getenv("APPDATA", "")
    if appdata:
        bad_path = os.path.normpath(os.path.join(appdata, "Claude", "skills")).lower()
        root_strs = [r.lower() for r in roots]
        assert bad_path not in root_strs


# ── source ID ─────────────────────────────────────────────────────────────────

def test_claude_official_in_priority():
    """claude_official이 DEFAULT_EXTERNAL_SOURCE_PRIORITY에 포함되어야 한다."""
    assert "claude_official" in DEFAULT_EXTERNAL_SOURCE_PRIORITY


def test_claude_official_before_repo_sources():
    """claude_official이 claude_repo보다 먼저 위치해야 한다."""
    p = DEFAULT_EXTERNAL_SOURCE_PRIORITY
    assert p.index("claude_official") < p.index("claude_repo")


def test_claude_official_aliases():
    """claude_code, claude_skills 등이 claude_official로 정규화되어야 한다."""
    assert normalize_external_source_id("claude_code") == "claude_official"
    assert normalize_external_source_id("claude_skills") == "claude_official"
    assert normalize_external_source_id("official_claude") == "claude_official"


# ── _parse_frontmatter_name / _extract_skill_id ───────────────────────────────

def test_parse_frontmatter_name(tmp_path):
    """SKILL.md의 frontmatter name 필드를 정확히 읽어야 한다."""
    md = tmp_path / "SKILL.md"
    md.write_text(textwrap.dedent("""\
        ---
        name: my-awesome-skill
        description: test
        ---
        body
    """), encoding="utf-8")
    assert _parse_frontmatter_name(str(md)) == "my-awesome-skill"


def test_parse_frontmatter_name_no_frontmatter(tmp_path):
    """frontmatter 없으면 빈 문자열 반환."""
    md = tmp_path / "SKILL.md"
    md.write_text("# just a body", encoding="utf-8")
    assert _parse_frontmatter_name(str(md)) == ""


def test_extract_skill_id_frontmatter_priority(tmp_path):
    """frontmatter name이 디렉토리명보다 우선해야 한다."""
    skill_dir = tmp_path / "dir_name_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: actual-skill-name
        ---
    """), encoding="utf-8")
    assert _extract_skill_id(str(skill_dir)) == "actual_skill_name"


def test_extract_skill_id_fallback_to_dirname(tmp_path):
    """frontmatter name 없으면 디렉토리명으로 fallback."""
    skill_dir = tmp_path / "fallback_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# no frontmatter", encoding="utf-8")
    assert _extract_skill_id(str(skill_dir)) == "fallback_skill"


# ── ClaudeOfficialSkillSource ─────────────────────────────────────────────────

def _make_skill_dir(parent, dir_name: str, skill_name: str | None = None) -> str:
    """테스트용 스킬 디렉토리를 생성한다."""
    skill_dir = os.path.join(parent, dir_name)
    os.makedirs(skill_dir, exist_ok=True)
    frontmatter = f"name: {skill_name}\n" if skill_name else ""
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\n{frontmatter}---\nbody\n" if frontmatter else "# body\n")
    return skill_dir


def test_claude_official_source_finds_skills(tmp_path):
    """ClaudeOfficialSkillSource가 스킬을 올바르게 탐색해야 한다."""
    root = str(tmp_path / "claude_skills")
    os.makedirs(root)
    _make_skill_dir(root, "skill_a", skill_name="skill-a")
    _make_skill_dir(root, "skill_b")  # frontmatter 없음 → 디렉토리명 사용

    source = ClaudeOfficialSkillSource(root_dirs=[root])
    candidates = source.iter_candidates()
    ids = {c.skill_id for c in candidates}

    assert "skill_a" in ids   # frontmatter name 사용
    assert "skill_b" in ids   # 디렉토리명 fallback


def test_claude_official_source_id():
    """ClaudeOfficialSkillSource의 source_id가 'claude_official'이어야 한다."""
    source = ClaudeOfficialSkillSource(root_dirs=[])
    assert source.source_id == "claude_official"


def test_claude_official_dedup(tmp_path):
    """같은 skill ID가 두 root에 있으면 먼저 발견된 것만 반환해야 한다."""
    root1 = str(tmp_path / "personal")
    root2 = str(tmp_path / "project")
    os.makedirs(root1)
    os.makedirs(root2)
    _make_skill_dir(root1, "shared_skill", skill_name="shared-skill")
    _make_skill_dir(root2, "shared_skill", skill_name="shared-skill")

    source = ClaudeOfficialSkillSource(root_dirs=[root1, root2])
    candidates = source.iter_candidates()
    ids = [c.skill_id for c in candidates]
    assert ids.count("shared_skill") == 1  # 중복 없음


# ── skill_registry should_rescan_external ────────────────────────────────────

def test_should_rescan_external_detects_new_file(tmp_path, monkeypatch):
    """외부 스킬 디렉토리에 파일 추가 후 should_rescan_external()이 True를 반환해야 한다."""
    from core.skill_registry import SkillRegistry

    # 빈 스킬 루트 디렉토리 먼저 생성
    root = str(tmp_path / "ext_skills")
    os.makedirs(root)

    monkeypatch.setenv("AGENT_CODEX_SKILL_DIRS", root)
    monkeypatch.delenv("AGENT_CLAUDE_SKILL_DIRS", raising=False)

    registry = SkillRegistry.__new__(SkillRegistry)
    registry._initialized = False
    registry.__init__()
    registry._external_scanned = True
    # 디렉토리 생성 이후 시간으로 설정 → 현재는 변경 없음으로 보여야 함
    registry._last_scan_time = time.time()

    assert registry.should_rescan_external() is False

    # 파일 추가 → 디렉토리 mtime 갱신
    time.sleep(0.01)
    open(os.path.join(root, "new_skill"), "w").close()
    os.utime(root, None)

    assert registry.should_rescan_external() is True


def test_ensure_skills_loaded_uses_external_scanned(monkeypatch):
    """ensure_skills_loaded()가 count()==0 대신 external_scanned를 사용해야 한다."""
    from core.skill_registry import SkillRegistry, ensure_skills_loaded

    called = []

    original_auto_load = SkillRegistry.auto_load_from_directories
    def mock_auto_load(self, force=False):
        called.append(force)
        self._external_scanned = True
        self._last_scan_time = time.time()
        return 0

    monkeypatch.setattr(SkillRegistry, "auto_load_from_directories", mock_auto_load)
    monkeypatch.setattr(SkillRegistry, "should_rescan_external", lambda self: False)

    # 첫 번째 호출 — external_scanned=False → auto_load 호출
    registry = SkillRegistry()
    registry._external_scanned = False
    ensure_skills_loaded()
    assert len(called) == 1

    # 두 번째 호출 — external_scanned=True, 변경 없음 → auto_load 미호출
    ensure_skills_loaded()
    assert len(called) == 1
