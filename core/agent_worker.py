"""
core/agent_worker.py
====================
터미널 모드용 워커 스크립트.

DynamicOrchestrator의 terminal_per_agent=True 모드에서
새 콘솔 창(subprocess)으로 실행된다.

사용법:
  python core/agent_worker.py --task-file <task.json> --result-file <result.json>

task.json 스키마:
  {
    "project_root": "...",
    "role": "backend_dev",
    "agent_data": { ... },
    "subtask": "...",
    "run_id": "run_xxx",
    "workspace": "C:/Project",
    "task_id": "backend_dev_module_1_scope_1",
    "broker_address": "127.0.0.1:52079"
  }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback


def main():
    parser = argparse.ArgumentParser(description="Agent Factory Worker")
    parser.add_argument("--task-file", required=True, help="task.json 경로")
    parser.add_argument("--result-file", required=True, help="result.json 출력 경로")
    args = parser.parse_args()

    # task.json 로드
    try:
        with open(args.task_file, encoding="utf-8") as fh:
            task = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        # 결과 파일에 에러 기록 후 종료
        err_result = {"ok": False, "reason": f"task_json_load_error: {exc}"}
        try:
            os.makedirs(os.path.dirname(args.result_file), exist_ok=True)
            with open(args.result_file, "w", encoding="utf-8") as fh:
                json.dump(err_result, fh, ensure_ascii=False)
        except Exception:
            pass
        print(f"[Worker] task.json 로드 실패: {exc}")
        sys.exit(1)

    project_root = task.get("project_root", "")
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)

    role = task.get("role", "unknown")
    subtask = task.get("subtask", "")
    run_id = task.get("run_id", "")
    workspace = task.get("workspace", "")
    task_id = task.get("task_id", "")
    agent_data = task.get("agent_data", {})

    print(f"[Worker:{role}] 시작 — {subtask[:80]}...")

    result = {"ok": False, "reason": "worker_unknown_error"}
    try:
        from core.model_router import ModelRouter
        from core.agent_runner import AgentRunner

        mr = ModelRouter()
        runner = AgentRunner(mr)
        run_result = runner.run(
            agent=agent_data,
            task_input=subtask,
            run_id=run_id,
            auto_approve=True,
            workspace=workspace,
            task_id=task_id,
        )
        result = run_result if run_result else {"ok": False, "reason": "empty_result"}
    except Exception as exc:
        result = {"ok": False, "reason": f"worker_exception: {exc}"}
        traceback.print_exc()

    # result.json 저장
    try:
        os.makedirs(os.path.dirname(args.result_file), exist_ok=True)
        with open(args.result_file, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        # 대체 경로에 저장 시도
        print(f"[Worker:{role}] result.json 쓰기 실패: {exc}")
        fallback = args.result_file + ".fallback.json"
        try:
            with open(fallback, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False)
        except Exception:
            pass

    status = "성공" if result.get("ok") else f"실패: {result.get('reason', '')[:100]}"
    print(f"[Worker:{role}] 완료 — {status}")


if __name__ == "__main__":
    main()
