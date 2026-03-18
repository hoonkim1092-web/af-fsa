"""
Episode Extractor — JSONL trace → EpisodeRecord 변환.

Phase 12: run_start → skill_call_* → run_end 시퀀스를 파싱하여
구조화된 EpisodeRecord를 생성.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.memory_system.models import EpisodeRecord

logger = logging.getLogger(__name__)


def extract_episode_from_jsonl(jsonl_path: str | Path) -> EpisodeRecord | None:
    """Parse a JSONL trace file into an EpisodeRecord."""
    path = Path(jsonl_path)
    if not path.exists():
        return None

    events = _parse_jsonl(path)
    if not events:
        return None

    return _events_to_episode(events)


def extract_episode_from_events(events: list[dict[str, Any]]) -> EpisodeRecord | None:
    """Convert a list of trace events into an EpisodeRecord."""
    if not events:
        return None
    return _events_to_episode(events)


def _parse_jsonl(path: Path) -> list[dict]:
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
    except Exception as exc:
        logger.error("episode_extractor parse error %s: %s", path, exc)
    return events


def _events_to_episode(events: list[dict]) -> EpisodeRecord:
    start_ev = next((e for e in events if e.get("event_type") == "run_start"), {})
    end_ev = next((e for e in events if e.get("event_type") == "run_end"), {})

    # Collect skill calls as actions (stack-based FIFO matching per skill with depth tracking)
    actions: list[dict[str, Any]] = []
    # pending_stacks[skill_name] = [(action_index, depth), ...] — oldest first
    pending_stacks: dict[str, list[tuple[int, int]]] = {}
    skill_depths: dict[str, int] = {}  # Track current depth per skill

    for ev in events:
        etype = ev.get("event_type", "")
        if etype == "skill_call_start":
            sname = ev.get("skill_name", "?")
            depth = skill_depths.get(sname, 0) + 1
            skill_depths[sname] = depth
            idx = len(actions)
            actions.append({
                "skill_name": sname,
                "args": ev.get("skill_args", {}),
                "result": None,
                "result_full": None,
                "depth": depth,
            })
            pending_stacks.setdefault(sname, []).append((idx, depth))
        elif etype == "skill_call_end":
            sname = ev.get("skill_name", "?")
            result_val = ev.get("skill_result", "")
            skill_depths[sname] = max(0, skill_depths.get(sname, 1) - 1)

            # Store both truncated and full result
            result_display = result_val
            if isinstance(result_val, str) and len(result_val) > 500:
                result_display = result_val[:500] + "..."

            # Match oldest pending action for this skill (FIFO with depth)
            stack = pending_stacks.get(sname, [])
            if stack:
                matched_idx, _ = stack.pop(0)
                actions[matched_idx]["result"] = result_display
                actions[matched_idx]["result_full"] = result_val

    ok = end_ev.get("ok")
    outcome = "success" if ok else ("failure" if ok is False else "partial")

    # Error info: reason from end event or captured_output with errors
    error_info = ""
    if not ok:
        error_info = end_ev.get("reason", "")
        if not error_info:
            # Try captured_output
            cap = next((e for e in events if e.get("event_type") == "captured_output"), {})
            cap_content = cap.get("content", "")
            # Extract error lines
            err_lines = [
                l for l in cap_content.split("\n")
                if any(kw in l.lower() for kw in ("error", "exception", "traceback", "failed"))
            ]
            if err_lines:
                error_info = "\n".join(err_lines[:5])

    # Parse timestamp with fallback handling
    ts = start_ev.get("timestamp")
    created = datetime.now(timezone.utc)
    if ts:
        try:
            created = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            logger.warning("Invalid timestamp format in run_start event: %s", ts)
            created = datetime.now(timezone.utc)

    return EpisodeRecord(
        run_id=start_ev.get("run_id", end_ev.get("run_id", "")),
        project_id=start_ev.get("project_id", ""),
        agent_name=start_ev.get("agent_name", ""),
        task_input=start_ev.get("task_input", "")[:2000],
        actions=actions,
        outcome=outcome,
        error_info=error_info,
        duration_ms=end_ev.get("duration_ms", 0),
        created_at=created,
    )
