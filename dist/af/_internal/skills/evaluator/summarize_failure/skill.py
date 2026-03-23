"""
summarize_failure 스킬 (Phase 4)

실패한 에이전트 실행 로그를 분석하여 에러 원인, 심각도, 수정 제안을 반환한다.
LLM을 통한 분석이 가능하면 활용하고, 불가능하면 규칙 기반 분석으로 fallback.
"""
import json
import os
import re
import glob as glob_mod


SKILL_ID = "summarize-failure"

# 에러 패턴 매핑 (정규식 → failure_type)
_ERROR_PATTERNS = {
    r"(?i)timeout|timed?\s*out": "timeout",
    r"(?i)connect(ion)?.*?(error|refused|reset|failed)": "skill_error",
    r"(?i)(api|http)\s*(error|4\d{2}|5\d{2})": "skill_error",
    r"(?i)invalid.*?(input|argument|param)": "invalid_input",
    r"(?i)(permission|access|auth).*?(denied|error|fail)": "hook_blocked",
    r"(?i)(import|module).*?(error|not found)": "skill_error",
    r"(?i)key\s*error|index\s*error|type\s*error|value\s*error": "skill_error",
}

# 심각도 매핑
_SEVERITY_MAP = {
    "timeout": "high",
    "skill_error": "high",
    "invalid_input": "medium",
    "hook_blocked": "critical",
    "unknown": "medium",
}


def propose(ctx: dict) -> dict:
    """스킬 실행 가능 여부 확인 및 필요한 입력 명시"""
    return {
        "skill_id": SKILL_ID,
        "required_fields": ["log_file"],
        "optional_fields": ["run_id", "include_suggestions"],
        "constraints": {
            "log_file": "Path to JSONL trace file of failed run",
            "include_suggestions": "boolean, default=True",
        },
    }


def apply(ctx: dict) -> dict:
    """실패한 실행 로그를 분석하여 원인, 심각도, 수정 제안 반환"""
    log_file = ctx.get("log_file")
    if not log_file:
        return {"ok": False, "reason": "missing_log_file"}

    include_suggestions = ctx.get("include_suggestions", True)

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

    # 이벤트 분류
    run_start = None
    run_end = None
    skill_calls = []
    captured_outputs = []
    last_skill_end = None

    for event in events:
        et = event.get("event_type")
        if et == "run_start":
            run_start = event
        elif et == "run_end":
            run_end = event
        elif et == "skill_call_start":
            skill_calls.append({"start": event, "end": None})
        elif et == "skill_call_end":
            # 마지막 매칭되지 않은 start와 매칭
            for sc in reversed(skill_calls):
                if sc["end"] is None and sc["start"].get("skill_name") == event.get("skill_name"):
                    sc["end"] = event
                    break
            last_skill_end = event
        elif et == "captured_output":
            captured_outputs.append(event)

    # run_id 결정
    run_id = ctx.get("run_id") or (run_start or {}).get("run_id", "unknown")

    # 실패 여부 확인
    is_failure = True
    if run_end:
        is_failure = not run_end.get("ok", False)

    if not is_failure:
        return {
            "ok": True,
            "run_id": run_id,
            "failure_type": "none",
            "root_cause": "이 실행은 성공적으로 완료되었습니다",
            "severity": "low",
        }

    # 에러 텍스트 수집
    error_texts = _collect_error_texts(run_end, last_skill_end, captured_outputs)
    combined_error_text = "\n".join(error_texts)

    # 실패 타입 분류
    failure_type = _classify_failure(combined_error_text, run_end, skill_calls)

    # 심각도 판정
    severity = _SEVERITY_MAP.get(failure_type, "medium")

    # 마지막 스킬 호출 정보
    affected_skill = ""
    error_context = {}
    if last_skill_end:
        affected_skill = last_skill_end.get("skill_name", "unknown")
        error_context = {
            "last_skill_call": affected_skill,
            "status": "failed",
            "captured_output": str(last_skill_end.get("skill_result", ""))[:500],
        }
    elif skill_calls:
        last_call = skill_calls[-1]
        affected_skill = last_call["start"].get("skill_name", "unknown")
        error_context = {
            "last_skill_call": affected_skill,
            "status": "incomplete" if last_call["end"] is None else "unknown",
        }

    # 근본 원인 추출
    root_cause = _extract_root_cause(combined_error_text, failure_type, affected_skill)

    # 에러 체인 분석: 어떤 스킬에서 연쇄 실패가 시작되었는지
    error_chain = _trace_error_chain(skill_calls, events)

    # Duration 이상 탐지: 특정 스킬이 비정상적으로 오래 걸렸는지
    duration_anomaly = _detect_duration_anomaly(skill_calls)

    # 심각도 세분화: 에러 체인 길이, duration 이상 등 반영
    severity = _calc_refined_severity(failure_type, error_chain, duration_anomaly, skill_calls)

    result = {
        "ok": True,
        "run_id": run_id,
        "failure_type": failure_type,
        "root_cause": root_cause,
        "affected_skill": affected_skill,
        "error_context": error_context,
        "severity": severity,
    }

    if error_chain:
        result["error_chain"] = error_chain
    if duration_anomaly:
        result["duration_anomaly"] = duration_anomaly

    # 수정 제안 생성
    if include_suggestions:
        result["suggestions"] = _generate_suggestions(
            failure_type, affected_skill, combined_error_text
        )

    # 유사 실패 검색
    log_dir = os.path.dirname(log_file)
    similar = _find_similar_failures(log_dir, failure_type, log_file)
    if similar:
        result["similar_failures"] = similar

    return result


def test(ctx: dict) -> dict:
    """스킬 자체 테스트"""
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        test_events = [
            {"timestamp": "2026-03-13T10:00:00Z", "event_type": "run_start",
             "run_id": "fail-run", "agent_name": "test", "task_input": "test"},
            {"timestamp": "2026-03-13T10:00:01Z", "event_type": "skill_call_start",
             "run_id": "fail-run", "skill_name": "web-search", "skill_args": {}},
            {"timestamp": "2026-03-13T10:00:02Z", "event_type": "skill_call_end",
             "run_id": "fail-run", "skill_name": "web-search",
             "skill_result": "ConnectionError: Network timeout"},
            {"timestamp": "2026-03-13T10:00:03Z", "event_type": "run_end",
             "run_id": "fail-run", "ok": False, "reason": "error", "duration_ms": 3000},
        ]
        for evt in test_events:
            f.write(json.dumps(evt) + "\n")
        tmp_path = f.name

    try:
        result = apply({"log_file": tmp_path})
        assert result["ok"], f"apply failed: {result}"
        assert result["failure_type"] != "none"
        assert result["severity"] in ("critical", "high", "medium", "low")
        return {"ok": True, "message": "summarize_failure self-test passed"}
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_jsonl(file_path: str) -> list:
    """JSONL 파일 파싱. 손상된 줄은 건너뜀."""
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
                    continue
    except (OSError, IOError):
        return []
    return events


def _collect_error_texts(
    run_end: dict | None,
    last_skill_end: dict | None,
    captured_outputs: list,
) -> list[str]:
    """에러 관련 텍스트 수집"""
    texts = []
    if run_end:
        reason = run_end.get("reason", "")
        if reason:
            texts.append(reason)
    if last_skill_end:
        result_str = str(last_skill_end.get("skill_result", ""))
        if result_str:
            texts.append(result_str)
    for co in captured_outputs:
        content = co.get("content", "")
        if content:
            texts.append(content[:2000])
    return texts


def _classify_failure(error_text: str, run_end: dict | None, skill_calls: list) -> str:
    """에러 텍스트와 이벤트 패턴으로 실패 타입 분류"""
    # run_end의 reason 기반 빠른 분류
    if run_end:
        reason = run_end.get("reason", "").lower()
        if "timeout" in reason:
            return "timeout"
        if "blocked" in reason:
            return "hook_blocked"

    # 에러 패턴 매칭
    for pattern, failure_type in _ERROR_PATTERNS.items():
        if re.search(pattern, error_text):
            return failure_type

    # 불완전한 스킬 호출이 있으면 timeout 가능성
    for sc in skill_calls:
        if sc["end"] is None:
            return "timeout"

    return "unknown"


def _extract_root_cause(error_text: str, failure_type: str, affected_skill: str) -> str:
    """에러 텍스트에서 가장 관련성 높은 에러 메시지 추출"""
    if not error_text.strip():
        return f"{affected_skill} 스킬 실행 중 알 수 없는 오류 발생" if affected_skill else "알 수 없는 오류"

    # 가장 관련 높은 에러 줄 찾기
    lines = error_text.strip().split("\n")
    error_keywords = ["error", "exception", "traceback", "failed", "timeout"]

    for line in reversed(lines):
        stripped = line.strip()
        if any(kw in stripped.lower() for kw in error_keywords):
            cause = stripped[:300]
            if affected_skill:
                return f"{affected_skill} 스킬: {cause}"
            return cause

    # 마지막 줄 반환
    last_line = lines[-1].strip()[:300]
    if affected_skill:
        return f"{affected_skill} 스킬 실행 실패: {last_line}"
    return last_line


def _generate_suggestions(failure_type: str, affected_skill: str, error_text: str) -> list[str]:
    """실패 타입에 따른 수정 제안 생성"""
    suggestions = []

    if failure_type == "timeout":
        suggestions.append("타임아웃 설정 증가 검토")
        suggestions.append("네트워크 연결 상태 확인")
        if affected_skill:
            suggestions.append(f"{affected_skill} 스킬의 응답 시간 프로파일링")

    elif failure_type == "skill_error":
        if affected_skill:
            suggestions.append(f"{affected_skill} 스킬의 입력 파라미터 검증")
            suggestions.append(f"{affected_skill} 스킬 단위 테스트 실행")
        if re.search(r"(?i)connect", error_text):
            suggestions.append("외부 API 연결 상태 확인")
        if re.search(r"(?i)(import|module)", error_text):
            suggestions.append("의존성 패키지 설치 확인 (pip install)")

    elif failure_type == "invalid_input":
        suggestions.append("입력 데이터 형식 및 필수 필드 확인")
        suggestions.append("입력 검증 로직 강화")

    elif failure_type == "hook_blocked":
        suggestions.append("Hook 설정 및 권한 확인")
        suggestions.append("HookEventBus 로그에서 차단 원인 확인")

    else:
        suggestions.append("실행 로그를 상세히 검토")
        if affected_skill:
            suggestions.append(f"{affected_skill} 스킬 디버깅 모드로 재실행")

    if not suggestions:
        suggestions.append("로그 파일을 수동으로 검토하십시오")

    return suggestions


def _find_similar_failures(log_dir: str, failure_type: str, exclude_file: str) -> list[dict]:
    """같은 디렉토리의 다른 로그에서 유사한 실패 검색"""
    similar = []
    if not os.path.isdir(log_dir):
        return similar

    pattern = os.path.join(log_dir, "trace_*.jsonl")
    for fpath in glob_mod.glob(pattern):
        if os.path.abspath(fpath) == os.path.abspath(exclude_file):
            continue

        events = _parse_jsonl(fpath)
        # 해당 로그의 에러 텍스트를 수집하여 같은 failure_type인지 판별
        other_run_end = None
        other_skill_end = None
        other_captured = []
        for event in events:
            et = event.get("event_type")
            if et == "run_end" and not event.get("ok", True):
                other_run_end = event
            elif et == "skill_call_end":
                other_skill_end = event
            elif et == "captured_output":
                other_captured.append(event)

        if other_run_end is None:
            continue

        other_error_texts = _collect_error_texts(other_run_end, other_skill_end, other_captured)
        other_combined = "\n".join(other_error_texts)
        other_type = _classify_failure(other_combined, other_run_end, [])

        if other_type == failure_type:
            similar.append({
                "run_id": other_run_end.get("run_id", ""),
                "reason": other_run_end.get("reason", ""),
            })

        if len(similar) >= 5:
            break

    return similar


def _trace_error_chain(skill_calls: list[dict], events: list[dict]) -> list[dict]:
    """에러 체인 분석: 어떤 스킬부터 실패가 시작되었는지 추적"""
    chain = []
    error_kw = {"error", "exception", "failed", "timeout", "refused", "traceback"}

    for sc in skill_calls:
        end_evt = sc.get("end")
        if end_evt is None:
            # 불완전한 호출 = 체인의 일부
            chain.append({
                "skill_name": sc["start"].get("skill_name", "unknown"),
                "status": "incomplete",
            })
            continue

        result_str = str(end_evt.get("skill_result", "")).lower()
        has_error = any(kw in result_str for kw in error_kw)
        if has_error:
            chain.append({
                "skill_name": end_evt.get("skill_name", "unknown"),
                "status": "error",
                "excerpt": str(end_evt.get("skill_result", ""))[:100],
            })

    return chain


def _detect_duration_anomaly(skill_calls: list[dict]) -> dict | None:
    """특정 스킬이 전체 실행 시간의 비정상적 비율을 차지하는지 탐지"""
    durations = []
    for sc in skill_calls:
        start_evt = sc.get("start")
        end_evt = sc.get("end")
        if start_evt and end_evt:
            start_ts = start_evt.get("timestamp", "")
            end_ts = end_evt.get("timestamp", "")
            dur = _calc_duration_between(start_ts, end_ts)
            durations.append((sc["start"].get("skill_name", "unknown"), dur))

    if len(durations) < 2:
        return None

    total = sum(d for _, d in durations)
    if total <= 0:
        return None

    for name, dur in durations:
        ratio = dur / total
        if ratio > 0.8:
            return {
                "skill_name": name,
                "duration_ms": dur,
                "ratio": round(ratio, 2),
                "message": f"{name} consumed {ratio:.0%} of total execution time",
            }

    return None


def _calc_duration_between(start_ts: str, end_ts: str) -> int:
    """두 타임스탬프 간 밀리초 차이"""
    if not start_ts or not end_ts:
        return 0
    from datetime import datetime as _dt
    fmts = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]
    t_start = t_end = None
    for fmt in fmts:
        if t_start is None:
            try:
                t_start = _dt.strptime(start_ts, fmt)
            except ValueError:
                pass
        if t_end is None:
            try:
                t_end = _dt.strptime(end_ts, fmt)
            except ValueError:
                pass
    if t_start and t_end:
        return max(0, int((t_end - t_start).total_seconds() * 1000))
    return 0


def _calc_refined_severity(
    failure_type: str,
    error_chain: list[dict],
    duration_anomaly: dict | None,
    skill_calls: list[dict],
) -> str:
    """에러 체인, duration 이상 등을 반영한 세분화된 심각도 판정"""
    base = _SEVERITY_MAP.get(failure_type, "medium")

    # 에러 체인이 2개 이상이면 연쇄 실패 → 심각도 상승
    if len(error_chain) >= 2 and base in ("medium", "low"):
        base = "high"

    # hook_blocked은 항상 critical
    if failure_type == "hook_blocked":
        return "critical"

    # duration 이상 + 에러 → high 이상
    if duration_anomaly and base == "medium":
        base = "high"

    # 불완전한 호출이 있으면 최소 high
    incomplete = sum(1 for sc in skill_calls if sc.get("end") is None)
    if incomplete > 0 and base in ("medium", "low"):
        base = "high"

    return base
