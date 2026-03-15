"""
generate_eval_dataset 스킬 (Phase 4)

여러 JSONL 실행 로그를 분석하여 에이전트 행동 검증을 위한 평가 데이터셋을 자동 생성한다.
각 로그에서 task_input, 스킬 호출, 성공 여부를 추출하여 eval_dataset.jsonl 생성.
"""
import json
import os
import re
import glob as glob_mod
from collections import defaultdict
from datetime import datetime


SKILL_ID = "generate-eval-dataset"


def propose(ctx: dict) -> dict:
    """스킬 실행 가능 여부 확인 및 필요한 입력 명시"""
    return {
        "skill_id": SKILL_ID,
        "required_fields": ["log_dir"],
        "optional_fields": ["min_runs", "success_ratio", "output_file"],
        "constraints": {
            "log_dir": "Directory containing trace_*.jsonl files",
            "min_runs": "int, minimum logs to process (default=1)",
            "success_ratio": "float 0-1, target success ratio in dataset (default=0.7)",
            "output_file": "Output JSONL file path (default=eval_dataset.jsonl)",
        },
    }


def apply(ctx: dict) -> dict:
    """로그 디렉토리를 스캔하여 평가 데이터셋 생성"""
    log_dir = ctx.get("log_dir")
    if not log_dir:
        return {"ok": False, "reason": "missing_log_dir"}

    min_runs = ctx.get("min_runs", 1)
    success_ratio = ctx.get("success_ratio", None)  # None이면 균형 조정 안 함
    output_file = ctx.get("output_file", "eval_dataset.jsonl")

    # 절대경로 또는 workspace 상대경로 지원
    workspace = ctx.get("workspace", ".")
    if not os.path.isabs(log_dir):
        log_dir = os.path.join(workspace, log_dir)
    if not os.path.isabs(output_file):
        output_file = os.path.join(workspace, output_file)

    if not os.path.isdir(log_dir):
        return {"ok": False, "reason": "directory_not_found", "log_dir": log_dir}

    # trace_*.jsonl 파일 스캔
    pattern = os.path.join(log_dir, "trace_*.jsonl")
    log_files = sorted(glob_mod.glob(pattern))

    if len(log_files) < min_runs:
        return {
            "ok": False,
            "reason": "insufficient_logs",
            "found": len(log_files),
            "required": min_runs,
        }

    # 각 로그 파일에서 실행 정보 추출
    runs = []
    for fpath in log_files:
        run_info = _extract_run_info(fpath)
        if run_info:
            runs.append(run_info)

    if not runs:
        return {"ok": False, "reason": "no_valid_runs"}

    # 스킬별 통계 집계
    skill_stats = defaultdict(lambda: {"count": 0, "success": 0, "total_duration_ms": 0})
    success_count = 0
    failure_count = 0

    for run in runs:
        if run["ok"]:
            success_count += 1
        else:
            failure_count += 1

        for sc in run["skill_calls"]:
            name = sc["skill_name"]
            skill_stats[name]["count"] += 1
            skill_stats[name]["total_duration_ms"] += sc.get("duration_ms", 0)
            if run["ok"]:
                skill_stats[name]["success"] += 1

    # 평가 케이스 생성
    eval_cases = []
    for run in runs:
        skill_names = [sc["skill_name"] for sc in run["skill_calls"]]
        if not skill_names:
            skill_names = ["none"]

        # 해당 task_input의 평균 성공률 계산
        task_success_rates = []
        task_durations = []
        for sn in set(skill_names):
            stats = skill_stats.get(sn)
            if stats and stats["count"] > 0:
                task_success_rates.append(stats["success"] / stats["count"])
                task_durations.append(stats["total_duration_ms"] / stats["count"])

        avg_success = sum(task_success_rates) / len(task_success_rates) if task_success_rates else 0
        avg_duration = sum(task_durations) / len(task_durations) if task_durations else 0

        case = {
            "input": run["task_input"][:500],
            "expected_skills": list(set(skill_names)),
            "success_rate": round(avg_success, 2),
            "avg_duration_ms": round(avg_duration),
            "actual_ok": run["ok"],
        }
        eval_cases.append(case)

    # 중복 task_input 제거 (유사 입력 병합)
    eval_cases = _deduplicate_cases(eval_cases)

    # success_ratio가 지정되면 데이터셋 균형 조정
    if success_ratio is not None and 0 < success_ratio < 1 and len(eval_cases) > 1:
        eval_cases = _balance_dataset(eval_cases, success_ratio)

    # Duration 이상치 필터링 (IQR 기반)
    _filter_duration_outliers(eval_cases)

    # JSONL 출력 파일 생성
    try:
        os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            for case in eval_cases:
                f.write(json.dumps(case, ensure_ascii=False) + "\n")
    except Exception as e:
        return {"ok": False, "reason": f"output_write_failed: {e}"}

    # 스킬별 커버리지 계산
    by_skill = {}
    for name, stats in skill_stats.items():
        by_skill[name] = {
            "count": stats["count"],
            "success_rate": round(stats["success"] / stats["count"], 2) if stats["count"] > 0 else 0,
        }

    # 데이터셋 품질 메트릭
    quality = _calc_dataset_quality(eval_cases, by_skill)

    return {
        "ok": True,
        "dataset_file": output_file,
        "total_cases": len(eval_cases),
        "success_cases": success_count,
        "failure_cases": failure_count,
        "coverage": {
            "skills": sorted(skill_stats.keys()),
            "by_skill": by_skill,
        },
        "quality": quality,
    }


def test(ctx: dict) -> dict:
    """스킬 자체 테스트"""
    import tempfile

    tmpdir = tempfile.mkdtemp()

    # 테스트용 로그 파일 3개 생성
    for i, (ok, skill) in enumerate([
        (True, "web-search"),
        (True, "code-gen"),
        (False, "web-search"),
    ]):
        fpath = os.path.join(tmpdir, f"trace_run_{i}.jsonl")
        events = [
            {"timestamp": f"2026-03-13T10:0{i}:00Z", "event_type": "run_start",
             "run_id": f"run_{i}", "agent_name": "test", "task_input": f"task {i}"},
            {"timestamp": f"2026-03-13T10:0{i}:01Z", "event_type": "skill_call_start",
             "run_id": f"run_{i}", "skill_name": skill, "skill_args": {}},
            {"timestamp": f"2026-03-13T10:0{i}:02Z", "event_type": "skill_call_end",
             "run_id": f"run_{i}", "skill_name": skill, "skill_result": "done"},
            {"timestamp": f"2026-03-13T10:0{i}:03Z", "event_type": "run_end",
             "run_id": f"run_{i}", "ok": ok, "reason": "success" if ok else "error",
             "duration_ms": 3000},
        ]
        with open(fpath, "w", encoding="utf-8") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

    output_file = os.path.join(tmpdir, "eval_dataset.jsonl")

    try:
        result = apply({"log_dir": tmpdir, "output_file": output_file})
        assert result["ok"], f"apply failed: {result}"
        assert result["total_cases"] == 3
        assert result["success_cases"] == 2
        assert result["failure_cases"] == 1
        assert os.path.exists(output_file)
        return {"ok": True, "message": "generate_eval_dataset self-test passed"}
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


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


def _extract_run_info(log_file: str) -> dict | None:
    """단일 JSONL 로그에서 실행 정보 추출"""
    events = _parse_jsonl(log_file)
    if not events:
        return None

    run_start = None
    run_end = None
    skill_calls = []
    pending_starts: list[dict] = []

    for event in events:
        et = event.get("event_type")
        if et == "run_start":
            run_start = event
        elif et == "run_end":
            run_end = event
        elif et == "skill_call_start":
            pending_starts.append(event)
        elif et == "skill_call_end":
            skill_name = event.get("skill_name", "unknown")
            start_evt = None
            for idx, ps in enumerate(pending_starts):
                if ps.get("skill_name", "unknown") == skill_name:
                    start_evt = pending_starts.pop(idx)
                    break
            duration_ms = _calc_duration_ms(
                (start_evt or {}).get("timestamp", ""),
                event.get("timestamp", ""),
            )
            skill_calls.append({
                "skill_name": skill_name,
                "duration_ms": duration_ms,
            })

    task_input = (run_start or {}).get("task_input", "")
    ok = run_end.get("ok", False) if run_end else False
    run_id = (run_start or {}).get("run_id", "")
    duration_ms = run_end.get("duration_ms", 0) if run_end else 0

    return {
        "run_id": run_id,
        "task_input": task_input,
        "ok": ok,
        "duration_ms": duration_ms,
        "skill_calls": skill_calls,
    }


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


def _parse_ts(ts: str, formats: list):
    """여러 형식을 시도하여 타임스탬프 파싱"""
    for fmt in formats:
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def _balance_dataset(cases: list[dict], target_ratio: float) -> list[dict]:
    """success_ratio에 맞게 데이터셋 균형 조정 (오버샘플링 없이 다운샘플링만)"""
    success_cases = [c for c in cases if c.get("actual_ok", False)]
    failure_cases = [c for c in cases if not c.get("actual_ok", False)]

    if not success_cases or not failure_cases:
        return cases  # 한쪽이 비어있으면 조정 불가

    total = len(cases)
    desired_success = int(total * target_ratio)
    desired_failure = total - desired_success

    # 부족한 쪽은 전부 사용, 초과하는 쪽을 다운샘플링
    if len(success_cases) > desired_success:
        success_cases = success_cases[:desired_success]
    if len(failure_cases) > desired_failure:
        failure_cases = failure_cases[:desired_failure]

    return success_cases + failure_cases


def _deduplicate_cases(cases: list[dict]) -> list[dict]:
    """유사한 task_input을 가진 케이스 병합 (정규화 후 중복 제거)"""
    seen: dict[str, dict] = {}
    for case in cases:
        # 정규화: 공백/대소문자 통일, 숫자 제거 → 핵심 패턴 비교
        raw_input = case.get("input", "")
        normalized = raw_input.strip().lower()
        # 숫자를 <N>으로 치환하여 "task 1"과 "task 2"를 같은 패턴으로 취급
        normalized = re.sub(r"\d+", "<N>", normalized)

        if normalized in seen:
            # 기존 케이스와 병합: success_rate 평균, 스킬 합집합
            existing = seen[normalized]
            existing["success_rate"] = round(
                (existing["success_rate"] + case.get("success_rate", 0)) / 2, 2
            )
            existing_skills = set(existing.get("expected_skills", []))
            existing_skills.update(case.get("expected_skills", []))
            existing["expected_skills"] = sorted(existing_skills)
            existing["_merge_count"] = existing.get("_merge_count", 1) + 1
        else:
            seen[normalized] = dict(case)

    return list(seen.values())


def _filter_duration_outliers(cases: list[dict]) -> None:
    """IQR 기반으로 비정상 duration에 플래그 추가 (제거하지 않음)"""
    durations = [c.get("avg_duration_ms", 0) for c in cases if c.get("avg_duration_ms", 0) > 0]
    if len(durations) < 4:
        return

    sorted_d = sorted(durations)
    n = len(sorted_d)
    # 선형 보간 사분위수: 정수 나눗셈보다 정확
    q1 = _percentile(sorted_d, 25)
    q3 = _percentile(sorted_d, 75)
    iqr = q3 - q1
    upper_bound = q3 + 1.5 * iqr

    for case in cases:
        dur = case.get("avg_duration_ms", 0)
        if dur > upper_bound > 0:
            case["duration_outlier"] = True


def _percentile(sorted_data: list, pct: float) -> float:
    """정렬된 데이터에서 선형 보간으로 백분위수 계산."""
    n = len(sorted_data)
    if n == 0:
        return 0.0
    k = (n - 1) * pct / 100.0
    floor_k = int(k)
    ceil_k = min(floor_k + 1, n - 1)
    frac = k - floor_k
    return sorted_data[floor_k] * (1 - frac) + sorted_data[ceil_k] * frac


def _calc_dataset_quality(cases: list[dict], by_skill: dict) -> dict:
    """데이터셋 품질 메트릭 산출"""
    if not cases:
        return {"score": 0, "issues": ["no_cases"]}

    issues = []
    scores = []

    # 1. 다양성: 고유 스킬 수 / 전체 케이스 수
    all_skills = set()
    for c in cases:
        all_skills.update(c.get("expected_skills", []))
    diversity = min(1.0, len(all_skills) / max(len(cases), 1))
    scores.append(diversity)
    if diversity < 0.2:
        issues.append("low_skill_diversity")

    # 2. 균형: 성공/실패 비율 (0.5에 가까울수록 좋음)
    ok_count = sum(1 for c in cases if c.get("actual_ok", False))
    if len(cases) > 0:
        balance = 1.0 - abs(0.5 - ok_count / len(cases)) * 2
    else:
        balance = 0
    scores.append(balance)
    if balance < 0.3:
        issues.append("imbalanced_dataset")

    # 3. 커버리지: 모든 스킬에 최소 2개 이상 케이스가 있는지
    low_coverage_skills = [name for name, stat in by_skill.items() if stat.get("count", 0) < 2]
    coverage = (1.0 - len(low_coverage_skills) / len(by_skill)) if by_skill else 0.0
    scores.append(max(0, coverage))
    if low_coverage_skills:
        issues.append(f"low_coverage_skills: {','.join(low_coverage_skills)}")

    overall = round(sum(scores) / len(scores), 2) if scores else 0

    return {
        "score": overall,
        "diversity": round(diversity, 2),
        "balance": round(balance, 2),
        "coverage": round(max(0, coverage), 2),
        "issues": issues if issues else [],
    }
