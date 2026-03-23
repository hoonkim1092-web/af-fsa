from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

from core.file_io import write_text
from core.file_lock import locked_file
from core.project_task_board import append_project_board_note
from core.utils import now_iso, safe_id

MAILBOX_REL_DIR = os.path.join("data", "comm")
MESSAGES_FILENAME = "messages.jsonl"
_ALLOWED_MESSAGE_TYPES = {
    "handoff",
    "blocker",
    "decision_request",
    "decision_response",
    "review_request",
    "review_result",
    "result",
}

# 개선 2: 메시지 타입별 우선순위 (낮을수록 먼저 처리)
_MESSAGE_PRIORITY: dict[str, int] = {
    "blocker": 0,
    "decision_request": 1,
    "review_request": 2,
    "handoff": 3,
    "decision_response": 4,
    "review_result": 5,
    "result": 6,
}

# 개선 1: 기본 TTL — pending 메시지 만료 시간(초). 0이면 무제한.
DEFAULT_MESSAGE_TTL_SECONDS = 0


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(values: Any) -> list[str]:
    cleaned: list[str] = []
    for item in values or []:
        text = _clean_text(item).replace("\\", "/")
        if not text or text in cleaned:
            continue
        cleaned.append(text)
    return cleaned


def _messages_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace), MAILBOX_REL_DIR, MESSAGES_FILENAME)


def _normalize_related_files(
    workspace: str,
    values: list[str] | None,
    strict: bool = True,
) -> list[str]:
    """related_files 경로를 검증·정규화한다.

    Args:
        strict: True면 파일이 실제로 존재해야 통과. False면 워크스페이스 경계만 검사.
                에이전트가 "곧 생성할 파일"을 handoff에 포함할 때 strict=False를 사용.
    """
    workspace_root = os.path.abspath(workspace)
    normalized: list[str] = []
    invalid: list[str] = []

    for item in values or []:
        raw = _clean_text(item).replace("\\", "/")
        if not raw:
            continue
        candidate = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(workspace_root, raw))
        try:
            if os.path.commonpath([workspace_root, candidate]) != workspace_root:
                invalid.append(raw)
                continue
        except ValueError:
            invalid.append(raw)
            continue
        # Bug 6 수정: strict=False이면 파일 존재 검사 생략
        if strict and not os.path.isfile(candidate):
            invalid.append(raw)
            continue
        relpath = os.path.relpath(candidate, workspace_root).replace("\\", "/")
        if relpath not in normalized:
            normalized.append(relpath)

    if invalid:
        raise ValueError("invalid_related_files:" + ",".join(invalid))
    return normalized


def _is_expired(message: dict[str, Any]) -> bool:
    """메시지 TTL이 지났으면 True."""
    expires_at = _clean_text(message.get("expires_at"))
    if not expires_at:
        return False
    return expires_at < now_iso()


def load_mailbox_messages(workspace: str) -> list[dict[str, Any]]:
    path = _messages_path(workspace)
    if not os.path.exists(path):
        return []
    messages: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = str(raw or "").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                messages.append(payload)
    return messages


def _write_messages(workspace: str, messages: list[dict[str, Any]]) -> str:
    path = _messages_path(workspace)
    lines = [json.dumps(message, ensure_ascii=False) for message in messages if isinstance(message, dict)]
    write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return path


def send_agent_message(
    workspace: str,
    from_role: str,
    to_role: str,
    message_type: str,
    body: str,
    task_id: str = "",
    related_files: list[str] | None = None,
    requires_ack: bool = False,
    ttl_seconds: int = DEFAULT_MESSAGE_TTL_SECONDS,
    in_reply_to: str = "",
    strict_files: bool = True,
) -> dict[str, Any]:
    """에이전트 간 메시지를 전송한다.

    Args:
        ttl_seconds: 메시지 유효 시간(초). 0이면 만료 없음.
        in_reply_to: 이 메시지가 응답하는 원본 message_id.
        strict_files: False면 아직 생성되지 않은 파일도 related_files에 허용.
    """
    sender = safe_id(from_role) or "unknown_sender"
    recipient = safe_id(to_role)
    kind = safe_id(message_type)
    message_body = _clean_text(body)
    task_key = safe_id(task_id)
    files = _normalize_related_files(workspace, related_files, strict=strict_files)

    if not recipient:
        raise ValueError("to_role_required")
    if kind not in _ALLOWED_MESSAGE_TYPES:
        raise ValueError(f"unsupported_message_type:{message_type}")
    if not message_body:
        raise ValueError("message_body_required")

    # 개선 4: 정렬된 역할 쌍으로 thread_id 생성 → request↔response가 같은 스레드에 묶임
    pair = "_".join(sorted([sender, recipient]))
    thread_id = safe_id(f"{task_key or 'general'}_{pair}")

    message: dict[str, Any] = {
        "message_id": f"msg_{uuid4().hex[:12]}",
        "thread_id": thread_id,
        "task_id": task_key,
        "from_role": sender,
        "to_role": recipient,
        "type": kind,
        "body": message_body,
        "related_files": files,
        "requires_ack": bool(requires_ack),
        "status": "pending",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    # 개선 1: TTL 설정 — now_iso()와 동일한 로컬 시간 형식으로 저장
    if ttl_seconds > 0:
        from datetime import datetime, timedelta
        expires = datetime.now() + timedelta(seconds=ttl_seconds)
        message["expires_at"] = expires.isoformat(timespec="seconds")
    # in_reply_to 연결
    if in_reply_to:
        message["in_reply_to"] = safe_id(in_reply_to)

    # Bug 1 수정: locked_file로 동시 쓰기 경합 방지
    messages_path = _messages_path(workspace)
    with locked_file(messages_path):
        messages = load_mailbox_messages(workspace)
        messages.append(message)
        _write_messages(workspace, messages)

    if task_key:
        append_project_board_note(
            workspace,
            f"mailbox:{kind}:{sender}->{recipient}: {message_body}",
            task_id=task_key,
        )
    return message


def read_inbox(
    workspace: str,
    role: str,
    task_id: str = "",
    include_acknowledged: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    recipient = safe_id(role)
    task_key = safe_id(task_id)
    allowed_statuses = {"pending", "acknowledged"} if include_acknowledged else {"pending"}
    selected: list[dict[str, Any]] = []

    for message in load_mailbox_messages(workspace):
        if safe_id(str(message.get("to_role") or "")) != recipient:
            continue
        if task_key and safe_id(str(message.get("task_id") or "")) != task_key:
            continue
        if str(message.get("status") or "pending") not in allowed_statuses:
            continue
        # 개선 1: 만료된 메시지 제외
        if _is_expired(message):
            continue
        selected.append(message)

    # 개선 2: blocker 우선, 그 다음 타입 순서, 마지막에 생성 시각 순
    selected.sort(key=lambda m: (
        _MESSAGE_PRIORITY.get(str(m.get("type") or "result"), 99),
        str(m.get("created_at") or ""),
    ))

    if limit <= 0:
        return selected
    return selected[:limit]


def ack_mailbox_message(workspace: str, message_id: str, role: str = "") -> bool:
    target_id = safe_id(message_id)
    actor_role = safe_id(role)
    if not target_id:
        return False

    # Bug 1 수정: locked_file로 동시 ack 경합 방지
    messages_path = _messages_path(workspace)
    with locked_file(messages_path):
        messages = load_mailbox_messages(workspace)
        updated = False
        for message in messages:
            if safe_id(str(message.get("message_id") or "")) != target_id:
                continue
            if actor_role and safe_id(str(message.get("to_role") or "")) != actor_role:
                continue
            message["status"] = "acknowledged"
            message["acked_at"] = now_iso()
            message["acked_by"] = actor_role or safe_id(str(message.get("to_role") or ""))
            message["updated_at"] = now_iso()
            updated = True
            break

        if not updated:
            return False
        _write_messages(workspace, messages)
    return True


def mailbox_prompt_digest(workspace: str, role: str = "", task_id: str = "", limit: int = 8, max_chars: int = 2000) -> str:
    if role:
        messages = read_inbox(workspace, role, task_id=task_id, include_acknowledged=False, limit=limit)
        scope = f"Inbox for {safe_id(role)}"
    else:
        all_messages = load_mailbox_messages(workspace)
        messages = [
            m for m in all_messages
            if str(m.get("status") or "pending") == "pending" and not _is_expired(m)
        ]
        # 개선 2: 글로벌 뷰도 우선순위 정렬
        messages.sort(key=lambda m: (
            _MESSAGE_PRIORITY.get(str(m.get("type") or "result"), 99),
            str(m.get("created_at") or ""),
        ))
        if limit > 0:
            messages = messages[:limit]
        scope = "Global mailbox"

    if not messages:
        return "No mailbox messages."

    type_counts: dict[str, int] = {}
    for message in messages:
        kind = safe_id(str(message.get("type") or "message")) or "message"
        type_counts[kind] = type_counts.get(kind, 0) + 1

    lines = [
        f"{scope}: {len(messages)} pending",
        f"Types: {json.dumps(type_counts, ensure_ascii=False, sort_keys=True)}",
    ]
    # 개선 3: blocker 메시지를 별도로 강조
    blockers = [m for m in messages if m.get("type") == "blocker"]
    if blockers:
        lines.append(f"⚠ BLOCKERS ({len(blockers)}): immediate attention required")
    for message in messages:
        files = ", ".join(_clean_list(message.get("related_files"))) or "-"
        ack_required = "yes" if bool(message.get("requires_ack")) else "no"
        prefix = "⚠ " if message.get("type") == "blocker" else "- "
        raw_body = str(message.get("body") or "")
        body_preview = raw_body[:120] + ("..." if len(raw_body) > 120 else "")
        lines.append(
            f"{prefix}id={message.get('message_id')} [{message.get('type')}] {message.get('from_role')} -> {message.get('to_role')} | "
            f"ack={ack_required} | task={message.get('task_id') or '-'} | files={files} | body={body_preview}"
        )
    result = "\n".join(lines)
    if max_chars > 0 and len(result) > max_chars:
        result = result[:max_chars].rstrip() + "\n[...mailbox truncated...]"
    return result
