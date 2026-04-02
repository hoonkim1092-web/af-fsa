import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from core.agent_runner import AgentRunner
from core.ast_memory_hub import AstMemoryHub
from core.continuity import OrchestratorManifestStore, workspace_runtime_file
from core.evaluator import StrategyEvaluator
from core.llm_engine import LLMEngine
from core.manager import AgentManager
from core.project_mailbox import load_mailbox_messages, mailbox_prompt_digest
from core.project_task_board import (
    board_is_complete,
    board_prompt_digest,
    load_project_board,
    next_board_tasks,
    reset_in_progress_tasks,
    update_project_board_task,
)
from core.agent_specializer import AgentSpecializer
from core.message_broker import MessageBroker
from core.utils import print_agent_msg, safe_id, safe_json_load


class DynamicOrchestrator:
    """
    Dynamic multi-agent orchestrator driven by a central PM model.
    """

    def __init__(self, mr, max_concurrent: int = 5, terminal_per_agent: bool | None = None, broker=None, visualizer=None):
        self.mr = mr
        self.max_concurrent = max_concurrent
        # terminal_per_agent: None이면 환경변수 AGENT_TERMINAL_MODE로 결정 (기본 비활성)
        if terminal_per_agent is None:
            terminal_per_agent = os.environ.get("AGENT_TERMINAL_MODE", "").lower() in ("1", "true", "yes")
        self.terminal_per_agent = terminal_per_agent
        self.agent_mgr = AgentManager(self.mr)
        self.runner = AgentRunner(self.mr)
        self.specializer = AgentSpecializer()
        self.broker = broker if broker is not None else MessageBroker()
        self._visualizer = visualizer

        engine_id = self.mr.pick("orchestrator") if hasattr(self.mr, "pick") else "gemini-1.5-pro-latest"
        self.llm = LLMEngine(model_name=engine_id)

        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.active_assignments: Dict[str, Dict[str, Any]] = {}
        # 개선 7: 태스크 완료 이벤트 — 하드코딩 sleep(2) 대신 적응적 대기에 사용
        self._task_done_event: asyncio.Event = asyncio.Event()
        self.state_board: Dict[str, Any] = {
            "completed_subtasks": [],
            "failed_subtasks": [],
            "interrupted_subtasks": [],
            "agents_status": {},
            "current_status": "",
        }
        self._task_retry_count: Dict[str, int] = {}  # task_id → 실패 횟수
        self._max_task_retries = 3
        self.memory_hub = AstMemoryHub()
        self.evaluator = StrategyEvaluator(model_name=engine_id)
        self._workspace: str | None = None
        self._manifest_store: OrchestratorManifestStore | None = None
        self._manifest_roles: List[str] = []
        self._manifest_project_desc = ""

    def _runtime_file(self, filename: str, workspace: str | None = None) -> Path:
        target_workspace = workspace or self._workspace
        if target_workspace:
            return workspace_runtime_file(target_workspace, filename)
        return Path(filename)

    def _sync_manifest(self, force: bool = False) -> None:
        if self._manifest_store is None:
            return
        self._manifest_store.save_snapshot(
            self.state_board,
            active_assignments=self.active_assignments,
            roles=self._manifest_roles,
            project_desc=self._manifest_project_desc,
            force=force,
        )

    def _prepare_resume_state(self, project_desc: str, roles: List[str], workspace: str | None) -> None:
        self._workspace = workspace or None
        self._manifest_roles = list(roles or [])
        self._manifest_project_desc = str(project_desc or "")
        self.active_assignments = {}
        # 개선 7: asyncio.run()마다 새 이벤트 루프가 생성되므로 Event도 재생성
        self._task_done_event = asyncio.Event()

        if not self._workspace:
            return

        reset_in_progress_tasks(self._workspace)
        self._manifest_store = OrchestratorManifestStore(self._workspace)
        loaded = self._manifest_store.load_resume_state()
        self.state_board = {
            "completed_subtasks": list(loaded.get("completed_subtasks", [])),
            "failed_subtasks": list(loaded.get("failed_subtasks", [])),
            "interrupted_subtasks": list(loaded.get("interrupted_subtasks", [])),
            "agents_status": dict(loaded.get("agents_status", {})),
            "current_status": str(loaded.get("current_status", "")),
        }
        for role in roles:
            self.state_board["agents_status"].setdefault(role, "idle")
        self.state_board["current_status"] = "running"
        self._sync_manifest(force=True)

    def _open_todo_items(self, workspace: str) -> List[str]:
        todo_path = os.path.join(workspace, ".todo.md")
        if not os.path.exists(todo_path):
            return []

        items: List[str] = []
        with open(todo_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = str(raw or "").strip()
                if line.startswith("- [ ] "):
                    items.append(line[6:].strip())
                elif line.startswith("- [/] "):
                    pass
                elif line.startswith("- ") and not line.startswith("- [x] ") and not line.startswith("- [/] "):
                    items.append(line[2:].strip())
        return [item for item in items if item]

    def _completed_todo_items(self, workspace: str) -> List[str]:
        todo_path = os.path.join(workspace, ".todo.md")
        if not os.path.exists(todo_path):
            return []

        items: List[str] = []
        with open(todo_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = str(raw or "").strip()
                if line.startswith("- [x] "):
                    items.append(line[6:].strip())
        return [item for item in items if item]

    def _completed_subtask_keys(self) -> set[str]:
        keys: set[str] = set()
        for bucket in ("completed_subtasks",):
            for item in self.state_board.get(bucket, []) or []:
                if not isinstance(item, dict):
                    continue
                task_id = safe_id(item.get("task_id"))
                if task_id:
                    keys.add(task_id)
                text = str(item.get("subtask") or "").strip()
                if text:
                    keys.add(safe_id(text))
        return keys

    def _todo_matches_role(self, todo_text: str, role: str) -> bool:
        prefix = str(todo_text or "").split(":", 1)[0]
        return safe_id(prefix) == safe_id(role)

    def _todo_role_prefix(self, todo_text: str) -> str:
        text = str(todo_text or "")
        if ":" not in text:
            return ""
        prefix = text.split(":", 1)[0]
        return safe_id(prefix)

    def _todo_fully_completed(self, workspace: str) -> bool:
        board = load_project_board(workspace)
        if board:
            return board_is_complete(board)
        completed_items = self._completed_todo_items(workspace)
        open_items = self._open_todo_items(workspace)
        return bool(completed_items) and not open_items

    def _fallback_next_tasks(self, available_roles: List[str], workspace: str) -> List[Dict[str, str]]:
        board = load_project_board(workspace)
        if board.get("tasks"):
            return next_board_tasks(board, available_roles, self._completed_subtask_keys())

        todo_items = self._open_todo_items(workspace)
        if not todo_items:
            return []

        completed = self._completed_subtask_keys()
        pending = [item for item in todo_items if safe_id(item) not in completed]
        if not pending:
            return []

        tasks: List[Dict[str, str]] = []
        used_items: set[str] = set()

        for role in available_roles:
            match = next(
                (item for item in pending if item not in used_items and self._todo_matches_role(item, role)),
                None,
            )
            if not match:
                continue
            used_items.add(match)
            tasks.append(
                {
                    "assigned_role": role,
                    "subtask_instruction": match,
                    "estimated_complexity": "HIGH",
                }
            )

        for role in available_roles:
            if any(task.get("assigned_role") == role for task in tasks):
                continue
            match = next(
                (
                    item
                    for item in pending
                    if item not in used_items and not self._todo_role_prefix(item)
                ),
                None,
            )
            if not match:
                break
            used_items.add(match)
            tasks.append(
                {
                    "assigned_role": role,
                    "subtask_instruction": match,
                    "estimated_complexity": "HIGH",
                }
            )

        return tasks

    async def _lilith_decide_next(
        self,
        project_desc: str,
        roles: List[str],
        workspace: str | None = None,
    ) -> List[Dict[str, str]]:
        working_count = sum(1 for status in self.state_board["agents_status"].values() if status == "working")
        if working_count >= self.max_concurrent:
            return []

        available_roles = [role for role in roles if self.state_board["agents_status"].get(role, "idle") == "idle"]
        if not available_roles:
            return []

        todo_content = ""
        target_workspace = workspace or os.getcwd()
        board = load_project_board(target_workspace)
        if self._todo_fully_completed(target_workspace):
            return []
        todo_path = os.path.join(target_workspace, ".todo.md")
        if os.path.exists(todo_path):
            with open(todo_path, "r", encoding="utf-8") as handle:
                todo_content = handle.read()

        # 개선 3: blocker 메시지를 별도로 감지하여 LLM에 강조
        try:
            raw_mailbox = load_mailbox_messages(target_workspace)
        except Exception:
            raw_mailbox = []
        blocker_messages = [
            m for m in raw_mailbox
            if m.get("type") == "blocker" and str(m.get("status") or "pending") == "pending"
        ]
        blocker_section = ""
        if blocker_messages:
            blocker_lines = [f"  ⚠ BLOCKER from={m.get('from_role')} task={m.get('task_id') or '-'}: {m.get('body')}" for m in blocker_messages]
            blocker_section = f"\n## ⚠ ACTIVE BLOCKERS (RESOLVE FIRST — {len(blocker_messages)} blocker(s)):\n" + "\n".join(blocker_lines) + "\n"

        prompt = f"""
        You are Lilith, the Master Orchestrator of Agent Factory V3.
        Your goal is to complete this project: {project_desc}

        ## Tactical Plan (.todo.md):
        {todo_content}

        ## Structured Project Board:
        {board_prompt_digest(board)}

        ## Current Board State:
        Completed works: {json.dumps(self.state_board['completed_subtasks'], ensure_ascii=False)}
        Failed works: {json.dumps(self.state_board['failed_subtasks'], ensure_ascii=False)}

        ## Global Context (Shared AST Memory):
        {self.memory_hub.get_summary()}
        {blocker_section}
        ## Agent Mailbox:
        {mailbox_prompt_digest(target_workspace)}

        ## Available Idle Agents:
        {json.dumps(available_roles, ensure_ascii=False)}

        ## Instruction:
        Based on the roadmap and current state, determine the next immediate sub-tasks
        that should be executed in parallel. If the project is complete, return an empty array.

        Return JSON ONLY:
        {{
            "next_tasks": [
                {{"assigned_role": "role_name", "subtask_instruction": "detailed instruction", "estimated_complexity": "LOW/HIGH", "task_id": "optional_task_id"}}
            ]
        }}
        """

        try:
            response = await asyncio.to_thread(self.llm.generate_json, prompt)
            data = response if isinstance(response, dict) else safe_json_load(response)
            tasks = data.get("next_tasks", [])
            completed = self._completed_subtask_keys() | {
                safe_id(item) for item in self._completed_todo_items(target_workspace)
            }
            filtered_tasks = []
            for task in tasks:
                if task.get("assigned_role") not in available_roles:
                    continue
                task_key = safe_id(str(task.get("task_id") or task.get("subtask_instruction") or ""))
                if task_key and task_key in completed:
                    continue
                filtered_tasks.append(task)
            try:
                log_path = self._runtime_file("dynamic_log.txt", target_workspace)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"\n[Lilith] Cycle: {getattr(self, '_current_cycle', '?')}\n"
                        f"Roles: {available_roles}\nTasks: {tasks}\nFilteredTasks: {filtered_tasks}\nRaw: {json.dumps(data, ensure_ascii=False)}\n"
                    )
            except Exception:
                pass  # 로그 실패는 오케스트레이션에 영향 없음

            if not filtered_tasks:
                fallback_tasks = self._fallback_next_tasks(available_roles, target_workspace)
                if fallback_tasks:
                    return fallback_tasks
                if not data:
                    print_agent_msg("Lilith", "LLM response empty. Retrying next cycle...", "")
                    return []
                return []
            return filtered_tasks
        except Exception as exc:
            print_agent_msg("Lilith", f"Failed to dynamically generate next tasks: {exc}", "")
            fallback_tasks = self._fallback_next_tasks(available_roles, target_workspace)
            if fallback_tasks:
                return fallback_tasks
            return []

    def _resolve_task_meta(
        self,
        workspace: str,
        role: str,
        subtask: str,
        task_id: str,
    ) -> dict | None:
        """project_board에서 task 메타데이터를 조회한다.

        task_id 매칭만 사용한다. instruction 폴백은 safe_id() 60자 절단으로 인해
        서로 다른 instruction이 동일한 키를 생성하는 잘못된 매칭을 유발할 수 있어 제거.
        """
        board = load_project_board(workspace)
        if not board or not board.get("tasks"):
            return None

        target_id = safe_id(task_id)
        if not target_id:
            return None

        for task in board.get("tasks", []):
            if not isinstance(task, dict):
                continue
            if safe_id(str(task.get("task_id", ""))) == target_id:
                return task
        return None

    def _safe_serialize(self, data: Any) -> Any:
        """JSON 직렬화 불가 객체를 문자열로 변환한다."""
        if isinstance(data, dict):
            return {k: self._safe_serialize(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._safe_serialize(v) for v in data]
        try:
            json.dumps(data)
            return data
        except (TypeError, ValueError):
            return str(data)

    def _cross_verified_evaluate(self, role: str, instruction: str, error_log: str, workspace: str) -> dict:
        """교차검증 기반 실패 평가 + 자가진화. CLI 2개 이상이면 교차검증, 아니면 단일 evaluator."""
        try:
            from core.cross_verification import CrossVerificationLoop

            pairs = self.mr.pick_multiple() if hasattr(self.mr, 'pick_multiple') else []
            if len(pairs) >= 2:
                loop = CrossVerificationLoop(workspace=workspace, level="dynamic", max_rounds=1)
                eval_task = (
                    f"다음 실행 결과의 실패 원인을 분석하고 수정 방향을 제시하세요.\n\n"
                    f"[역할] {role}\n[태스크]\n{instruction}\n\n"
                    f"[오류 로그]\n{error_log[:2000]}\n\n"
                    f'반드시 JSON으로 답변하세요:\n'
                    f'{{"action": "retry"|"abort", '
                    f'"reasoning": "분석 내용", '
                    f'"new_instruction": "수정된 태스크 지시"}}'
                )
                judgment = loop.run(eval_task, "당신은 코드 디버깅 전문가입니다.")

                # 자가진화: failure_patterns가 있으면 관련 스킬 진화 트리거
                if judgment.failure_patterns:
                    self._try_evolve_from_patterns(judgment.failure_patterns, error_log, workspace)

                if judgment.verdict in ("pass", "partial") and judgment.merged_output:
                    import re as _re, json as _json
                    match = _re.search(r'\{[\s\S]*"action"[\s\S]*\}', judgment.merged_output)
                    if match:
                        data = _json.loads(match.group())
                        action = str(data.get("action", "abort")).strip().lower()
                        if action in ("retry", "abort"):
                            print_agent_msg("CrossVerify", f"교차검증 판정: {action}", "")
                            return {
                                "action": action,
                                "reasoning": str(data.get("reasoning", "")),
                                "new_instruction": str(data.get("new_instruction", "")),
                            }
        except Exception as exc:
            print_agent_msg("CrossVerify", f"교차검증 실패, 단일 evaluator 폴백: {exc}", "")

        return self.evaluator.evaluate_failure(role=role, instruction=instruction, error_log=error_log)

    def _try_evolve_from_patterns(self, failure_patterns: list, error_log: str, workspace: str) -> None:
        """failure_patterns에서 관련 스킬을 찾아 자가진화를 시도한다."""
        try:
            import re as _re
            from core.skill_creator import evolve_skill
            from core.skill_evolution_bus import SkillEvolutionBus

            skills_dir = os.path.join(os.path.dirname(__file__), "..", "skills")
            if not os.path.isdir(skills_dir):
                return

            skill_names = [
                d for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d)) and not d.startswith(".")
            ]
            feedback = f"failure_patterns: {failure_patterns}"
            evolved = []

            for pattern in failure_patterns:
                keywords = _re.split(r"[_\-:/ ]+", str(pattern).lower())
                for skill_name in skill_names:
                    name_lower = skill_name.lower().replace("-", "_")
                    if any(kw and kw in name_lower for kw in keywords if len(kw) > 2):
                        skill_dir = os.path.join(skills_dir, skill_name)
                        ok = evolve_skill(skill_dir, feedback=feedback, error_log=error_log[:1000])
                        if ok:
                            evolved.append(skill_name)
                            print_agent_msg("Evolve", f"스킬 진화 성공: {skill_name}", "")

            if evolved:
                try:
                    bus = SkillEvolutionBus()
                    for name in evolved:
                        bus.on_skill_evolved(
                            skill_name=name,
                            trigger="cross_verification_orchestrator",
                            old_version="",
                            new_version="",
                        )
                except Exception:
                    pass
        except Exception as exc:
            print_agent_msg("Evolve", f"자가진화 시도 실패 (무시): {exc}", "")

    async def _run_agent_in_thread(
        self,
        agent_data: Dict[str, Any],
        subtask: str,
        run_id: str,
        target_workspace: str,
        task_id: str,
    ) -> Dict[str, Any]:
        """기존 방식: asyncio.to_thread로 같은 프로세스에서 실행."""
        result = await asyncio.to_thread(
            self.runner.run,
            agent_data,
            subtask,
            run_id,
            True,
            target_workspace,
            task_id,
        )
        return result or {}

    async def _run_agent_in_terminal(
        self,
        agent_data: Dict[str, Any],
        role: str,
        subtask: str,
        run_id: str,
        target_workspace: str,
        task_id: str,
    ) -> Dict[str, Any]:
        """터미널 모드: 새 콘솔 창에서 agent_worker.py를 실행하고 결과를 폴링한다."""
        runs_dir = os.path.join(target_workspace, "runs", run_id)
        os.makedirs(runs_dir, exist_ok=True)
        task_file = os.path.join(runs_dir, "task.json")
        result_file = os.path.join(runs_dir, "result.json")

        task_payload = {
            "project_root": str(Path(__file__).parent.parent),
            "role": role,
            "agent_data": self._safe_serialize(agent_data),
            "subtask": subtask,
            "run_id": run_id,
            "workspace": target_workspace,
            "task_id": task_id,
            "broker_address": self.broker.get_broker_address(),
        }
        with open(task_file, "w", encoding="utf-8") as fh:
            json.dump(task_payload, fh, ensure_ascii=False, indent=2)

        # PyInstaller frozen exe → "af.exe worker --task-file ..."
        # 일반 Python → "python core/agent_worker.py --task-file ..."
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "worker", "--task-file", task_file, "--result-file", result_file]
        else:
            worker_script = str(Path(__file__).parent / "agent_worker.py")
            cmd = [sys.executable, worker_script, "--task-file", task_file, "--result-file", result_file]

        popen_kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE

        proc = subprocess.Popen(cmd, **popen_kwargs)
        print_agent_msg("System", f"[{role}] 새 터미널 창에서 실행 중 (pid={proc.pid})", "")

        # 결과 파일 폴링 (최대 3600초)
        max_wait = 3600.0
        poll_interval = 0.5
        waited = 0.0
        while waited < max_wait:
            await asyncio.sleep(poll_interval)
            waited += poll_interval
            if os.path.exists(result_file):
                try:
                    with open(result_file, encoding="utf-8") as fh:
                        return json.load(fh)
                except (json.JSONDecodeError, OSError):
                    continue
            # 프로세스가 비정상 종료되고 결과 파일도 없는 경우
            if proc.poll() is not None and not os.path.exists(result_file):
                return {"ok": False, "reason": f"worker_exited_code_{proc.returncode}"}

        proc.kill()
        return {"ok": False, "reason": "worker_timeout"}

    async def _execute_agent_task(
        self,
        role: str,
        subtask: str,
        run_id: str,
        workspace: str | None = None,
        task_id: str = "",
    ):
        print_agent_msg("System", f"Dispatching [{role}] -> {subtask[:50]}...", "")
        self.state_board["agents_status"][role] = "working"
        if self._visualizer and not self.terminal_per_agent:
            self._visualizer.update_from_status(role, "working", task_summary=subtask[:30])

        # target_workspace를 try 밖에서 초기화해야 except 블록에서도 참조 가능하다.
        target_workspace = workspace or os.getcwd()
        try:
            update_project_board_task(target_workspace, role, subtask, "in_progress", task_id=task_id)
            assignment: Dict[str, Any] = {
                "role": role,
                "subtask": subtask,
                "workspace": target_workspace,
            }
            if task_id:
                assignment["task_id"] = task_id
            self.active_assignments.setdefault(run_id, assignment)
            self._sync_manifest()
            agent_data = self.agent_mgr.get_or_create(role, workspace=target_workspace)

            # 1-Agent-per-1-Task: 보드에서 task_meta를 조회하여 에이전트를 작업 단위로 특화
            task_meta = self._resolve_task_meta(target_workspace, role, subtask, task_id)
            if task_meta:
                agent_data = self.specializer.specialize(agent_data, task_meta, target_workspace)

            # 실행 방식 선택: terminal_per_agent=True → 새 콘솔 창, False → 기존 스레드
            if self.terminal_per_agent:
                result = await self._run_agent_in_terminal(
                    agent_data, role, subtask, run_id, target_workspace, task_id
                )
            else:
                result = await self._run_agent_in_thread(
                    agent_data, subtask, run_id, target_workspace, task_id
                )

            if result and result.get("ok"):
                completed_entry = {"role": role, "subtask": subtask, "result": "Success"}
                if task_id:
                    completed_entry["task_id"] = task_id
                self.state_board["completed_subtasks"].append(completed_entry)
                update_project_board_task(target_workspace, role, subtask, "completed", note="Success", task_id=task_id)
                await self.memory_hub.update_ast_state(
                    filepath=f"Project_Scope_{role}",
                    author_role=role,
                    changes_summary=f"Completed subtask: {subtask[:50]}",
                )
                print_agent_msg(role, "Task completed.", "")
                if self._visualizer and not self.terminal_per_agent:
                    self._visualizer.mark_completed(role)
                self._sync_manifest()
            else:
                reason = result.get("reason", "Unknown error") if result else "No result"
                print_agent_msg(role, f"Task failed: {reason[:100]}", "")
                _retry_key = task_id or f"{role}:{subtask[:60]}"
                self._task_retry_count[_retry_key] = self._task_retry_count.get(_retry_key, 0) + 1
                if self._visualizer and not self.terminal_per_agent:
                    self._visualizer.mark_failed(role)
                eval_res = await asyncio.to_thread(
                    self._cross_verified_evaluate,
                    role=role,
                    instruction=subtask,
                    error_log=reason,
                    workspace=target_workspace,
                )
                evaluator_action = str(eval_res.get("action") or "abort").strip().lower()
                evaluator_advice = str(eval_res.get("new_instruction") or "").strip()
                failed_entry = {
                    "role": role,
                    "subtask": subtask,
                    "reason": reason,
                    "evaluator_action": evaluator_action,
                    "evaluator_advice": evaluator_advice,
                }
                if task_id:
                    failed_entry["task_id"] = task_id
                self.state_board["failed_subtasks"].append(failed_entry)
                retry_note = reason if not evaluator_advice else f"{reason} | advice: {evaluator_advice}"
                next_status = "blocked" if evaluator_action == "retry" else "failed"
                update_project_board_task(target_workspace, role, subtask, next_status, note=retry_note, task_id=task_id)
                self._sync_manifest()
        except Exception as exc:
            crashed_entry = {"role": role, "subtask": subtask, "reason": str(exc)}
            if task_id:
                crashed_entry["task_id"] = task_id
            self.state_board["failed_subtasks"].append(crashed_entry)
            update_project_board_task(target_workspace, role, subtask, "failed", note=str(exc), task_id=task_id)
            print_agent_msg(role, f"Task crashed: {exc}", "")
            self._sync_manifest()
        finally:
            self.state_board["agents_status"][role] = "idle"
            if self._visualizer and not self.terminal_per_agent:
                self._visualizer.update_from_status(role, "idle")
            self.active_tasks.pop(run_id, None)
            self.active_assignments.pop(run_id, None)
            self._sync_manifest()
            # 개선 7: 태스크 완료를 메인 루프에 즉시 알린다
            self._task_done_event.set()

    async def _orchestration_loop(self, project_desc: str, roles: List[str], workspace: str | None = None):
        target_workspace = workspace or os.getcwd()

        # 터미널 모드: TCP 브로커 서버 시작
        if self.terminal_per_agent:
            try:
                port = await self.broker.start_tcp_server()
                print_agent_msg("System", f"MessageBroker TCP 서버 시작: 127.0.0.1:{port}", "")
            except Exception as exc:
                print_agent_msg("System", f"MessageBroker TCP 시작 실패 (JSONL 폴백): {exc}", "")

        for role in roles:
            self.state_board["agents_status"][role] = "idle"
            if self._visualizer and not self.terminal_per_agent:
                self._visualizer.register_agent(role)

        if self._visualizer and not self.terminal_per_agent:
            from core.terminal_visualizer import VisualMode
            if self._visualizer.mode == VisualMode.DASHBOARD:
                self._visualizer.print_dashboard()

        cycle = 0
        max_cycles = 50

        while cycle < max_cycles:
            cycle += 1
            self._current_cycle = cycle
            print_agent_msg("Lilith", f"--- Dynamic Sync Cycle {cycle} ---", "")

            new_tasks = await self._lilith_decide_next(project_desc, roles, target_workspace)
            if not new_tasks:
                active_workers = [
                    r for r, status in self.state_board["agents_status"].items() if status == "working"
                ]
                if not active_workers:
                    print_agent_msg("Lilith", "No more tasks to assign and no agents are working.", "")
                    break
                print_agent_msg("Lilith", f"Waiting for active agents: {', '.join(active_workers)}", "")
                # 개선 7: 태스크 완료 이벤트를 기다리되 최대 10초까지만 대기
                self._task_done_event.clear()
                try:
                    await asyncio.wait_for(self._task_done_event.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass
                continue

            dispatched = False
            for task in new_tasks:
                role = task.get("assigned_role")
                instruction = task.get("subtask_instruction", "")
                plan_task_id = str(task.get("task_id") or "")
                if role == "__placeholder__":
                    continue
                # retry 횟수 초과 시 해당 태스크 스킵
                retry_key = plan_task_id or f"{role}:{instruction[:60]}"
                if self._task_retry_count.get(retry_key, 0) >= self._max_task_retries:
                    print_agent_msg("Lilith", f"[{role}] 태스크 {self._max_task_retries}회 실패 — 스킵", "")
                    update_project_board_task(target_workspace, role, instruction, "failed",
                                             note=f"max_retries({self._max_task_retries}) exceeded", task_id=plan_task_id)
                    continue
                if role and instruction and role in roles and self.state_board["agents_status"].get(role) == "idle":
                    run_token = f"run_{int(time.time())}_{role}_{uuid.uuid4().hex[:6]}"
                    self.state_board["agents_status"][role] = "working"
                    assignment: Dict[str, Any] = {
                        "role": role,
                        "subtask": instruction,
                        "workspace": target_workspace,
                    }
                    if plan_task_id:
                        assignment["task_id"] = plan_task_id
                    self.active_assignments[run_token] = assignment
                    self._sync_manifest()
                    task_obj = asyncio.create_task(
                        self._execute_agent_task(role, instruction, run_token, target_workspace, task_id=plan_task_id)
                    )
                    self.active_tasks[run_token] = task_obj
                    dispatched = True

            # 개선 7: 태스크를 하나라도 디스패치했으면 즉시 다음 사이클; 아니면 짧게 대기
            if not dispatched:
                await asyncio.sleep(1)

        if cycle >= max_cycles:
            self.state_board["current_status"] = "stopped_max_cycles"
        elif self.state_board["failed_subtasks"]:
            self.state_board["current_status"] = "partial"
        else:
            self.state_board["current_status"] = "completed"
        self._sync_manifest(force=True)

        if self.active_tasks:
            print_agent_msg("Lilith", "Waiting for remaining tasks to complete before exit...", "")
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)

        # 브로커 종료
        try:
            await self.broker.shutdown()
        except Exception:
            pass

    def run_project(self, project_desc: str, roles: List[str], workspace: str | None = None) -> Dict[str, Any]:
        print_agent_msg("System", "Initializing Dynamic LLM-Driven Orchestrator (V3)", "")
        self._prepare_resume_state(project_desc, roles, workspace)
        try:
            asyncio.run(self._orchestration_loop(project_desc, roles, workspace))
        except Exception as exc:
            import traceback

            self.state_board["current_status"] = "crashed"
            crash_path = self._runtime_file("crash.log", workspace)
            with crash_path.open("w", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
            self._sync_manifest(force=True)
            print_agent_msg("System", f"Orchestration loop crashed: {exc}", "")
        else:
            self._sync_manifest(force=True)
        return self.state_board


