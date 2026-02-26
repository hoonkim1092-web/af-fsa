import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


NOISE_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<permissions instructions>",
    "<collaboration_mode>",
)


def safe_id(text: str, fallback: str = "id") -> str:
    t = str(text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or fallback


def safe_key(text: str, fallback: str = "entry") -> str:
    cleaned = "".join([c for c in str(text or "") if c.isalnum() or c in (" ", "_", "-")]).strip()
    if not cleaned:
        return fallback
    return cleaned[:120]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)].rstrip() + "..."


def resolve_global_root(repo_root: Path, user_key_raw: str) -> tuple[str, Path]:
    user_key = safe_id(user_key_raw, fallback="")
    if not user_key:
        raise ValueError("missing_user_key")

    override = str(os.getenv("AGENT_GLOBAL_PROJECT_ROOT", "")).strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = (repo_root / "projects" / f"global_{user_key}").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return user_key, root


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_env_value(env_path: Path, key: str) -> str:
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""
    for line in lines:
        raw = str(line).strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        if str(k).strip() != key:
            continue
        return str(v).strip().strip('"').strip("'")
    return ""


def read_windows_user_env(key: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            value, _typ = winreg.QueryValueEx(k, key)
            return str(value or "").strip()
    except Exception:
        return ""


def is_noise(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return True
    if len(s) < 2:
        return True
    lower = s.lower()
    for prefix in NOISE_PREFIXES:
        if lower.startswith(prefix.lower()):
            return True
    return False


def extract_chat_text(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    if str(row.get("type") or "") != "response_item":
        return ""
    payload = row.get("payload", {})
    if not isinstance(payload, dict):
        return ""
    role = str(payload.get("role") or "")
    if role not in ("user", "assistant"):
        return ""

    parts = payload.get("content")
    if not isinstance(parts, list):
        return ""

    text_fields = {
        "user": ("input_text", "text"),
        "assistant": ("output_text", "text"),
    }
    accepted = text_fields.get(role, ("text",))
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        ptype = str(part.get("type") or "")
        if ptype not in accepted:
            continue
        text = str(part.get("text") or "").strip()
        if text:
            chunks.append(text)

    full = "\n".join(chunks).strip()
    if not full:
        return ""
    return f"[{role}] {full}"


def iter_session_files(sessions_root: Path) -> list[Path]:
    if not sessions_root.exists():
        return []
    files = [p for p in sessions_root.rglob("rollout-*.jsonl") if p.is_file()]
    files.sort(key=lambda p: p.as_posix())
    return files


def resolve_sessions_root(override_path: str) -> Path:
    if str(override_path or "").strip():
        return Path(str(override_path).strip()).expanduser().resolve()

    env_direct = str(os.getenv("CODEX_SESSIONS_ROOT", "")).strip()
    if env_direct:
        return Path(env_direct).expanduser().resolve()

    candidates: list[Path] = []
    codex_home = str(os.getenv("CODEX_HOME", "")).strip()
    if codex_home:
        candidates.append(Path(codex_home).expanduser() / "sessions")

    candidates.append(Path.home() / ".codex" / "sessions")

    win_profile = str(os.getenv("USERPROFILE", "")).strip()
    if win_profile:
        candidates.append(Path(win_profile) / ".codex" / "sessions")

    home = str(os.getenv("HOME", "")).strip()
    if home:
        candidates.append(Path(home) / ".codex" / "sessions")

    seen: set[str] = set()
    uniq: list[Path] = []
    for c in candidates:
        key = c.expanduser().as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c.expanduser())

    for c in uniq:
        try:
            if c.exists():
                return c.resolve()
        except Exception:
            continue
    return (uniq[0] if uniq else (Path.home() / ".codex" / "sessions")).resolve()


def collect_new_events(
    files: list[Path],
    cursor_file: str,
    cursor_line: int,
) -> tuple[list[dict], str, int]:
    events: list[dict] = []
    last_seen_file = cursor_file
    last_seen_line = cursor_line

    started = not bool(cursor_file)
    for path in files:
        rel = path.as_posix()
        if not started:
            if rel == cursor_file:
                started = True
            else:
                continue

        start_line = cursor_line + 1 if rel == cursor_file else 0
        try:
            with path.open("r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    if idx < start_line:
                        continue
                    line = line.lstrip("\ufeff").strip()
                    if not line:
                        last_seen_file = rel
                        last_seen_line = idx
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        last_seen_file = rel
                        last_seen_line = idx
                        continue

                    text = extract_chat_text(row)
                    if text and (not is_noise(text)):
                        events.append(
                            {
                                "file": rel,
                                "line": idx,
                                "timestamp": str(row.get("timestamp") or ""),
                                "text": text,
                            }
                        )

                    last_seen_file = rel
                    last_seen_line = idx
        except Exception:
            continue

    return events, last_seen_file, last_seen_line


def write_memory_entries(memory_dir: Path, events: list[dict], user_key: str) -> int:
    memory_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for ev in events:
        text = str(ev.get("text") or "").strip()
        if not text:
            continue
        ts = str(ev.get("timestamp") or "")
        key_seed = f"{ev.get('file')}:{ev.get('line')}:{text[:80]}"
        digest = hashlib.sha1(key_seed.encode("utf-8")).hexdigest()[:12]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        key = safe_key(text, fallback="codex_message")
        file_name = f"{safe_id(key, fallback='entry')}_{digest}_{stamp}.json"

        summary = truncate(text.replace("\r", " ").replace("\n", " "), 260)
        now = now_iso()
        record = {
            "key": key,
            "value": summary,
            "category": "codex_chat",
            "agent_id": "general",
            "memory_scope": "global",
            "source": "codex_session_bridge",
            "global_user_key": user_key,
            "created_at": now,
            "updated_at": now,
            "details": {
                "text": truncate(text, 4000),
                "event_timestamp": ts,
                "session_file": str(ev.get("file") or ""),
                "session_line": int(ev.get("line") or 0),
            },
        }
        save_json(memory_dir / file_name, record)
        written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirror Codex CLI session user messages into global memory.")
    parser.add_argument("--repo-root", default="", help="agent-factory root path")
    parser.add_argument("--sessions-root", default="", help="override Codex sessions root")
    parser.add_argument("--bootstrap-limit", type=int, default=120, help="max entries to import on first run")
    parser.add_argument("--max-write", type=int, default=240, help="max entries to write in one run")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if str(args.repo_root).strip() else Path(__file__).resolve().parents[1]
    user_key_raw = str(os.getenv("AGENT_GLOBAL_USER_KEY", "")).strip()
    if not user_key_raw:
        user_key_raw = read_env_value(repo_root / ".env", "AGENT_GLOBAL_USER_KEY")
    if not user_key_raw:
        user_key_raw = read_windows_user_env("AGENT_GLOBAL_USER_KEY")
    if not user_key_raw:
        print(json.dumps({"ok": True, "skipped": True, "reason": "missing_global_user_key"}, ensure_ascii=False))
        return 0

    try:
        user_key, global_root = resolve_global_root(repo_root, user_key_raw)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1

    sessions_root = resolve_sessions_root(args.sessions_root)
    files = iter_session_files(sessions_root)
    if not files:
        print(json.dumps({"ok": True, "skipped": True, "reason": "no_session_files", "sessions_root": str(sessions_root)}, ensure_ascii=False))
        return 0

    state_dir = global_root / "data" / "memory" / "codex" / "_bridge_state"
    state_path = state_dir / "session_cursor.json"
    state = load_json(state_path)
    cursor_file = str(state.get("last_file") or "")
    cursor_line = int(state.get("last_line") or -1)
    if cursor_line < -1:
        cursor_line = -1

    # Recover automatically when old session file was rotated/deleted.
    known_files = {p.as_posix() for p in files}
    if cursor_file and cursor_file not in known_files:
        cursor_file = ""
        cursor_line = -1

    events, last_seen_file, last_seen_line = collect_new_events(files, cursor_file=cursor_file, cursor_line=cursor_line)
    first_run = not bool(cursor_file)
    if first_run and len(events) > max(0, int(args.bootstrap_limit)):
        events = events[-max(0, int(args.bootstrap_limit)) :]
    if len(events) > max(0, int(args.max_write)):
        events = events[-max(0, int(args.max_write)) :]

    memory_dir = global_root / "data" / "memory" / "general" / "codex_chat"
    written = write_memory_entries(memory_dir, events, user_key=user_key)

    save_json(
        state_path,
        {
            "last_file": last_seen_file,
            "last_line": last_seen_line,
            "updated_at": now_iso(),
            "user_key": user_key,
            "sessions_root": str(sessions_root),
            "written_last_run": written,
        },
    )

    print(
        json.dumps(
            {
                "ok": True,
                "written": written,
                "events_seen": len(events),
                "global_root": str(global_root),
                "memory_dir": str(memory_dir),
                "state_path": str(state_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
