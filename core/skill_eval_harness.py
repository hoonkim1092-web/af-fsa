from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from core.file_io import read_yaml
from core.skill_feedback import SkillFeedbackLoop
from core.utils import now_iso, safe_id


EVAL_REPORT_FILENAME = "skill-eval-report.json"
EVAL_FILENAMES = ("evals.yml", "evals.yaml")
FEEDBACK_FILENAME = "skill-usage.jsonl"


@dataclass
class EvalCaseResult:
    name: str
    passed: bool
    ok: bool
    duration_ms: float
    error: str = ""
    expected_ok: bool = True
    result_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalPhaseSummary:
    name: str
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    pass_rate: float = 0.0
    avg_duration_ms: float = 0.0
    details: list[EvalCaseResult] = field(default_factory=list)

    def finalize(self) -> None:
        if self.total_cases > 0:
            self.pass_rate = round(self.passed / self.total_cases, 3)
        durations = [item.duration_ms for item in self.details]
        if durations:
            self.avg_duration_ms = round(sum(durations) / len(durations), 2)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["details"] = [item.to_dict() for item in self.details]
        return data


@dataclass
class ShadowEvalSummary:
    baseline_skill_path: str = ""
    total_cases: int = 0
    candidate_passed: int = 0
    baseline_passed: int = 0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    delta: float = 0.0
    replay_cases: int = 0
    replay_human_overrides: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def finalize(self) -> None:
        if self.total_cases > 0:
            self.delta = round((self.wins - self.losses) / self.total_cases, 3)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillEvalReport:
    skill_id: str
    skill_path: str
    evals_path: str = ""
    static_gate: dict[str, Any] = field(default_factory=dict)
    contract_eval: EvalPhaseSummary = field(default_factory=lambda: EvalPhaseSummary(name="contract"))
    hidden_eval: EvalPhaseSummary = field(default_factory=lambda: EvalPhaseSummary(name="hidden"))
    shadow_eval: ShadowEvalSummary = field(default_factory=ShadowEvalSummary)
    recommended_stage: str = "draft"
    report_path: str = ""
    written_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_path": self.skill_path,
            "evals_path": self.evals_path,
            "static_gate": dict(self.static_gate),
            "contract_eval": self.contract_eval.to_dict(),
            "hidden_eval": self.hidden_eval.to_dict(),
            "shadow_eval": self.shadow_eval.to_dict(),
            "recommended_stage": self.recommended_stage,
            "report_path": self.report_path,
            "written_at": self.written_at,
        }


class SkillEvalHarness:
    """Run contract, hidden, and shadow evaluation for a skill package."""

    def __init__(self, *, pass_rate_threshold: float = 0.8, verbose: bool = False):
        self.pass_rate_threshold = float(pass_rate_threshold)
        self.verbose = verbose

    def evaluate(
        self,
        skill_path: str,
        *,
        evals_path: str | None = None,
        baseline_skill_path: str | None = None,
        report_path: str | None = None,
        feedback_path: str | None = None,
        runs_dir: str | None = None,
    ) -> SkillEvalReport:
        skill_path = os.path.abspath(skill_path)
        skill_dir = os.path.dirname(skill_path)
        skill_id = _extract_skill_id(skill_path)
        evals_path = os.path.abspath(evals_path) if evals_path else _discover_evals_path(skill_dir)
        spec = read_yaml(evals_path) if evals_path and os.path.exists(evals_path) else {}

        static_gate, candidate_callable = _load_skill_callable(skill_path)
        contract_cases = _coerce_cases(spec.get("contract"), spec.get("public"), spec.get("cases"))
        hidden_cases = _coerce_cases(spec.get("hidden"))

        shadow_config = spec.get("shadow") if isinstance(spec.get("shadow"), dict) else {}
        shadow_cases = _coerce_cases(shadow_config.get("cases"), spec.get("shadow_cases"))
        resolved_baseline = (
            os.path.abspath(str(baseline_skill_path))
            if baseline_skill_path
            else _resolve_optional_path(skill_dir, shadow_config.get("baseline_skill_path"))
        )
        shadow_replay_cases = _load_shadow_replay_cases(
            skill_dir,
            shadow_config,
            resolved_baseline,
            feedback_path=feedback_path,
            runs_dir=runs_dir,
        )

        contract_eval = self._run_phase("contract", candidate_callable, contract_cases)
        hidden_eval = self._run_phase("hidden", candidate_callable, hidden_cases)
        shadow_eval = self._run_shadow(
            candidate_callable,
            resolved_baseline,
            shadow_cases,
            replay_cases=shadow_replay_cases,
        )

        recommended_stage = self._recommend_stage(static_gate, contract_eval, hidden_eval, shadow_eval)
        output_path = report_path or os.path.join(skill_dir, EVAL_REPORT_FILENAME)
        report = SkillEvalReport(
            skill_id=skill_id,
            skill_path=skill_path,
            evals_path=evals_path or "",
            static_gate=static_gate,
            contract_eval=contract_eval,
            hidden_eval=hidden_eval,
            shadow_eval=shadow_eval,
            recommended_stage=recommended_stage,
            report_path=os.path.abspath(output_path),
            written_at=now_iso(),
        )
        _write_json(report.report_path, report.to_dict())
        return report

    def _run_phase(
        self,
        phase_name: str,
        entrypoint: Callable[[dict[str, Any]], Any] | None,
        cases: list[dict[str, Any]],
    ) -> EvalPhaseSummary:
        summary = EvalPhaseSummary(name=phase_name, total_cases=len(cases))
        if entrypoint is None:
            summary.errors = len(cases)
            summary.failed = len(cases)
            summary.details.extend(
                EvalCaseResult(
                    name=str(case.get("name") or f"{phase_name}-{index + 1}"),
                    passed=False,
                    ok=False,
                    duration_ms=0.0,
                    error="missing_skill_entrypoint",
                )
                for index, case in enumerate(cases)
            )
            summary.finalize()
            return summary

        for index, case in enumerate(cases):
            summary.details.append(_run_eval_case(entrypoint, case, phase_name, index))

        summary.passed = sum(1 for item in summary.details if item.passed)
        summary.failed = sum(1 for item in summary.details if not item.passed)
        summary.errors = sum(1 for item in summary.details if item.error)
        summary.finalize()
        return summary

    def _run_shadow(
        self,
        candidate_callable: Callable[[dict[str, Any]], Any] | None,
        baseline_skill_path: str | None,
        cases: list[dict[str, Any]],
        *,
        replay_cases: list[dict[str, Any]] | None = None,
    ) -> ShadowEvalSummary:
        summary = ShadowEvalSummary(baseline_skill_path=baseline_skill_path or "")
        if candidate_callable is None:
            return summary

        baseline_callable: Callable[[dict[str, Any]], Any] | None = None
        if baseline_skill_path and os.path.exists(baseline_skill_path):
            _baseline_gate, baseline_callable = _load_skill_callable(baseline_skill_path)

        if baseline_callable is not None:
            for index, case in enumerate(cases):
                candidate_result = _run_eval_case(candidate_callable, case, "shadow-candidate", index)
                baseline_result = _run_eval_case(baseline_callable, case, "shadow-baseline", index)
                outcome = "tie"
                if candidate_result.passed and not baseline_result.passed:
                    outcome = "win"
                    summary.wins += 1
                elif baseline_result.passed and not candidate_result.passed:
                    outcome = "loss"
                    summary.losses += 1
                else:
                    summary.ties += 1
                summary.total_cases += 1
                summary.candidate_passed += int(candidate_result.passed)
                summary.baseline_passed += int(baseline_result.passed)
                summary.details.append(
                    {
                        "name": candidate_result.name,
                        "source": "case",
                        "candidate_passed": candidate_result.passed,
                        "baseline_passed": baseline_result.passed,
                        "outcome": outcome,
                    }
                )

            for index, case in enumerate(replay_cases or []):
                replay_case = dict(case)
                replay_case["expect_ok"] = True
                candidate_result = _run_eval_case(candidate_callable, replay_case, "shadow-replay-candidate", index)
                baseline_result = _run_eval_case(baseline_callable, replay_case, "shadow-replay-baseline", index)
                recorded_run_ok = bool(case.get("recorded_run_ok", case.get("baseline_ok", False)))
                approval_rejects = _coerce_int(case.get("approval_rejects"), 0)
                outcome = "tie"
                if candidate_result.passed and not baseline_result.passed:
                    outcome = "win"
                    summary.wins += 1
                elif baseline_result.passed and not candidate_result.passed:
                    outcome = "loss"
                    summary.losses += 1
                else:
                    summary.ties += 1
                summary.total_cases += 1
                summary.replay_cases += 1
                if approval_rejects > 0:
                    summary.replay_human_overrides += 1
                summary.candidate_passed += int(candidate_result.passed)
                summary.baseline_passed += int(baseline_result.passed)
                summary.details.append(
                    {
                        "name": str(case.get("name") or candidate_result.name),
                        "source": "replay",
                        "run_id": str(case.get("run_id") or ""),
                        "task_excerpt": str(case.get("task_excerpt") or ""),
                        "trace_path": str(case.get("trace_path") or ""),
                        "approval_rejects": approval_rejects,
                        "tool_names": list(case.get("tool_names") or []),
                        "candidate_passed": candidate_result.passed,
                        "baseline_passed": baseline_result.passed,
                        "recorded_run_ok": recorded_run_ok,
                        "candidate_error": candidate_result.error,
                        "baseline_error": baseline_result.error,
                        "candidate_result_excerpt": candidate_result.result_excerpt,
                        "baseline_result_excerpt": baseline_result.result_excerpt,
                        "outcome": outcome,
                    }
                )

        summary.finalize()
        return summary

    def _recommend_stage(
        self,
        static_gate: dict[str, Any],
        contract_eval: EvalPhaseSummary,
        hidden_eval: EvalPhaseSummary,
        shadow_eval: ShadowEvalSummary,
    ) -> str:
        if not static_gate.get("ok", False):
            return "draft"

        contract_ok = contract_eval.total_cases == 0 or contract_eval.pass_rate >= self.pass_rate_threshold
        hidden_ok = hidden_eval.total_cases == 0 or hidden_eval.pass_rate >= self.pass_rate_threshold
        if not contract_ok or not hidden_ok:
            return "draft"
        if shadow_eval.total_cases > 0 and shadow_eval.delta >= 0:
            return "canary"
        return "candidate"


def load_eval_report(path: str) -> SkillEvalReport:
    payload = _read_json(path)
    return SkillEvalReport(
        skill_id=str(payload.get("skill_id") or ""),
        skill_path=str(payload.get("skill_path") or ""),
        evals_path=str(payload.get("evals_path") or ""),
        static_gate=payload.get("static_gate") or {},
        contract_eval=_phase_from_payload("contract", payload.get("contract_eval") or {}),
        hidden_eval=_phase_from_payload("hidden", payload.get("hidden_eval") or {}),
        shadow_eval=_shadow_from_payload(payload.get("shadow_eval") or {}),
        recommended_stage=str(payload.get("recommended_stage") or "draft"),
        report_path=str(payload.get("report_path") or os.path.abspath(path)),
        written_at=str(payload.get("written_at") or ""),
    )


def _phase_from_payload(default_name: str, payload: dict[str, Any]) -> EvalPhaseSummary:
    details = [EvalCaseResult(**item) for item in (payload.get("details") or []) if isinstance(item, dict)]
    summary = EvalPhaseSummary(
        name=str(payload.get("name") or default_name),
        total_cases=int(payload.get("total_cases") or 0),
        passed=int(payload.get("passed") or 0),
        failed=int(payload.get("failed") or 0),
        errors=int(payload.get("errors") or 0),
        pass_rate=float(payload.get("pass_rate") or 0.0),
        avg_duration_ms=float(payload.get("avg_duration_ms") or 0.0),
        details=details,
    )
    return summary


def _shadow_from_payload(payload: dict[str, Any]) -> ShadowEvalSummary:
    return ShadowEvalSummary(
        baseline_skill_path=str(payload.get("baseline_skill_path") or ""),
        total_cases=int(payload.get("total_cases") or 0),
        candidate_passed=int(payload.get("candidate_passed") or 0),
        baseline_passed=int(payload.get("baseline_passed") or 0),
        wins=int(payload.get("wins") or 0),
        losses=int(payload.get("losses") or 0),
        ties=int(payload.get("ties") or 0),
        delta=float(payload.get("delta") or 0.0),
        replay_cases=int(payload.get("replay_cases") or 0),
        replay_human_overrides=int(payload.get("replay_human_overrides") or 0),
        details=list(payload.get("details") or []),
    )


def _coerce_cases(*raw_groups: Any) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for raw in raw_groups:
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    cases.append(item)
    return cases


def _load_shadow_replay_cases(
    skill_dir: str,
    shadow_config: dict[str, Any],
    baseline_skill_path: str,
    *,
    feedback_path: str | None = None,
    runs_dir: str | None = None,
) -> list[dict[str, Any]]:
    replay_config = shadow_config.get("replay") if isinstance(shadow_config.get("replay"), dict) else {}
    replay_declared = bool(replay_config)
    if replay_config and "enabled" in replay_config:
        replay_enabled = _is_truthy(replay_config.get("enabled"))
    else:
        replay_enabled = replay_declared or _is_truthy(shadow_config.get("replay_from_feedback"))
    if not replay_enabled or not baseline_skill_path:
        return []

    baseline_skill_id = _extract_skill_id(baseline_skill_path)
    if not baseline_skill_id:
        return []

    resolved_feedback_path = _resolve_shadow_feedback_path(skill_dir, replay_config, feedback_path)
    resolved_runs_dir = _resolve_shadow_runs_dir(skill_dir, replay_config, runs_dir)
    if not resolved_feedback_path or not os.path.exists(resolved_feedback_path):
        return []
    if not resolved_runs_dir or not os.path.isdir(resolved_runs_dir):
        return []

    max_runs = max(0, _coerce_int(replay_config.get("max_runs", replay_config.get("limit", 5)), 5))
    if max_runs == 0:
        return []

    allowed_statuses = {
        str(item).strip().lower()
        for item in (replay_config.get("statuses") or [])
        if str(item).strip()
    }
    feedback_loop = SkillFeedbackLoop(resolved_feedback_path)
    runtime_events = feedback_loop.iter_events(skill_id=baseline_skill_id, event_type="skill_runtime")
    replay_cases: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()

    for event in reversed(runtime_events):
        run_id = str(event.get("run_id") or "").strip()
        status = str(event.get("status") or "").strip().lower()
        if not run_id or run_id in seen_run_ids:
            continue
        if allowed_statuses and status not in allowed_statuses:
            continue
        trace_path = os.path.join(resolved_runs_dir, run_id, "chat_trace.json")
        if not os.path.exists(trace_path):
            continue
        trace_payload = _read_json(trace_path)
        if not trace_payload:
            continue
        replay_cases.append(_build_shadow_replay_case(event, trace_payload, trace_path))
        seen_run_ids.add(run_id)
        if len(replay_cases) >= max_runs:
            break

    return replay_cases


def _resolve_shadow_feedback_path(skill_dir: str, replay_config: dict[str, Any], feedback_path: str | None) -> str:
    if feedback_path:
        return os.path.abspath(str(feedback_path))
    configured_path = _resolve_optional_path(skill_dir, replay_config.get("feedback_path"))
    if configured_path:
        return configured_path
    workspace_root = _infer_workspace_root(skill_dir)
    if workspace_root:
        return os.path.join(workspace_root, "data", FEEDBACK_FILENAME)
    return ""


def _resolve_shadow_runs_dir(skill_dir: str, replay_config: dict[str, Any], runs_dir: str | None) -> str:
    if runs_dir:
        return os.path.abspath(str(runs_dir))
    configured_path = _resolve_optional_path(skill_dir, replay_config.get("runs_dir"))
    if configured_path:
        return configured_path
    workspace_root = _infer_workspace_root(skill_dir)
    if workspace_root:
        return os.path.join(workspace_root, "runs")
    return ""


def _build_shadow_replay_case(event: dict[str, Any], trace_payload: dict[str, Any], trace_path: str) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    result = trace_payload.get("result") if isinstance(trace_payload.get("result"), dict) else {}
    transcript = trace_payload.get("transcript") if isinstance(trace_payload.get("transcript"), list) else []
    run_id = str(event.get("run_id") or trace_payload.get("run_id") or "").strip()
    task_text = str(trace_payload.get("task") or "").strip()
    if not task_text:
        for item in transcript:
            if str(item.get("kind") or "") != "user":
                continue
            item_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            task_text = str(item_payload.get("text") or "").strip()
            if task_text:
                break
    if task_text.lower().startswith("task: "):
        task_text = task_text[6:].strip()

    tool_names: list[str] = []
    for item in transcript:
        if str(item.get("kind") or "") != "tool_call":
            continue
        item_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        tool_name = str(item_payload.get("name") or "").strip()
        if tool_name:
            tool_names.append(tool_name)

    approval_rejects = _coerce_int(result.get("approval_rejects", payload.get("approval_rejects", 0)), 0)
    workspace = str(payload.get("workspace") or _infer_workspace_root(trace_path) or "").strip()
    reason = str(result.get("reason") or payload.get("reason") or "").strip()
    recorded_run_ok = str(event.get("status") or "").strip().lower() == "succeeded"

    return {
        "name": f"shadow-replay-{run_id or 'run'}",
        "ctx": {
            "task": task_text,
            "task_input": task_text,
            "goal": task_text,
            "prompt": task_text,
            "run_id": run_id,
            "workspace": workspace,
            "agent_role": str(trace_payload.get("agent_role") or event.get("agent_role") or ""),
            "mode": "shadow_replay",
            "replay_trace": {
                "trace_path": os.path.abspath(trace_path),
                "tool_names": tool_names,
                "tool_count": len(tool_names),
                "approval_rejects": approval_rejects,
                "recorded_run_ok": recorded_run_ok,
                "reason": reason,
            },
        },
        "recorded_run_ok": recorded_run_ok,
        "run_id": run_id,
        "trace_path": os.path.abspath(trace_path),
        "task_excerpt": _excerpt(task_text, limit=120),
        "approval_rejects": approval_rejects,
        "tool_names": tool_names,
    }


def _resolve_optional_path(base_dir: str, raw_path: Any) -> str:
    path_text = str(raw_path or "").strip()
    if not path_text:
        return ""
    if os.path.isabs(path_text):
        return os.path.abspath(path_text)
    return os.path.abspath(os.path.join(base_dir, path_text))


def _discover_evals_path(skill_dir: str) -> str:
    for filename in EVAL_FILENAMES:
        candidate = os.path.join(skill_dir, filename)
        if os.path.exists(candidate):
            return candidate
    return ""


def _infer_workspace_root(raw_path: str) -> str:
    normalized = os.path.abspath(str(raw_path or ""))
    if not normalized:
        return ""
    for marker in (f"{os.sep}skills{os.sep}", f"{os.sep}runs{os.sep}", f"{os.sep}data{os.sep}"):
        if marker in normalized:
            return normalized.split(marker, 1)[0]
    directory = normalized if os.path.isdir(normalized) else os.path.dirname(normalized)
    if os.path.basename(directory) in {"skills", "runs", "data"}:
        return os.path.dirname(directory)
    parent = os.path.dirname(directory)
    if os.path.basename(parent) in {"skills", "runs", "data"}:
        return os.path.dirname(parent)
    return parent


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


_module_counter = 0


def _load_skill_callable(skill_path: str) -> tuple[dict[str, Any], Callable[[dict[str, Any]], Any] | None]:
    global _module_counter
    _module_counter += 1
    skill_path = os.path.abspath(skill_path)
    gate = {
        "ok": False,
        "entrypoint": "",
        "error": "",
    }
    if not os.path.exists(skill_path):
        gate["error"] = "missing_skill_path"
        return gate, None

    try:
        module_name = f"_skill_eval_{_module_counter}_{os.path.basename(os.path.dirname(skill_path))}"
        spec = importlib.util.spec_from_file_location(module_name, skill_path)
        if spec is None or spec.loader is None:
            gate["error"] = "module_spec_failed"
            return gate, None
        module = importlib.util.module_from_spec(spec)
        skill_dir = os.path.dirname(skill_path)
        parent_dir = os.path.dirname(skill_dir)
        added_paths: list[str] = []
        for candidate_path in (skill_dir, parent_dir):
            if candidate_path and candidate_path not in sys.path:
                sys.path.insert(0, candidate_path)
                added_paths.append(candidate_path)
        try:
            spec.loader.exec_module(module)
        finally:
            for candidate_path in added_paths:
                if candidate_path in sys.path:
                    sys.path.remove(candidate_path)
    except Exception as exc:
        gate["error"] = f"load_failed:{type(exc).__name__}:{exc}"
        return gate, None

    entrypoint = getattr(module, "apply", None) or getattr(module, "test", None)
    if not callable(entrypoint):
        gate["error"] = "missing_apply_or_test"
        return gate, None

    gate["ok"] = True
    gate["entrypoint"] = getattr(entrypoint, "__name__", "")
    return gate, entrypoint


def _run_eval_case(
    entrypoint: Callable[[dict[str, Any]], Any],
    case: dict[str, Any],
    phase_name: str,
    index: int,
) -> EvalCaseResult:
    name = str(case.get("name") or f"{phase_name}-{index + 1}")
    ctx = case.get("ctx")
    if not isinstance(ctx, dict):
        ctx = case.get("input") if isinstance(case.get("input"), dict) else {}
    expected_ok = bool(case.get("expect_ok", case.get("ok", True)))
    expected_subset = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    expected_contains = str(case.get("expect_contains") or "").strip()

    started = time.perf_counter()
    try:
        result = entrypoint(dict(ctx))
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        if not isinstance(result, dict):
            return EvalCaseResult(
                name=name,
                passed=False,
                ok=False,
                duration_ms=duration_ms,
                error=f"invalid_result_type:{type(result).__name__}",
                expected_ok=expected_ok,
                result_excerpt=_excerpt(result),
            )
        if "ok" not in result:
            return EvalCaseResult(
                name=name,
                passed=False,
                ok=False,
                duration_ms=duration_ms,
                error="missing_ok_field",
                expected_ok=expected_ok,
                result_excerpt=_excerpt(result),
            )
        ok = bool(result.get("ok", False))
        matched = ok == expected_ok
        if matched and expected_subset:
            matched = all(result.get(key) == value for key, value in expected_subset.items())
        if matched and expected_contains:
            matched = expected_contains in json.dumps(result, ensure_ascii=False, default=str)
        return EvalCaseResult(
            name=name,
            passed=matched,
            ok=ok,
            duration_ms=duration_ms,
            expected_ok=expected_ok,
            result_excerpt=_excerpt(result),
        )
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return EvalCaseResult(
            name=name,
            passed=False,
            ok=False,
            duration_ms=duration_ms,
            error=f"{type(exc).__name__}:{exc}",
            expected_ok=expected_ok,
        )


def _excerpt(value: Any, limit: int = 180) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value)
    text = text.replace("\n", " ").strip()
    return text[:limit]


def _extract_skill_id(skill_path: str) -> str:
    return safe_id(os.path.basename(os.path.dirname(skill_path)))


def _read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run next-generation skill evaluation harness")
    parser.add_argument("skill_path", help="Path to skill.py")
    parser.add_argument("--evals", dest="evals_path", help="Path to evals.yml/evals.yaml")
    parser.add_argument("--baseline", dest="baseline_skill_path", help="Path to baseline skill.py for shadow eval")
    parser.add_argument("--feedback-log", dest="feedback_path", help="Path to skill-usage.jsonl for shadow replay")
    parser.add_argument("--runs-dir", dest="runs_dir", help="Path to runs directory for shadow replay")
    parser.add_argument("--report", dest="report_path", help="Output path for skill-eval-report.json")
    parser.add_argument("--json", action="store_true", help="Print the final report as JSON")
    args = parser.parse_args(argv)

    harness = SkillEvalHarness()
    report = harness.evaluate(
        args.skill_path,
        evals_path=args.evals_path,
        baseline_skill_path=args.baseline_skill_path,
        report_path=args.report_path,
        feedback_path=args.feedback_path,
        runs_dir=args.runs_dir,
    )

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"[SkillEvalHarness] {report.skill_id} -> {report.recommended_stage} ({report.report_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
