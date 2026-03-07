"""
/api/run — SSE 스트리밍 기반 에이전트 실행
"""
import os
import sys
import json
import asyncio
import traceback
from pathlib import Path
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

router = APIRouter(tags=["run"])

ROOT_DIR = Path(__file__).parent.parent.parent


class RunRequest(BaseModel):
    agent_id: str
    task: str
    project_id: str | None = None
    auto_approve: bool = False
    execution_mode: str = "approval"
    fsa: bool = False


def _safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    out = []
    for ch in t:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    while "__" in s:
        s = s.replace("__", "_")
    return s or "default"


@router.post("/run")
async def run_agent(req: RunRequest, request: Request):
    """SSE 스트리밍으로 에이전트 실행 결과를 실시간 전달."""

    async def event_stream():
        try:
            # 기본 메시지: 실행 시작
            execution_mode = "fsa" if (req.fsa or str(req.execution_mode).strip().lower() == "fsa") else "approval"

            yield {"event": "status", "data": json.dumps({
                "type": "start",
                "agent_id": req.agent_id,
                "message": f"Starting agent: {req.agent_id} (mode={execution_mode})"
            }, ensure_ascii=False)}

            # model_utils에서 엔진 선택 결과 확인
            sys.path.insert(0, str(ROOT_DIR))
            from model_utils import resolve_dynamic_model, TIER_PRIMARY, TIER_CROSS_FALLBACK, TIER_FREE_FALLBACK, TIER_UNCALLABLE

            # 에이전트에 해당하는 엔진 선택
            engine_map = {
                "lilith": "researcher_gemini",
                "himari": "researcher_gemini",
                "deadbyte": "coder_claude",
                "saiba_midori": "coder_claude",
            }
            engine_id = engine_map.get(req.agent_id, "gemini_flash")
            sel = resolve_dynamic_model(engine_id)

            # 폴백/uncallable 처리
            if sel.tier == TIER_UNCALLABLE:
                yield {"event": "fallback", "data": json.dumps({
                    "type": "uncallable",
                    "model": sel.model,
                    "reason": sel.reason,
                }, ensure_ascii=False)}
                return

            if sel.tier in (TIER_CROSS_FALLBACK, TIER_FREE_FALLBACK):
                yield {"event": "fallback", "data": json.dumps({
                    "type": sel.tier,
                    "model": sel.model,
                    "reason": sel.reason,
                    "needs_approval": not req.auto_approve,
                }, ensure_ascii=False)}

                if not req.auto_approve:
                    # 클라이언트가 /api/fallback/approve 또는 /api/fallback/reject로 응답해야 함
                    return

            # 정상 실행 (또는 auto_approve된 폴백)
            yield {"event": "status", "data": json.dumps({
                "type": "engine_selected",
                "model": sel.model,
                "tier": sel.tier,
            }, ensure_ascii=False)}

            # 에이전트 실행
            from agent_launcher import AgentFactory
            factory = AgentFactory()
            workspace = None
            if req.project_id:
                workspace = str(ROOT_DIR / "projects" / _safe_id(req.project_id))

            yield {"event": "status", "data": json.dumps({
                "type": "running",
                "message": f"Executing with model: {sel.model}"
            }, ensure_ascii=False)}

            # 동기 실행을 비동기로 래핑
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: factory.run(
                    task_input=req.task,
                    role_spec=req.agent_id,
                    execution_mode=execution_mode,
                    workspace=workspace,
                )
            )

            yield {"event": "result", "data": json.dumps({
                "type": "complete",
                "success": result.get("ok", False) if isinstance(result, dict) else False,
                "output": str(result),
            }, ensure_ascii=False)}

        except Exception as e:
            yield {"event": "error", "data": json.dumps({
                "type": "error",
                "message": str(e),
                "traceback": traceback.format_exc(),
            }, ensure_ascii=False)}

    return EventSourceResponse(event_stream())
