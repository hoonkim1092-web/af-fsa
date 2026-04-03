from __future__ import annotations


def safe_id(text: str) -> str:
    value = (text or "").strip().lower()
    chars = []
    for ch in value:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            chars.append(ch)
        else:
            chars.append("_")
    normalized = "".join(chars)
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = normalized.strip("_")
    return normalized or "skill"


EXTERNAL_SOURCE_ID_ALIASES = {
    "codex_official": "codex_official",
    "official_codex": "codex_official",
    "official_codex_skills": "codex_official",
    "codex_skills": "codex_official",
    # Claude Code 로컬 스킬 (~/.claude/skills/, PROJECT/.claude/skills/)
    "claude_official": "claude_official",
    "claude_code": "claude_official",
    "claude_skills": "claude_official",
    "official_claude": "claude_official",
    # Git clone 폴백
    "claude": "claude_repo",
    "claude_repo": "claude_repo",
    "codex": "codex_repo",
    "codex_repo": "codex_repo",
}

EXTERNAL_SOURCE_ID_LEGACY_IDS = {
    "claude_repo": ["claude"],
    "codex_repo": ["codex"],
}

DEFAULT_EXTERNAL_SOURCE_PRIORITY = [
    "codex_official",
    "claude_official",   # Claude Code 로컬 스킬 (personal + project)
    "claude_repo",       # Git clone 폴백 — CLI 존재와 무관하게 항상 활성
    "codex_repo",        # Git clone 폴백 — 항상 활성
    "registry",
    "external_cache",
]


def normalize_external_source_id(raw: str | None, default: str = "external") -> str:
    sid = safe_id(str(raw or ""))
    if not sid:
        return default
    return EXTERNAL_SOURCE_ID_ALIASES.get(sid, sid)


def legacy_external_source_ids(raw: str | None) -> list[str]:
    canonical = normalize_external_source_id(raw, default="")
    if not canonical:
        return []
    return list(EXTERNAL_SOURCE_ID_LEGACY_IDS.get(canonical, []))
