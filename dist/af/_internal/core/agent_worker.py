"""
core/agent_worker.py
====================
에이전트 태스크 워커 — 별도 프로세스(새 콘솔 창)에서 실행된다.

DynamicOrchestrator가 각 에이전트 태스크를 이 스크립트를 통해
독립된 터미널 창으로 실행한다.

입력:  --task-file   <path>  JSON 태스크 파일
출력:  --result-file <path>  JSON 결과 파일

task.json 스키마:
    {
        "project_root": "...",  # sys.path에 추가할 프로젝트 루트
        "role": "engineer",
        "agent_data": {...},    # AgentManager가 만든 에이전트 딕셔너리
        "subtask": "...",
        "run_id": "run_xxx",
        "workspace": "...",
        "task_id": "optional"
    }

result.json 스키마:
    {
        "ok": true/false,
        "reason": "..."
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _setup_path(project_root: str) -> None:
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)


def _print_banner(role: str, subtask: str) -> None:
    width = 60
    print("=" * width)
    print(f"  Agent: {role}")
    print(f"  Task : {subtask[:width - 9]}")
    print("=" * width)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Task Worker")
    parser.add_argument("--task-file", required=True, help="JSON 태스크 파일 경로")
    parser.add_argument("--result-file", required=True, help="JSON 결과 파일 경로")
    args = parser.parse_args()

    task_file = os.path.abspath(args.task_file)
    result_file = os.path.abspath(args.result_file)

    # 태스크 로드
    try:
        with open(task_file, encoding="utf-8") as fh:
            task: dict = json.load(fh)
    except Exception as exc:
        _write_result(result_file, ok=False, reason=f"task_file_load_error:{exc}")
        return

    project_root = task.get("project_root", "")
    _setup_path(project_root)

    role = task.get("role", "Agent")
    subtask = task.get("subtask", "")
    run_id = task.get("run_id", "")
    workspace = task.get("workspace", os.getcwd())
    task_id = task.get("task_id", "")
    agent_data = task.get("agent_data", {})

    _print_banner(role, subtask)

    # 오케스트레이터 보드에서 배정된 태스크임을 훅 시스템에 알린다
    os.environ["AGENT_WORKER_SUBPROCESS"] = "1"

    broker_address = task.get("broker_address", "")

    # MessageBroker 상태 알림 헬퍼 — 매번 독립 이벤트루프에서 새 연결을 사용
    def _broker_notify(status: str, detail: str = "") -> None:
        """브로커에 상태를 전송하고 즉시 연결을 닫는다.
        각 호출마다 새 asyncio.run() + 새 연결을 사용해 이벤트루프 충돌을 방지한다."""
        if not broker_address:
            return
        try:
            import asyncio as _aio
            from core.message_client import MessageClient

            async def _send():
                client = MessageClient(broker_address, agent_id=role)
                if await client.connect():
                    await client.send_status(status, detail)
                    await client.close()

            _aio.run(_send())
        except Exception as exc:
            print(f"[worker] 브로커 알림 실패 ({status}): {exc}")

    if broker_address:
        _broker_notify("started", subtask[:100])
        print(f"[worker] MessageBroker 연결됨: {broker_address}")

    try:
        from core.agent_runner import AgentRunner, ModelRouter

        mr = ModelRouter()
        runner = AgentRunner(mr)

        result = runner.run(
            agent=agent_data,
            task_input=subtask,
            run_id=run_id,
            auto_approve=True,
            workspace=workspace,
            task_id=task_id,
        ) or {}
    except Exception as exc:
        import traceback
        traceback.print_exc()
        result = {"ok": False, "reason": f"worker_exception:{exc}"}

    ok = bool(result.get("ok", False))
    reason = str(result.get("reason", ""))
    print()
    print("=" * 60)
    print(f"  {'완료 ✓' if ok else '실패 ✗'}  {reason[:80]}")
    print("=" * 60)

    # 브로커에 완료 상태 전송
    if broker_address:
        _broker_notify("completed" if ok else "failed", reason[:200])

    _write_result(result_file, ok=ok, reason=reason)

    # Windows 콘솔: 창이 즉시 닫히지 않도록 대기
    if sys.platform == "win32":
        try:
            input("\nEnter를 누르면 이 창이 닫힙니다...")
        except (EOFError, KeyboardInterrupt):
            pass


def _write_result(result_file: str, ok: bool, reason: str) -> None:
    try:
        os.makedirs(os.path.dirname(result_file), exist_ok=True)
        with open(result_file, "w", encoding="utf-8") as fh:
            json.dump({"ok": ok, "reason": reason}, fh, ensure_ascii=False)
    except Exception as exc:
        print(f"[worker] result 파일 쓰기 실패: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
