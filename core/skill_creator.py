"""
core/skill_creator.py
=====================
Claude Code??Skill Creator? ?숈씪??諛⑹떇?쇰줈 ?ㅽ궗???앹꽦?섎뒗 紐⑤뱢.

吏?먰븯???ㅽ궗 ?뺤떇:
  - Knowledge Skill (SKILL.md 湲곕컲) ??Claude Code ?명솚
  - Action Skill (skill.py + meta.yaml) ??agent-factory 湲곗〈 ?뺤떇

二쇱슂 ?⑥닔:
  - create_skill(): ??뷀삎/CLI ?ㅽ궗 ?앹꽦
  - init_skill_dir(): ?ㅽ궗 ?붾젆?좊━ 珥덇린??
  - validate_skill(): ?ㅽ궗 援ъ“ 寃利?
  - generate_skill_content(): LLM?쇰줈 SKILL.md ?댁슜 ?앹꽦
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
{title} ??Action Skill
========================
?먮룞 ?앹꽦???≪뀡 ?ㅽ궗.
"""


def propose(ctx):
    """?ㅽ궗 硫뷀??곗씠?곕? 諛섑솚?⑸땲??"""
    return {{
        "skill_id": "{skill_id}",
        "description": "{description}",
        "required_keys": [],
        "optional_keys": [],
    }}


def apply(ctx):
    """?ㅽ궗 二쇱슂 濡쒖쭅???ㅽ뻾?⑸땲??"""
    # TODO: 援ы쁽 ?꾩슂
    return {{"ok": True, "result": "not_implemented"}}


def test(ctx):
    """?ㅽ궗 ?뚯뒪?몃? ?ㅽ뻾?⑸땲??"""
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
    """?ㅽ궗 ?대쫫??hyphen-case濡??뺢퇋?뷀빀?덈떎."""
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
    """?ㅽ궗 ?붾젆?좊━ 援ъ“瑜?寃利앺빀?덈떎. (SKILL.md ?먮뒗 skill.py 湲곕컲)"""
    if not os.path.isdir(skill_path):
        return False, f"?붾젆?좊━媛 議댁옱?섏? ?딆뒿?덈떎: {skill_path}"

    skill_md = os.path.join(skill_path, "SKILL.md")
    skill_py = os.path.join(skill_path, "skill.py")

    # Knowledge Skill (SKILL.md) 寃利?
    if os.path.exists(skill_md):
        return _validate_skill_md(skill_md)

    # Action Skill (skill.py) 寃利?
    if os.path.exists(skill_py):
        return _validate_skill_py(skill_py, skill_path)

    # skill.md (?뚮Ц?? ???덉슜
    skill_md_lower = os.path.join(skill_path, "skill.md")
    if os.path.exists(skill_md_lower):
        return _validate_skill_md(skill_md_lower)

    return False, "SKILL.md ?먮뒗 skill.py 媛 ?놁뒿?덈떎."


def _validate_skill_md(path: str) -> tuple[bool, str]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        return False, "YAML frontmatter媛 ?놁뒿?덈떎 (--- 濡??쒖옉?댁빞 ?⑸땲??"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "frontmatter ?뺤떇???щ컮瑜댁? ?딆뒿?덈떎"

    try:
        frontmatter = yaml.safe_load(match.group(1))
        if not isinstance(frontmatter, dict):
            return False, "frontmatter??YAML dict ?ъ빞 ?⑸땲??
    except yaml.YAMLError as e:
        return False, f"YAML ?뚯떛 ?ㅻ쪟: {e}"

    allowed = {"name", "description", "license", "allowed-tools", "allowed_tools", "approval-required-tools", "approval_required_tools", "disable-model-invocation", "disable_model_invocation", "model-invocable", "model_invocable", "user-invocable", "user_invocable", "planner-invocable", "planner_invocable", "argument-hint", "argument_hint", "model", "context", "context_mode", "agent", "hooks", "hook", "metadata"}
    unexpected = set(frontmatter.keys()) - allowed
    if unexpected:
        return False, f"?덉슜?섏? ?딅뒗 frontmatter ?? {', '.join(sorted(unexpected))}"

    if "name" not in frontmatter:
        return False, "frontmatter??'name' ???놁뒿?덈떎"
    if "description" not in frontmatter:
        return False, "frontmatter??'description' ???놁뒿?덈떎"

    name = str(frontmatter["name"]).strip()
    if name and not re.match(r"^[a-z0-9-]+$", name):
        return False, f"?대쫫 '{name}' ? hyphen-case ?ъ빞 ?⑸땲??(?뚮Ц?? ?レ옄, ?섏씠?덈쭔)"

    if name and len(name) > MAX_SKILL_NAME_LENGTH:
        return False, f"?대쫫???덈Т 源곷땲??({len(name)}?? 理쒕? {MAX_SKILL_NAME_LENGTH}??"

    desc = str(frontmatter.get("description", "")).strip()
    if desc and len(desc) > 1024:
        return False, f"description ???덈Т 源곷땲??({len(desc)}?? 理쒕? 1024??"

    return True, "?ㅽ궗 寃利??듦낵!"


def _validate_skill_py(py_path: str, skill_dir: str) -> tuple[bool, str]:
    with open(py_path, "r", encoding="utf-8") as f:
        code = f.read()
    required_funcs = ["propose", "apply", "test"]
    missing = [fn for fn in required_funcs if f"def {fn}(" not in code]
    if missing:
        return False, f"skill.py ???꾩닔 ?⑥닔媛 ?놁뒿?덈떎: {', '.join(missing)}"

    meta_path = os.path.join(skill_dir, "meta.yaml")
    if not os.path.exists(meta_path):
        return False, "meta.yaml ???놁뒿?덈떎"

    return True, "?≪뀡 ?ㅽ궗 寃利??듦낵!"


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
    ?ㅽ궗 ?붾젆?좊━瑜?珥덇린?뷀빀?덈떎.

    Args:
        name: ?ㅽ궗 ?대쫫 (hyphen-case 濡??먮룞 ?뺢퇋??
        output_dir: ?ㅽ궗 ?붾젆?좊━媛 ?앹꽦???곸쐞 寃쎈줈
        skill_type: "knowledge" (SKILL.md) ?먮뒗 "action" (skill.py)
        resources: ?앹꽦??由ъ냼???붾젆?좊━ 紐⑸줉 ["scripts", "references", "assets"]
        description: ?ㅽ궗 ?ㅻ챸
        examples: ?덉젣 ?뚯씪 ?ы븿 ?щ?

    Returns:
        ?앹꽦???ㅽ궗 ?붾젆?좊━ 寃쎈줈, ?ㅽ뙣 ??None
    """
    skill_name = normalize_skill_name(name)
    if not skill_name:
        print("[ERROR] ?ㅽ궗 ?대쫫??臾몄옄 ?먮뒗 ?レ옄媛 ?ы븿?섏뼱???⑸땲??")
        return None

    if len(skill_name) > MAX_SKILL_NAME_LENGTH:
        print(f"[ERROR] ?ㅽ궗 ?대쫫???덈Т 源곷땲??({len(skill_name)}?? 理쒕? {MAX_SKILL_NAME_LENGTH}??")
        return None

    skill_dir = os.path.join(output_dir, skill_name)
    if os.path.exists(skill_dir):
        print(f"[ERROR] ?대? 議댁옱?섎뒗 ?붾젆?좊━: {skill_dir}")
        return None

    os.makedirs(skill_dir, exist_ok=True)
    title = _title_case(skill_name)
    skill_id = _safe_id(skill_name)
    now = datetime.datetime.now().isoformat()

    if skill_type == "knowledge":
        # SKILL.md ?앹꽦
        desc = description or f"TODO - {title} ?ㅽ궗??湲곕뒫怨??ъ슜 ?쒖젏???ㅻ챸?⑸땲??"
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
        print(f"[OK] SKILL.md ?앹꽦?? {skill_md_path}")

    else:
        # Action Skill
        desc = description or f"{title} ?≪뀡 ?ㅽ궗"
        code = ACTION_SKILL_TEMPLATE.format(
            title=title,
            skill_id=skill_id,
            description=desc,
        )
        py_path = os.path.join(skill_dir, "skill.py")
        with open(py_path, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"[OK] skill.py ?앹꽦?? {py_path}")

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
        print(f"[OK] meta.yaml ?앹꽦?? {meta_path}")

    # 由ъ냼???붾젆?좊━
    for res in (resources or []):
        if res not in ALLOWED_RESOURCES:
            print(f"[WARN] ?????녿뒗 由ъ냼?????臾댁떆: {res}")
            continue
        res_dir = os.path.join(skill_dir, res)
        os.makedirs(res_dir, exist_ok=True)
        if examples:
            _create_example_file(res_dir, res, skill_name, title)
        print(f"[OK] {res}/ ?붾젆?좊━ ?앹꽦??)

    print(f"\n[OK] ?ㅽ궗 '{skill_name}' 珥덇린???꾨즺: {skill_dir}")
    return skill_dir


def _knowledge_body_template(title: str) -> str:
    return f"""\
## 媛쒖슂

[TODO] {title} ?ㅽ궗???쒓났?섎뒗 湲곕뒫??1-2臾몄옣?쇰줈 ?ㅻ챸?⑸땲??

## ?뚰겕?뚮줈??

[TODO] ???ㅽ궗??二쇱슂 ?뚰겕?뚮줈?곕? ?묒꽦?⑸땲??

### ?④퀎 1: 以鍮?

[TODO] 泥?踰덉㎏ ?④퀎瑜??ㅻ챸?⑸땲??

### ?④퀎 2: ?ㅽ뻾

[TODO] ??踰덉㎏ ?④퀎瑜??ㅻ챸?⑸땲??

### ?④퀎 3: 寃利?

[TODO] 寃곌낵瑜?寃利앺븯??諛⑸쾿???ㅻ챸?⑸땲??
"""


def _create_example_file(res_dir: str, res_type: str, skill_name: str, title: str):
    if res_type == "scripts":
        path = os.path.join(res_dir, "example.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'#!/usr/bin/env python3\n"""{title} ?덉젣 ?ㅽ겕由쏀듃"""\n\ndef main():\n    print("{skill_name} ?덉젣 ?ㅽ겕由쏀듃")\n\nif __name__ == "__main__":\n    main()\n')
    elif res_type == "references":
        path = os.path.join(res_dir, "reference.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title} 李몄“ 臾몄꽌\n\n[TODO] ?곸꽭 李몄“ 臾몄꽌瑜??묒꽦?⑸땲??\n")
    elif res_type == "assets":
        path = os.path.join(res_dir, "README.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{title} ?먯뀑 ?붾젆?좊━\n\n?쒗뵆由? ?대?吏, ?고듃 ??異쒕젰???ъ슜?섎뒗 ?뚯씪???ш린????ν빀?덈떎.\n")


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
    LLM?쇰줈 ?ㅽ궗 ?댁슜???앹꽦?⑸땲??

    Args:
        skill_name: ?ㅽ궗 ?대쫫
        role: ?먯씠?꾪듃 ??븷
        context: 異붽? 而⑦뀓?ㅽ듃 (?ъ슜 ?덉떆, ?꾨찓???뺣낫 ??
        skill_type: "knowledge" ?먮뒗 "action"
        coding_engine: LLM 紐⑤뜽 ?대쫫 (None ?대㈃ ?먮룞 ?좏깮)
        existing_content: 湲곗〈 ?ㅽ궗 ?뚯씪 ?댁슜 (update/evolve ???ъ슜)
        feedback: ?쇰뱶諛??띿뒪??(evolve ???ъ슜)

    Returns:
        ?앹꽦???ㅽ궗 ?뚯씪 ?댁슜 (str), ?ㅽ뙣 ??None
    """
    try:
        from model_utils import get_best_model, resolve_dynamic_model
        from core.llm_engine import LLMEngine
    except ImportError:
        print("[ERROR] LLM ?붿쭊??濡쒕뱶?????놁뒿?덈떎. model_utils ?먮뒗 core.llm_engine 紐⑤뱢???뺤씤?섏꽭??")
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

    # 湲곗〈 肄섑뀗痢??쇰뱶諛깆씠 ?덉쑝硫?吏꾪솕 紐⑤뱶 ?꾨＼?꾪듃 援ъ꽦
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
        print(f"[ERROR] LLM ?앹꽦 ?ㅽ뙣: {e}")
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
    ?ㅽ궗???앹꽦?⑸땲?? (?붾젆?좊━ 珥덇린??+ ?좏깮??LLM 肄섑뀗痢??앹꽦 + 寃利?

    Claude Code??Skill Creator? ?숈씪??6?④퀎 ?꾨줈?몄뒪:
      1. ?ㅽ궗 ?댄빐 (name, context)
      2. 由ъ냼??怨꾪쉷 (resources)
      3. 珥덇린??(init_skill_dir)
      4. 肄섑뀗痢??묒꽦 (generate or template)
      5. 寃利?(validate_skill)
      6. 諛섎났 媛쒖꽑 (?ъ슜?먭? ?섎룞?쇰줈)

    Args:
        name: ?ㅽ궗 ?대쫫
        output_dir: 異쒕젰 ?붾젆?좊━
        skill_type: "knowledge" ?먮뒗 "action"
        role: ?먯씠?꾪듃 ??븷
        context: 異붽? 而⑦뀓?ㅽ듃
        resources: 由ъ냼???붾젆?좊━ 紐⑸줉
        use_llm: LLM?쇰줈 肄섑뀗痢??먮룞 ?앹꽦 ?щ?
        coding_engine: LLM ?붿쭊 (None=?먮룞 ?좏깮)

    Returns:
        ?앹꽦???ㅽ궗 ?붾젆?좊━ 寃쎈줈, ?ㅽ뙣 ??None
    """
    skill_name = normalize_skill_name(name)
    print(f"\n{'='*60}")
    print(f"  Skill Creator ??'{skill_name}'")
    print(f"  Type: {skill_type} | LLM: {'ON' if use_llm else 'OFF'}")
    print(f"{'='*60}\n")

    # Step 3: 珥덇린??
    print("[Step 1/3] ?ㅽ궗 ?붾젆?좊━ 珥덇린??..")
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

    # Step 4: LLM 肄섑뀗痢??앹꽦 (?좏깮)
    if use_llm:
        print("\n[Step 2/3] LLM?쇰줈 ?ㅽ궗 肄섑뀗痢??앹꽦 以?..")
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
            print(f"[OK] LLM ?앹꽦 肄섑뀗痢???? {target}")
        else:
            print("[WARN] LLM ?앹꽦 ?ㅽ뙣, ?쒗뵆由우쓣 ?좎??⑸땲??")
    else:
        print("\n[Step 2/3] ?쒗뵆由??ъ슜 (LLM ?놁씠)")

    # Step 5: 寃利?
    print("\n[Step 3/3] ?ㅽ궗 寃利?以?..")
    ok, msg = validate_skill(skill_dir)
    if ok:
        print(f"[OK] {msg}")
    else:
        print(f"[WARN] 寃利??ㅽ뙣: {msg}")
        print("  ??SKILL.md ?먮뒗 skill.py瑜??몄쭛?섏뿬 臾몄젣瑜??섏젙?섏꽭??")

    # ?꾨즺 硫붿떆吏
    print(f"\n{'='*60}")
    print(f"  ?ㅽ궗 ?앹꽦 ?꾨즺: {skill_dir}")
    print(f"{'='*60}")
    print("\n?ㅼ쓬 ?④퀎:")
    print(f"  1. ?몄쭛: {skill_dir}")
    if skill_type == "knowledge":
        print("  2. SKILL.md??[TODO] ??ぉ???꾩꽦?섏꽭??)
    else:
        print("  2. skill.py??apply() ?⑥닔瑜?援ы쁽?섏꽭??)
    print(f"  3. 寃利? python -m core.skill_creator validate {skill_dir}")
    print("  4. ?ㅼ젣 ?ъ슜 ??諛섎났 媛쒖꽑?섏꽭??)

    return skill_dir


# ---------------------------------------------------------------------------
# Version Bump Helper
# ---------------------------------------------------------------------------
def _bump_minor_version(version: str) -> str:
    """0.1.0 ??0.2.0 ?뺥깭濡?minor 踰꾩쟾???щ┰?덈떎."""
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
    """?ㅽ궗 ?붾젆?좊━???ㅽ궗 ??낆쓣 媛먯??⑸땲??"""
    if os.path.exists(os.path.join(skill_dir, "skill.py")):
        return "action"
    return "knowledge"


def _read_skill_file(skill_dir: str, skill_type: str) -> str:
    """?ㅽ궗 ?붾젆?좊━??二쇱슂 ?뚯씪 ?댁슜???쎌뒿?덈떎."""
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
    """meta.yaml瑜??쎌뒿?덈떎. ?놁쑝硫?鍮?dict."""
    meta_path = os.path.join(skill_dir, "meta.yaml")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    return {}


def _write_meta(skill_dir: str, meta: dict):
    """meta.yaml瑜??곷땲??"""
    meta_path = os.path.join(skill_dir, "meta.yaml")
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, default_flow_style=False)


def _skill_name_from_dir(skill_dir: str) -> str:
    """?붾젆?좊━ ?대쫫?먯꽌 ?ㅽ궗 ?대쫫??異붿텧?⑸땲??"""
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
    湲곗〈 ?ㅽ궗???낅뜲?댄듃?⑸땲??

    - LLM ?ъ슜 ?? 湲곗〈 ?댁슜??LLM??蹂대궡 媛쒖꽑 ?붿껌, .bak 諛깆뾽 ????뼱?곌린
    - LLM ?놁씠: 踰꾩쟾 bump + validate留??섑뻾

    Args:
        skill_dir: ?ㅽ궗 ?붾젆?좊━ 寃쎈줈
        use_llm: LLM?쇰줈 肄섑뀗痢?媛쒖꽑 ?щ?
        coding_engine: LLM ?붿쭊 ?대쫫

    Returns:
        ?깃났 ?щ?
    """
    if not os.path.isdir(skill_dir):
        print(f"[ERROR] ?붾젆?좊━媛 議댁옱?섏? ?딆뒿?덈떎: {skill_dir}")
        return False

    skill_type = _detect_skill_type(skill_dir)
    skill_name = _skill_name_from_dir(skill_dir)
    meta = _read_meta(skill_dir)
    now = datetime.datetime.now().isoformat()

    print(f"\n[update] ?ㅽ궗 ?낅뜲?댄듃: {skill_name} (type={skill_type}, llm={'ON' if use_llm else 'OFF'})")

    if use_llm:
        existing = _read_skill_file(skill_dir, skill_type)
        if not existing:
            print("[ERROR] ?ㅽ궗 ?뚯씪???쎌쓣 ???놁뒿?덈떎.")
            return False

        # .bak 諛깆뾽
        if skill_type == "action":
            src = os.path.join(skill_dir, "skill.py")
        else:
            src = os.path.join(skill_dir, "SKILL.md")
            if not os.path.exists(src):
                src = os.path.join(skill_dir, "skill.md")
        bak = src + ".bak"
        shutil.copy2(src, bak)
        print(f"[OK] 諛깆뾽 ?앹꽦: {bak}")

        # LLM 媛쒖꽑 ?붿껌
        content = generate_skill_content(
            skill_name=skill_name,
            skill_type=skill_type,
            coding_engine=coding_engine,
            existing_content=existing,
            feedback="湲곗〈 ?댁슜??媛쒖꽑?섍퀬 ?덉쭏???믪뿬二쇱꽭?? description???쇱슦??留ㅼ묶??理쒖쟻?뷀븯?몄슂.",
        )
        if content:
            with open(src, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[OK] LLM 媛쒖꽑 肄섑뀗痢???? {src}")
        else:
            print("[WARN] LLM ?앹꽦 ?ㅽ뙣, 湲곗〈 ?댁슜???좎??⑸땲??")
            # 諛깆뾽 蹂듭썝
            shutil.copy2(bak, src)

    # 踰꾩쟾 bump
    old_version = meta.get("version", "0.1.0")
    new_version = _bump_minor_version(old_version)
    meta["version"] = new_version
    meta["updated_at"] = now
    if skill_type == "action":
        _write_meta(skill_dir, meta)
        print(f"[OK] 踰꾩쟾 bump: {old_version} ??{new_version}")

    # 寃利?
    ok, msg = validate_skill(skill_dir)
    if ok:
        print(f"[OK] 寃利??듦낵: {msg}")
    else:
        print(f"[WARN] 寃利??ㅽ뙣: {msg}")
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
    ?쇰뱶諛??먮윭 濡쒓렇瑜?湲곕컲?쇰줈 ?ㅽ궗??吏꾪솕?쒗궢?덈떎.

    - knowledge ?ㅽ궗: description + body 媛쒖꽑 (?쇱슦???쒕떇)
    - action ?ㅽ궗: apply() 濡쒖쭅 媛쒖꽑

    Args:
        skill_dir: ?ㅽ궗 ?붾젆?좊━ 寃쎈줈
        feedback: ?쇰뱶諛??띿뒪??
        error_log: ?먮윭 濡쒓렇 ?띿뒪??
        coding_engine: LLM ?붿쭊 ?대쫫

    Returns:
        ?깃났 ?щ?
    """
    if not os.path.isdir(skill_dir):
        print(f"[ERROR] ?붾젆?좊━媛 議댁옱?섏? ?딆뒿?덈떎: {skill_dir}")
        return False

    combined_feedback = ""
    if feedback:
        combined_feedback += f"[User Feedback]\n{feedback}\n"
    if error_log:
        combined_feedback += f"[Error Log]\n{error_log}\n"

    if not combined_feedback.strip():
        print("[ERROR] feedback ?먮뒗 error_log媛 ?꾩슂?⑸땲??")
        return False

    skill_type = _detect_skill_type(skill_dir)
    skill_name = _skill_name_from_dir(skill_dir)
    existing = _read_skill_file(skill_dir, skill_type)

    if not existing:
        print("[ERROR] ?ㅽ궗 ?뚯씪???쎌쓣 ???놁뒿?덈떎.")
        return False

    print(f"\n[evolve] ?ㅽ궗 吏꾪솕: {skill_name} (type={skill_type})")

    # .bak 諛깆뾽
    if skill_type == "action":
        src = os.path.join(skill_dir, "skill.py")
    else:
        src = os.path.join(skill_dir, "SKILL.md")
        if not os.path.exists(src):
            src = os.path.join(skill_dir, "skill.md")
    bak = src + ".bak"
    shutil.copy2(src, bak)
    print(f"[OK] 諛깆뾽 ?앹꽦: {bak}")

    # LLM 吏꾪솕 ?붿껌
    content = generate_skill_content(
        skill_name=skill_name,
        skill_type=skill_type,
        coding_engine=coding_engine,
        existing_content=existing,
        feedback=combined_feedback,
    )
    if not content:
        print("[ERROR] LLM ?앹꽦 ?ㅽ뙣, 湲곗〈 ?댁슜???좎??⑸땲??")
        shutil.copy2(bak, src)
        return False

    with open(src, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] 吏꾪솕 肄섑뀗痢???? {src}")

    # 踰꾩쟾 bump
    meta = _read_meta(skill_dir)
    old_version = meta.get("version", "0.1.0")
    new_version = _bump_minor_version(old_version)
    meta["version"] = new_version
    meta["updated_at"] = datetime.datetime.now().isoformat()
    if skill_type == "action":
        _write_meta(skill_dir, meta)
    print(f"[OK] 踰꾩쟾 bump: {old_version} ??{new_version}")

    # 寃利?
    ok, msg = validate_skill(skill_dir)
    if ok:
        print(f"[OK] 寃利??듦낵: {msg}")
    else:
        print(f"[WARN] 寃利??ㅽ뙣: {msg}")
    return ok


# ---------------------------------------------------------------------------
# Retire Skill
# ---------------------------------------------------------------------------
def retire_skill(skill_dir: str) -> bool:
    """
    ?ㅽ궗???꾩뭅?대툕(??? ?곹깭濡?蹂寃쏀빀?덈떎.

    - meta.yaml??status瑜?'archived'濡? retired_at ??꾩뒪?ы봽 異붽?
    - skill-lock.yaml ?숆린??

    Args:
        skill_dir: ?ㅽ궗 ?붾젆?좊━ 寃쎈줈

    Returns:
        ?깃났 ?щ?
    """
    if not os.path.isdir(skill_dir):
        print(f"[ERROR] ?붾젆?좊━媛 議댁옱?섏? ?딆뒿?덈떎: {skill_dir}")
        return False

    skill_name = _skill_name_from_dir(skill_dir)
    skill_type = _detect_skill_type(skill_dir)
    now = datetime.datetime.now().isoformat()

    print(f"\n[retire] ?ㅽ궗 ??? {skill_name}")

    # meta.yaml ?낅뜲?댄듃 (?놁쑝硫??앹꽦)
    meta = _read_meta(skill_dir)
    meta["status"] = "archived"
    meta["retired_at"] = now
    meta.setdefault("name", skill_name)
    meta.setdefault("type", skill_type)
    meta.setdefault("version", "0.1.0")
    _write_meta(skill_dir, meta)
    print(f"[OK] meta.yaml status ??archived")

    # skill-lock.yaml ?숆린??
    try:
        from core.utils import lock_skill_state
        skill_id = _safe_id(skill_name)
        lock_skill_state(skill_id, {
            "status": "archived",
            "retired_at": now,
            "version": meta.get("version", "0.1.0"),
        })
        print(f"[OK] skill-lock.yaml ?숆린???꾨즺")
    except Exception as e:
        print(f"[WARN] skill-lock ?숆린???ㅽ뙣: {e}")

    return True


# ---------------------------------------------------------------------------
# Benchmark Skill
# ---------------------------------------------------------------------------
def benchmark_skill(skill_dir: str) -> dict:
    """
    ?≪뀡 ?ㅽ궗??test()瑜?寃⑸━ ?ㅽ뻾?섍퀬 踰ㅼ튂留덊겕 寃곌낵瑜???ν빀?덈떎.

    - action ?ㅽ궗留?吏??(knowledge ?ㅽ궗? ?ㅽ궢)
    - run_isolated()濡?skill.py??test() ?ㅽ뻾
    - benchmark.json??寃곌낵 ???
    - ?댁쟾 踰ㅼ튂留덊겕 寃곌낵? 鍮꾧탳?섏뿬 regression 寃쎄퀬

    Args:
        skill_dir: ?ㅽ궗 ?붾젆?좊━ 寃쎈줈

    Returns:
        踰ㅼ튂留덊겕 寃곌낵 dict
    """
    if not os.path.isdir(skill_dir):
        print(f"[ERROR] ?붾젆?좊━媛 議댁옱?섏? ?딆뒿?덈떎: {skill_dir}")
        return {"ok": False, "reason": "dir_not_found"}

    skill_type = _detect_skill_type(skill_dir)
    skill_name = _skill_name_from_dir(skill_dir)

    if skill_type != "action":
        print(f"[SKIP] knowledge ?ㅽ궗? 踰ㅼ튂留덊겕瑜?吏?먰븯吏 ?딆뒿?덈떎: {skill_name}")
        return {"ok": True, "reason": "knowledge_skill_skipped"}

    skill_py = os.path.join(skill_dir, "skill.py")
    if not os.path.exists(skill_py):
        print(f"[ERROR] skill.py媛 議댁옱?섏? ?딆뒿?덈떎: {skill_py}")
        return {"ok": False, "reason": "skill_py_not_found"}

    print(f"\n[benchmark] ?ㅽ궗 踰ㅼ튂留덊겕: {skill_name}")

    # 寃⑸━ ?ㅽ뻾
    try:
        from core.security_guard import run_isolated
    except ImportError:
        print("[ERROR] core.security_guard 紐⑤뱢??濡쒕뱶?????놁뒿?덈떎.")
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

    print(f"[{'OK' if ok else 'FAIL'}] test() ??ok={ok}, duration={duration}s")

    # benchmark.json ???(?댁쟾 寃곌낵 蹂댁〈)
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

    # regression 寃쎄퀬
    if history:
        prev = history[-1]
        if prev.get("ok") and not ok:
            print(f"[WARN] REGRESSION 媛먯?! ?댁쟾 踰ㅼ튂留덊겕??ok=True ??쇰굹 ?꾩옱 ok=False")
        prev_dur = prev.get("duration_sec", 0)
        if prev_dur > 0 and duration > prev_dur * 2:
            print(f"[WARN] ?깅뒫 ??? ?댁쟾 {prev_dur}s ???꾩옱 {duration}s (2諛??댁긽 ?먮젮吏?")

    history.append(result)
    with open(bench_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"[OK] 踰ㅼ튂留덊겕 ??? {bench_path}")

    return result


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def cli_main(argv: list[str] | None = None):
    """
    ?ㅽ궗 ?앹꽦 CLI.

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
    subparsers = parser.add_subparsers(dest="command", help="紐낅졊")

    # --- create ---
    p_create = subparsers.add_parser("create", help="?ㅽ궗 ?앹꽦 (珥덇린??+ 肄섑뀗痢??앹꽦 + 寃利?")
    p_create.add_argument("name", help="?ㅽ궗 ?대쫫 (hyphen-case濡??먮룞 蹂??")
    p_create.add_argument("--path", "-p", default=None, help="異쒕젰 ?붾젆?좊━ (湲곕낯: skills/forge)")
    p_create.add_argument("--type", "-t", choices=["knowledge", "action"], default="knowledge", help="?ㅽ궗 ???)
    p_create.add_argument("--role", "-r", default="", help="?먯씠?꾪듃 ??븷")
    p_create.add_argument("--context", "-c", default="", help="?ㅽ궗 ?⑸룄/而⑦뀓?ㅽ듃 ?ㅻ챸")
    p_create.add_argument("--resources", default="", help="由ъ냼???붾젆?좊━ (scripts,references,assets)")
    p_create.add_argument("--llm", action="store_true", help="LLM?쇰줈 肄섑뀗痢??먮룞 ?앹꽦")
    p_create.add_argument("--engine", default=None, help="LLM ?붿쭊 ?대쫫")

    # --- init ---
    p_init = subparsers.add_parser("init", help="?ㅽ궗 ?붾젆?좊━ 珥덇린??(?쒗뵆由용쭔)")
    p_init.add_argument("name", help="?ㅽ궗 ?대쫫")
    p_init.add_argument("--path", "-p", required=True, help="異쒕젰 ?붾젆?좊━")
    p_init.add_argument("--type", "-t", choices=["knowledge", "action"], default="knowledge", help="?ㅽ궗 ???)
    p_init.add_argument("--resources", default="", help="由ъ냼???붾젆?좊━ (scripts,references,assets)")
    p_init.add_argument("--examples", action="store_true", help="?덉젣 ?뚯씪 ?ы븿")
    p_init.add_argument("--description", "-d", default="", help="?ㅽ궗 ?ㅻ챸")

    # --- validate ---
    p_validate = subparsers.add_parser("validate", help="?ㅽ궗 援ъ“ 寃利?)
    p_validate.add_argument("path", help="?ㅽ궗 ?붾젆?좊━ 寃쎈줈")

    # --- generate ---
    p_gen = subparsers.add_parser("generate", help="LLM?쇰줈 ?ㅽ궗 肄섑뀗痢좊쭔 ?앹꽦 (湲곗〈 ?붾젆?좊━????뼱?곌린)")
    p_gen.add_argument("name", help="?ㅽ궗 ?대쫫")
    p_gen.add_argument("--path", "-p", required=True, help="湲곗〈 ?ㅽ궗 ?붾젆?좊━ 寃쎈줈")
    p_gen.add_argument("--type", "-t", choices=["knowledge", "action"], default="knowledge", help="?ㅽ궗 ???)
    p_gen.add_argument("--role", "-r", default="", help="?먯씠?꾪듃 ??븷")
    p_gen.add_argument("--context", "-c", default="", help="異붽? 而⑦뀓?ㅽ듃")
    p_gen.add_argument("--engine", default=None, help="LLM ?붿쭊 ?대쫫")

    # --- update ---
    p_update = subparsers.add_parser("update", help="?ㅽ궗 ?낅뜲?댄듃 (踰꾩쟾 bump + ?좏깮??LLM 媛쒖꽑)")
    p_update.add_argument("path", help="?ㅽ궗 ?붾젆?좊━ 寃쎈줈")
    p_update.add_argument("--llm", action="store_true", help="LLM?쇰줈 肄섑뀗痢?媛쒖꽑")
    p_update.add_argument("--engine", default=None, help="LLM ?붿쭊 ?대쫫")

    # --- evolve ---
    p_evolve = subparsers.add_parser("evolve", help="?쇰뱶諛?湲곕컲 ?ㅽ궗 吏꾪솕")
    p_evolve.add_argument("path", help="?ㅽ궗 ?붾젆?좊━ 寃쎈줈")
    p_evolve.add_argument("--feedback", default="", help="?쇰뱶諛??띿뒪??)
    p_evolve.add_argument("--feedback-file", default="", help="?쇰뱶諛??뚯씪 寃쎈줈")
    p_evolve.add_argument("--error-log", default="", help="?먮윭 濡쒓렇 ?띿뒪??)
    p_evolve.add_argument("--engine", default=None, help="LLM ?붿쭊 ?대쫫")

    # --- retire ---
    p_retire = subparsers.add_parser("retire", help="?ㅽ궗 ?꾩뭅?대툕(???")
    p_retire.add_argument("path", help="?ㅽ궗 ?붾젆?좊━ 寃쎈줈")

    # --- benchmark ---
    p_bench = subparsers.add_parser("benchmark", help="?≪뀡 ?ㅽ궗 踰ㅼ튂留덊겕")
    p_bench.add_argument("path", help="?ㅽ궗 ?붾젆?좊━ 寃쎈줈")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    if args.command == "create":
        resources = [r.strip() for r in args.resources.split(",") if r.strip()] if args.resources else None
        output_dir = args.path
        if not output_dir:
            # 湲곕낯: skills/forge
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
            print(f"[OK] 肄섑뀗痢??앹꽦 ?꾨즺: {target}")
        else:
            print("[ERROR] 肄섑뀗痢??앹꽦 ?ㅽ뙣")
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
                print(f"[ERROR] ?쇰뱶諛??뚯씪???쎌쓣 ???놁뒿?덈떎: {e}")
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

