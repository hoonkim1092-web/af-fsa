"""
core/rubric_compiler.py
=======================
Rulers 패턴 기반 실행 가능한 rubric 컴파일러.

rubrics/*.yaml 정의를 로드해 artifact dict에 대해 채점 실행.
각 차원(dimension)을 독립적으로 평가하고 가중 합산하여 최종 점수 반환.

사용법:
    compiler = RubricCompiler()
    result = compiler.evaluate(artifact, artifact_type="architecture_plan")
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

RUBRICS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rubrics")


@dataclass
class DimensionScore:
    name: str
    score: float        # 0.0 - 1.0 (정규화됨)
    raw_score: int      # 1 / 3 / 5
    weight: float
    evidence_checks_passed: list[str] = field(default_factory=list)
    evidence_checks_failed: list[str] = field(default_factory=list)


@dataclass
class RubricResult:
    artifact_type: str
    rubric_name: str
    total_score: float              # 0.0 - 1.0 (가중 평균)
    status: str                     # "pass" | "pass_with_warnings" | "fail"
    dimension_scores: list[DimensionScore] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def weakest_dimensions(self, n: int = 2) -> list[str]:
        sorted_dims = sorted(self.dimension_scores, key=lambda d: d.score)
        return [d.name for d in sorted_dims[:n]]


class RubricCompiler:
    """rubric YAML을 로드하고 artifact에 대해 채점을 실행한다."""

    def __init__(self, rubrics_dir: str = RUBRICS_DIR):
        self._rubrics_dir = rubrics_dir
        self._cache: dict[str, dict] = {}

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def evaluate(self, artifact: dict, artifact_type: str) -> RubricResult:
        """artifact_type에 맞는 rubric을 찾아 채점하고 RubricResult를 반환."""
        rubric = self._load_rubric_for_type(artifact_type)
        if rubric is None:
            # rubric이 없으면 기본 결과 반환 (pass_with_warnings)
            return RubricResult(
                artifact_type=artifact_type,
                rubric_name="(none)",
                total_score=0.65,
                status="pass_with_warnings",
                warnings=[f"No rubric found for artifact_type='{artifact_type}'"],
            )

        rubric_name = rubric.get("name", artifact_type)
        thresholds = rubric.get("thresholds", {})
        pass_threshold = float(thresholds.get("pass", 4.0))
        warn_threshold = float(thresholds.get("pass_with_warnings", 3.0))
        scoring_cfg = rubric.get("scoring", {})
        max_raw = float(scoring_cfg.get("max_score_per_dimension", 5))

        dimension_scores: list[DimensionScore] = []
        errors: list[str] = []
        warnings: list[str] = []

        for dim_def in (rubric.get("dimensions") or []):
            ds = self._score_dimension(artifact, dim_def, max_raw)
            dimension_scores.append(ds)
            for fail in ds.evidence_checks_failed:
                warnings.append(f"[{ds.name}] evidence_check failed: {fail}")

        # 가중 평균 (raw 5점 척도)
        weighted_sum = sum(d.raw_score * d.weight for d in dimension_scores)
        total_weight = sum(d.weight for d in dimension_scores) or 1.0
        weighted_avg = weighted_sum / total_weight  # 1.0 - 5.0

        # 정규화 (0.0 - 1.0)
        total_score = round((weighted_avg - 1) / (max_raw - 1), 3)  # 1→0.0, 5→1.0
        total_score = max(0.0, min(1.0, total_score))

        # 상태 판정 (원래 1-5 척도 임계값 기준)
        if weighted_avg >= pass_threshold:
            status = "pass"
        elif weighted_avg >= warn_threshold:
            status = "pass_with_warnings"
        else:
            status = "fail"
            errors.append(
                f"Rubric score {weighted_avg:.2f} below pass_with_warnings threshold {warn_threshold}"
            )

        return RubricResult(
            artifact_type=artifact_type,
            rubric_name=rubric_name,
            total_score=total_score,
            status=status,
            dimension_scores=dimension_scores,
            errors=errors,
            warnings=warnings,
        )

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────

    def _load_rubric_for_type(self, artifact_type: str) -> dict | None:
        """artifact_type을 지원하는 첫 번째 rubric YAML을 반환."""
        if artifact_type in self._cache:
            return self._cache[artifact_type]

        if not os.path.isdir(self._rubrics_dir):
            return None

        for fname in sorted(os.listdir(self._rubrics_dir)):
            if not fname.endswith(".yaml"):
                continue
            fpath = os.path.join(self._rubrics_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    rubric = yaml.safe_load(f) or {}
            except Exception:
                continue
            supported = rubric.get("artifact_types") or []
            if artifact_type in supported:
                self._cache[artifact_type] = rubric
                return rubric

        return None

    def _score_dimension(self, artifact: dict, dim_def: dict, max_raw: float) -> DimensionScore:
        """단일 차원을 채점한다."""
        name = str(dim_def.get("name") or "unknown")
        weight = float(dim_def.get("weight") or 0.0)
        checks_passed: list[str] = []
        checks_failed: list[str] = []

        # 프로그래매틱 evidence_check 실행
        checks_ok = True
        for check in (dim_def.get("evidence_checks") or []):
            field_name = check.get("field", "")
            rule = check.get("rule", "")
            desc = check.get("description", field_name)
            value = check.get("value")
            ok = self._run_check(artifact, field_name, rule, value)
            if ok:
                checks_passed.append(desc)
            else:
                checks_failed.append(desc)
                checks_ok = False

        # 점수 결정: evidence_check 실패 비율에 따라 raw_score 조정
        fail_ratio = len(checks_failed) / max(1, len(checks_passed) + len(checks_failed))
        if fail_ratio == 0:
            raw_score = 5
        elif fail_ratio <= 0.34:
            raw_score = 3
        else:
            raw_score = 1

        # 정규화
        normalized = round((raw_score - 1) / (max_raw - 1), 3)
        normalized = max(0.0, min(1.0, normalized))

        return DimensionScore(
            name=name,
            score=normalized,
            raw_score=raw_score,
            weight=weight,
            evidence_checks_passed=checks_passed,
            evidence_checks_failed=checks_failed,
        )

    def _run_check(self, artifact: dict, field_name: str, rule: str, value: Any) -> bool:
        """단일 evidence_check rule을 실행한다."""
        # 중첩 필드 지원 (예: "_claim_map")
        obj = artifact.get(field_name)

        if rule == "not_empty":
            return bool(obj)

        if rule == "min_count":
            if isinstance(obj, (list, dict)):
                return len(obj) >= int(value or 0)
            return False

        if rule == "all_have_owner":
            if not isinstance(obj, list):
                return True  # 필드 없으면 건너뜀
            return all(
                isinstance(item, dict) and bool(item.get("owner") or item.get("owner_role"))
                for item in obj
            )

        if rule == "all_have_mitigation":
            if not isinstance(obj, list):
                return True
            return all(
                isinstance(item, dict) and bool(
                    item.get("mitigation") or item.get("response") or item.get("strategy")
                )
                for item in obj
            )

        if rule == "grounding_ratio_min":
            # claim_map이 없으면 건너뜀 (pass로 처리)
            if not isinstance(obj, dict):
                return True
            claims = obj.get("claims") or []
            if not claims:
                return True
            grounded = sum(1 for c in claims if c.get("status") == "grounded")
            ratio = grounded / len(claims)
            return ratio >= float(value or 0.5)

        # 알 수 없는 rule은 pass로 처리
        return True


__all__ = ["RubricCompiler", "RubricResult", "DimensionScore"]
