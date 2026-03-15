"""
Pre-flight Evaluator (Phase 5b)

스킬을 레지스트리에 active로 등록하기 전에 N번 반복 실행하여
신뢰도 점수를 매기는 독립된 오프라인 Evals 도구.

점수 체계:
  - reliability: 성공률 (pass_count / total_runs)
  - performance: 평균 실행 시간 (ms)
  - consistency: 결과 구조 일관성 (출력 키셋 동일 비율)
  - overall: 가중 평균 (reliability * 0.6 + consistency * 0.3 + speed_score * 0.1)

등록 게이트:
  - overall >= 0.8 → "active" (자동 승격)
  - overall >= 0.5 → "candidate" (수동 검토 필요)
  - overall <  0.5 → "rejected" (등록 거부)
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict


# ---------------------------------------------------------------------------
# Score thresholds
# ---------------------------------------------------------------------------
THRESHOLD_ACTIVE = float(os.getenv("PREFLIGHT_THRESHOLD_ACTIVE", "0.8"))
THRESHOLD_CANDIDATE = float(os.getenv("PREFLIGHT_THRESHOLD_CANDIDATE", "0.5"))
DEFAULT_RUNS = int(os.getenv("PREFLIGHT_DEFAULT_RUNS", "10"))


@dataclass
class PreflightResult:
    """단일 스킬 pre-flight 평가 결과"""
    skill_id: str
    skill_path: str
    total_runs: int = 0
    pass_count: int = 0
    fail_count: int = 0
    error_count: int = 0
    avg_duration_ms: float = 0.0
    min_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    stddev_duration_ms: float = 0.0
    reliability: float = 0.0
    consistency: float = 0.0
    stability: float = 0.0  # duration 안정성 (stddev 기반)
    speed_score: float = 0.0
    overall: float = 0.0
    status: str = "pending"  # active | candidate | rejected | error
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    run_details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # run_details는 요약만
        d["run_details"] = d["run_details"][:3]  # 처음 3개만 저장
        # warnings가 비어있으면 제거
        if not d.get("warnings"):
            d.pop("warnings", None)
        return d


class PreflightEvaluator:
    """
    스킬 pre-flight 평가기.

    사용법:
        evaluator = PreflightEvaluator()
        result = evaluator.evaluate("skills/evaluator/trace_execution/skill.py", runs=10)
        print(result.overall, result.status)
    """

    def __init__(
        self,
        runs: int = DEFAULT_RUNS,
        verbose: bool = False,
        threshold_active: float = THRESHOLD_ACTIVE,
        threshold_candidate: float = THRESHOLD_CANDIDATE,
    ):
        self.runs = runs
        self.verbose = verbose
        self.threshold_active = threshold_active
        self.threshold_candidate = threshold_candidate

    def evaluate(self, skill_path: str, runs: int | None = None, ctx: dict | None = None) -> PreflightResult:
        """
        스킬을 N번 실행하여 신뢰도 점수를 산출한다.

        Args:
            skill_path: skill.py 파일 경로 (절대 또는 상대)
            runs: 실행 횟수 (기본: self.runs)
            ctx: test() 함수에 전달할 컨텍스트 (기본: {})

        Returns:
            PreflightResult 인스턴스
        """
        num_runs = runs or self.runs
        ctx = ctx or {}

        # 스킬 모듈 로드
        skill_path = os.path.abspath(skill_path)
        result = PreflightResult(
            skill_id=_extract_skill_id(skill_path),
            skill_path=skill_path,
            total_runs=num_runs,
        )

        module = _load_skill_module(skill_path)
        if module is None:
            result.status = "error"
            result.errors.append(f"Failed to load skill module: {skill_path}")
            return result

        # test() 또는 apply() 함수 찾기
        test_fn = getattr(module, "test", None)
        apply_fn = getattr(module, "apply", None)

        if test_fn is None and apply_fn is None:
            result.status = "error"
            result.errors.append("Skill has no test() or apply() function")
            return result

        # test()가 있으면 test() 사용, 없으면 apply() 사용
        target_fn = test_fn if test_fn is not None else apply_fn
        fn_name = "test" if test_fn is not None else "apply"

        if self.verbose:
            print(f"[Preflight] Evaluating {result.skill_id} via {fn_name}() x {num_runs}")

        # N번 실행
        durations = []
        output_key_sets = []

        for i in range(num_runs):
            run_detail = {"run": i + 1, "ok": False, "duration_ms": 0, "error": None}

            t_start = time.perf_counter()
            try:
                run_result = target_fn(dict(ctx))
                duration_ms = (time.perf_counter() - t_start) * 1000
                run_detail["duration_ms"] = round(duration_ms, 2)
                durations.append(duration_ms)

                # 성공 여부 판정
                if isinstance(run_result, dict):
                    ok = run_result.get("ok", False)
                    output_key_sets.append(frozenset(run_result.keys()))
                else:
                    ok = bool(run_result)

                if ok:
                    result.pass_count += 1
                    run_detail["ok"] = True
                else:
                    result.fail_count += 1
                    run_detail["error"] = str(run_result.get("reason", "returned ok=False")) if isinstance(run_result, dict) else "falsy return"

            except Exception as e:
                duration_ms = (time.perf_counter() - t_start) * 1000
                run_detail["duration_ms"] = round(duration_ms, 2)
                run_detail["error"] = f"{type(e).__name__}: {e}"
                result.error_count += 1
                result.errors.append(f"Run {i+1}: {type(e).__name__}: {e}")
                durations.append(duration_ms)

            result.run_details.append(run_detail)

            if self.verbose:
                status_mark = "PASS" if run_detail["ok"] else "FAIL"
                print(f"  Run {i+1}/{num_runs}: {status_mark} ({run_detail['duration_ms']:.0f}ms)")

        # 점수 계산
        result.reliability = result.pass_count / num_runs if num_runs > 0 else 0
        result.consistency = _calc_consistency(output_key_sets)

        if durations:
            result.avg_duration_ms = round(sum(durations) / len(durations), 2)
            result.min_duration_ms = round(min(durations), 2)
            result.max_duration_ms = round(max(durations), 2)
            result.stddev_duration_ms = round(_calc_stddev(durations), 2)
            # speed_score: 100ms 이하 = 1.0, 5000ms 이상 = 0.0
            result.speed_score = round(max(0.0, min(1.0, 1.0 - (result.avg_duration_ms - 100) / 4900)), 2)
            # stability: stddev/avg 비율 (CoV) 기반, CoV 0 = 1.0, CoV >= 1.0 = 0.0
            if result.avg_duration_ms > 0:
                cov = result.stddev_duration_ms / result.avg_duration_ms
                result.stability = round(max(0.0, min(1.0, 1.0 - cov)), 2)
            else:
                result.stability = 1.0

        # Duration 트렌드 경고: 후반부가 전반부보다 지속적으로 느려지면 메모리 누수 가능성
        if len(durations) >= 6:
            half = len(durations) // 2
            first_half_avg = sum(durations[:half]) / half
            second_half_avg = sum(durations[half:]) / (len(durations) - half)
            if first_half_avg > 0 and second_half_avg / first_half_avg > 1.5:
                result.warnings.append(
                    f"Duration trend: second half {second_half_avg:.0f}ms vs first half "
                    f"{first_half_avg:.0f}ms (>{1.5}x, possible memory leak)"
                )

        result.overall = round(
            result.reliability * 0.5 + result.consistency * 0.25 + result.stability * 0.15 + result.speed_score * 0.1,
            3,
        )

        # 상태 결정
        if result.overall >= self.threshold_active:
            result.status = "active"
        elif result.overall >= self.threshold_candidate:
            result.status = "candidate"
        else:
            result.status = "rejected"

        if self.verbose:
            print(f"\n[Preflight] {result.skill_id}: {result.status.upper()} "
                  f"(overall={result.overall}, reliability={result.reliability}, "
                  f"consistency={result.consistency}, speed={result.speed_score})")

        return result

    def evaluate_and_gate(
        self,
        skill_path: str,
        runs: int | None = None,
        ctx: dict | None = None,
        auto_promote: bool = True,
    ) -> PreflightResult:
        """
        평가 후 registry.yaml 상태를 자동 업데이트한다.

        Args:
            auto_promote: True이면 threshold 충족 시 자동으로 status 변경
        """
        result = self.evaluate(skill_path, runs=runs, ctx=ctx)

        if auto_promote:
            self._update_registry_status(result)

        return result

    def _update_registry_status(self, result: PreflightResult) -> None:
        """registry.yaml에서 스킬 상태를 업데이트.

        글로벌(SKILLS_DIR)과 프로젝트 로컬(PROJECT_SKILLS_DIR) 양쪽 레지스트리를
        탐색하여 스킬 ID가 매칭되는 곳을 업데이트한다.
        """
        try:
            from core.config_paths import SKILLS_DIR, PROJECT_SKILLS_DIR

            # 글로벌 → 프로젝트 로컬 순서로 탐색
            candidates = []
            for base_dir in [SKILLS_DIR, PROJECT_SKILLS_DIR]:
                if base_dir:
                    rp = os.path.join(base_dir, "registry.yaml")
                    if os.path.exists(rp) and rp not in candidates:
                        candidates.append(rp)

            if not candidates:
                return

            import yaml
            # skill_id 매칭: 원본 ID와 _/- 변환 모두 시도
            skill_id = result.skill_id
            alt_id = skill_id.replace("-", "_")  # underscore 기반 ID도 매칭 시도

            updated = False
            for registry_path in candidates:
                with open(registry_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                skills = data.get("skills", {})
                # 정확한 매칭 또는 _/- 변환 매칭
                skill_entry = skills.get(skill_id) or skills.get(alt_id)
                if skill_entry is None:
                    continue

                skill_entry["status"] = result.status
                skill_entry["preflight_score"] = result.overall
                skill_entry["preflight_reliability"] = result.reliability
                skill_entry["preflight_runs"] = result.total_runs

                from core.utils import now_iso
                skill_entry["updated_at"] = now_iso()

                with open(registry_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=True)

                updated = True
                if self.verbose:
                    print(f"[Preflight] Registry updated: {skill_id} → {result.status} ({registry_path})")
                break  # 첫 번째 매칭된 레지스트리만 업데이트

            if not updated and self.verbose:
                print(f"[Preflight] Skill '{skill_id}' not found in any registry")

        except Exception as e:
            if self.verbose:
                print(f"[Preflight] Registry update failed: {e}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def cli_main(argv: list[str] | None = None):
    """Pre-flight Evals CLI 엔트리포인트"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Pre-flight Skill Evaluator - 스킬 신뢰도 사전 검증",
        prog="run_factory_cli.py preflight",
    )
    parser.add_argument("skill_path", type=str, help="skill.py 파일 경로")
    parser.add_argument("-n", "--runs", type=int, default=DEFAULT_RUNS,
                        help=f"실행 횟수 (기본: {DEFAULT_RUNS})")
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 출력")
    parser.add_argument("--no-promote", action="store_true",
                        help="registry.yaml 자동 업데이트 비활성화")
    parser.add_argument("--threshold-active", type=float, default=THRESHOLD_ACTIVE,
                        help=f"active 승격 임계값 (기본: {THRESHOLD_ACTIVE})")
    parser.add_argument("--threshold-candidate", type=float, default=THRESHOLD_CANDIDATE,
                        help=f"candidate 임계값 (기본: {THRESHOLD_CANDIDATE})")
    parser.add_argument("--json", action="store_true", help="JSON 형식 출력")

    args = parser.parse_args(argv)

    evaluator = PreflightEvaluator(
        runs=args.runs,
        verbose=args.verbose,
        threshold_active=args.threshold_active,
        threshold_candidate=args.threshold_candidate,
    )
    result = evaluator.evaluate_and_gate(
        args.skill_path,
        runs=args.runs,
        auto_promote=not args.no_promote,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(result)

    # 종료 코드: active=0, candidate=1, rejected=2, error=3
    exit_codes = {"active": 0, "candidate": 1, "rejected": 2, "error": 3}
    sys.exit(exit_codes.get(result.status, 3))


def _print_report(result: PreflightResult):
    """결과를 사람이 읽기 좋은 형식으로 출력"""
    W = 56
    sep = "=" * W

    status_emoji = {
        "active": "PASS",
        "candidate": "WARN",
        "rejected": "FAIL",
        "error": "ERR ",
    }

    print(f"\n{sep}")
    print(f"  Pre-flight Evaluation Report")
    print(sep)
    print(f"  Skill:        {result.skill_id}")
    print(f"  Path:         {result.skill_path}")
    print(f"  Status:       [{status_emoji.get(result.status, '????')}] {result.status.upper()}")
    print(f"{sep}")
    print(f"  Total Runs:   {result.total_runs}")
    print(f"  Passed:       {result.pass_count}")
    print(f"  Failed:       {result.fail_count}")
    print(f"  Errors:       {result.error_count}")
    print(f"{'-' * W}")
    print(f"  Reliability:  {result.reliability:.1%}")
    print(f"  Consistency:  {result.consistency:.1%}")
    print(f"  Stability:    {result.stability:.1%}")
    print(f"  Speed Score:  {result.speed_score:.1%}")
    print(f"  Overall:      {result.overall:.1%}")
    print(f"{'-' * W}")
    print(f"  Avg Duration: {result.avg_duration_ms:.0f}ms")
    print(f"  Std Duration: {result.stddev_duration_ms:.0f}ms")
    print(f"  Min Duration: {result.min_duration_ms:.0f}ms")
    print(f"  Max Duration: {result.max_duration_ms:.0f}ms")

    if result.warnings:
        print(f"{'-' * W}")
        print(f"  Warnings ({len(result.warnings)}):")
        for warn in result.warnings[:5]:
            print(f"    - {warn[:80]}")

    if result.errors:
        print(f"{'-' * W}")
        print(f"  Errors ({len(result.errors)}):")
        for err in result.errors[:5]:
            print(f"    - {err[:80]}")

    print(sep)
    print()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_module_counter = 0

def _load_skill_module(skill_path: str):
    """skill.py를 동적으로 로드"""
    global _module_counter
    _module_counter += 1
    try:
        module_name = f"_preflight_skill_{_module_counter}_{os.path.basename(os.path.dirname(skill_path))}"
        spec = importlib.util.spec_from_file_location(module_name, skill_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)

        # 스킬 디렉토리를 sys.path에 임시 추가
        skill_dir = os.path.dirname(skill_path)
        parent_dir = os.path.dirname(skill_dir)
        added_paths = []
        for p in [skill_dir, parent_dir]:
            if p not in sys.path:
                sys.path.insert(0, p)
                added_paths.append(p)

        try:
            spec.loader.exec_module(module)
        finally:
            for p in added_paths:
                if p in sys.path:
                    sys.path.remove(p)

        return module
    except Exception:
        return None


def _extract_skill_id(skill_path: str) -> str:
    """skill.py 경로에서 skill_id 추출"""
    # skills/evaluator/trace_execution/skill.py → trace-execution
    parent = os.path.basename(os.path.dirname(skill_path))
    return parent.replace("_", "-")


def _calc_consistency(key_sets: list[frozenset]) -> float:
    """출력 키셋의 일관성 비율 계산"""
    if len(key_sets) < 2:
        return 1.0

    # 가장 빈번한 키셋과 비교
    from collections import Counter
    counter = Counter(key_sets)
    most_common_count = counter.most_common(1)[0][1]
    return round(most_common_count / len(key_sets), 2)


def _calc_stddev(values: list[float]) -> float:
    """표준편차 계산 (math.stdev 없이)"""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance ** 0.5
