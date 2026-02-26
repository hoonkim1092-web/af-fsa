#!/usr/bin/env python3
"""
Generate root AGENTS.md from every YAML file under agents/.

Supports both:
- File agents:   agents/<agent_id>.yaml
- Folder agents: agents/<folder>/<config>.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]

ROLE_EMOJI = {
    "Architect": "🐍",
    "PM": "😈",
    "Designer": "🎨",
    "Dev": "☠️",
}

CORE_AGENT_EMOJI = {
    "tanjiro_logimind_planning_director": "🎴",
    "lilith": "😈",
    "deadbyte": "☠️",
    "iguro_obanai": "🐍",
    "saiba_midori": "🎨",
    "himari": "🦽",
}

CORE_AGENT_NAME_EMOJI = {
    "kamado tanjiro": "🎴",
    "lilith": "😈",
    "deadbyte": "☠️",
    "iguro obanai": "🐍",
    "saiba midori": "🎨",
    "himari": "🦽",
}

ROLE_ORDER = {
    "Architect": 0,
    "PM": 1,
    "Designer": 2,
    "Dev": 3,
    "": 4,
}


@dataclass
class AgentRecord:
    kind: str
    agent_id: str
    name: str
    role: str
    role_type: str
    emoji: str
    source: str


def _resolve_path(path_arg: str) -> Path:
    p = Path(path_arg)
    if p.is_absolute():
        return p
    return REPO_ROOT / p


def _read_text_with_fallback(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    # Last resort: replace undecodable bytes.
    return path.read_text(encoding="utf-8", errors="replace")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = _read_text_with_fallback(path)
        data = yaml.safe_load(raw) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[WARN] Failed to parse YAML: {path} ({exc})", file=sys.stderr)
        return {}


def _first_non_empty_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""


def _coerce_type_role(type_value: Any) -> str:
    if not isinstance(type_value, str) or not type_value.strip():
        return ""
    text = type_value.strip().replace("-", " ").replace("_", " ")
    return " ".join(part.capitalize() for part in text.split())


def _extract_role(data: dict[str, Any]) -> str:
    direct_role = data.get("role")
    identity_role = data.get("identity", {}).get("role_summary") if isinstance(data.get("identity"), dict) else None
    context_role = data.get("context", {}).get("role") if isinstance(data.get("context"), dict) else None

    roles_value = data.get("roles")
    roles_role = ""
    if isinstance(roles_value, list) and roles_value:
        first = roles_value[0]
        if isinstance(first, str):
            roles_role = first.strip()
        elif isinstance(first, dict):
            roles_role = _first_non_empty_str(first.get("name"), first.get("id"))

    type_role = _coerce_type_role(data.get("type"))
    return _first_non_empty_str(direct_role, identity_role, context_role, roles_role, type_role)


def _extract_name(data: dict[str, Any], fallback: str) -> str:
    context_name = data.get("context", {}).get("name") if isinstance(data.get("context"), dict) else None
    return _first_non_empty_str(data.get("name"), context_name, fallback)


def _extract_dashboard_overrides(data: dict[str, Any]) -> tuple[str, str]:
    dashboard = data.get("dashboard")
    if not isinstance(dashboard, dict):
        return "", ""
    return (
        _first_non_empty_str(dashboard.get("role")),
        _first_non_empty_str(dashboard.get("emoji")),
    )


def _detect_role_type(role: str) -> str:
    r = role.casefold().replace("-", " ")
    if "frontend tech lead" in r:
        return "Dev"
    if "architect" in r:
        return "Architect"
    if re.search(r"\bpm\b", r) or "project manager" in r or "product manager" in r or "planning director" in r:
        return "PM"
    if "designer" in r or "ui/ux" in r:
        return "Designer"
    if re.search(r"\bdev\b", r) or "developer" in r:
        return "Dev"
    return ""


def _resolve_core_agent_emoji(agent_id: str, name: str) -> str:
    by_id = CORE_AGENT_EMOJI.get(agent_id.casefold())
    if by_id:
        return by_id
    return CORE_AGENT_NAME_EMOJI.get(name.casefold(), "")


def _collect_yaml_files(agents_dir: Path) -> list[Path]:
    yaml_files = list(agents_dir.rglob("*.yaml"))
    yaml_files.extend(agents_dir.rglob("*.yml"))
    unique = {p.resolve(): p for p in yaml_files}
    return sorted(unique.values(), key=lambda p: str(p).lower())


def _relative_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def collect_records(repo_root: Path, agents_dir: Path) -> list[AgentRecord]:
    records: list[AgentRecord] = []
    for yaml_path in _collect_yaml_files(agents_dir):
        rel_from_agents = yaml_path.relative_to(agents_dir)
        kind = "file" if len(rel_from_agents.parts) == 1 else "folder"
        if kind == "file":
            agent_id = rel_from_agents.stem
        else:
            agent_id = rel_from_agents.parts[0]

        data = _load_yaml(yaml_path)
        name = _extract_name(data, fallback=agent_id)
        base_role = _extract_role(data)
        dashboard_role, dashboard_emoji = _extract_dashboard_overrides(data)
        role = dashboard_role or base_role or "Unknown"
        role_type = _detect_role_type(role)
        emoji = dashboard_emoji or _resolve_core_agent_emoji(agent_id, name) or ROLE_EMOJI.get(role_type, "")
        source = _relative_posix(yaml_path, repo_root)

        records.append(
            AgentRecord(
                kind=kind,
                agent_id=agent_id,
                name=name,
                role=role,
                role_type=role_type,
                emoji=emoji,
                source=source,
            )
        )

    records.sort(
        key=lambda r: (
            ROLE_ORDER.get(r.role_type, 99),
            r.role.casefold(),
            r.name.casefold(),
            r.source.casefold(),
        )
    )
    return records


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def render_markdown(records: list[AgentRecord], agents_dir_label: str) -> str:
    lines: list[str] = []
    lines.append("# AGENTS")
    lines.append("")
    lines.append("This file is auto-generated. Do not edit manually.")
    lines.append("")
    lines.append("## Role Emoji Mapping")
    lines.append("")
    lines.append("| Role | Emoji |")
    lines.append("| --- | --- |")
    lines.append("| Architect | 🐍 |")
    lines.append("| PM | 😈 |")
    lines.append("| Designer | 🎨 |")
    lines.append("| Dev | ☠️ |")
    lines.append("")
    lines.append(f"## Agents ({len(records)})")
    lines.append("")
    lines.append(f"Source: `{agents_dir_label}` (recursive YAML scan)")
    lines.append("")
    lines.append("| Kind | Agent ID | Name | Role | Emoji | YAML |")
    lines.append("| --- | --- | --- | --- | --- | --- |")

    for row in records:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_escape(row.kind),
                    _md_escape(row.agent_id),
                    _md_escape(row.name),
                    _md_escape(row.role),
                    _md_escape(row.emoji),
                    f"`{_md_escape(row.source)}`",
                ]
            )
            + " |"
        )

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate root AGENTS.md table from YAML files in agents/."
    )
    parser.add_argument(
        "--agents-dir",
        default="agents",
        help="Agents directory to scan recursively (default: agents)",
    )
    parser.add_argument(
        "--output",
        default="AGENTS.md",
        help="Output markdown path (default: AGENTS.md at repo root)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    agents_dir = _resolve_path(args.agents_dir)
    output_path = _resolve_path(args.output)

    if not agents_dir.exists() or not agents_dir.is_dir():
        print(f"[ERROR] agents directory not found: {agents_dir}", file=sys.stderr)
        return 1

    records = collect_records(REPO_ROOT, agents_dir)
    agents_dir_label = _relative_posix(agents_dir, REPO_ROOT)
    markdown = render_markdown(records, agents_dir_label=agents_dir_label)

    output_path.write_text(markdown, encoding="utf-8")
    print(f"[OK] Generated {output_path} ({len(records)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
