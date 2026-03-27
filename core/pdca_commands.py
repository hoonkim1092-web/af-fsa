"""
core/pdca_commands.py
=====================
PDCA 슬래시 커맨드 레지스트리.

/plan    → ProjectPipeline.prepare()
/design  → LLM으로 3가지 아키텍처 옵션 생성
/do      → ApprovalGate.approve() + ProjectPipeline.execute()
/check   → Evaluator agent 실행
/iterate → FSALoop.run_mission()
/report  → 완료 보고서 생성
/status  → 현재 PDCA 상태 표시
/next    → 다음 논리적 단계로 자동 진행
/level   → 프로젝트 레벨 변경
"""
from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

from core.pdca_state import PDCAPhase, PDCAState, PDCAStateMachine, ProjectLevel

if TYPE_CHECKING:
    pass


# ── 색상 유틸 ──
def _c(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _hr(char: str = "─", width: int = 50) -> str:
    return _c(char * width, "36")


def _box_header(title: str):
    print()
    print(_hr("━"))
    print(f"  {_c(title, '1;36')}")
    print(_hr("━"))


def _checkpoint_prompt(options: list[tuple[str, str]]) -> str:
    """체크포인트 선택지를 출력하고 사용자 입력을 받는다."""
    print()
    print(_hr())
    for key, label in options:
        print(f"  {_c(f'[{key}]', '1;33')} {label}")
    print(_hr())
    while True:
        try:
            raw = input(f"  {_c('>', '33')} ").strip()
        except (EOFError, KeyboardInterrupt):
            return "cancel"
        keys = [k for k, _ in options]
        if raw in keys:
            return raw
        # 숫자로도 선택 가능
        for i, (k, _) in enumerate(options, 1):
            if raw == str(i):
                return k
        print(_c(f"  {', '.join(keys)} 중 하나를 선택하세요.", "31"))


# ──────────────────────────────────────────────────────────────
# 커맨드 레지스트리
# ──────────────────────────────────────────────────────────────

class PDCACommandRegistry:
    """PDCA 슬래시 커맨드를 등록하고 디스패치한다."""

    COMMANDS = (
        "/plan", "/design", "/do", "/check",
        "/iterate", "/report", "/status", "/next", "/level",
    )

    def __init__(self, sm: PDCAStateMachine, chat: Any):
        self.sm = sm          # PDCAStateMachine
        self.chat = chat      # PDCAInteractiveChat (순환 임포트 방지 위해 Any)

    @property
    def state(self) -> PDCAState:
        return self.sm.state

    @property
    def workspace(self) -> str:
        return self.sm.workspace

    def dispatch(self, raw_input: str) -> bool:
        """
        슬래시 커맨드면 처리 후 True, 일반 입력이면 False 반환.
        """
        stripped = raw_input.strip()
        if not stripped.startswith("/"):
            return False

        parts = stripped.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/status":  self.cmd_status,
            "/next":    self.cmd_next,
            "/level":   self.cmd_level,
            "/plan":    self.cmd_plan,
            "/design":  self.cmd_design,
            "/do":      self.cmd_do,
            "/check":   self.cmd_check,
            "/iterate": self.cmd_iterate,
            "/report":  self.cmd_report,
        }

        if cmd in handlers:
            try:
                handlers[cmd](args)
            except KeyboardInterrupt:
                print(_c("\n  (중단됨)", "33"))
            return True
        return False

    # ──────────────────────────────────────────────────────────
    # /status
    # ──────────────────────────────────────────────────────────

    def cmd_status(self, _args: str = ""):
        _box_header("[PDCA 현재 상태]")
        for line in self.sm.status_lines():
            print(line)
        nxt = self.sm.next_phase()
        if nxt:
            print(f"  다음 단계 : {_c(nxt.value, '32')} (/next 로 진행)")
        print()

    # ──────────────────────────────────────────────────────────
    # /next
    # ──────────────────────────────────────────────────────────

    def cmd_next(self, _args: str = ""):
        nxt = self.sm.next_phase()
        if nxt is None:
            print(_c("  이미 완료 상태입니다. /plan 으로 새 기획을 시작하세요.", "33"))
            return

        current = self.sm.current
        print(f"  {_c(current.value, '90')} → {_c(nxt.value, '32')}")

        # 자동 실행 가능한 커맨드로 연결
        _auto_cmd = {
            PDCAPhase.IDLE:         lambda: self.cmd_plan(self.state.task_description),
            PDCAPhase.PLAN_REVIEW:  lambda: self.cmd_design(""),
            PDCAPhase.DESIGN_REVIEW:lambda: self.cmd_do(""),
            PDCAPhase.DO_RUNNING:   lambda: self.cmd_check(""),
            PDCAPhase.CHECK_REVIEW: lambda: self.cmd_report(""),
            PDCAPhase.ITERATE:      lambda: self.cmd_check(""),
        }

        if current in _auto_cmd:
            _auto_cmd[current]()
        else:
            if self.sm.can_transition(nxt):
                self.sm.transition(nxt)
                print(f"  {_c(f'✓ {nxt.value} 단계로 이동했습니다.', '32')}")
            else:
                print(_c(f"  '{nxt.value}' 로 직접 이동할 수 없습니다. 현재 단계를 완료하세요.", "31"))

    # ──────────────────────────────────────────────────────────
    # /level
    # ──────────────────────────────────────────────────────────

    def cmd_level(self, args: str = ""):
        _level_map = {
            "1": ProjectLevel.STARTER, "starter": ProjectLevel.STARTER,
            "2": ProjectLevel.DYNAMIC, "dynamic": ProjectLevel.DYNAMIC,
            "3": ProjectLevel.ENTERPRISE, "enterprise": ProjectLevel.ENTERPRISE,
        }
        key = args.strip().lower()

        if key in _level_map:
            level = _level_map[key]
        else:
            print(f"\n  현재 레벨: {_c(self.state.level, '33')}")
            print()
            print(f"    {_c('[1]', '1;32')} Starter    — 간단한 앱")
            print(f"    {_c('[2]', '1;33')} Dynamic    — 풀스택")
            print(f"    {_c('[3]', '1;35')} Enterprise — 마이크로서비스")
            print()
            try:
                raw = input(f"  {_c('>', '33')} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if raw not in _level_map:
                print(_c("  취소됨.", "90"))
                return
            level = _level_map[raw]

        self.state.level = level.value
        self.state.save(self.workspace)
        print(f"  {_c(f'✓ 레벨이 {level.value.capitalize()} 으로 변경되었습니다.', '32')}")

    # ──────────────────────────────────────────────────────────
    # /plan
    # ──────────────────────────────────────────────────────────

    def cmd_plan(self, args: str = ""):
        """ProjectPipeline.prepare() 를 호출해 기획 문서를 생성한다."""
        task = args.strip() or self.state.task_description
        if not task:
            try:
                task = input(f"  기획할 내용을 입력하세요: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if not task:
            return

        self.state.task_description = task
        self.sm.transition(PDCAPhase.PLAN)
        print(f"\n  {_c('기획 문서를 생성 중입니다...', '36')}")

        try:
            result = self._run_plan(task)
        except Exception as e:
            print(_c(f"\n  기획 오류: {e}", "31"))
            self.sm.force_transition(PDCAPhase.IDLE)
            return

        # 결과를 state에 저장
        self.state.plan_brief = result or {}
        self.sm.transition(PDCAPhase.PLAN_REVIEW)
        self._show_plan_review()

    def _run_plan(self, task: str) -> dict:
        """기존 ProjectPipeline.prepare() 를 최소 모드로 호출."""
        try:
            from core.project_pipeline import ProjectPipeline
            from core.model_router import ModelRouter

            pp = ProjectPipeline(workspace=self.workspace, model_router=ModelRouter())
            result = pp.prepare(
                task_input=task,
                workspace=self.workspace,
                execution_mode="approval",
                enable_build=False,
                requested_role=None,
                route={"pipeline": "single"},
            )
            if result:
                brief = {}
                if hasattr(result, "project_brief") and result.project_brief:
                    brief = result.project_brief if isinstance(result.project_brief, dict) else {}
                return brief
        except Exception:
            pass

        # 폴백: LLM으로 직접 생성
        return self._run_plan_via_llm(task)

    def _run_plan_via_llm(self, task: str) -> dict:
        """LLM을 직접 호출해 프로젝트 브리프를 생성한다."""
        prompt = (
            f"다음 프로젝트를 한국어로 기획해주세요:\n\n"
            f"프로젝트: {self.state.project_name}\n"
            f"목표: {task}\n"
            f"레벨: {self.state.level}\n\n"
            f"아래 형식으로 답변하세요:\n"
            f"## 목표\n(1~2문장)\n\n"
            f"## 핵심 기능\n- 기능 1\n- 기능 2\n...\n\n"
            f"## 제약 조건\n- 제약 1\n...\n\n"
            f"## 예상 산출물\n- 산출물 1\n..."
        )
        try:
            response = self.chat.send_message(prompt)
            return {"raw": response, "task": task}
        except Exception:
            return {"task": task}

    def _show_plan_review(self):
        _box_header("[기획 완료] 검토가 필요합니다")

        brief = self.state.plan_brief
        raw = brief.get("raw", "")
        if raw:
            print()
            print(raw)
        else:
            print(f"\n  목표: {brief.get('task', self.state.task_description)}")
            wdir = os.path.join(self.workspace, "docs", "work-items")
            if os.path.exists(wdir):
                for fn in sorted(os.listdir(wdir)):
                    if fn.endswith(".md"):
                        print(f"  📄 {fn}")

        choice = _checkpoint_prompt([
            ("approve", "승인 — 설계 단계로 진행"),
            ("revise",  "수정 요청 — 피드백 입력"),
            ("cancel",  "취소"),
        ])

        if choice == "approve":
            self.sm.transition(PDCAPhase.DESIGN)
            print(f"\n  {_c('✓ 승인되었습니다. /design 으로 설계를 시작하세요.', '32')}")
        elif choice == "revise":
            try:
                feedback = input(f"\n  피드백을 입력하세요: ").strip()
            except (EOFError, KeyboardInterrupt):
                feedback = ""
            if feedback:
                self.sm.force_transition(PDCAPhase.PLAN)
                self.cmd_plan(self.state.task_description + " [수정사항: " + feedback + "]")
        else:
            self.sm.force_transition(PDCAPhase.IDLE)
            print(_c("  기획이 취소되었습니다.", "33"))

    # ──────────────────────────────────────────────────────────
    # /design
    # ──────────────────────────────────────────────────────────

    def cmd_design(self, args: str = ""):
        """LLM으로 3가지 아키텍처 옵션을 생성한다."""
        if self.sm.current not in (PDCAPhase.PLAN_REVIEW, PDCAPhase.DESIGN, PDCAPhase.DESIGN_REVIEW):
            if self.sm.current == PDCAPhase.IDLE:
                print(_c("  먼저 /plan 으로 기획을 완료하세요.", "31"))
                return
            # 허용되지 않는 단계면 강제 진행
            if not self.sm.can_transition(PDCAPhase.DESIGN):
                print(_c(f"  현재 단계({self.sm.current.value})에서 설계를 시작할 수 없습니다.", "31"))
                return

        self.sm.force_transition(PDCAPhase.DESIGN)
        print(f"\n  {_c('3가지 아키텍처 옵션을 생성 중입니다...', '36')}")

        try:
            options = self._generate_design_options()
        except Exception as e:
            print(_c(f"\n  설계 오류: {e}", "31"))
            return

        self.state.design_options = options
        self.state.save(self.workspace)
        self.sm.force_transition(PDCAPhase.DESIGN_REVIEW)
        self._show_design_review(options)

    def _generate_design_options(self) -> list[dict]:
        level = self.state.level
        task = self.state.task_description
        project = self.state.project_name

        if level == ProjectLevel.STARTER.value:
            arch_hint = "프론트엔드 전용, 정적 사이트 또는 단일 Python 스크립트 기반"
        elif level == ProjectLevel.DYNAMIC.value:
            arch_hint = "풀스택: REST API(백엔드) + 프론트엔드 + DB(PostgreSQL/SQLite)"
        else:
            arch_hint = "마이크로서비스: API Gateway + 서비스 분리 + 메시지 큐 + K8s"

        prompt = (
            f"프로젝트: {project}\n목표: {task}\n아키텍처 방향: {arch_hint}\n\n"
            f"아래 3가지 아키텍처 옵션을 한국어로 제안해주세요.\n"
            f"각 옵션은 다음 형식으로 작성하세요:\n\n"
            f"### [옵션 1] Minimal — 단순 구조, 빠른 구현\n"
            f"- 특징: ...\n- 기술 스택: ...\n- 장점: ...\n- 단점: ...\n\n"
            f"### [옵션 2] Clean — 레이어드 아키텍처, 확장성\n"
            f"- 특징: ...\n\n"
            f"### [옵션 3] Pragmatic — 실용적 균형, 성능 최적화\n"
            f"- 특징: ..."
        )

        response = self.chat.send_message(prompt)

        # 파싱 (간단히 섹션 분리)
        options = []
        names = ["Minimal", "Clean", "Pragmatic"]
        descs = ["단순 구조, 빠른 구현", "레이어드 아키텍처, 확장성", "실용적 균형, 성능 최적화"]
        sections = response.split("### [옵션") if "### [옵션" in response else [""] * 3

        for i in range(3):
            content = sections[i + 1].strip() if i + 1 < len(sections) else ""
            options.append({
                "index": i,
                "name": names[i],
                "description": descs[i],
                "content": content,
                "raw": content,
            })

        return options

    def _show_design_review(self, options: list[dict]):
        _box_header("[설계 완료] 아키텍처 옵션을 선택하세요")

        for i, opt in enumerate(options, 1):
            print()
            opt_name = opt.get("name", "")
            print(f"  {_c(f'[옵션 {i}] {opt_name}', '1;33')} — {opt['description']}")
            content = opt.get("content", "")
            if content:
                # 처음 200자만 미리보기
                preview = content[:300].replace("\n", "\n  ")
                print(f"  {_c(preview, '90')}")

        choice = _checkpoint_prompt([
            ("1", "옵션 1 선택 (Minimal)"),
            ("2", "옵션 2 선택 (Clean)"),
            ("3", "옵션 3 선택 (Pragmatic)"),
            ("revise", "재설계 요청"),
            ("cancel", "취소"),
        ])

        if choice in ("1", "2", "3"):
            idx = int(choice) - 1
            self.state.selected_design = idx
            self.state.save(self.workspace)
            self.sm.transition(PDCAPhase.DO)
            opt_name = options[idx]["name"]
            print(f"\n  {_c(f'✓ [{idx+1}] {opt_name} 선택됨. /do 로 구현을 시작하세요.', '32')}")
        elif choice == "revise":
            self.sm.force_transition(PDCAPhase.DESIGN)
            self.cmd_design("")
        else:
            self.sm.force_transition(PDCAPhase.PLAN_REVIEW)
            print(_c("  설계가 취소되었습니다. /plan 으로 돌아갑니다.", "33"))

    # ──────────────────────────────────────────────────────────
    # /do
    # ──────────────────────────────────────────────────────────

    def cmd_do(self, args: str = ""):
        """승인 후 구현을 실행한다."""
        if self.sm.current not in (PDCAPhase.DO, PDCAPhase.DESIGN_REVIEW):
            if not self.sm.can_transition(PDCAPhase.DO):
                print(_c(f"  현재 단계({self.sm.current.value})에서 구현을 시작할 수 없습니다.", "31"))
                return

        # 선택된 설계 요약 표시
        _box_header("[구현 시작] 구현 범위를 확인하세요")
        print(f"\n  프로젝트 : {self.state.project_name}")
        print(f"  목표     : {self.state.task_description}")
        if self.state.selected_design >= 0 and self.state.design_options:
            opt = self.state.design_options[self.state.selected_design]
            print(f"  아키텍처 : [{self.state.selected_design + 1}] {opt.get('name', '')}")
        print(f"  레벨     : {self.state.level}")

        choice = _checkpoint_prompt([
            ("approve", "승인 — 구현 시작"),
            ("cancel",  "취소"),
        ])

        if choice != "approve":
            print(_c("  구현이 취소되었습니다.", "33"))
            return

        self.sm.force_transition(PDCAPhase.DO_RUNNING)
        print(f"\n  {_c('구현을 시작합니다...', '36')}")

        try:
            self._run_do()
        except KeyboardInterrupt:
            print(_c("\n  구현이 중단되었습니다.", "33"))
            self.sm.force_transition(PDCAPhase.DO)
            return
        except Exception as e:
            print(_c(f"\n  구현 오류: {e}", "31"))
            self.sm.force_transition(PDCAPhase.DO)
            return

        self.sm.transition(PDCAPhase.CHECK)
        print(f"\n  {_c('✓ 구현 완료. /check 로 검증을 진행하세요.', '32')}")

    def _run_do(self):
        """구현을 실행한다. Dynamic/Enterprise 레벨에서는 교차검증 루프를 사용한다."""
        task = self.state.task_description

        # 선택된 설계 옵션을 태스크에 포함
        if self.state.selected_design >= 0 and self.state.design_options:
            opt = self.state.design_options[self.state.selected_design]
            content = opt.get("content", "")
            if content:
                task = f"{task}\n\n[선택된 아키텍처]\n{content}"

        level = self.state.level

        # ── 교차검증 루프 (Dynamic/Enterprise) ──
        if level in (ProjectLevel.DYNAMIC.value, ProjectLevel.ENTERPRISE.value):
            judgment = self._run_do_with_verification(task, level)
            # 검증 이력 저장
            if hasattr(self.state, 'verification_history'):
                self.state.verification_history.append({
                    "phase": "do",
                    "verdict": judgment.verdict,
                    "confidence": judgment.confidence,
                    "provider": judgment.selected_provider,
                    "round": judgment.round_num,
                })
                self.state.save(self.workspace)
            # PASS/PARTIAL → 결과 출력 후 종료
            if judgment.verdict in ("pass", "partial") and judgment.merged_output:
                print(f"\n  {_c('[교차검증 결과]', '1;36')}")
                print(f"  판정: {_c(judgment.verdict.upper(), '32')}")
                print(f"  신뢰도: {judgment.confidence:.0%}")
                if judgment.feedback:
                    print(f"\n  {judgment.feedback[:400]}")
            return

        # ── Starter: 기존 AgentFactory 단일 실행 ──
        pipeline_mode = "single"
        from agent_launcher import AgentFactory
        factory = AgentFactory()
        factory.run(
            task_input=task,
            role_spec="General Assistant",
            enable_build=False,
            execution_mode="approval",
            pipeline_mode=pipeline_mode,
        )

    def _run_do_with_verification(self, task: str, level: str):
        """교차검증 루프로 구현 태스크를 실행한다."""
        from core.cross_verification import CrossVerificationLoop

        print(f"\n  {_c('교차검증 루프를 시작합니다...', '36')}")

        loop = CrossVerificationLoop(
            workspace=self.workspace,
            level=level,
        )
        return loop.run(
            task=task,
            system_prompt="당신은 시니어 풀스택 개발자입니다. 주어진 태스크를 완전히 구현하세요.",
        )

    # ──────────────────────────────────────────────────────────
    # /check
    # ──────────────────────────────────────────────────────────

    def cmd_check(self, args: str = ""):
        """갭 분석을 실행한다."""
        if self.sm.current not in (PDCAPhase.CHECK, PDCAPhase.DO_RUNNING, PDCAPhase.ITERATE):
            if not self.sm.can_transition(PDCAPhase.CHECK):
                print(_c(f"  현재 단계({self.sm.current.value})에서 검증을 실행할 수 없습니다.", "31"))
                return

        self.sm.force_transition(PDCAPhase.CHECK)
        print(f"\n  {_c('갭 분석을 실행 중입니다...', '36')}")

        prompt = (
            f"프로젝트: {self.state.project_name}\n"
            f"목표: {self.state.task_description}\n\n"
            f"현재 workspace를 분석하고 다음을 평가해주세요:\n"
            f"1. 목표 달성도 (0~100%)\n"
            f"2. 누락된 기능 목록\n"
            f"3. 개선 필요 사항\n"
            f"4. 권장 다음 액션\n\n"
            f"한국어로 답변해주세요."
        )

        try:
            response = self.chat.send_message(prompt)
            self.state.gap_analysis = {"raw": response}
            self.state.save(self.workspace)
        except Exception as e:
            print(_c(f"\n  검증 오류: {e}", "31"))
            self.sm.force_transition(PDCAPhase.DO_RUNNING)
            return

        self.sm.transition(PDCAPhase.CHECK_REVIEW)
        self._show_check_review(response)

    def _show_check_review(self, analysis: str):
        _box_header("[검증 결과] 검토가 필요합니다")
        print()
        print(analysis)

        choice = _checkpoint_prompt([
            ("complete", "완료 — 프로젝트 종료"),
            ("iterate",  "자동 수정 — AI가 갭을 자동으로 수정"),
            ("manual",   "수동 수정 — 직접 수정 후 재검증"),
            ("cancel",   "취소"),
        ])

        if choice == "complete":
            self.sm.transition(PDCAPhase.COMPLETE)
            print(f"\n  {_c('✓ 프로젝트가 완료되었습니다! /report 로 보고서를 생성하세요.', '32')}")
        elif choice == "iterate":
            self.sm.transition(PDCAPhase.ITERATE)
            self.cmd_iterate("")
        elif choice == "manual":
            self.sm.force_transition(PDCAPhase.DO)
            print(_c("  수정 후 /check 로 재검증하세요.", "33"))
        else:
            print(_c("  취소됨.", "90"))

    # ──────────────────────────────────────────────────────────
    # /iterate
    # ──────────────────────────────────────────────────────────

    def cmd_iterate(self, args: str = ""):
        """FSALoop으로 갭을 자동 수정한다."""
        if self.sm.current not in (PDCAPhase.ITERATE, PDCAPhase.CHECK_REVIEW):
            if not self.sm.can_transition(PDCAPhase.ITERATE):
                print(_c(f"  현재 단계에서 자동 수정을 실행할 수 없습니다.", "31"))
                return

        self.sm.force_transition(PDCAPhase.ITERATE)
        count = self.state.iterate_count
        max_iter = self.state.max_iterate

        if count >= max_iter:
            print(_c(f"  최대 반복 횟수({max_iter})에 도달했습니다. 수동 검토가 필요합니다.", "33"))
            self.sm.force_transition(PDCAPhase.CHECK_REVIEW)
            return

        print(f"\n  {_c(f'자동 수정 중... ({count + 1}/{max_iter})', '36')}")

        gap_raw = self.state.gap_analysis.get("raw", "")
        task = f"{self.state.task_description}\n\n[갭 분석 결과 기반 수정]\n{gap_raw}"
        level = self.state.level

        ok = False
        try:
            # Dynamic/Enterprise: 교차검증 루프로 자동 수정
            if level in (ProjectLevel.DYNAMIC.value, ProjectLevel.ENTERPRISE.value):
                from core.cross_verification import CrossVerificationLoop

                loop = CrossVerificationLoop(workspace=self.workspace, level=level)
                judgment = loop.run(
                    task=task,
                    system_prompt=(
                        "당신은 코드 수정 전문가입니다. "
                        "갭 분석 결과를 바탕으로 누락된 기능을 구현하고 버그를 수정하세요."
                    ),
                )
                ok = judgment.verdict in ("pass", "partial")
                # 진화 이력 저장
                if hasattr(self.state, 'verification_history'):
                    self.state.verification_history.append({
                        "phase": "iterate",
                        "iterate_count": count,
                        "verdict": judgment.verdict,
                        "confidence": judgment.confidence,
                        "patterns": judgment.failure_patterns,
                    })
                if ok:
                    print(f"\n  {_c(f'교차검증 판정: {judgment.verdict.upper()} '
                                   f'(신뢰도 {judgment.confidence:.0%})', '32')}")
                else:
                    print(_c(f"\n  교차검증 판정: {judgment.verdict.upper()}", "33"))
                    if judgment.feedback:
                        print(f"  {judgment.feedback[:300]}")
            else:
                # Starter: FSALoop 단일 실행
                from core.fsa_loop import FSALoop
                from core.agent_runner import AgentRunner
                from core.model_router import ModelRouter
                from core.manager import AgentManager

                mr = ModelRouter()
                runner = AgentRunner(mr)
                manager = AgentManager(workspace=self.workspace)
                agent = manager.get_or_create("General Assistant", workspace=self.workspace)

                fsa = FSALoop(runner=runner)
                fsa.max_cycles = 2
                result = fsa.run_mission(
                    agent=agent,
                    task_input=task,
                    run_id=f"{self.state.project_id}_iterate_{count + 1}",
                    workspace=self.workspace,
                )
                ok = result.get("ok", False) if isinstance(result, dict) else bool(result)
        except Exception as e:
            print(_c(f"\n  자동 수정 오류: {e}", "31"))
            ok = False

        self.state.iterate_count += 1
        self.state.save(self.workspace)

        if ok:
            self.sm.transition(PDCAPhase.CHECK)
            print(f"\n  {_c('✓ 수정 완료. /check 로 재검증하세요.', '32')}")
            self.cmd_check("")
        else:
            print(_c(f"\n  수정 불완전. 재시도하려면 /iterate, 수동 수정은 /do.", "33"))
            self.sm.force_transition(PDCAPhase.CHECK_REVIEW)

    # ──────────────────────────────────────────────────────────
    # /report
    # ──────────────────────────────────────────────────────────

    def cmd_report(self, _args: str = ""):
        """완료 보고서를 생성하고 출력한다."""
        _box_header("[완료 보고서]")

        runs_dir = os.path.join(self.workspace, "runs")
        artifacts_dir = os.path.join(self.workspace, "artifacts")

        run_count = 0
        if os.path.exists(runs_dir):
            run_count = len([d for d in os.listdir(runs_dir) if os.path.isdir(os.path.join(runs_dir, d))])

        artifact_count = 0
        if os.path.exists(artifacts_dir):
            artifact_count = sum(
                len(files) for _, _, files in os.walk(artifacts_dir)
            )

        print(f"\n  프로젝트 : {self.state.project_name}")
        print(f"  목표     : {self.state.task_description}")
        print(f"  레벨     : {self.state.level}")
        print(f"  반복     : {self.state.iterate_count}회")
        print(f"  실행 기록: {run_count}개")
        print(f"  산출물   : {artifact_count}개")

        if self.state.design_options and self.state.selected_design >= 0:
            opt = self.state.design_options[self.state.selected_design]
            print(f"  아키텍처 : {opt.get('name', '')}")

        gap_raw = self.state.gap_analysis.get("raw", "")
        if gap_raw:
            print(f"\n  {_c('[최종 검증 결과]', '36')}")
            print(f"  {gap_raw[:500]}")

        # 보고서 파일로 저장
        from core.utils import now_iso, _safe_write_json
        report = {
            "project_id": self.state.project_id,
            "project_name": self.state.project_name,
            "task": self.state.task_description,
            "level": self.state.level,
            "iterate_count": self.state.iterate_count,
            "run_count": run_count,
            "artifact_count": artifact_count,
            "gap_analysis": self.state.gap_analysis,
            "created_at": now_iso(),
        }
        report_path = os.path.join(self.workspace, "artifacts", "pdca_report.json")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        _safe_write_json(report_path, report)
        print(f"\n  {_c(f'✓ 보고서 저장: {report_path}', '90')}")
        print()

        if self.sm.current != PDCAPhase.COMPLETE:
            self.sm.force_transition(PDCAPhase.COMPLETE)
