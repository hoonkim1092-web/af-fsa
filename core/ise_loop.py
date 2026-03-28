"""
core/ise_loop.py
================
Infinite Self-Evolution Loop (ISE Loop) -- 무한 자기진화 루프.

FSA Loop을 감싸는 메타 루프로, 태스크가 완료될 때까지
실행 → 분석 → 재설계 → 진화 → 실행 사이클을 무한 반복한다.

에스컬레이션 레벨:
  Level 1: 단순 재시도 (FSA 내부 피드백 주입)
  Level 2: 전략 피벗 (접근법 변경)
  Level 3: 설계 재시작 (아키텍처 재구성)
  Level 4: 스킬 진화 + 설계 재시작
  Level 5: 태스크 분해 (서브태스크 분할 → 각각 ISE 실행)

탈출 조건:
  - 성공 (ok=True)
  - 지능형 정체 감지 → 사용자 에스컬레이션 (abort가 아닌 일시정지)
  - KeyboardInterrupt
"""
from __future__ import annotations

import os
import time

from core.fsa_loop import FSALoop
from core.agent_runner import AgentRunner
from core.ise_strategy_ledger import StrategyLedger
from core.ise_analyzer import ISEAnalyzer, ISEAnalysis
from core.ise_redesigner import ISERedesigner
from core.ise_stall_detector import StallDetector
from core.utils import now_iso, print_agent_msg


class ISELoop:
    """
    무한 자기진화 메타 루프.
    기존 FSALoop을 inner loop으로 감싸 에스컬레이션 + 학습 + 재설계를 수행한다.
    """

    def __init__(
        self,
        fsa_loop: FSALoop,
        runner: AgentRunner,
        agent_mgr=None,
        visualizer=None,
    ):
        self.fsa = fsa_loop
        self.runner = runner
        self.agent_mgr = agent_mgr
        self._visualizer = visualizer

        # 분석/재설계/정체감지 엔진
        model_name = "gemini-1.5-pro-latest"
        if hasattr(runner, "mr") and hasattr(runner.mr, "pick"):
            model_name = runner.mr.pick("evaluator") or model_name
        self.analyzer = ISEAnalyzer(model_name=model_name)
        self.redesigner = ISERedesigner(model_name=model_name)
        self.stall_detector = StallDetector()

    def run_mission(
        self,
        agent: dict,
        task_input: str,
        run_id: str,
        workspace: str | None = None,
    ) -> dict:
        """
        ISE 메타 루프 실행.

        Returns:
            {
                "ok": bool,
                "reason": str,
                "meta_cycles": int,
                "max_escalation_level": int,
                "strategy_ledger": dict,
            }
        """
        target_workspace = workspace or os.getcwd()
        ledger = StrategyLedger(run_id=run_id, original_task=task_input)
        meta_cycle = 0
        current_task = task_input
        current_agent = agent
        max_level_reached = 0

        print_agent_msg("ISE", "무한 자기진화 루프(ISE) 시작", "♾️")
        print_agent_msg("ISE", f"run_id={run_id}", "📋")

        try:
            while True:
                meta_cycle += 1
                print_agent_msg(
                    "ISE",
                    f"메타 사이클 {meta_cycle} 시작 (최대 에스컬레이션: Level {max_level_reached})",
                    "🔄",
                )

                # ── 정체 감지 ──
                stall_action = self.stall_detector.check(ledger)
                if stall_action == "human_escalation":
                    human_result = self._request_human_help(ledger, meta_cycle)
                    if human_result.get("continue"):
                        hint = human_result.get("hint", "")
                        if hint:
                            current_task = (
                                f"[사용자 힌트]\n{hint}\n\n"
                                f"[Original Task]\n{task_input}"
                            )
                        ledger.reset_escalation_counters()
                        continue
                    else:
                        # 사용자가 명시적으로 중단 선택
                        ledger.save(target_workspace)
                        return {
                            "ok": False,
                            "reason": human_result.get("reason", "사용자 중단"),
                            "meta_cycles": meta_cycle,
                            "max_escalation_level": max_level_reached,
                            "strategy_ledger": ledger.to_dict(),
                        }
                elif stall_action == "creativity_injection":
                    current_task = self.redesigner.inject_creativity(current_task, ledger)

                # ── Phase 1: EXECUTE (FSA inner loop) ──
                result = self.fsa.run_mission(
                    current_agent,
                    current_task,
                    run_id=f"{run_id}_ise{meta_cycle}",
                    workspace=target_workspace,
                )

                if result.get("ok"):
                    print_agent_msg("ISE", f"메타 사이클 {meta_cycle}에서 성공!", "✅")
                    ledger.save(target_workspace)
                    return {
                        "ok": True,
                        "reason": result.get("reason", "ISE 루프 성공"),
                        "meta_cycles": meta_cycle,
                        "max_escalation_level": max_level_reached,
                        "strategy_ledger": ledger.to_dict(),
                    }

                # ── Phase 2: ANALYZE ──
                print_agent_msg("ISE", "실패 분석 중...", "🔍")
                analysis = self.analyzer.analyze_failure(
                    task=current_task,
                    result=result,
                    ledger=ledger,
                )

                # ── Phase 3: ESCALATE — 에스컬레이션 레벨 결정 ──
                level = self._decide_escalation(ledger, analysis)
                max_level_reached = max(max_level_reached, level)

                # 원장에 시도 기록
                ledger.record_attempt(
                    meta_cycle=meta_cycle,
                    task_input=current_task,
                    result=result,
                    analysis=analysis.to_dict(),
                    escalation_level=level,
                    strategy_description=analysis.suggested_strategy,
                )

                print_agent_msg(
                    "ISE",
                    f"에스컬레이션 Level {level} | "
                    f"에러: {analysis.error_category} | "
                    f"근본적: {analysis.is_fundamental}",
                    "📊",
                )

                # ── Phase 4: Act — 레벨별 대응 ──
                if level == 1:
                    current_task = self.redesigner.apply_retry_feedback(task_input, analysis)

                elif level == 2:
                    current_task = self.redesigner.apply_pivot(task_input, analysis, ledger)

                elif level == 3:
                    print_agent_msg("ISE", "설계 재시작: 아키텍처를 전면 재구성합니다", "🏗️")
                    current_task = self.redesigner.redesign_task(task_input, analysis, ledger)

                elif level == 4:
                    print_agent_msg("ISE", "스킬 진화 + 설계 재시작", "🧬")
                    self._evolve_skills(analysis, ledger, run_id, meta_cycle)
                    current_task = self.redesigner.redesign_task(task_input, analysis, ledger)

                elif level == 5:
                    print_agent_msg("ISE", "태스크 분해: 서브태스크로 분할 실행합니다", "🔀")
                    sub_results = self._decompose_and_execute(
                        task_input, current_agent, analysis, ledger,
                        run_id, meta_cycle, target_workspace,
                    )
                    if sub_results and all(r.get("ok") for r in sub_results):
                        print_agent_msg("ISE", "모든 서브태스크 성공!", "✅")
                        ledger.save(target_workspace)
                        return {
                            "ok": True,
                            "reason": "ISE 태스크 분해 후 전체 성공",
                            "meta_cycles": meta_cycle,
                            "max_escalation_level": 5,
                            "strategy_ledger": ledger.to_dict(),
                        }
                    # 분해 실패: 카운터 리셋 후 Level 2부터 재시도
                    print_agent_msg("ISE", "서브태스크 일부 실패, 카운터 리셋 후 재시도", "🔁")
                    ledger.reset_escalation_counters()
                    current_task = self.redesigner.apply_pivot(task_input, analysis, ledger)

                # ── Phase 5: 원장 영속화 ──
                ledger.save(target_workspace)

                # ── 지수 백오프 (같은 레벨 반복 시) ──
                backoff = self.stall_detector.compute_backoff(ledger, level)
                if backoff > 0:
                    print_agent_msg("ISE", f"백오프 대기: {backoff:.1f}초", "⏳")
                    time.sleep(backoff)

        except KeyboardInterrupt:
            print_agent_msg("ISE", "사용자 인터럽트 — 루프 중단", "⛔")
            ledger.save(target_workspace)
            return {
                "ok": False,
                "reason": "KeyboardInterrupt",
                "meta_cycles": meta_cycle,
                "max_escalation_level": max_level_reached,
                "strategy_ledger": ledger.to_dict(),
            }

    # ── 에스컬레이션 결정 ──

    def _decide_escalation(self, ledger: StrategyLedger, analysis: ISEAnalysis) -> int:
        """
        전략 원장과 분석 결과를 바탕으로 에스컬레이션 레벨을 결정한다.

        Level 1: 첫 실패 또는 일시적 에러
        Level 2: 같은 에러 반복 2회+ (전략 피벗 필요)
        Level 3: 피벗 3회+ 실패 or abort 판정 or 근본적 결함
        Level 4: 설계 재시작 실패 + 스킬 결함 식별
        Level 5: Level 4 실패 (태스크 분해)
        """
        repeat_count = ledger.consecutive_same_error_count()
        pivot_count = ledger.pivot_count()
        redesign_count = ledger.redesign_count()

        # 근본적 결함이면 바로 Level 3 이상으로 에스컬레이션
        if analysis.is_fundamental:
            if redesign_count == 0:
                return 3
            elif ledger.has_skill_failure():
                return 4
            else:
                return 5

        # abort 판정이면 설계 재시작
        if analysis.evaluator_action == "abort":
            if redesign_count == 0:
                return 3
            elif ledger.has_skill_failure():
                return 4
            else:
                return 5

        # 점진적 에스컬레이션
        if repeat_count == 0:
            return 1
        elif repeat_count <= 2 and pivot_count < 3:
            return 2
        elif pivot_count >= 3 and redesign_count == 0:
            return 3
        elif redesign_count >= 1 and ledger.has_skill_failure():
            return 4
        elif redesign_count >= 2 or ledger.decompose_count() == 0:
            return 5
        else:
            # 모든 레벨 순회 후에도 실패: Level 2로 리셋 (새 피벗 시도)
            return 2

    # ── 스킬 진화 ──

    def _evolve_skills(
        self,
        analysis: ISEAnalysis,
        ledger: StrategyLedger,
        run_id: str,
        meta_cycle: int,
    ) -> list[str]:
        """실패한 스킬을 자동 진화시킨다."""
        evolved = []
        failed_skills = analysis.failed_skills
        if not failed_skills:
            return evolved

        try:
            from core.skill_creator import evolve_skill
            from core.skill_evolution_bus import SkillEvolutionBus
            from core.config_paths import SKILLS_DIR, PROJECT_SKILLS_DIR

            search_dirs = []
            if PROJECT_SKILLS_DIR and os.path.isdir(PROJECT_SKILLS_DIR):
                search_dirs.append(PROJECT_SKILLS_DIR)
            if os.path.isdir(SKILLS_DIR):
                search_dirs.append(SKILLS_DIR)

            coding_engine = None
            if hasattr(self.runner, "mr") and hasattr(self.runner.mr, "pick"):
                coding_engine = self.runner.mr.pick("coding")

            for skill_name in failed_skills:
                skill_dir = self._find_skill_dir(skill_name, search_dirs)
                if not skill_dir:
                    continue

                print_agent_msg("ISE", f"스킬 진화 시도: {skill_name}", "🧬")
                success = evolve_skill(
                    skill_dir=skill_dir,
                    feedback=analysis.evaluator_reasoning,
                    error_log=analysis.root_cause,
                    coding_engine=coding_engine,
                )
                if success:
                    evolved.append(skill_name)
                    # EvolutionBus 캐시 무효화
                    try:
                        evo_bus = SkillEvolutionBus.get_instance()
                        evo_bus.bind_runner(self.runner)
                        evo_bus.on_skill_evolved(
                            skill_id=skill_name,
                            skill_dir=skill_dir,
                            old_version="unknown",
                            new_version="evolved",
                            trigger="ise_failure",
                        )
                    except Exception:
                        pass
                    print_agent_msg("ISE", f"스킬 진화 성공: {skill_name}", "✅")
                else:
                    print_agent_msg("ISE", f"스킬 진화 실패: {skill_name}", "⚠️")

        except ImportError as e:
            print_agent_msg("ISE", f"스킬 진화 모듈 임포트 실패: {e}", "⚠️")

        return evolved

    def _find_skill_dir(self, skill_name: str, search_dirs: list[str]) -> str | None:
        """스킬 이름으로 디렉토리를 찾는다."""
        for base_dir in search_dirs:
            candidate = os.path.join(base_dir, skill_name)
            if os.path.isdir(candidate):
                return candidate
            # 하이픈/언더스코어 변환
            alt = skill_name.replace("-", "_")
            candidate2 = os.path.join(base_dir, alt)
            if os.path.isdir(candidate2):
                return candidate2
        return None

    # ── 태스크 분해 + 실행 ──

    def _decompose_and_execute(
        self,
        original_task: str,
        agent: dict,
        analysis: ISEAnalysis,
        ledger: StrategyLedger,
        run_id: str,
        meta_cycle: int,
        workspace: str,
    ) -> list[dict]:
        """태스크를 분해하고 각 서브태스크를 FSA로 실행한다."""
        subtasks = self.redesigner.decompose_task(original_task, analysis, ledger)
        if not subtasks:
            return [{"ok": False, "reason": "태스크 분해 실패"}]

        print_agent_msg("ISE", f"{len(subtasks)}개 서브태스크로 분해됨", "📋")
        results = []
        completed_indices: set[int] = set()

        # 의존 관계 순서대로 실행
        for priority_pass in range(1, len(subtasks) + 1):
            for i, st in enumerate(subtasks):
                if i in completed_indices:
                    continue
                # 의존 관계 확인
                deps = st.get("dependencies", [])
                if not all(d in completed_indices for d in deps):
                    continue

                sub_task = st["subtask"]
                sub_run_id = f"{run_id}_ise{meta_cycle}_sub{i}"

                print_agent_msg("ISE", f"서브태스크 {i+1}/{len(subtasks)}: {sub_task[:80]}...", "▶️")

                # 서브태스크는 FSA(inner loop)로 실행
                sub_result = self.fsa.run_mission(
                    agent, sub_task,
                    run_id=sub_run_id,
                    workspace=workspace,
                )
                results.append(sub_result)

                if sub_result.get("ok"):
                    completed_indices.add(i)
                    print_agent_msg("ISE", f"서브태스크 {i+1} 완료", "✅")
                else:
                    print_agent_msg("ISE", f"서브태스크 {i+1} 실패: {sub_result.get('reason', '')[:100]}", "❌")
                    return results  # 서브태스크 실패 시 조기 종료

            # 더 이상 실행 가능한 서브태스크가 없으면 종료
            if len(completed_indices) == len(subtasks):
                break

        return results

    # ── 사용자 에스컬레이션 ──

    def _request_human_help(self, ledger: StrategyLedger, meta_cycle: int) -> dict:
        """
        사용자에게 도움을 요청한다. abort가 아닌 일시정지.
        """
        print("\n" + "=" * 60)
        print("  [ISE] 자기진화 루프 정체 감지 — 사용자 도움 요청")
        print("=" * 60)
        print(f"  메타 사이클: {meta_cycle}")
        print(f"  총 시도: {len(ledger.entries)}회")
        print(f"  경과 시간: {ledger.total_elapsed_sec():.0f}초")

        top_errors = ledger.top_error_signatures(3)
        if top_errors:
            print(f"\n  반복 에러 패턴:")
            for sig, count in top_errors:
                print(f"    - [{count}회] {sig[:80]}")

        failed_descs = ledger.failed_strategy_descriptions(5)
        if failed_descs:
            print(f"\n  실패한 접근법:")
            for desc in failed_descs:
                print(f"    - {desc}")

        print(f"\n  레벨별 시도 횟수: {ledger.level_counts()}")
        print()
        print("  [1] 힌트를 제공하고 계속 (hint)")
        print("  [2] 파일을 수동 수정 후 계속 (manual)")
        print("  [3] 완전 중단 (abort)")
        print()

        try:
            choice = input("  선택 [1/2/3]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return {"ok": False, "reason": "ISE 루프: 입력 불가 — 중단", "continue": False}

        if choice in ("1", "hint"):
            try:
                hint = input("  힌트 입력: ").strip()
            except (EOFError, KeyboardInterrupt):
                return {"ok": False, "reason": "ISE 루프: 입력 불가 — 중단", "continue": False}
            ledger.record_human_hint(hint)
            print_agent_msg("ISE", f"사용자 힌트 반영: {hint[:80]}", "💡")
            return {"ok": False, "reason": "human_hint_provided", "hint": hint, "continue": True}
        elif choice in ("2", "manual"):
            try:
                input("  수정 후 Enter를 누르세요...")
            except (EOFError, KeyboardInterrupt):
                pass
            print_agent_msg("ISE", "수동 수정 반영, 루프 계속", "🔧")
            return {"ok": False, "reason": "manual_edit", "continue": True}
        else:
            return {"ok": False, "reason": "ISE 루프: 사용자 중단", "continue": False}
