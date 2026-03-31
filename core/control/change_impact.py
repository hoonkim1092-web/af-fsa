"""
core/control/change_impact.py
==============================
ChangeImpactProfiler — 변경 요청의 영향 범위 분석기.

유지보수의 핵심 질문 "무엇이 영향받는가"를 자동으로 파악한다.
git diff + task_input 키워드 + board 아티팩트 매핑을 조합한다.

출력: ImpactProfile
  affected_files:         변경 대상 파일
  affected_modules:       영향받는 module_id
  affected_roles:         영향받는 role
  affected_task_ids:      영향받는 task_id
  evidence_scope:         targeted evidence 수집 디렉토리
  regression_test_scope:  회귀 테스트 대상 패턴
  blast_radius:           "isolated" | "module" | "cross_module" | "system_wide"
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field, asdict


@dataclass
class ImpactProfile:
    affected_files: list[str] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    affected_roles: list[str] = field(default_factory=list)
    affected_task_ids: list[str] = field(default_factory=list)
    evidence_scope: list[str] = field(default_factory=list)
    regression_test_scope: list[str] = field(default_factory=list)
    blast_radius: str = "module"   # "isolated" | "module" | "cross_module" | "system_wide"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ImpactProfile":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def summary(self) -> str:
        return (
            f"blast_radius={self.blast_radius}, "
            f"files={len(self.affected_files)}, "
            f"modules={len(self.affected_modules)}, "
            f"roles={len(self.affected_roles)}"
        )


class ChangeImpactProfiler:
    """변경 요청의 영향 범위를 분석하고 ImpactProfile을 반환한다."""

    # core/ 하위 변경은 시스템 전역 영향으로 간주
    _SYSTEM_WIDE_PATTERNS = ["core/", "core\\"]
    # blast_radius 판정 임계값
    _CROSS_MODULE_MIN_MODULES = 4
    _SYSTEM_WIDE_MIN_FILES = 10

    def profile(
        self,
        task_input: str,
        workspace: str,
        board: dict | None = None,
    ) -> ImpactProfile:
        """
        영향 분석 흐름:
          1. git diff (staged + unstaged)에서 affected_files 추출
          2. task_input에서 파일/모듈 명시 추출 (키워드 매칭)
          3. board.modules[].tasks → 파일 경로 매핑 (artifacts 필드 활용)
          4. affected_files → module 매칭 → role 매칭
          5. blast_radius 판정
        """
        # 1. git diff에서 파일 목록 추출
        git_files = self._get_git_diff_files(workspace)

        # 2. task_input 키워드에서 파일 힌트 추출
        input_files = self._extract_files_from_input(task_input, workspace)

        # 합집합 (중복 제거, 정규화)
        affected_files = self._merge_files(git_files, input_files, workspace)

        # 3 & 4. board 있으면 module/role/task 역추적
        if board:
            affected_modules, affected_roles, affected_task_ids = self._trace_board_impact(
                affected_files, board
            )
        else:
            affected_modules, affected_roles, affected_task_ids = [], [], []

        # 5. blast_radius 판정
        blast_radius = self._compute_blast_radius(affected_files, affected_modules, board)

        # evidence_scope: 영향받는 파일의 디렉토리
        evidence_scope = self._compute_evidence_scope(affected_files, workspace)

        # regression_test_scope: 파일→테스트 매핑
        regression_test_scope = self._compute_test_scope(affected_files, workspace)

        profile = ImpactProfile(
            affected_files=affected_files,
            affected_modules=affected_modules,
            affected_roles=affected_roles,
            affected_task_ids=affected_task_ids,
            evidence_scope=evidence_scope,
            regression_test_scope=regression_test_scope,
            blast_radius=blast_radius,
        )

        # 결과 저장
        self._save(profile, workspace)
        return profile

    # ── 내부 ──

    def _get_git_diff_files(self, workspace: str) -> list[str]:
        """git diff (staged + unstaged)에서 변경된 파일 경로를 추출한다."""
        files: list[str] = []
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, cwd=workspace, timeout=10
            )
            if result.returncode == 0:
                files.extend(
                    line.strip() for line in result.stdout.splitlines() if line.strip()
                )
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--staged"],
                capture_output=True, text=True, cwd=workspace, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    f = line.strip()
                    if f and f not in files:
                        files.append(f)
        except Exception:
            pass
        return files

    def _extract_files_from_input(self, task_input: str, workspace: str) -> list[str]:
        """task_input에서 파일/모듈 경로를 추출한다."""
        files: list[str] = []
        # .py, .yaml, .json, .md 확장자 패턴 매칭
        for match in re.finditer(r"[\w/\\.\-]+\.(?:py|yaml|yml|json|md|txt)", task_input):
            candidate = match.group()
            # workspace 기준으로 실제 존재하는 파일만
            full = os.path.join(workspace, candidate)
            if os.path.isfile(full):
                files.append(candidate)
            elif os.path.isfile(candidate):  # 절대 경로
                files.append(os.path.relpath(candidate, workspace))
        return files

    def _merge_files(self, a: list[str], b: list[str], workspace: str) -> list[str]:
        """두 파일 목록을 합치고 중복을 제거한다."""
        seen: set[str] = set()
        result: list[str] = []
        for f in a + b:
            norm = os.path.normpath(f)
            if norm not in seen:
                seen.add(norm)
                result.append(f)
        return result

    def _trace_board_impact(
        self,
        affected_files: list[str],
        board: dict,
    ) -> tuple[list[str], list[str], list[str]]:
        """board에서 module/role/task_id를 역추적한다."""
        modules: list[str] = []
        roles: list[str] = []
        task_ids: list[str] = []

        for task in (board.get("tasks") or []):
            task_affected = False
            for artifact in (task.get("artifacts") or []):
                if self._path_matches_any(artifact, affected_files):
                    task_affected = True
                    break

            if task_affected:
                tid = task.get("task_id", "")
                if tid and tid not in task_ids:
                    task_ids.append(tid)
                mid = task.get("module_id", "")
                if mid and mid not in modules:
                    modules.append(mid)
                role = task.get("owner_role", "")
                if role and role not in roles:
                    roles.append(role)

        return modules, roles, task_ids

    def _path_matches_any(self, artifact: str, file_list: list[str]) -> bool:
        """artifact 경로가 file_list의 어떤 항목과 매칭되는지 확인한다."""
        art_norm = os.path.normpath(artifact).lower()
        for f in file_list:
            f_norm = os.path.normpath(f).lower()
            if art_norm == f_norm or art_norm.endswith(f_norm) or f_norm.endswith(art_norm):
                return True
        return False

    def _compute_blast_radius(
        self,
        affected_files: list[str],
        affected_modules: list[str],
        board: dict | None,
    ) -> str:
        """blast_radius를 판정한다."""
        file_count = len(affected_files)
        module_count = len(affected_modules)

        # system_wide: core/ 변경 또는 파일 10개 이상
        if file_count >= self._SYSTEM_WIDE_MIN_FILES:
            return "system_wide"
        for f in affected_files:
            norm = f.replace("\\", "/")
            if any(norm.startswith(p) for p in self._SYSTEM_WIDE_PATTERNS):
                return "system_wide"

        # cross_module: 4개 이상 모듈
        if module_count >= self._CROSS_MODULE_MIN_MODULES:
            return "cross_module"

        # 모듈 간 의존성 확인
        if board and module_count >= 2:
            if self._has_cross_dependencies(affected_modules, board):
                return "cross_module"

        # module: 2-3개 모듈
        if module_count >= 2:
            return "module"

        # isolated: 1개 이하 모듈
        return "isolated"

    def _has_cross_dependencies(self, modules: list[str], board: dict) -> bool:
        """모듈 간 depends_on 관계가 있는지 확인한다."""
        module_set = set(modules)
        for mod in (board.get("modules") or []):
            if mod.get("id") in module_set:
                for dep in (mod.get("depends_on") or []):
                    if dep in module_set:
                        return True
        return False

    def _compute_evidence_scope(self, affected_files: list[str], workspace: str) -> list[str]:
        """영향받는 파일의 고유 디렉토리 목록을 반환한다."""
        dirs: list[str] = []
        seen: set[str] = set()
        for f in affected_files:
            d = os.path.dirname(f)
            if d and d not in seen:
                seen.add(d)
                dirs.append(d)
        return dirs

    def _compute_test_scope(self, affected_files: list[str], workspace: str) -> list[str]:
        """영향받는 파일에 대응하는 테스트 파일 패턴을 반환한다."""
        patterns: list[str] = []
        for f in affected_files:
            if not f.endswith(".py"):
                continue
            # core/foo.py → tests/test_foo.py
            base = os.path.basename(f).replace(".py", "")
            pattern = f"tests/test_{base}.py"
            # core/bar/baz.py → tests/test_bar_baz.py
            parts = f.replace("\\", "/").split("/")
            if len(parts) > 1:
                compound = "_".join(p.replace(".py", "") for p in parts[-2:])
                patterns.append(f"tests/test_{compound}.py")
            if pattern not in patterns:
                patterns.append(pattern)
        return patterns

    def _save(self, profile: ImpactProfile, workspace: str) -> None:
        """분석 결과를 .af_runtime/control/change_impact.json에 저장한다."""
        control_dir = os.path.join(workspace, ".af_runtime", "control")
        os.makedirs(control_dir, exist_ok=True)
        path = os.path.join(control_dir, "change_impact.json")
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[ChangeImpactProfiler] save failed: {exc}")


__all__ = ["ImpactProfile", "ChangeImpactProfiler"]
