import os
import json
import re
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
    (V24) Full Self Automation (FSA) Loop Orchestrator with Workspace-Scoped Git Safety.
    Implements EXECUTE -> TRACE -> EVAL -> SUMMARIZE -> DATASETS -> REFLECT cycle.

    Git 범위 원칙:
    - commit/rollback은 workspace(프로젝트 디렉토리) 안에서만 동작한다.
    - factory 코드(core/, skills/ 등)는 에이전트 git 조작 범위 밖이다.
    - rollback은 tracked 파일 변경만 되돌린다. untracked 파일은 보존된다.
    """
    def __init__(self, runner: AgentRunner, agent_mgr=None, visualizer=None):
        self.runner = runner
        self.agent_mgr = agent_mgr
        self._visualizer = visualizer
        # Fallback evaluator (used when agent_mgr is unavailable or evaluator agent fails)
        self.evaluator = StrategyEvaluator(
            model_name=runner.mr.pick('evaluator') if hasattr(runner.mr, 'pick') else 'gemini-1.5-pro-latest'
        )
        self.max_cycles = 5
        # NOTE: GitManager는 __init__에서 생성하지 않는다.
        # run_mission()에서 workspace를 받아 그 범위로 생성한다.

    def run_mission(self, agent: dict, task_input: str, run_id: str, workspace: str | None = None):
        print(f"\n🌀 [FSALoop] 풀 셀프 자동화 모드(FSA) 시작: {run_id}")

        # workspace 기반 GitManager 생성 — factory 루트가 아닌 프로젝트 디렉토리
        target_workspace = workspace or os.getcwd()
        self.workspace = target_workspace   # 교차검증 evaluator에서 참조
        git = GitManager(target_workspace)

        agent_name = agent.get("name", "Agent") if isinstance(agent, dict) else "Agent"

        current_task = task_input
        for cycle in range(1, self.max_cycles + 1):
            if self._visualizer:
                self._visualizer.update_from_fsa_step(agent_name, "execute", cycle, self.max_cycles)
            else:
                print(f"\n🔄 [Cycle {cycle}/{self.max_cycles}] 실행 및 자동 커밋 준비...")

            # ── Step 1: Pre-Commit for safety (workspace 범위) ──
            commit_msg = f"AEE Auto-Save: {run_id} Cycle {cycle}"
            git.commit(commit_msg)

            # ── Step 2: EXECUTE ──
            result = self.runner.run(
                agent,
                current_task,
                run_id=f"{run_id}_c{cycle}",
                auto_approve=True,
                workspace=target_workspace,
            )

            # Step 2b: TRACE — LangSmithTracingHook auto-collects (Phase 1, no-op if disabled)

            if result.get("ok"):
                if self._visualizer:
                    self._visualizer.mark_completed(agent_name)
                else:
                    print(f"✅ [Cycle {cycle}] 성공적으로 완료됨.")
                return result

            # ── Step 3: Failure & Rollback (workspace tracked 파일만) ──
            if self._visualizer:
                self._visualizer.update_from_fsa_step(agent_name, "eval", cycle, self.max_cycles)
            else:
                print(f"⚠️ [Cycle {cycle}] 실패 감지: {result.get('reason')}")
                print(f"⏪ [FSALoop] workspace tracked 파일 변경을 되돌립니다.")

            try:
                git.rollback()
            except Exception as e:
                print_agent_msg("Critical", f"Rollback 실패: {e}", "🛑")

            # ── Step 4: EVAL — 교차검증 평가 (2개 이상 CLI) 또는 단일 evaluator ──
            eval_res = self._run_cross_verified_evaluator(agent, current_task, result, run_id, cycle)

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
            from core.skill_enricher import enrich_skill_metadata
            from core.skill_evolution_bus import SkillEvolutionBus

            coding_engine = None
            if hasattr(self.runner, 'mr') and hasattr(self.runner.mr, 'pick'):
                coding_engine = self.runner.mr.pick('coding')

            # 진화 전 버전 기록
            old_version = self._read_skill_version(skill_dir)

            success = evolve_skill(
                skill_dir=skill_dir,
                feedback=eval_reasoning,
                error_log=error_reason,
                coding_engine=coding_engine,
            )

            if not success:
                print_agent_msg("SkillEvolve", f"진화 실패, 기존 코드 유지: {skill_name}", "⚠️")
                return

            # 메타데이터도 함께 보강 (키워드/태그/설명 최신화)
            enrich_skill_metadata(skill_dir, coding_engine=coding_engine, force=True)

            # 샌드박스 검증 (action 스킬만)
            skill_py = os.path.join(skill_dir, "skill.py")
            if os.path.exists(skill_py):
                verified = self._verify_evolved_skill(skill_py, skill_name)
                if not verified:
                    self._rollback_skill(skill_dir, skill_name)
                    return

            new_version = self._read_skill_version(skill_dir)

            # EvolutionBus: 전체 캐시 체인 무효화 + 이벤트 브로드캐스트
            evo_bus = SkillEvolutionBus.get_instance()
            evo_bus.bind_runner(self.runner)
            evo_bus.on_skill_evolved(
                skill_id=skill_name,
                skill_dir=skill_dir,
                old_version=old_version,
                new_version=new_version,
                trigger="fsa_failure",
            )

            print_agent_msg("SkillEvolve", f"스킬 진화 성공 + 전체 캐시 무효화: {skill_name}", "✅")

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

        _SKIP = {"forge", "_external_cache", "__pycache__", "warehouse"}
        for base_dir in search_dirs:
            try:
                for item in os.listdir(base_dir):
                    skill_dir = os.path.join(base_dir, item)
                    if not os.path.isdir(skill_dir):
                        continue
                    if item.startswith(".") or item in _SKIP:
                        continue
                    # 단어 경계 매칭으로 오탐 방지 (BUG-6 수정)
                    # \b는 _를 단어 문자로 취급해 오작동 → lookaround 방식으로 교체
                    skill_name_lower = item.lower().replace("-", "_")
                    pattern = rf"(?<![a-zA-Z0-9_]){re.escape(skill_name_lower)}(?![a-zA-Z0-9_])"
                    if re.search(pattern, combined) or re.search(
                        rf"(?<![a-zA-Z0-9_]){re.escape(item.lower())}(?![a-zA-Z0-9_])", combined
                    ):
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
        """레지스트리를 강제 리로딩하여 진화된 스킬을 반영합니다 (하위 호환용)."""
        try:
            from core.skill_registry import get_global_registry
            registry = get_global_registry()
            registry.auto_load_from_directories(force=True)
            print_agent_msg("SkillEvolve", f"레지스트리 핫리로딩 완료 ({registry.count()}개 스킬)", "🔄")
        except Exception as e:
            print_agent_msg("SkillEvolve", f"핫리로딩 실패: {e}", "⚠️")

    def _read_skill_version(self, skill_dir: str) -> str:
        """meta.yaml에서 현재 스킬 버전을 읽어옵니다."""
        meta_path = os.path.join(skill_dir, "meta.yaml")
        if not os.path.exists(meta_path):
            return "0.1.0"
        try:
            import yaml
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
            return str(meta.get("version", "0.1.0"))
        except Exception:
            return "0.1.0"

    def _run_cross_verified_evaluator(
        self, agent: dict, current_task: str, result: dict, run_id: str, cycle: int
    ) -> dict:
        """교차검증 기반 평가: 여러 엔진이 실패를 분석하고 Opus가 최종 판정.

        2개 이상 CLI가 설치된 경우에만 활성화.
        실패 시 기존 _run_evaluator() 로 폴백한다.
        """
        try:
            from core.cross_verification import CrossVerificationLoop

            # 설치된 CLI가 2개 미만이면 교차검증 의미 없음
            mr = getattr(self.runner, 'mr', None)
            pairs = mr.pick_multiple() if (mr is not None and hasattr(mr, 'pick_multiple')) else []
            if len(pairs) < 2:
                return self._run_evaluator(agent, current_task, result, run_id, cycle)

            workspace = getattr(self, 'workspace', None) or os.getcwd()
            loop = CrossVerificationLoop(workspace=workspace, level="dynamic", max_rounds=1)

            eval_task = (
                f"다음 실행 결과의 실패 원인을 분석하고 수정 방향을 제시하세요.\n\n"
                f"[원래 태스크]\n{current_task}\n\n"
                f"[오류 로그]\n{result.get('reason', '')[:2000]}\n\n"
                f"반드시 JSON으로 답변하세요:\n"
                f'{{"action": "retry"|"abort", '
                f'"reasoning": "분석 내용", '
                f'"new_instruction": "수정된 태스크 지시"}}'
            )
            judgment = loop.run(eval_task, "당신은 코드 디버깅 전문가입니다.")

            if judgment.verdict in ("pass", "partial") and judgment.merged_output:
                parsed = self._parse_eval_result(judgment.merged_output)
                if parsed.get("action") in ("retry", "abort"):
                    return parsed

        except Exception as exc:
            print_agent_msg("FSALoop", f"교차검증 평가 실패, fallback 사용: {exc}", "⚠️")

        return self._run_evaluator(agent, current_task, result, run_id, cycle)

    def _parse_eval_result(self, text: str) -> dict:
        """평가 결과 텍스트에서 action/reasoning/new_instruction을 추출한다."""
        import re, json as _json
        match = re.search(r'\{[\s\S]*"action"[\s\S]*\}', text)
        if match:
            try:
                data = _json.loads(match.group())
                return {
                    "action": str(data.get("action", "abort")).strip().lower(),
                    "reasoning": str(data.get("reasoning", "")),
                    "new_instruction": str(data.get("new_instruction", "")),
                }
            except Exception:
                pass
        return {"action": "abort", "reasoning": "판정 파싱 실패", "new_instruction": ""}

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
