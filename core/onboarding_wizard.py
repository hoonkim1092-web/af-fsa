"""
core/onboarding_wizard.py
=========================
BKIT 스타일 대화형 온보딩 위저드.

input() 기반 (LLM 미사용) → 빠르고 결정적.
3단계: 프로젝트명 → 설명 → 레벨 선택 → PDCAState 초기화.
"""
from __future__ import annotations

import os
import re
import sys

from core.pdca_state import PDCAState, PDCAPhase, ProjectLevel


# ── 색상 유틸 ──
def _c(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _hr(char: str = "═", width: int = 50) -> str:
    return _c(char * width, "36")


def _print_welcome():
    print()
    print(_hr("═"))
    print(_c("  Agent Factory — 프로젝트 시작하기", "1;36"))
    print(_hr("═"))
    print()


def _safe_project_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_\-]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "my_project"


class OnboardingWizard:
    """신규 프로젝트 온보딩을 단계별로 안내한다."""

    def __init__(self, projects_root: str):
        self.projects_root = projects_root

    def run(self) -> PDCAState | None:
        """온보딩 위저드 실행. 완료 시 PDCAState 반환, 취소 시 None."""
        _print_welcome()

        # ── Step 1: 프로젝트명 ──
        project_name = self._ask_project_name()
        if project_name is None:
            return None

        # ── Step 2: 설명 ──
        task_description = self._ask_task_description(project_name)
        if task_description is None:
            return None

        # ── Step 3: 레벨 선택 ──
        level = self._ask_level()
        if level is None:
            return None

        # PDCAState 생성
        project_id = _safe_project_id(project_name)
        state = PDCAState.create(
            project_id=project_id,
            project_name=project_name,
            task_description=task_description,
            level=level,
        )

        # 환경변수 설정
        workspace = os.path.join(self.projects_root, project_id)
        os.makedirs(workspace, exist_ok=True)
        os.environ.setdefault("AGENT_PROJECTS_DIR", self.projects_root)
        os.environ["AGENT_PROJECT_ID"] = project_id
        os.environ["AGENT_PROJECT_ROOT"] = workspace

        # 상태 저장
        state.save(workspace)

        print()
        print(_c("  ✓ 프로젝트가 초기화되었습니다!", "1;32"))
        print(f"  경로: {_c(workspace, '90')}")
        print()
        print(f"  {_c('/next', '32')} 로 기획을 시작하거나 {_c('/help', '32')} 로 명령어를 확인하세요.")
        print(_hr("─"))
        print()

        return state

    # ──────────────────────────────────────────────────────────
    # 단계별 입력
    # ──────────────────────────────────────────────────────────

    def _ask_project_name(self) -> str | None:
        print(f"  {_c('1/3', '90')} 어떤 프로젝트를 만들까요?")
        print(f"  {_c('(프로젝트 이름을 입력하세요)', '90')}")
        print()
        while True:
            try:
                raw = input(f"  {_c('>', '33')} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return None
            if raw.lower() in ("exit", "quit", "취소"):
                return None
            if raw:
                print()
                return raw
            print(_c("  이름을 입력해주세요.", "31"))

    def _ask_task_description(self, project_name: str) -> str | None:
        print(f"  {_c('2/3', '90')} '{_c(project_name, '33')}'에서 어떤 걸 만들고 싶으세요?")
        print(f"  {_c('(자유롭게 설명해 주세요)', '90')}")
        print()
        while True:
            try:
                raw = input(f"  {_c('>', '33')} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return None
            if raw.lower() in ("exit", "quit", "취소"):
                return None
            if raw:
                print()
                return raw
            print(_c("  설명을 입력해주세요.", "31"))

    def _ask_level(self) -> ProjectLevel | None:
        print(f"  {_c('3/3', '90')} 프로젝트 규모를 선택하세요:")
        print()
        print(f"    {_c('[1]', '1;32')} {_c('Starter', '32')}    — 간단한 앱 (정적 사이트, 스크립트, 단일 기능)")
        print(f"    {_c('[2]', '1;33')} {_c('Dynamic', '33')}    — 풀스택 (API, DB, 인증 포함)")
        print(f"    {_c('[3]', '1;35')} {_c('Enterprise', '35')} — 마이크로서비스, 멀티 에이전트 오케스트레이션")
        print()

        _level_map = {
            "1": ProjectLevel.STARTER,
            "2": ProjectLevel.DYNAMIC,
            "3": ProjectLevel.ENTERPRISE,
            "starter": ProjectLevel.STARTER,
            "dynamic": ProjectLevel.DYNAMIC,
            "enterprise": ProjectLevel.ENTERPRISE,
        }

        while True:
            try:
                raw = input(f"  {_c('>', '33')} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return None
            if raw in ("exit", "quit", "취소"):
                return None
            if raw in _level_map:
                level = _level_map[raw]
                label = level.value.capitalize()
                print(f"  {_c(f'✓ {label} 선택됨', '32')}")
                print()
                return level
            print(_c("  1, 2, 3 중 하나를 입력해주세요.", "31"))


# ──────────────────────────────────────────────────────────────
# 재개 안내 출력
# ──────────────────────────────────────────────────────────────

def print_resume_banner(state: PDCAState):
    """기존 세션 재개 시 상태를 출력한다."""
    from core.pdca_state import PDCAStateMachine, _PHASE_LABEL_KO
    print()
    print(_hr("═"))
    print(_c("  Agent Factory — 세션 재개", "1;36"))
    print(_hr("═"))
    print()
    print(f"  프로젝트 : {_c(state.project_name or state.project_id, '33')}")
    print(f"  레벨     : {_c(state.level.capitalize(), '33')}")

    phase = state.phase_enum
    label = _PHASE_LABEL_KO.get(phase, phase.value)
    print(f"  현재 단계: {_c(label, '32')} ({state.phase})")
    print()
    print(f"  {_c('/status', '32')} 로 상세 상태 확인 | {_c('/next', '32')} 로 다음 단계 진행")
    print(_hr("─"))
    print()
