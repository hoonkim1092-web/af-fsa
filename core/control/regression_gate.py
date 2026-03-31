"""
core/control/regression_gate.py
=================================
RegressionSafetyGate — 유지보수 후 회귀 안전성을 검증하는 gate.

검증 흐름:
  1. impact_profile.regression_test_scope에 해당하는 테스트 실행
     - pytest discovery: 영향받는 파일에 대응하는 test_*.py 매칭
  2. blast_radius == "system_wide"면 전체 테스트 실행
  3. 테스트 실패 시 pass=False + 실패 목록
  4. 테스트 없으면 warning 태그 + pass=True (테스트 부재는 차단 사유 아님)

매칭 규칙:
  core/foo.py         → tests/test_foo.py
  core/bar/baz.py     → tests/test_bar_baz.py OR tests/bar/test_baz.py
"""
from __future__ import annotations

import os
import subprocess
from typing import Any


class RegressionSafetyGate:
    """유지보수 후 회귀 안전성을 검증하는 gate."""

    # 전체 테스트 실행 시 타임아웃 (초)
    _FULL_SUITE_TIMEOUT = 300
    _TARGETED_TIMEOUT = 120

    def check(
        self,
        workspace: str,
        impact_profile: Any,  # ImpactProfile
        execution_policy: Any = None,  # ExecutionPolicy
    ) -> dict:
        """
        Returns:
          {
            "pass": bool,
            "test_results": dict,
            "warnings": list[str],
            "tests_run": int,
            "tests_failed": int,
            "skipped": bool,
          }
        """
        # execution_policy가 regression test를 요구하지 않으면 스킵
        if execution_policy is not None:
            if hasattr(execution_policy, "requires_regression_test"):
                if not execution_policy.requires_regression_test:
                    return {
                        "pass": True,
                        "skipped": True,
                        "reason": "policy does not require regression test",
                        "test_results": {},
                        "warnings": [],
                        "tests_run": 0,
                        "tests_failed": 0,
                    }

        blast_radius = getattr(impact_profile, "blast_radius", "module") if impact_profile else "module"
        affected_files = getattr(impact_profile, "affected_files", []) if impact_profile else []
        test_scope = getattr(impact_profile, "regression_test_scope", []) if impact_profile else []

        # blast_radius가 system_wide이면 전체 테스트 실행
        if blast_radius == "system_wide":
            return self._run_full_suite(workspace)

        # 대상 테스트 파일 수집
        test_files = self._collect_test_files(workspace, test_scope, affected_files)

        if not test_files:
            return {
                "pass": True,
                "skipped": False,
                "test_results": {},
                "warnings": [
                    f"no test files found for {len(affected_files)} affected file(s) — "
                    "regression coverage may be incomplete"
                ],
                "tests_run": 0,
                "tests_failed": 0,
            }

        return self._run_targeted(workspace, test_files)

    def _discover_tests(self, affected_files: list[str], workspace: str) -> list[str]:
        """
        affected_files에 대응하는 테스트 파일을 찾는다.

        매칭 규칙:
          core/foo.py         → tests/test_foo.py
          core/bar/baz.py     → tests/test_bar_baz.py OR tests/bar/test_baz.py
        """
        found: list[str] = []
        seen: set[str] = set()

        for filepath in affected_files:
            if not filepath.endswith(".py"):
                continue
            norm = filepath.replace("\\", "/")
            parts = norm.split("/")
            base = parts[-1].replace(".py", "")

            candidates = [
                f"tests/test_{base}.py",
            ]
            # compound: core/bar/baz.py → tests/test_bar_baz.py
            if len(parts) >= 2:
                compound = "_".join(p.replace(".py", "") for p in parts[-2:])
                candidates.append(f"tests/test_{compound}.py")
                # subdirectory: tests/bar/test_baz.py
                if len(parts) >= 2:
                    subdir = "/".join(parts[:-1][-1:])
                    candidates.append(f"tests/{subdir}/test_{base}.py")

            for candidate in candidates:
                full = os.path.join(workspace, candidate)
                if os.path.isfile(full) and candidate not in seen:
                    seen.add(candidate)
                    found.append(candidate)

        return found

    def _collect_test_files(
        self,
        workspace: str,
        test_scope: list[str],
        affected_files: list[str],
    ) -> list[str]:
        """
        test_scope (ImpactProfile 제안) + discover_tests로 테스트 파일을 수집한다.
        """
        found: set[str] = set()

        # ImpactProfile이 제안한 패턴
        for pattern in test_scope:
            full = os.path.join(workspace, pattern)
            if os.path.isfile(full):
                found.add(pattern)

        # 자체 discovery
        for path in self._discover_tests(affected_files, workspace):
            found.add(path)

        return sorted(found)

    def _run_targeted(self, workspace: str, test_files: list[str]) -> dict:
        """지정된 테스트 파일들을 실행한다."""
        args = ["python", "-m", "pytest", "--tb=short", "-q"] + test_files
        return self._run_pytest(workspace, args, self._TARGETED_TIMEOUT)

    def _run_full_suite(self, workspace: str) -> dict:
        """전체 테스트 스위트를 실행한다."""
        tests_dir = os.path.join(workspace, "tests")
        if not os.path.isdir(tests_dir):
            return {
                "pass": True,
                "skipped": False,
                "test_results": {},
                "warnings": ["tests/ directory not found — skipping full suite"],
                "tests_run": 0,
                "tests_failed": 0,
            }
        args = ["python", "-m", "pytest", "--tb=short", "-q", "tests/"]
        return self._run_pytest(workspace, args, self._FULL_SUITE_TIMEOUT)

    def _run_pytest(self, workspace: str, args: list[str], timeout: int) -> dict:
        """pytest를 실행하고 결과를 파싱한다."""
        try:
            result = subprocess.run(
                args,
                capture_output=True, text=True,
                cwd=workspace, timeout=timeout,
            )
            passed, failed, warnings = self._parse_pytest_output(result.stdout + result.stderr)

            return {
                "pass": result.returncode == 0,
                "skipped": False,
                "test_results": {
                    "returncode": result.returncode,
                    "stdout": result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout,
                    "stderr": result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr,
                },
                "warnings": warnings,
                "tests_run": passed + failed,
                "tests_failed": failed,
            }
        except subprocess.TimeoutExpired:
            return {
                "pass": False,
                "skipped": False,
                "test_results": {"error": f"pytest timed out after {timeout}s"},
                "warnings": [f"pytest timed out after {timeout}s"],
                "tests_run": 0,
                "tests_failed": 0,
            }
        except FileNotFoundError:
            return {
                "pass": True,
                "skipped": True,
                "reason": "pytest not available",
                "test_results": {},
                "warnings": ["pytest not found — skipping regression check"],
                "tests_run": 0,
                "tests_failed": 0,
            }
        except Exception as exc:
            return {
                "pass": False,
                "skipped": False,
                "test_results": {"error": str(exc)},
                "warnings": [f"pytest execution error: {exc}"],
                "tests_run": 0,
                "tests_failed": 0,
            }

    def _parse_pytest_output(self, output: str) -> tuple[int, int, list[str]]:
        """pytest 출력에서 passed/failed 수와 경고를 파싱한다."""
        import re

        passed = 0
        failed = 0
        warnings: list[str] = []

        # 예: "5 passed, 2 failed in 3.14s"
        summary_match = re.search(
            r"(\d+)\s+passed|(\d+)\s+failed|(\d+)\s+warning",
            output,
        )
        for m in re.finditer(r"(\d+)\s+(passed|failed|warning)", output):
            count = int(m.group(1))
            kind = m.group(2)
            if kind == "passed":
                passed = count
            elif kind == "failed":
                failed = count
            elif kind == "warning":
                warnings.append(f"{count} pytest warning(s)")

        return passed, failed, warnings


__all__ = ["RegressionSafetyGate"]
