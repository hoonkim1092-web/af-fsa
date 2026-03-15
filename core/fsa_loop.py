import os
import json
import time
from core.agent_runner import AgentRunner
from core.git_manager import GitManager
from core.utils import now_iso, print_agent_msg, safe_json_load
from core.evaluator import StrategyEvaluator
from core.config_paths import SKILLS_DIR, PROJECT_SKILLS_DIR


def parse_evaluator_response(result: dict) -> dict:
    """Parse an evaluator agent's run result into action/reasoning/new_instruction."""
    if not result.get("ok"):
        return {"action": "abort", "reasoning": f"Evaluator agent failed: {result.get('reason', '')}", "new_instruction": ""}

    # Try to extract JSON from the agent's output
    output = result.get("output", "") or result.get("reason", "")
    if isinstance(output, dict):
        return {
            "action": str(output.get("action", "abort")).strip().lower(),
            "reasoning": str(output.get("reasoning", "")),
            "new_instruction": str(output.get("new_instruction", "")),
        }

    # Try JSON extraction from text
    data = safe_json_load(output) if isinstance(output, str) else {}
    if data and "action" in data:
        return {
            "action": str(data.get("action", "abort")).strip().lower(),
            "reasoning": str(data.get("reasoning", "")),
            "new_instruction": str(data.get("new_instruction", "")),
        }

    return {"action": "abort", "reasoning": "Could not parse evaluator response", "new_instruction": ""}


class FSALoop:
    """
    (V23) Full Self Automation (FSA) Loop Orchestrator with Git Safety.
    Implements EXECUTE -> TRACE -> EVAL -> SUMMARIZE -> DATASETS -> REFLECT cycle.
    """
    def __init__(self, runner: AgentRunner, agent_mgr=None):
        self.runner = runner
        self.git = GitManager()
        self.agent_mgr = agent_mgr
        # Fallback evaluator (used when agent_mgr is unavailable or evaluator agent fails)
        self.evaluator = StrategyEvaluator(
            model_name=runner.mr.pick('evaluator') if hasattr(runner.mr, 'pick') else 'gemini-1.5-pro-latest'
        )
        self.max_cycles = 5

    def run_mission(self, agent: dict, task_input: str, run_id: str, workspace: str | None = None):
        print(f"\n🌀 [FSALoop] 풀 셀프 자동화 모드(FSA) 시작: {run_id}")

        current_task = task_input
        for cycle in range(1, self.max_cycles + 1):
            print(f"\n🔄 [Cycle {cycle}/{self.max_cycles}] 실행 및 자동 커밋 준비...")

            # ── Step 1: Pre-Commit for safety ──
            commit_msg = f"AEE Auto-Save: {run_id} Cycle {cycle}"
            self.git.commit(commit_msg)

            # ── Step 2: EXECUTE ──
            result = self.runner.run(
                agent,
                current_task,
                run_id=f"{run_id}_c{cycle}",
                auto_approve=True,
                workspace=workspace,
            )

            # Step 2b: TRACE — LangSmithTracingHook auto-collects (Phase 1, no-op if disabled)

            if result.get("ok"):
                print(f"✅ [Cycle {cycle}] 성공적으로 완료됨.")
                return result

            # ── Step 3: Failure & Rollback ──
            print(f"⚠️ [Cycle {cycle}] 실패 감지: {result.get('reason')}")
            print(f"⏪ [FSALoop] 안전을 위해 Git Rollback을 수행합니다.")

            try:
                self.git.rollback()
            except Exception as e:
                print_agent_msg("Critical", f"Rollback 실패: {e}", "🛑")

            # ── Step 4: EVAL — Evaluator 에이전트 또는 fallback ──
            eval_res = self._run_evaluator(agent, current_task, result, run_id, cycle)

            action = eval_res.get("action", "abort")
            if action == "abort":
                print_agent_msg("Evaluator", f"Catastrophic failure. Aborting sequence. Reason: {eval_res.get('reasoning')}", "🛑")
                return {"ok": False, "reason": "Evaluator aborted task."}

            # ── Step 5: SUMMARIZE + DATASETS — handled by evaluator agent's skills ──
            # (trace_execution, summarize_failure, generate_eval_dataset are in evaluator's skill set)

            # ── Step 5b: SKILL EVOLVE — 실패 원인이 특정 스킬이면 자동 진화 시도 ──
            self._try_evolve_failed_skill(
                error_reason=result.get("reason", ""),
                eval_reasoning=eval_res.get("reasoning", ""),
                run_id=run_id,
                cycle=cycle,
            )

            # ── Step 6: REFLECT — inject feedback into next cycle's task ──
            print_agent_msg("Evaluator", f"Decision: {action.upper()} | Reasoning: {eval_res.get('reasoning')}", "💡")
            current_task = f"[EVALUATOR {action.upper()} ADVICE]\n{eval_res.get('new_instruction')}\n\n[Original Task]\n{task_input}"

        return {"ok": False, "reason": "최대 재시도 횟수(5회) 초과로 중단되었습니다."}

    def _try_evolve_failed_skill(
        self, error_reason: str, eval_reasoning: str, run_id: str, cycle: int
    ):
        """
        실패 원인에서 스킬 이름을 추출하고, 해당 스킬을 자동 진화시킵니다.

        흐름:
          1. 에러 로그/평가에서 스킬 디렉토리 식별
          2. evolve_skill()로 LLM 기반 코드 개선
          3. security_guard.run_isolated()로 샌드박스 검증
          4. 검증 통과 시 레지스트리 핫리로딩
          5. 실패 시 .bak 롤백 유지
        """
        skill_dir = self._detect_failed_skill_dir(error_reason, eval_reasoning)
        if not skill_dir:
            return

        skill_name = os.path.basename(skill_dir)
        print_agent_msg("SkillEvolve", f"스킬 진화 시도: {skill_name} (cycle {cycle})", "🧬")

        try:
            from core.skill_creator import evolve_skill

            coding_engine = None
            if hasattr(self.runner, 'mr') and hasattr(self.runner.mr, 'pick'):
                coding_engine = self.runner.mr.pick('coding')

            success = evolve_skill(
                skill_dir=skill_dir,
                feedback=eval_reasoning,
                error_log=error_reason,
                coding_engine=coding_engine,
            )

            if not success:
                print_agent_msg("SkillEvolve", f"진화 실패, 기존 코드 유지: {skill_name}", "⚠️")
                return

            # 샌드박스 검증 (action 스킬만)
            skill_py = os.path.join(skill_dir, "skill.py")
            if os.path.exists(skill_py):
                verified = self._verify_evolved_skill(skill_py, skill_name)
                if not verified:
                    self._rollback_skill(skill_dir, skill_name)
                    return

            # 핫리로딩: 레지스트리 갱신
            self._hot_reload_registry(skill_name)

            print_agent_msg("SkillEvolve", f"스킬 진화 성공 + 핫리로딩 완료: {skill_name}", "✅")

        except Exception as e:
            print_agent_msg("SkillEvolve", f"진화 프로세스 예외: {e}", "⚠️")

    def _detect_failed_skill_dir(self, error_reason: str, eval_reasoning: str) -> str | None:
        """에러 로그에서 실패한 스킬 디렉토리를 추출합니다."""
        combined = f"{error_reason}\n{eval_reasoning}".lower()

        # 스킬 디렉토리 탐색 대상
        search_dirs = []
        if PROJECT_SKILLS_DIR and os.path.isdir(PROJECT_SKILLS_DIR):
            search_dirs.append(PROJECT_SKILLS_DIR)
        if os.path.isdir(SKILLS_DIR):
            search_dirs.append(SKILLS_DIR)

        for base_dir in search_dirs:
            try:
                for item in os.listdir(base_dir):
                    skill_dir = os.path.join(base_dir, item)
                    if not os.path.isdir(skill_dir):
                        continue
                    if item.startswith(".") or item in ("forge", "_external_cache"):
                        continue
                    # 스킬 이름이 에러 메시지에 포함되어 있는지 확인
                    skill_name_lower = item.lower().replace("-", "_").replace(" ", "_")
                    if skill_name_lower in combined or item.lower() in combined:
                        return skill_dir
            except Exception:
                continue

        return None

    def _verify_evolved_skill(self, skill_py: str, skill_name: str) -> bool:
        """진화된 스킬을 샌드박스에서 검증합니다."""
        try:
            from core.security_guard import quick_guard, run_isolated

            # AST 보안 검사
            with open(skill_py, "r", encoding="utf-8") as f:
                code = f.read()

            safe, violations = quick_guard(code)
            if not safe:
                print_agent_msg("SkillEvolve", f"보안 검사 실패 ({skill_name}): {violations}", "🛑")
                return False

            # 격리 실행 검증
            ok, result, stderr = run_isolated(skill_py, timeout_sec=15)
            if not ok:
                reason = result.get("reason", "") or result.get("error", "") or stderr
                print_agent_msg("SkillEvolve", f"샌드박스 검증 실패 ({skill_name}): {reason}", "🛑")
                return False

            print_agent_msg("SkillEvolve", f"샌드박스 검증 통과: {skill_name}", "✅")
            return True

        except Exception as e:
            print_agent_msg("SkillEvolve", f"검증 중 예외 ({skill_name}): {e}", "⚠️")
            return False

    def _rollback_skill(self, skill_dir: str, skill_name: str):
        """진화 실패 시 .bak 파일로 롤백합니다."""
        import shutil
        restored = False
        for filename in ("skill.py", "SKILL.md", "skill.md"):
            bak = os.path.join(skill_dir, filename + ".bak")
            src = os.path.join(skill_dir, filename)
            if os.path.exists(bak):
                try:
                    shutil.copy2(bak, src)
                    os.remove(bak)
                    print_agent_msg("SkillEvolve", f"롤백 완료: {skill_name}/{filename}", "⏪")
                    restored = True
                except Exception as e:
                    print_agent_msg("SkillEvolve", f"롤백 실패: {skill_name}/{filename}: {e}", "⚠️")
        if not restored:
            print_agent_msg("SkillEvolve", f"롤백 대상 .bak 파일 없음: {skill_name}", "⚠️")

    def _hot_reload_registry(self, skill_name: str):
        """레지스트리를 강제 리로딩하여 진화된 스킬을 반영합니다."""
        try:
            from core.skill_registry import get_global_registry
            registry = get_global_registry()
            registry.auto_load_from_directories(force=True)
            print_agent_msg("SkillEvolve", f"레지스트리 핫리로딩 완료 ({registry.count()}개 스킬)", "🔄")
        except Exception as e:
            print_agent_msg("SkillEvolve", f"핫리로딩 실패: {e}", "⚠️")

    def _run_evaluator(self, agent: dict, current_task: str, result: dict, run_id: str, cycle: int) -> dict:
        """Try evaluator agent first, fall back to StrategyEvaluator."""
        if self.agent_mgr is not None:
            try:
                evaluator_agent = self.agent_mgr.get_or_create("evaluator")
                eval_task = (
                    f"[EVAL REQUEST] run_id={run_id}\n"
                    f"Error: {result.get('reason')}\n"
                    f"Original Task: {current_task}"
                )
                eval_result = self.runner.run(
                    evaluator_agent,
                    eval_task,
                    run_id=f"{run_id}_eval_c{cycle}",
                    auto_approve=True,
                )
                parsed = parse_evaluator_response(eval_result)
                if parsed.get("action") != "abort" or "Could not parse" not in parsed.get("reasoning", ""):
                    return parsed
                # If parsing failed, fall through to legacy evaluator
            except Exception as e:
                print_agent_msg("FSALoop", f"Evaluator 에이전트 호출 실패, fallback 사용: {e}", "⚠️")

        # Fallback: legacy StrategyEvaluator
        return self.evaluator.evaluate_failure(
            role=agent.get("role", "General"),
            instruction=current_task,
            error_log=result.get("reason", "Unknown error"),
        )
