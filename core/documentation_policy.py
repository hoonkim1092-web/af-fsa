from __future__ import annotations

import locale
import os
from datetime import datetime

from core.file_io import write_text

ARCHITECTURE_DOC_REL_PATH = os.path.join("docs", "architecture.md")
CHANGE_HISTORY_REL_PATH = os.path.join("docs", "change_history.md")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_language_code(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = text.split(":", 1)[0].strip()
    text = text.split(".", 1)[0].strip()
    text = text.replace("_", "-")
    parts = [part for part in text.split("-") if part]
    if not parts:
        return ""
    primary = parts[0].lower()
    if len(parts) == 1:
        return primary
    region = parts[1].upper()
    extras = parts[2:]
    return "-".join([primary, region, *extras])


def get_document_language_code() -> str:
    candidates = [
        os.getenv("AGENT_DOC_LANGUAGE_CODE", ""),
        os.getenv("LC_ALL", ""),
        os.getenv("LC_MESSAGES", ""),
        os.getenv("LANG", ""),
        os.getenv("LANGUAGE", ""),
    ]

    for candidate in candidates:
        normalized = _normalize_language_code(candidate)
        if normalized:
            return normalized

    for getter in (locale.getlocale, locale.getdefaultlocale):
        try:
            value = getter()[0]
        except Exception:
            value = ""
        normalized = _normalize_language_code(value or "")
        if normalized:
            return normalized
    return "en-US"


def documentation_language_profile() -> dict[str, object]:
    language_code = get_document_language_code()
    primary = language_code.split("-", 1)[0].lower()

    if primary == "ko":
        return {
            "language_code": language_code,
            "language_name": "한국어",
            "architecture_title": "아키텍처",
            "change_history_title": "변경 이력",
            "project_todo_title": "프로젝트 TODO",
            "resume_brief_title": "작업 재개 요약",
            "labels": {
                "generated_at": "생성 시각",
                "trigger": "생성 트리거",
                "workspace": "워크스페이스",
                "project": "프로젝트",
                "roles": "역할",
                "status": "상태",
                "completed_count": "완료 수",
                "failed_count": "실패 수",
                "interrupted_count": "중단 수",
                "interrupted_subtasks": "중단된 하위 작업",
                "recent_failures": "최근 실패",
                "open_todos": "미완료 TODO",
                "latest_cli_session": "최신 CLI 세션",
                "provider": "프로바이더",
                "run_id": "런 ID",
                "model": "모델",
                "session_id": "세션 ID",
                "transcript": "트랜스크립트",
                "state_file": "상태 파일",
                "last_response_excerpt": "마지막 응답 발췌",
            },
            "todo_items": [
                "아키텍처나 워크플로가 바뀌면 `docs/architecture.md`를 현재 설계에 맞게 갱신한다.",
                "설계 변경마다 날짜, 요약, 이유, 영향 파일, 후속 작업을 `docs/change_history.md`에 추가한다.",
                f"생성하거나 수정하는 모든 문서는 운영체제 언어 코드 `{language_code}`에 맞는 언어인 한국어로 작성한다.",
            ],
            "architecture_template": (
                "# 아키텍처\n\n"
                "이 문서는 현재 아키텍처와 워크플로를 설명하는 살아 있는 기준 문서다.\n"
                "설계, 워크플로, 인터페이스, 데이터 흐름, 구현 전략이 바뀌면 같은 작업 안에서 갱신한다.\n\n"
                "## 메타데이터\n"
                f"- 마지막 업데이트: {_now_iso()}\n"
                "- 상태: active\n"
                f"- 문서 언어: 한국어 (OS: `{language_code}`)\n\n"
                "## 현재 설계\n"
                "- 요약:\n"
                "- 핵심 구성 요소:\n"
                "- 데이터 흐름:\n"
                "- 제약 사항:\n"
                "- 열린 질문:\n\n"
                "## 문서 규칙\n"
                "- 설계가 바뀌면 같은 작업 안에서 이 파일을 갱신한다.\n"
                "- 작업을 닫기 전에 `docs/change_history.md`에 대응되는 항목을 추가한다.\n"
                f"- 이 저장소에서 생성하거나 수정하는 모든 문서는 운영체제 언어 코드 `{language_code}`에 맞는 언어인 한국어로 작성한다.\n"
                "- 코드, 경로, 명령어, API 식별자는 필요한 경우 원문 그대로 유지한다.\n"
            ),
            "change_history_template": (
                "# 변경 이력\n\n"
                "설계, 아키텍처, 워크플로, 구현 전략이 바뀔 때마다 항목을 하나씩 추가한다.\n\n"
                "## 항목 템플릿\n"
                "### YYYY-MM-DD HH:MM:SS\n"
                "- 요약:\n"
                "- 이유:\n"
                "- 영향 파일:\n"
                "- 후속 작업:\n\n"
                "## 이력\n"
                f"### {_now_iso()}\n"
                "- 요약: 문서 계약 초기화.\n"
                "- 이유: 아키텍처와 워크플로 변경 이력을 안정적으로 보존하기 위해.\n"
                f"- 영향 파일: `{ARCHITECTURE_DOC_REL_PATH.replace(os.sep, '/')}`, `{CHANGE_HISTORY_REL_PATH.replace(os.sep, '/')}`\n"
                f"- 후속 작업: 이 파일을 append-only로 유지하고, 생성하거나 수정하는 모든 문서를 운영체제 언어 코드 `{language_code}`에 맞는 한국어로 작성한다.\n"
            ),
            "contract": (
                "[Documentation Contract]\n"
                "이 작업에서 아키텍처, 설계, 워크플로, 요구사항, 인터페이스, 데이터 흐름, 구현 전략이 바뀌면 "
                "`docs/architecture.md`를 갱신하고 `docs/change_history.md`에 같은 작업 안에서 항목을 추가해야 한다.\n"
                "파일이 없으면 생성한다.\n"
                f"이 작업에서 생성하거나 수정하는 모든 문서는 운영체제 언어 코드 `{language_code}`에 맞는 언어인 한국어로 작성해야 한다.\n"
                "코드, 파일 경로, 명령어, API 식별자는 필요한 경우 원문 그대로 유지할 수 있지만, 제목·본문·요약·변경 이력 설명은 한국어로 작성한다.\n"
                "설계 수준 변경이 없으면 해당 문서는 건드리지 않는다."
            ),
        }

    return {
        "language_code": language_code,
        "language_name": "English",
        "architecture_title": "Architecture",
        "change_history_title": "Change History",
        "project_todo_title": "Project TODO",
        "resume_brief_title": "Resume Brief",
        "labels": {
            "generated_at": "Generated At",
            "trigger": "Trigger",
            "workspace": "Workspace",
            "project": "Project",
            "roles": "Roles",
            "status": "Status",
            "completed_count": "Completed Count",
            "failed_count": "Failed Count",
            "interrupted_count": "Interrupted Count",
            "interrupted_subtasks": "Interrupted Subtasks",
            "recent_failures": "Recent Failures",
            "open_todos": "Open Todos",
            "latest_cli_session": "Latest CLI Session",
            "provider": "Provider",
            "run_id": "Run ID",
            "model": "Model",
            "session_id": "Session ID",
            "transcript": "Transcript",
            "state_file": "State File",
            "last_response_excerpt": "Last Response Excerpt",
        },
        "todo_items": [
            "Update docs/architecture.md to match the latest design when architecture or workflow changes.",
            "Append docs/change_history.md with date, summary, reason, affected files, and follow-up for every design update.",
            f"Write every generated or updated document in the language that matches the operating-system language code `{language_code}`.",
        ],
        "architecture_template": (
            "# Architecture\n\n"
            "This file is the living source of truth for the current architecture and workflow.\n"
            "Update it whenever the design, workflow, interfaces, data flow, or implementation strategy changes.\n\n"
            "## Metadata\n"
            f"- Last updated: {_now_iso()}\n"
            "- Status: active\n"
            f"- Documentation language: English (OS: `{language_code}`)\n\n"
            "## Current Design\n"
            "- Summary:\n"
            "- Core components:\n"
            "- Data flow:\n"
            "- Constraints:\n"
            "- Open questions:\n\n"
            "## Documentation Rule\n"
            "- When the design changes, update this file in the same task.\n"
            "- Append the matching entry to `docs/change_history.md` before closing the task.\n"
            f"- All generated or updated documents in this repository must use the language that matches OS language code `{language_code}`.\n"
            "- Keep code, paths, commands, and API identifiers in their original form when needed.\n"
        ),
        "change_history_template": (
            "# Change History\n\n"
            "Append one new entry per design, architecture, workflow, or implementation-strategy update.\n\n"
            "## Entry Template\n"
            "### YYYY-MM-DD HH:MM:SS\n"
            "- Summary:\n"
            "- Reason:\n"
            "- Affected files:\n"
            "- Follow-up:\n\n"
            "## History\n"
            f"### {_now_iso()}\n"
            "- Summary: Documentation contract initialized.\n"
            "- Reason: Preserve architecture and workflow changes in a stable project history.\n"
            f"- Affected files: `{ARCHITECTURE_DOC_REL_PATH.replace(os.sep, '/')}`, `{CHANGE_HISTORY_REL_PATH.replace(os.sep, '/')}`\n"
            f"- Follow-up: Keep this file append-only and write generated or updated documents in the language that matches OS language code `{language_code}`.\n"
        ),
        "contract": (
            "[Documentation Contract]\n"
            "If this task changes architecture, design, workflow, requirements, interfaces, data flow, or implementation strategy, "
            "you MUST update `docs/architecture.md` and append `docs/change_history.md` in the same task.\n"
            "Create those files if they do not exist.\n"
            f"All documentation files created or updated in this task MUST be written in the language that matches OS language code `{language_code}`.\n"
            "Keep code, file paths, commands, and API identifiers in their original form when needed, but write headings, body text, summaries, and change-log entries in that language.\n"
            "If no design-level change happened, leave those files untouched."
        ),
    }


def project_todo_title() -> str:
    return str(documentation_language_profile()["project_todo_title"])


def resume_brief_strings() -> dict[str, object]:
    profile = documentation_language_profile()
    return {
        "title": str(profile["resume_brief_title"]),
        "labels": dict(profile["labels"]),
    }


def _architecture_doc_template() -> str:
    return str(documentation_language_profile()["architecture_template"])


def _change_history_template() -> str:
    return str(documentation_language_profile()["change_history_template"])


def ensure_documentation_files(workspace: str) -> dict[str, str]:
    workspace_path = os.path.abspath(workspace)
    architecture_path = os.path.join(workspace_path, ARCHITECTURE_DOC_REL_PATH)
    history_path = os.path.join(workspace_path, CHANGE_HISTORY_REL_PATH)

    if not os.path.exists(architecture_path):
        write_text(architecture_path, _architecture_doc_template())
    if not os.path.exists(history_path):
        write_text(history_path, _change_history_template())

    return {
        "architecture_path": architecture_path,
        "change_history_path": history_path,
    }


def documentation_todo_items() -> list[str]:
    return list(documentation_language_profile()["todo_items"])


def inject_documentation_contract(system_prompt: str) -> str:
    base = str(system_prompt or "").strip()
    marker = "[Documentation Contract]"
    if marker in base:
        return base

    contract = str(documentation_language_profile()["contract"])
    return f"{base}\n\n{contract}".strip()
