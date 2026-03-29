"""
core/skill_creator.py
=====================
Claude Code의 Skill Creator와 동일한 방식으로 스킬을 생성하는 모듈.

지원하는 스킬 형식:
  - Knowledge Skill (SKILL.md 기반) — Claude Code 호환
  - Action Skill (skill.py + meta.yaml) — agent-factory 기존 형식

주요 함수:
  - create_skill(): 대화형/CLI 스킬 생성
  - init_skill_dir(): 스킬 디렉토리 초기화
  - validate_skill(): 스킬 구조 검증
  - generate_skill_content(): LLM으로 SKILL.md 내용 생성
"""

import os
import re
import json
import time
import shutil
import datetime

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_SKILL_NAME_LENGTH = 64
ALLOWED_RESOURCES = {"scripts", "references", "assets"}

SKILL_MD_TEMPLATE = """\
---
name: {name}
description: "{description}"
---

# {title}

{body}
"""

ACTION_SKILL_TEMPLATE = '''\
"""
{title} — Action Skill
========================
자동 생성된 액션 스킬.
"""


def propose(ctx):
    """스킬 메타데이터를 반환합니다."""
    return {{
        "skill_id": "{skill_id}",
        "description": "{description}",
        "required_keys": [],
        "optional_keys": [],
    }}


def apply(ctx):
    """스킬 주요 로직을 실행합니다."""
    # TODO: 구현 필요
    return {{"ok": True, "result": "not_implemented"}}


def test(ctx):
    """스킬 테스트를 실행합니다."""
    r = apply(ctx or {{}})
    return {{"ok": bool(r.get("ok")), "result": r}}
'''

META_YAML_TEMPLATE = {
    "type": "action",
    "status": "active",
    "version": "0.1.0",
    "capabilities": [],
    "last_test_ok": False,
}


# ---------------------------------------------------------------------------
# Name Helpers
# ---------------------------------------------------------------------------
def normalize_skill_name(name: str) -> str:
    """스킬 이름을 hyphen-case로 정규화합니다."""
    normalized = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized


def _title_case(name: str) -> str:
    return " ".join(word.capitalize() for word in name.split("-"))


def _safe_id(name: str) -> str:
    t = name.strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_skill(skill_path: str) -> tuple[bool, str]:
    """스킬 디렉토리 구조를 검증합니다. (SKILL.md 또는 skill.py 기반)"""
    if not os.path.isdir(skill_path):
        return False, f"디렉토리가 존재하지 않습니다: {skill_path}"

    skill_md = os.path.join(skill_path, "SKILL.md")
    skill_py = os.path.join(skill_path, "skill.py")

    # Knowledge Skill (SKILL.md) 검증
    if os.path.exists(skill_md):
        return _validate_skill_md(skill_md)

    # Action Skill (skill.py) 검증
    if os.path.exists(skill_py):
        return _validate_skill_py(skill_py, skill_path)

    # skill.md (소문자) 도 허용
    skill_md_lower = os.path.join(skill_path, "skill.md")
    if os.path.exists(skill_md_lower):
        return _validate_skill_md(skill_md_lower)

    return False, "SKILL.md 또는 skill.py 가 없습니다."


def _validate_skill_md(path: str) -> tuple[bool, str]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        return False, "YAML frontmatter가 없습니다 (--- 로 시작해야 합니다)"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "frontmatter 형식이 올바르지 않습니다"

    try:
        frontmatter = yaml.safe_load(match.group(1))
        if not isinstance(frontmatter, dict):
            return False, "frontmatter는 YAML dict 여야 합니다"
    except yaml.YAMLError as e:
        return False, f"YAML 파싱 오류: {e}"

    allowed = {"name", "description", "license", "allowed-tools", "allowed_tools", "approval-required-tools", "approval_required_tools", "disable-model-invocation", "disable_model_invocation", "model-invocable", "model_invocable", "user-invocable", "user_invocable", "planner-invocable", "planner_invocable", "argument-hint", "argument_hint", "model", "context", "context_mode", "agent", "hooks", "hook", "metadata"}
    unexpected = set(frontmatter.keys()) - allowed
    if unexpected:
        return False, f"허용되지 않는 frontmatter 키: {', '.join(sorted(unexpected))}"

    if "name" not in frontmatter:
        return False, "frontmatter에 'name' 이 없습니다"
    if "description" not in frontmatter:
        return False, "frontmatter에 'description' 이 없습니다"

    name = str(frontmatter["name"]).strip()
    if name and not re.match(r"^[a-z0-9-]+$", name):
        return False, f"이름 '{name}' 은 hyphen-case 여야 합니다 (소문자, 숫자, 하이픈만)"

    if name and len(name) > MAX_SKILL_NAME_LENGTH:
        return False, f"이름이 너무 깁니다 ({len(name)}자, 최대 {MAX_SKILL_NAME_LENGTH}자)"

    desc = str(frontmatter.get("description", "")).strip()
    if desc and len(desc) > 1024:
        return False, f"description 이 너무 깁니다 ({len(desc)}자, 최대 1024자)"

    return True, "스킬 검증 통과!"


def _validate_skill_py(py_path: str, skill_dir: str) -> tuple[bool, str]:
    with open(py_path, "r", encoding="utf-8") as f:
        code = f.read()
    required_funcs = ["propose", "apply", "test"]
    missing = [fn for fn in required_funcs if f"def {fn}(" not in code]
    if missing:
        return False, f"skill.py 에 필수 함수가 없습니다: {', '.join(missing)}"

    meta_path = os.path.join(skill_dir, "meta.yaml")
    if not os.path.exists(meta_path):
        return False, "meta.yaml 이 없습니다"

    return True, "액션 스킬 검증 통과!"


# ---------------------------------------------------------------------------
# Skill Directory Initialization
# ---------------------------------------------------------------------------
def init_skill_dir(
    name: str,
    output_dir: str,
    skill_type: str = "knowledge",
    resources: list[str] | None = None,
    description: str = "",
    examples: bool = False,
) -> str | None:
    """
    스킬 디렉토리를 초기화합니다.

    Args:
        name: 스킬 이름 (hyphen-case 로 자동 정규화)
        output_dir: 스킬 디렉토리가 생성될 상위 경로
        skill_type: "knowledge" (SKILL.md) 또는 "action" (skill.py)
        resources: 생성할 리소스 디렉토리 목록 ["scripts", "references", "assets"]
        description: 스킬 설명
        examples: 예제 파일 포함 여부

    Returns:
        생성된 스킬 디렉토리 경로, 실패 시 None
    """
    skill_name = normalize_skill_name(name)
    if not skill_name:
        print("[ERROR] 스킬 이름에 문자 또는 숫자가 포함되어야 합니다.")
        return None

    if len(skill_name) > MAX_SKILL_NAME_LENGTH:
        print(f"[ERROR] 스킬 이름이 너무 깁니다 ({len(skill_name)}자, 최대 {MAX_SKILL_NAME_LENGTH}자)")
        return None

    skill_dir = os.path.join(output_dir, skill_name)
    if os.path.exists(skill_dir):
        print(f"[ERROR] 이미 존재하는 디렉토리: {skill_dir}")
        return None

    os.makedirs(skill_dir, exist_ok=True)
    title = _title_case(skill_name)
    skill_id = _safe_id(skill_name)
    now = datetime.datetime.now().isoformat()

    if skill_type == "knowledge":
        # SKILL.md 생성
        desc = description or f"TODO - {title} 스킬의 기능과 사용 시점을 설명합니다."
        body = _knowledge_body_template(title)
        content = SKILL_MD_TEMPLATE.format(
            name=skill_name,
            description=desc,
            title=title,
            body=body,
        )
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] SKILL.md 생성됨: {skill_md_path}")

    else:
        # Action Skill
        desc = description or f"{title} 액션 스킬"
        code = ACTION_SKILL_TEMPLATE.format(
            title=title,
            skill_id=skill_id,
            description=desc,
        )
        py_path = os.path.join(skill_dir, "skill.py")
        with open(py_path, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"[OK] skill.py 생성됨: {py_path}")

        meta = {
            **META_YAML_TEMPLATE,
            "id": skill_id,
            "name": skill_name,
            "capabilities": [skill_name],
            "created_at": now,
            "updated_at": now,
        }
        meta_path = os.path.join(skill_dir, "meta.yaml")
        with open(meta_path, "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)
        print(f"[OK] meta.yaml 생성됨: {meta_path}")

    # 리소스 디렉토리
    for res in (resources or []):
        if res not in ALLOWED_RESOURCES:
            print(f"[WARN] 알 수 없는 리소스 타입 무시: {res}")
            continue
        res_dir = os.path.join(skill_dir, res)
        os.makedirs(res_dir, exist_ok=True)
        if examples:
            _create_example_file(res_dir, res, skill_name, title)
        print(f"[OK] {res}/ 디렉토리 생성됨")

    print(f"\n[OK] 스킬 '{skill_name}' 초기화 완료: {skill_dir}")
    return skill_dir


def _knowledge_body_template(title: str) -> str:
    return f"""\
## 개요

[TODO] {title} 스킬이 제공하는 기능을 1-2문장으로 설명합니다.

## 워크플로우

[TODO] 이 스킬의 주요 워크플로우를 작성합니다.

### 단계 1: 준비

[TODO] 첫 번째 단계를 설명합니다.

### 단계 2: 실행

[TODO] 두 번째 단계를 설명합니다.

### 단계 3: 검증

[TODO] 결과를 검증하는 방법을 설명합니다.
"""


def _create_example_file(res_dir: str, res_type: str, skill_name: str, title: str):
    if res_type == "scripts":
        path = os.path.join(res_dir, "example.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'#!/usr/bin/env python3\n"""{title} 예제 스크립트"""\n\ndef main():\n    print("{skill_name} 예제 스크립트")\n\nif __name__ == "__main__":\n    main()\n')
    elif res_type == "references":
        path = os.path.join(res_dir, "reference.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title} 참조 문서\n\n[TODO] 상세 참조 문서를 작성합니다.\n")
    elif res_type == "assets":
        path = os.path.join(res_dir, "README.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{title} 에셋 디렉토리\n\n템플릿, 이미지, 폰트 등 출력에 사용되는 파일을 여기에 저장합니다.\n")


# ---------------------------------------------------------------------------
# LLM-Powered Skill Content Generation
# ---------------------------------------------------------------------------
def generate_skill_content(
    skill_name: str,
    role: str = "",
    context: str = "",
    skill_type: str = "knowledge",
    coding_engine: str | None = None,
    existing_content: str = "",
    feedback: str = "",
) -> str | None:
    """
    LLM으로 스킬 내용을 생성합니다.

    Args:
        skill_name: 스킬 이름
        role: 에이전트 역할
        context: 추가 컨텍스트 (사용 예시, 도메인 정보 등)
        skill_type: "knowledge" 또는 "action"
        coding_engine: LLM 모델 이름 (None 이면 자동 선택)
        existing_content: 기존 스킬 파일 내용 (update/evolve 시 사용)
        feedback: 피드백 텍스트 (evolve 시 사용)

    Returns:
        생성된 스킬 파일 내용 (str), 실패 시 None
    """
    try:
        from model_utils import get_best_model, resolve_dynamic_model
        from core.llm_engine import LLMEngine
    except ImportError:
        print("[ERROR] LLM 엔진을 로드할 수 없습니다. model_utils 또는 core.llm_engine 모듈을 확인하세요.")
        return None

    if coding_engine is None:
        try:
            sel = resolve_dynamic_model("codex")
            coding_engine = sel.model if hasattr(sel, "model") else str(sel)
        except Exception:
            coding_engine = "gemini-2.0-flash"

    llm = LLMEngine(model_name=get_best_model([coding_engine]))
    title = _title_case(normalize_skill_name(skill_name))
    skill_id = _safe_id(skill_name)

    # 기존 콘텐츠/피드백이 있으면 진화 모드 프롬프트 구성
    evolution_block = ""
    if existing_content:
        evolution_block += f"\n\n--- EXISTING CONTENT (improve this) ---\n{existing_content}\n--- END EXISTING CONTENT ---\n"
    if feedback:
        evolution_block += f"\n--- FEEDBACK / ERROR LOG ---\n{feedback}\n--- END FEEDBACK ---\n"

    evolution_instruction = ""
    if existing_content or feedback:
        evolution_instruction = (
            "\nIMPORTANT: You are IMPROVING existing content, not creating from scratch. "
            "Preserve the overall structure but refine quality, fix issues mentioned in feedback, "
            "and improve description for better router matching.\n"
        )

    if skill_type == "knowledge":
        prompt = f"""\
Create a Knowledge Skill (SKILL.md format) for '{skill_name}'.
Role: {role or 'General'}
Context: {context or 'N/A'}
{evolution_instruction}
Requirements:
- Start with YAML frontmatter: name (hyphen-case) and description (when to use this skill)
- Description must explain WHAT the skill does AND WHEN to use it
- Body should contain procedural knowledge, workflows, checklists
- Use Korean for the body content
- Follow Claude Code SKILL.md format with Progressive Disclosure
- Keep SKILL.md under 500 lines
- Use imperative/infinitive form
{evolution_block}
Return ONLY the markdown content including frontmatter.
"""
    else:
        prompt = f"""\
Generate a Python action skill module '{skill_id}.py'.
Role: {role or 'General'}
Context: {context or 'N/A'}
{evolution_instruction}
Requirements:
- Implement exactly three functions: propose(ctx)->dict, apply(ctx)->dict, test(ctx)->dict
- propose() returns skill metadata (skill_id, description, required_keys, optional_keys)
- apply() implements the main logic using ctx["data_dir"] and ctx["artifacts_dir"]
- test() calls apply() and verifies the result
- Code docstrings in Korean
- No os, sys, subprocess, shutil, importlib, eval, exec usage
{evolution_block}
Return ONLY the Python code.
"""

    try:
        content = llm.generate(prompt)
        content = content.replace("```python", "").replace("```markdown", "").replace("```", "").strip()
        return content
    except Exception as e:
        print(f"[ERROR] LLM 생성 실패: {e}")
        return None


# ---------------------------------------------------------------------------
# High-Level Create Skill (Init + Generate + Validate)
# ---------------------------------------------------------------------------
def create_skill(
    name: str,
    output_dir: str,
    skill_type: str = "knowledge",
    role: str = "",
    context: str = "",
    resources: list[str] | None = None,
    use_llm: bool = False,
    coding_engine: str | None = None,
) -> str | None:
    """
    스킬을 생성합니다. (디렉토리 초기화 + 선택적 LLM 콘텐츠 생성 + 검증)

    Claude Code의 Skill Creator와 동일한 6단계 프로세스:
      1. 스킬 이해 (name, context)
      2. 리소스 계획 (resources)
      3. 초기화 (init_skill_dir)
      4. 콘텐츠 작성 (generate or template)
      5. 검증 (validate_skill)
      6. 반복 개선 (사용자가 수동으로)

    Args:
        name: 스킬 이름
        output_dir: 출력 디렉토리
        skill_type: "knowledge" 또는 "action"
        role: 에이전트 역할
        context: 추가 컨텍스트
        resources: 리소스 디렉토리 목록
        use_llm: LLM으로 콘텐츠 자동 생성 여부
        coding_engine: LLM 엔진 (None=자동 선택)

    Returns:
        생성된 스킬 디렉토리 경로, 실패 시 None
    """
    skill_name = normalize_skill_name(name)
    print(f"\n{'='*60}")
    print(f"  Skill Creator — '{skill_name}'")
    print(f"  Type: {skill_type} | LLM: {'ON' if use_llm else 'OFF'}")
    print(f"{'='*60}\n")

    # Step 3: 초기화
    print("[Step 1/3] 스킬 디렉토리 초기화...")
    skill_dir = init_skill_dir(
        name=skill_name,
        output_dir=output_dir,
        skill_type=skill_type,
        resources=resources,
        description=context[:200] if context else "",
        examples=False,
    )
    if not skill_dir:
        return None

    # Step 4: LLM 콘텐츠 생성 (선택)
    if use_llm:
        print("\n[Step 2/3] LLM으로 스킬 콘텐츠 생성 중...")
        content = generate_skill_content(
            skill_name=skill_name,
            role=role,
            context=context,
            skill_type=skill_type,
            coding_engine=coding_engine,
        )
        if content:
            if skill_type == "knowledge":
                target = os.path.join(skill_dir, "SKILL.md")
            else:
                target = os.path.join(skill_dir, "skill.py")
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[OK] LLM 생성 콘텐츠 저장: {target}")
        else:
            print("[WARN] LLM 생성 실패, 템플릿을 유지합니다.")
    else:
        print("\n[Step 2/3] 템플릿 사용 (LLM 없이)")

    # Step 5: 검증
    print("\n[Step 3/3] 스킬 검증 중...")
    ok, msg = validate_skill(skill_dir)
    if ok:
        print(f"[OK] {msg}")
    else:
        print(f"[WARN] 검증 실패: {msg}")
        print("  → SKILL.md 또는 skill.py를 편집하여 문제를 수정하세요.")

    # 완료 메시지
    print(f"\n{'='*60}")
    print(f"  스킬 생성 완료: {skill_dir}")
    print(f"{'='*60}")
    print("\n다음 단계:")
    print(f"  1. 편집: {skill_dir}")
    if skill_type == "knowledge":
        print("  2. SKILL.md의 [TODO] 항목을 완성하세요")
    else:
        print("  2. skill.py의 apply() 함수를 구현하세요")
    print(f"  3. 검증: python -m core.skill_creator validate {skill_dir}")
    print("  4. 실제 사용 후 반복 개선하세요")

    return skill_dir


# ---------------------------------------------------------------------------
# Version Bump Helper
# ---------------------------------------------------------------------------
def _bump_minor_version(version: str) -> str:
    """0.1.0 → 0.2.0 형태로 minor 버전을 올립니다."""
    parts = str(version or "0.1.0").split(".")
    if len(parts) < 3:
        parts = ["0", "1", "0"]
    try:
        parts[1] = str(int(parts[1]) + 1)
        parts[2] = "0"
    except (ValueError, IndexError):
        parts = ["0", "2", "0"]
    return ".".join(parts)


def _detect_skill_type(skill_dir: str) -> str:
    """스킬 디렉토리의 스킬 타입을 감지합니다."""
    if os.path.exists(os.path.join(skill_dir, "skill.py")):
        return "action"
    return "knowledge"


def _read_skill_file(skill_dir: str, skill_type: str) -> str:
    """스킬 디렉토리의 주요 파일 내용을 읽습니다."""
    if skill_type == "action":
        path = os.path.join(skill_dir, "skill.py")
    else:
        path = os.path.join(skill_dir, "SKILL.md")
        if not os.path.exists(path):
            path = os.path.join(skill_dir, "skill.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def _read_meta(skill_dir: str) -> dict:
    """meta.yaml를 읽습니다. 없으면 빈 dict."""
    meta_path = os.path.join(skill_dir, "meta.yaml")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    return {}


def _write_meta(skill_dir: str, meta: dict):
    """meta.yaml를 씁니다."""
    meta_path = os.path.join(skill_dir, "meta.yaml")
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)


def _skill_name_from_dir(skill_dir: str) -> str:
    """디렉토리 이름에서 스킬 이름을 추출합니다."""
    return os.path.basename(os.path.normpath(skill_dir))


# ---------------------------------------------------------------------------
# Update Skill
# ---------------------------------------------------------------------------
def update_skill(
    skill_dir: str,
    use_llm: bool = False,
    coding_engine: str | None = None,
) -> bool:
    """
    기존 스킬을 업데이트합니다.

    - LLM 사용 시: 기존 내용을 LLM에 보내 개선 요청, .bak 백업 후 덮어쓰기
    - LLM 없이: 버전 bump + validate만 수행

    Args:
        skill_dir: 스킬 디렉토리 경로
        use_llm: LLM으로 콘텐츠 개선 여부
        coding_engine: LLM 엔진 이름

    Returns:
        성공 여부
    """
    if not os.path.isdir(skill_dir):
        print(f"[ERROR] 디렉토리가 존재하지 않습니다: {skill_dir}")
        return False

    skill_type = _detect_skill_type(skill_dir)
    skill_name = _skill_name_from_dir(skill_dir)
    meta = _read_meta(skill_dir)
    now = datetime.datetime.now().isoformat()

    print(f"\n[update] 스킬 업데이트: {skill_name} (type={skill_type}, llm={'ON' if use_llm else 'OFF'})")

    if use_llm:
        existing = _read_skill_file(skill_dir, skill_type)
        if not existing:
            print("[ERROR] 스킬 파일을 읽을 수 없습니다.")
            return False

        # .bak 백업
        if skill_type == "action":
            src = os.path.join(skill_dir, "skill.py")
        else:
            src = os.path.join(skill_dir, "SKILL.md")
            if not os.path.exists(src):
                src = os.path.join(skill_dir, "skill.md")
        bak = src + ".bak"
        shutil.copy2(src, bak)
        print(f"[OK] 백업 생성: {bak}")

        # LLM 개선 요청
        content = generate_skill_content(
            skill_name=skill_name,
            skill_type=skill_type,
            coding_engine=coding_engine,
            existing_content=existing,
            feedback="기존 내용을 개선하고 품질을 높여주세요. description을 라우터 매칭에 최적화하세요.",
        )
        if content:
            with open(src, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[OK] LLM 개선 콘텐츠 저장: {src}")
        else:
            print("[WARN] LLM 생성 실패, 기존 내용을 유지합니다.")
            # 백업 복원
            shutil.copy2(bak, src)

    # 버전 bump
    old_version = meta.get("version", "0.1.0")
    new_version = _bump_minor_version(old_version)
    meta["version"] = new_version
    meta["updated_at"] = now
    if skill_type == "action":
        _write_meta(skill_dir, meta)
        print(f"[OK] 버전 bump: {old_version} → {new_version}")

    # 검증
    ok, msg = validate_skill(skill_dir)
    if ok:
        print(f"[OK] 검증 통과: {msg}")
    else:
        print(f"[WARN] 검증 실패: {msg}")
    return ok


# ---------------------------------------------------------------------------
# Evolve Skill
# ---------------------------------------------------------------------------
def evolve_skill(
    skill_dir: str,
    feedback: str = "",
    error_log: str = "",
    coding_engine: str | None = None,
) -> bool:
    """
    피드백/에러 로그를 기반으로 스킬을 진화시킵니다.

    - knowledge 스킬: description + body 개선 (라우터 튜닝)
    - action 스킬: apply() 로직 개선

    Args:
        skill_dir: 스킬 디렉토리 경로
        feedback: 피드백 텍스트
        error_log: 에러 로그 텍스트
        coding_engine: LLM 엔진 이름

    Returns:
        성공 여부
    """
    if not os.path.isdir(skill_dir):
        print(f"[ERROR] 디렉토리가 존재하지 않습니다: {skill_dir}")
        return False

    combined_feedback = ""
    if feedback:
        combined_feedback += f"[User Feedback]\n{feedback}\n"
    if error_log:
        combined_feedback += f"[Error Log]\n{error_log}\n"

    if not combined_feedback.strip():
        print("[ERROR] feedback 또는 error_log가 필요합니다.")
        return False

    skill_type = _detect_skill_type(skill_dir)
    skill_name = _skill_name_from_dir(skill_dir)
    existing = _read_skill_file(skill_dir, skill_type)

    if not existing:
        print("[ERROR] 스킬 파일을 읽을 수 없습니다.")
        return False

    print(f"\n[evolve] 스킬 진화: {skill_name} (type={skill_type})")

    # .bak 백업
    if skill_type == "action":
        src = os.path.join(skill_dir, "skill.py")
    else:
        src = os.path.join(skill_dir, "SKILL.md")
        if not os.path.exists(src):
            src = os.path.join(skill_dir, "skill.md")
    bak = src + ".bak"
    shutil.copy2(src, bak)
    print(f"[OK] 백업 생성: {bak}")

    # LLM 진화 요청
    content = generate_skill_content(
        skill_name=skill_name,
        skill_type=skill_type,
        coding_engine=coding_engine,
        existing_content=existing,
        feedback=combined_feedback,
    )
    if not content:
        print("[ERROR] LLM 생성 실패, 기존 내용을 유지합니다.")
        shutil.copy2(bak, src)
        return False

    with open(src, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] 진화 콘텐츠 저장: {src}")

    # 버전 bump
    meta = _read_meta(skill_dir)
    old_version = meta.get("version", "0.1.0")
    new_version = _bump_minor_version(old_version)
    meta["version"] = new_version
    meta["updated_at"] = datetime.datetime.now().isoformat()
    if skill_type == "action":
        _write_meta(skill_dir, meta)
    print(f"[OK] 버전 bump: {old_version} → {new_version}")

    # 검증
    ok, msg = validate_skill(skill_dir)
    if ok:
        print(f"[OK] 검증 통과: {msg}")
    else:
        print(f"[WARN] 검증 실패: {msg}")
    return ok


# ---------------------------------------------------------------------------
# Retire Skill
# ---------------------------------------------------------------------------
def retire_skill(skill_dir: str) -> bool:
    """
    스킬을 아카이브(은퇴) 상태로 변경합니다.

    - meta.yaml의 status를 'archived'로, retired_at 타임스탬프 추가
    - skill-lock.yaml 동기화

    Args:
        skill_dir: 스킬 디렉토리 경로

    Returns:
        성공 여부
    """
    if not os.path.isdir(skill_dir):
        print(f"[ERROR] 디렉토리가 존재하지 않습니다: {skill_dir}")
        return False

    skill_name = _skill_name_from_dir(skill_dir)
    skill_type = _detect_skill_type(skill_dir)
    now = datetime.datetime.now().isoformat()

    print(f"\n[retire] 스킬 은퇴: {skill_name}")

    # meta.yaml 업데이트 (없으면 생성)
    meta = _read_meta(skill_dir)
    meta["status"] = "archived"
    meta["retired_at"] = now
    meta.setdefault("name", skill_name)
    meta.setdefault("type", skill_type)
    meta.setdefault("version", "0.1.0")
    _write_meta(skill_dir, meta)
    print(f"[OK] meta.yaml status → archived")

    # skill-lock.yaml 동기화
    try:
        from core.utils import lock_skill_state
        skill_id = _safe_id(skill_name)
        lock_skill_state(skill_id, {
            "status": "archived",
            "retired_at": now,
            "version": meta.get("version", "0.1.0"),
        })
        print(f"[OK] skill-lock.yaml 동기화 완료")
    except Exception as e:
        print(f"[WARN] skill-lock 동기화 실패: {e}")

    return True


# ---------------------------------------------------------------------------
# Benchmark Skill
# ---------------------------------------------------------------------------
def benchmark_skill(skill_dir: str) -> dict:
    """
    액션 스킬의 test()를 격리 실행하고 벤치마크 결과를 저장합니다.

    - action 스킬만 지원 (knowledge 스킬은 스킵)
    - run_isolated()로 skill.py의 test() 실행
    - benchmark.json에 결과 저장
    - 이전 벤치마크 결과와 비교하여 regression 경고

    Args:
        skill_dir: 스킬 디렉토리 경로

    Returns:
        벤치마크 결과 dict
    """
    if not os.path.isdir(skill_dir):
        print(f"[ERROR] 디렉토리가 존재하지 않습니다: {skill_dir}")
        return {"ok": False, "reason": "dir_not_found"}

    skill_type = _detect_skill_type(skill_dir)
    skill_name = _skill_name_from_dir(skill_dir)

    if skill_type != "action":
        print(f"[SKIP] knowledge 스킬은 벤치마크를 지원하지 않습니다: {skill_name}")
        return {"ok": True, "reason": "knowledge_skill_skipped"}

    skill_py = os.path.join(skill_dir, "skill.py")
    if not os.path.exists(skill_py):
        print(f"[ERROR] skill.py가 존재하지 않습니다: {skill_py}")
        return {"ok": False, "reason": "skill_py_not_found"}

    print(f"\n[benchmark] 스킬 벤치마크: {skill_name}")

    # 격리 실행
    try:
        from core.security_guard import run_isolated
    except ImportError:
        print("[ERROR] core.security_guard 모듈을 로드할 수 없습니다.")
        return {"ok": False, "reason": "import_error"}

    start_time = time.time()
    ok, test_result, stderr = run_isolated(skill_py)
    duration = round(time.time() - start_time, 3)

    result = {
        "timestamp": datetime.datetime.now().isoformat(),
        "skill_name": skill_name,
        "ok": ok,
        "test_result": test_result,
        "duration_sec": duration,
    }
    if stderr and stderr.strip():
        result["stderr"] = stderr.strip()

    print(f"[{'OK' if ok else 'FAIL'}] test() → ok={ok}, duration={duration}s")

    # benchmark.json 저장 (이전 결과 보존)
    bench_path = os.path.join(skill_dir, "benchmark.json")
    history = []
    if os.path.exists(bench_path):
        try:
            with open(bench_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                history = data
            elif isinstance(data, dict):
                history = [data]
        except (json.JSONDecodeError, OSError):
            pass

    # regression 경고
    if history:
        prev = history[-1]
        if prev.get("ok") and not ok:
            print(f"[WARN] REGRESSION 감지! 이전 벤치마크는 ok=True 였으나 현재 ok=False")
        prev_dur = prev.get("duration_sec", 0)
        if prev_dur > 0 and duration > prev_dur * 2:
            print(f"[WARN] 성능 저하: 이전 {prev_dur}s → 현재 {duration}s (2배 이상 느려짐)")

    history.append(result)
    with open(bench_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"[OK] 벤치마크 저장: {bench_path}")

    return result


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def cli_main(argv: list[str] | None = None):
    """
    스킬 생성 CLI.

    Usage:
        python -m core.skill_creator create <name> [options]
        python -m core.skill_creator validate <path>
        python -m core.skill_creator init <name> --path <dir> [options]
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Agent Factory Skill Creator - Claude Code Style",
        prog="skill-creator",
    )
    subparsers = parser.add_subparsers(dest="command", help="명령")

    # --- create ---
    p_create = subparsers.add_parser("create", help="스킬 생성 (초기화 + 콘텐츠 생성 + 검증)")
    p_create.add_argument("name", help="스킬 이름 (hyphen-case로 자동 변환)")
    p_create.add_argument("--path", "-p", default=None, help="출력 디렉토리 (기본: skills/forge)")
    p_create.add_argument("--type", "-t", choices=["knowledge", "action"], default="knowledge", help="스킬 타입")
    p_create.add_argument("--role", "-r", default="", help="에이전트 역할")
    p_create.add_argument("--context", "-c", default="", help="스킬 용도/컨텍스트 설명")
    p_create.add_argument("--resources", default="", help="리소스 디렉토리 (scripts,references,assets)")
    p_create.add_argument("--llm", action="store_true", help="LLM으로 콘텐츠 자동 생성")
    p_create.add_argument("--engine", default=None, help="LLM 엔진 이름")

    # --- init ---
    p_init = subparsers.add_parser("init", help="스킬 디렉토리 초기화 (템플릿만)")
    p_init.add_argument("name", help="스킬 이름")
    p_init.add_argument("--path", "-p", required=True, help="출력 디렉토리")
    p_init.add_argument("--type", "-t", choices=["knowledge", "action"], default="knowledge", help="스킬 타입")
    p_init.add_argument("--resources", default="", help="리소스 디렉토리 (scripts,references,assets)")
    p_init.add_argument("--examples", action="store_true", help="예제 파일 포함")
    p_init.add_argument("--description", "-d", default="", help="스킬 설명")

    # --- validate ---
    p_validate = subparsers.add_parser("validate", help="스킬 구조 검증")
    p_validate.add_argument("path", help="스킬 디렉토리 경로")

    # --- generate ---
    p_gen = subparsers.add_parser("generate", help="LLM으로 스킬 콘텐츠만 생성 (기존 디렉토리에 덮어쓰기)")
    p_gen.add_argument("name", help="스킬 이름")
    p_gen.add_argument("--path", "-p", required=True, help="기존 스킬 디렉토리 경로")
    p_gen.add_argument("--type", "-t", choices=["knowledge", "action"], default="knowledge", help="스킬 타입")
    p_gen.add_argument("--role", "-r", default="", help="에이전트 역할")
    p_gen.add_argument("--context", "-c", default="", help="추가 컨텍스트")
    p_gen.add_argument("--engine", default=None, help="LLM 엔진 이름")

    # --- update ---
    p_update = subparsers.add_parser("update", help="스킬 업데이트 (버전 bump + 선택적 LLM 개선)")
    p_update.add_argument("path", help="스킬 디렉토리 경로")
    p_update.add_argument("--llm", action="store_true", help="LLM으로 콘텐츠 개선")
    p_update.add_argument("--engine", default=None, help="LLM 엔진 이름")

    # --- evolve ---
    p_evolve = subparsers.add_parser("evolve", help="피드백 기반 스킬 진화")
    p_evolve.add_argument("path", help="스킬 디렉토리 경로")
    p_evolve.add_argument("--feedback", default="", help="피드백 텍스트")
    p_evolve.add_argument("--feedback-file", default="", help="피드백 파일 경로")
    p_evolve.add_argument("--error-log", default="", help="에러 로그 텍스트")
    p_evolve.add_argument("--engine", default=None, help="LLM 엔진 이름")

    # --- retire ---
    p_retire = subparsers.add_parser("retire", help="스킬 아카이브(은퇴)")
    p_retire.add_argument("path", help="스킬 디렉토리 경로")

    # --- benchmark ---
    p_bench = subparsers.add_parser("benchmark", help="액션 스킬 벤치마크")
    p_bench.add_argument("path", help="스킬 디렉토리 경로")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    if args.command == "create":
        resources = [r.strip() for r in args.resources.split(",") if r.strip()] if args.resources else None
        output_dir = args.path
        if not output_dir:
            # 기본: skills/forge
            base = os.getenv("AGENT_PROJECT_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_dir = os.path.join(base, "skills", "forge")
        os.makedirs(output_dir, exist_ok=True)
        create_skill(
            name=args.name,
            output_dir=output_dir,
            skill_type=args.type,
            role=args.role,
            context=args.context,
            resources=resources,
            use_llm=args.llm,
            coding_engine=args.engine,
        )

    elif args.command == "init":
        resources = [r.strip() for r in args.resources.split(",") if r.strip()] if args.resources else None
        init_skill_dir(
            name=args.name,
            output_dir=args.path,
            skill_type=args.type,
            resources=resources,
            description=args.description,
            examples=args.examples,
        )

    elif args.command == "validate":
        ok, msg = validate_skill(args.path)
        print(msg)
        if not ok:
            raise SystemExit(1)

    elif args.command == "generate":
        content = generate_skill_content(
            skill_name=args.name,
            role=args.role,
            context=args.context,
            skill_type=args.type,
            coding_engine=args.engine,
        )
        if content:
            if args.type == "knowledge":
                target = os.path.join(args.path, "SKILL.md")
            else:
                target = os.path.join(args.path, "skill.py")
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[OK] 콘텐츠 생성 완료: {target}")
        else:
            print("[ERROR] 콘텐츠 생성 실패")
            raise SystemExit(1)

    elif args.command == "update":
        ok = update_skill(
            skill_dir=args.path,
            use_llm=args.llm,
            coding_engine=args.engine,
        )
        if not ok:
            raise SystemExit(1)

    elif args.command == "evolve":
        feedback = args.feedback
        if args.feedback_file:
            try:
                with open(args.feedback_file, "r", encoding="utf-8") as f:
                    feedback = f.read()
            except OSError as e:
                print(f"[ERROR] 피드백 파일을 읽을 수 없습니다: {e}")
                raise SystemExit(1)
        ok = evolve_skill(
            skill_dir=args.path,
            feedback=feedback,
            error_log=args.error_log,
            coding_engine=args.engine,
        )
        if not ok:
            raise SystemExit(1)

    elif args.command == "retire":
        ok = retire_skill(skill_dir=args.path)
        if not ok:
            raise SystemExit(1)

    elif args.command == "benchmark":
        result = benchmark_skill(skill_dir=args.path)
        if not result.get("ok"):
            raise SystemExit(1)


if __name__ == "__main__":
    cli_main()
