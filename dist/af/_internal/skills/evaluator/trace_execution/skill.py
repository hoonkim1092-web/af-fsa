"""
trace_execution 스킬 (Phase 4)

JSONL 로그 파일에서 에이전트 실행 흐름을 추출하고 구조화된 분석 결과를 반환한다.
이벤트 타입: run_start, skill_call_start, skill_call_end, captured_output, run_end
"""
import json
import os
from datetime import datetime


SKILL_ID = "trace-execution"


def propose(ctx: dict) -> dict:
    """스킬 실행 가능 여부 확인 및 필요한 입력 명시"""
    return {
        "skill_id": SKILL_ID,
        "required_fields": ["log_file"],
        "optional_fields": ["run_id", "include_details"],
        "constraints": {
            "log_file": "Path to JSONL trace file",
            "include_details": "boolean, default=True",
        },
    }


def apply(ctx: dict) -> dict:
    """JSONL 로그 파일을 파싱하여 실행 흐름을 구조화된 형태로 반환"""
    log_file = ctx.get("log_file")
    if not log_file:
        return {"ok": False, "reason": "missing_log_file"}

    include_details = ctx.get("include_details", True)

    # 절대경로 또는 workspace 상대경로 지원
    if not os.path.isabs(log_file):
        workspace = ctx.get("workspace", ".")
        log_file = os.path.join(workspace, log_file)

    if not os.path.exists(log_file):
        return {"ok": False, "reason": "file_not_found", "log_file": log_file}

    # JSONL 파싱
    events = _parse_jsonl(log_file)
    if not events:
        return {"ok": False, "reason": "empty_or_invalid_log", "log_file": log_file}

    # 이벤트별 분류
    run_start = None
    run_end = None
    # 스택 기반: 같은 스킬이 여러 번 호출되어도 순서대로 매칭
    pending_starts: list[dict] = []
    skill_calls = []
    captured_outputs = []

    for event in events:
        event_type = event.get("event_type")
        if event_type == "run_start":
            run_start = event
        elif event_type == "run_end":
            run_end = event
        elif event_type == "skill_call_start":
            pending_starts.append(event)
        elif event_type == "skill_call_end":
            skill_name = event.get("skill_name", "unknown")
            # 가장 먼저 들어온(가장 오래된) 매칭되지 않은 start를 찾음
            start_event = None
            for idx, ps in enumerate(pending_starts):
                if ps.get("skill_name", "unknown") == skill_name:
                    start_event = pending_starts.pop(idx)
                    break
            call_info = _build_skill_call(
                len(skill_calls) + 1, skill_name, start_event, event, include_details
            )
            skill_calls.append(call_info)
        elif event_type == "captured_output":
            captured_outputs.append(event)

    # 불완전한 스킬 호출 (end 이벤트 누락) 처리
    for start_event in pending_starts:
        skill_name = start_event.get("skill_name", "unknown")
        call_info = _build_skill_call(
            len(skill_calls) + 1, skill_name, start_event, None, include_details
        )
        call_info["status"] = "incomplete"
        skill_calls.append(call_info)

    # run_id 결정
    run_id = ctx.get("run_id") or (run_start or {}).get("run_id", "unknown")

    # 전체 실행 시간 계산
    total_duration_ms = 0
    if run_end:
        total_duration_ms = run_end.get("duration_ms", 0)
    elif run_start and events:
        total_duration_ms = _calc_duration_ms(
            run_start.get("timestamp"), events[-1].get("timestamp")
        )

    # 실행 상태 결정
    if run_end:
        status = "success" if run_end.get("ok", False) else "failure"
        reason = run_end.get("reason", "")
    else:
        status = "incomplete"
        reason = "missing_run_end"

    # 타임라인 갭 분석: 이벤트 간 비정상적 지연 감지
    anomalies = _detect_timeline_anomalies(events, total_duration_ms)

    # 스킬 결과에서 에러 패턴 감지
    skill_errors = _detect_skill_errors(skill_calls)

    result = {
        "ok": True,
        "run_id": run_id,
        "agent_name": (run_start or {}).get("agent_name", "unknown"),
        "total_duration_ms": total_duration_ms,
        "task_input": (run_start or {}).get("task_input", ""),
        "skill_calls": skill_calls,
        "status": status,
        "reason": reason,
        "total_events": len(events),
    }

    if anomalies:
        result["anomalies"] = anomalies
    if skill_errors:
        result["skill_errors"] = skill_errors

    if include_details and captured_outputs:
        result["captured_outputs"] = [
            co.get("content", "")[:2000] for co in captured_outputs
        ]

    return result


def test(ctx: dict) -> dict:
    """스킬 자체 테스트"""
    import tempfile

    # 테스트용 JSONL 파일 생성
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        test_events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "test-run", "agent_name": "test-agent", "task_input": "test"},
            {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
             "run_id": "test-run", "skill_name": "web-search", "skill_args": {"q": "test"}},
            {"timestamp": "2026-03-13T10:00:02Z", "event_type": "skill_call_end",
             "run_id": "test-run", "skill_name": "web-search", "skill_result": "done"},
            {"timestamp": "2026-03-13T10:00:03Z", "event_type": "run_end",
             "run_id": "test-run", "ok": True, "reason": "success", "duration_ms": 3000},
        ]
        for evt in test_events:
            f.write(json.dumps(evt) + "\n")
        tmp_path = f.name

    try:
        result = apply({"log_file": tmp_path})
        assert result["ok"], f"apply failed: {result}"
        assert result["run_id"] == "test-run"
        assert len(result["skill_calls"]) == 1
        assert result["status"] == "success"
        return {"ok": True, "message": "trace_execution self-test passed"}
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_jsonl(file_path: str) -> list:
    """JSONL 파일을 파싱하여 이벤트 리스트 반환. 손상된 줄은 건너뜀."""
    events = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 손상된 줄은 건너뜀
    except (OSError, IOError):
        return []
    return events


def _build_skill_call(
    sequence: int,
    skill_name: str,
    start_event: dict | None,
    end_event: dict | None,
    include_details: bool,
) -> dict:
    """스킬 호출 정보 구성"""
    call = {
        "sequence": sequence,
        "skill_name": skill_name,
    }

    start_ts = (start_event or {}).get("timestamp", "")
    end_ts = (end_event or {}).get("timestamp", "")
    call["start_time"] = start_ts
    call["end_time"] = end_ts
    call["duration_ms"] = _calc_duration_ms(start_ts, end_ts)

    if include_details:
        args = (start_event or {}).get("skill_args", {})
        call["args_summary"] = _summarize_dict(args, max_len=200)
        result_str = (end_event or {}).get("skill_result", "")
        call["result_summary"] = str(result_str)[:200] if result_str else ""

    return call


def _calc_duration_ms(start_ts: str, end_ts: str) -> int:
    """두 ISO 타임스탬프 간 밀리초 차이 계산"""
    if not start_ts or not end_ts:
        return 0
    try:
        fmt_candidates = [
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]
        t_start = _parse_ts(start_ts, fmt_candidates)
        t_end = _parse_ts(end_ts, fmt_candidates)
        if t_start and t_end:
            delta = (t_end - t_start).total_seconds() * 1000
            return max(0, int(delta))
    except Exception:
        pass
    return 0


def _parse_ts(ts: str, formats: list) -> datetime | None:
    """여러 형식을 시도하여 타임스탬프 파싱"""
    for fmt in formats:
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def _summarize_dict(d: dict, max_len: int = 200) -> str:
    """딕셔너리를 요약 문자열로 변환"""
    if not d:
        return ""
    parts = []
    for k, v in d.items():
        parts.append(f"{k}={str(v)[:50]}")
    summary = ", ".join(parts)
    return summary[:max_len]


# ---------------------------------------------------------------------------
# Timeline anomaly detection
# ---------------------------------------------------------------------------

_ERROR_KEYWORDS = frozenset({"error", "exception", "traceback", "failed", "timeout", "refused", "denied"})

# 전체 실행 시간의 50% 이상을 차지하는 이벤트 간 갭을 비정상으로 판정
_GAP_RATIO_THRESHOLD = 0.5
# 또는 절대값으로 10초 이상 갭
_GAP_ABSOLUTE_THRESHOLD_MS = 10_000


def _detect_timeline_anomalies(events: list[dict], total_duration_ms: int) -> list[dict]:
    """이벤트 간 비정상적 지연 감지"""
    anomalies = []
    if len(events) < 2:
        return anomalies

    timestamps = []
    for evt in events:
        ts_str = evt.get("timestamp", "")
        if ts_str:
            ts = _parse_ts(ts_str, [
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
            ])
            timestamps.append((ts, evt))

    for i in range(1, len(timestamps)):
        prev_ts, prev_evt = timestamps[i - 1]
        curr_ts, curr_evt = timestamps[i]
        if prev_ts is None or curr_ts is None:
            continue

        gap_ms = int((curr_ts - prev_ts).total_seconds() * 1000)
        if gap_ms < 0:
            anomalies.append({
                "type": "timestamp_rewind",
                "between": [prev_evt.get("event_type", "?"), curr_evt.get("event_type", "?")],
                "gap_ms": gap_ms,
            })
            continue

        is_large_gap = (
            gap_ms >= _GAP_ABSOLUTE_THRESHOLD_MS
            or (total_duration_ms > 0 and gap_ms / total_duration_ms > _GAP_RATIO_THRESHOLD)
        )
        if is_large_gap:
            anomalies.append({
                "type": "large_gap",
                "between": [prev_evt.get("event_type", "?"), curr_evt.get("event_type", "?")],
                "gap_ms": gap_ms,
            })

    return anomalies


def _detect_skill_errors(skill_calls: list[dict]) -> list[dict]:
    """스킬 호출 결과에서 에러 패턴 감지"""
    errors = []
    for sc in skill_calls:
        result_summary = sc.get("result_summary", "").lower()
        if not result_summary:
            continue
        for kw in _ERROR_KEYWORDS:
            if kw in result_summary:
                errors.append({
                    "skill_name": sc.get("skill_name", "unknown"),
                    "sequence": sc.get("sequence", 0),
                    "pattern": kw,
                    "excerpt": sc.get("result_summary", "")[:100],
                })
                break
    return errors
