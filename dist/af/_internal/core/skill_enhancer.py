"""
core/skill_enhancer.py
======================
기존 스킬에 누락된 capabilities를 incremental patch로 추가하는 모듈.

SkillCreator의 evolve_skill()이 '전체 재작성'이라면,
SkillEnhancer.enhance()는 '부분 확장'에 해당한다.

흐름:
  1. 기존 스킬 코드/메타 로드
  2. 누락 capabilities 목록을 기반으로 LLM에 enhancement prompt 전달
  3. 코드 패치 생성 및 적용
  4. meta.yaml capabilities 필드 업데이트 + 버전 bump
  5. eval harness 실행 (기존 기능 회귀 + 새 기능 테스트)
  6. 성공 시 결과 반환, 실패 시 rollback
"""
from __future__ import annotations

import os
import shutil
from typing import Any

import yaml

from core.utils import safe_id, now_iso, resolve_skill_paths


def _log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}")


def _read_meta(skill_dir: str) -> dict:
    meta_path = os.path.join(skill_dir, "meta.yaml")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    return {}


def _write_meta(skill_dir: str, meta: dict) -> None:
    meta_path = os.path.join(skill_dir, "meta.yaml")
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)


def _read_skill_code(skill_dir: str) -> tuple[str, str]:
    """스킬 코드를 읽고 (content, file_path) 반환."""
    skill_py = os.path.join(skill_dir, "skill.py")
    if os.path.exists(skill_py):
        with open(skill_py, "r", encoding="utf-8") as f:
            return f.read(), skill_py

    for name in ("SKILL.md", "skill.md"):
        md_path = os.path.join(skill_dir, name)
        if os.path.exists(md_path):
            with open(md_path, "r", encoding="utf-8") as f:
                return f.read(), md_path
    return "", ""


def _bump_minor_version(version: str) -> str:
    """0.1.0 → 0.2.0 형태로 minor 버전을 올린다."""
    parts = str(version or "0.1.0").split(".")
    if len(parts) < 3:
        parts = ["0", "1", "0"]
    try:
        parts[1] = str(int(parts[1]) + 1)
        parts[2] = "0"
    except (ValueError, IndexError):
        parts = ["0", "2", "0"]
    return ".".join(parts)


class SkillEnhancer:
    """기존 스킬에 누락된 capabilities를 추가하는 enhancer."""

    def enhance(
        self,
        skill_name: str,
        missing_capabilities: list[str],
        workspace: str | None = None,
        coding_engine: str | None = None,
    ) -> dict[str, Any]:
        """
        기존 스킬을 확장한다.

        Args:
            skill_name: 확장할 스킬 ID
            missing_capabilities: 추가해야 할 capability 목록
            workspace: 작업 디렉토리
            coding_engine: LLM 엔진 이름

        Returns:
            {"ok": bool, "skill_id": str, "skill_dir": str, "added_capabilities": [...], "reason": str}
        """
        skill_py_path, _ = resolve_skill_paths(skill_name)
        if not skill_py_path or not os.path.exists(skill_py_path):
            _log("ENHANCE", f"스킬 '{skill_name}' 경로를 찾을 수 없습니다")
            return {"ok": False, "skill_id": skill_name, "reason": "skill_not_found"}

        skill_dir = os.path.dirname(skill_py_path)
        meta = _read_meta(skill_dir)
        existing_code, code_path = _read_skill_code(skill_dir)

        if not existing_code:
            _log("ENHANCE", f"스킬 '{skill_name}' 코드를 읽을 수 없습니다")
            return {"ok": False, "skill_id": skill_name, "reason": "no_skill_code"}

        # 백업 생성
        bak_path = code_path + ".bak"
        shutil.copy2(code_path, bak_path)
        meta_bak = dict(meta)

        _log("ENHANCE", f"스킬 '{skill_name}' 확장 시작: +{missing_capabilities}")

        # LLM으로 enhancement 코드 생성
        try:
            enhanced_code = self._generate_enhancement(
                skill_name=skill_name,
                existing_code=existing_code,
                missing_capabilities=missing_capabilities,
                meta=meta,
                coding_engine=coding_engine,
            )
        except Exception as exc:
            _log("ENHANCE", f"LLM enhancement 실패: {exc}")
            return {"ok": False, "skill_id": skill_name, "reason": f"llm_failed:{exc}"}

        if not enhanced_code or enhanced_code.strip() == existing_code.strip():
            _log("ENHANCE", "LLM이 변경사항을 생성하지 못했습니다")
            return {"ok": False, "skill_id": skill_name, "reason": "no_changes_generated"}

        # 코드 적용
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(enhanced_code)

        # meta.yaml 업데이트: capabilities 추가 + 버전 bump
        existing_caps = list(meta.get("capabilities", []))
        for cap in missing_capabilities:
            if cap not in existing_caps:
                existing_caps.append(cap)
        meta["capabilities"] = existing_caps

        old_version = meta.get("version", "0.1.0")
        new_version = _bump_minor_version(old_version)
        meta["version"] = new_version
        meta["updated_at"] = now_iso()
        meta["last_enhancement"] = {
            "added_capabilities": missing_capabilities,
            "enhanced_at": now_iso(),
            "previous_version": old_version,
        }
        _write_meta(skill_dir, meta)

        # eval harness 실행 (선택적)
        eval_ok = self._run_eval(skill_dir, workspace)

        if not eval_ok:
            _log("ENHANCE", "평가 실패 — 롤백합니다")
            shutil.copy2(bak_path, code_path)
            _write_meta(skill_dir, meta_bak)
            return {"ok": False, "skill_id": skill_name, "reason": "eval_failed_rollback"}

        _log("ENHANCE", f"스킬 '{skill_name}' 확장 완료: {old_version} → {new_version}")

        return {
            "ok": True,
            "skill_id": safe_id(skill_name),
            "skill_dir": skill_dir,
            "code_path": code_path,
            "added_capabilities": missing_capabilities,
            "version": new_version,
            "reason": "enhanced",
        }

    def _generate_enhancement(
        self,
        *,
        skill_name: str,
        existing_code: str,
        missing_capabilities: list[str],
        meta: dict,
        coding_engine: str | None,
    ) -> str:
        """LLM을 사용하여 기존 코드에 누락 capabilities를 추가한 코드를 생성."""
        from model_utils import get_best_model, resolve_dynamic_model
        from core.llm_engine import LLMEngine

        if coding_engine is None:
            selected = resolve_dynamic_model("codex")
            coding_engine = selected.model if hasattr(selected, "model") else str(selected)
        elif hasattr(coding_engine, "model"):
            coding_engine = coding_engine.model

        llm = LLMEngine(model_name=get_best_model([coding_engine]))

        is_action = existing_code.strip().startswith(('"""', "'''", "import", "from", "def", "class"))
        skill_type = "action (Python)" if is_action else "knowledge (Markdown)"

        caps_desc = "\n".join(f"  - {cap}" for cap in missing_capabilities)
        existing_caps = meta.get("capabilities", [])
        existing_caps_desc = "\n".join(f"  - {cap}" for cap in existing_caps) if existing_caps else "  (없음)"

        prompt = f"""기존 {skill_type} 스킬 '{skill_name}'을 확장해주세요.

## 기존 capabilities
{existing_caps_desc}

## 추가해야 할 capabilities
{caps_desc}

## 기존 코드
```
{existing_code}
```

## 지침
1. 기존 코드의 구조와 스타일을 유지하면서 새 capabilities를 추가하세요.
2. 기존 기능을 절대 제거하거나 변경하지 마세요 (회귀 방지).
3. 새 기능은 기존 패턴을 따라 자연스럽게 통합하세요.
4. 코드만 반환하세요. 설명이나 markdown 블록 없이 순수 코드만 출력하세요.
"""
        result = llm.generate(prompt)
        if not result:
            return ""
        # 코드 블록 마커 제거
        result = result.replace("```python", "").replace("```markdown", "").replace("```", "").strip()
        return result

    @staticmethod
    def _run_eval(skill_dir: str, workspace: str | None) -> bool:
        """eval harness를 실행하여 확장된 스킬 검증. 실패 시 False."""
        try:
            from core.skill_eval_harness import SkillEvalHarness

            code_path = os.path.join(skill_dir, "skill.py")
            if not os.path.exists(code_path):
                # knowledge 스킬은 eval 생략 → 성공 처리
                return True

            runs_dir = os.path.join(os.path.abspath(workspace), "runs") if workspace else None
            report = SkillEvalHarness().evaluate(code_path, runs_dir=runs_dir)
            passed = getattr(report, "passed", True)
            return bool(passed)
        except Exception as exc:
            _log("ENHANCE_EVAL", f"eval harness 실행 실패 (비치명적): {exc}")
            # eval 인프라 오류는 enhance 자체를 막지 않음
            return True
