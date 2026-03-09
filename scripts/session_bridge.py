import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


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
    cleaned = "".join(c for c in str(text or "") if c.isalnum() or c in (" ", "_", "-")).strip()
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

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as handle:
            value, _typ = winreg.QueryValueEx(handle, key)
            return str(value or "").strip()
    except Exception:
        return ""


def is_noise(text: str) -> bool:
    s = str(text or "").strip()
    if not s or len(s) < 2:
        return True
    lower = s.lower()
    return any(lower.startswith(prefix.lower()) for prefix in NOISE_PREFIXES)


def _extract_text_chunks(value: object) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []

    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(_extract_text_chunks(item))
        return chunks

    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return _extract_text_chunks(value.get("text"))

        for key in ("content", "parts", "message", "value"):
            if key in value:
                chunks = _extract_text_chunks(value.get(key))
                if chunks:
                    return chunks
        return []

    return []


def _join_chunks(chunks: list[str]) -> str:
    return "\n".join(str(chunk).strip() for chunk in chunks if str(chunk).strip()).strip()


def _extract_codex_chat_text(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    if str(row.get("type") or "") != "response_item":
        return ""
    payload = row.get("payload", {})
    if not isinstance(payload, dict):
        return ""
    role = str(payload.get("role") or "").strip().lower()
    if role not in ("user", "assistant"):
        return ""

    parts = payload.get("content")
    if not isinstance(parts, list):
        return ""

    accepted = {
        "user": ("input_text", "text"),
        "assistant": ("output_text", "text"),
    }
    allowed = accepted.get(role, ("text",))
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") not in allowed:
            continue
        chunks.extend(_extract_text_chunks(part))

    text = _join_chunks(chunks)
    return f"[{role}] {text}" if text else ""


def _extract_generic_role_content_text(row: dict) -> str:
    if not isinstance(row, dict):
        return ""

    candidates = [row]
    for key in ("payload", "message", "data"):
        nested = row.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)

    for candidate in candidates:
        role = str(candidate.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        chunks = _extract_text_chunks(candidate.get("content"))
        if not chunks:
            chunks = _extract_text_chunks(candidate.get("parts"))
        if not chunks and isinstance(candidate.get("text"), str):
            chunks = _extract_text_chunks(candidate.get("text"))
        text = _join_chunks(chunks)
        if text:
            return f"[{role}] {text}"
    return ""


@dataclass(frozen=True)
class SessionBridgeProvider:
    provider_id: str
    session_globs: tuple[str, ...]
    direct_root_envs: tuple[str, ...]
    home_envs: tuple[str, ...]
    home_suffixes: tuple[str, ...]
    memory_category: str
    source_name: str
    extractors: tuple[Callable[[dict], str], ...]

    def extract_chat_text(self, row: dict) -> str:
        for extractor in self.extractors:
            text = extractor(row)
            if text:
                return text
        return ""


PROVIDERS: dict[str, SessionBridgeProvider] = {
    "codex": SessionBridgeProvider(
        provider_id="codex",
        session_globs=("rollout-*.jsonl",),
        direct_root_envs=("CODEX_SESSIONS_ROOT",),
        home_envs=("CODEX_HOME",),
        home_suffixes=(".codex/sessions",),
        memory_category="codex_chat",
        source_name="codex_session_bridge",
        extractors=(_extract_codex_chat_text, _extract_generic_role_content_text),
    ),
    "claude": SessionBridgeProvider(
        provider_id="claude",
        session_globs=("*.jsonl",),
        direct_root_envs=("CLAUDE_SESSIONS_ROOT",),
        home_envs=("CLAUDE_HOME",),
        home_suffixes=(".claude/sessions",),
        memory_category="claude_chat",
        source_name="claude_session_bridge",
        extractors=(_extract_generic_role_content_text,),
    ),
    "gemini": SessionBridgeProvider(
        provider_id="gemini",
        session_globs=("*.jsonl",),
        direct_root_envs=("GEMINI_SESSIONS_ROOT",),
        home_envs=("GEMINI_HOME",),
        home_suffixes=(".gemini/sessions",),
        memory_category="gemini_chat",
        source_name="gemini_session_bridge",
        extractors=(_extract_generic_role_content_text,),
    ),
}


def get_provider(provider_id: str) -> SessionBridgeProvider:
    key = safe_id(provider_id, fallback="")
    if key not in PROVIDERS:
        raise ValueError(f"unsupported_provider:{provider_id}")
    return PROVIDERS[key]


def resolve_sessions_root(provider: SessionBridgeProvider, override_path: str | Path | None) -> Path:
    if str(override_path or "").strip():
        return Path(str(override_path).strip()).expanduser().resolve()

    for env_name in provider.direct_root_envs:
        env_direct = str(os.getenv(env_name, "")).strip()
        if env_direct:
            return Path(env_direct).expanduser().resolve()

    candidates: list[Path] = []

    for env_name in provider.home_envs:
        home_root = str(os.getenv(env_name, "")).strip()
        if home_root:
            candidates.append(Path(home_root).expanduser() / "sessions")

    home_roots = [Path.home()]
    win_profile = str(os.getenv("USERPROFILE", "")).strip()
    if win_profile:
        home_roots.append(Path(win_profile))
    home = str(os.getenv("HOME", "")).strip()
    if home:
        home_roots.append(Path(home))

    seen_roots: set[str] = set()
    uniq_home_roots: list[Path] = []
    for root in home_roots:
        key = root.expanduser().as_posix().lower()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        uniq_home_roots.append(root.expanduser())

    for root in uniq_home_roots:
        for suffix in provider.home_suffixes:
            candidates.append(root / suffix)

    seen: set[str] = set()
    uniq_candidates: list[Path] = []
    for candidate in candidates:
        key = candidate.expanduser().as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
        uniq_candidates.append(candidate.expanduser())

    for candidate in uniq_candidates:
        try:
            if candidate.exists():
                return candidate.resolve()
        except Exception:
            continue

    fallback = uniq_candidates[0] if uniq_candidates else (Path.home() / provider.home_suffixes[0])
    return fallback.resolve()


def iter_session_files(sessions_root: Path, patterns: tuple[str, ...]) -> list[Path]:
    if not sessions_root.exists():
        return []

    files: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sessions_root.rglob(pattern):
            if not path.is_file():
                continue
            key = path.resolve().as_posix().lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(path.resolve())
    files.sort(key=lambda path: path.as_posix())
    return files


def collect_new_events(
    files: list[Path],
    cursor_file: str,
    cursor_line: int,
    provider: SessionBridgeProvider,
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
            with path.open("r", encoding="utf-8") as handle:
                for idx, line in enumerate(handle):
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

                    text = provider.extract_chat_text(row)
                    if text and not is_noise(text):
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


def write_memory_entries(
    memory_dir: Path,
    events: list[dict],
    user_key: str,
    provider: SessionBridgeProvider,
) -> int:
    memory_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for event in events:
        text = str(event.get("text") or "").strip()
        if not text:
            continue
        ts = str(event.get("timestamp") or "")
        key_seed = f"{event.get('file')}:{event.get('line')}:{text[:80]}"
        digest = hashlib.sha1(key_seed.encode("utf-8")).hexdigest()[:12]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        key = safe_key(text, fallback=f"{provider.provider_id}_message")
        file_name = f"{safe_id(key, fallback='entry')}_{digest}_{stamp}.json"

        summary = truncate(text.replace("\r", " ").replace("\n", " "), 260)
        now = now_iso()
        record = {
            "key": key,
            "value": summary,
            "category": provider.memory_category,
            "agent_id": "general",
            "memory_scope": "global",
            "source": provider.source_name,
            "global_user_key": user_key,
            "created_at": now,
            "updated_at": now,
            "details": {
                "text": truncate(text, 4000),
                "event_timestamp": ts,
                "session_file": str(event.get("file") or ""),
                "session_line": int(event.get("line") or 0),
                "provider": provider.provider_id,
            },
        }
        save_json(memory_dir / file_name, record)
        written += 1
    return written


def _resolve_user_key(repo_root: Path) -> str:
    user_key_raw = str(os.getenv("AGENT_GLOBAL_USER_KEY", "")).strip()
    if not user_key_raw:
        user_key_raw = read_env_value(repo_root / ".env", "AGENT_GLOBAL_USER_KEY")
    if not user_key_raw:
        user_key_raw = read_windows_user_env("AGENT_GLOBAL_USER_KEY")
    return user_key_raw


def run_bridge(
    provider_id: str,
    repo_root: str | Path,
    sessions_root: str | Path | None = None,
    bootstrap_limit: int = 120,
    max_write: int = 240,
) -> dict:
    provider = get_provider(provider_id)
    repo_root_path = Path(repo_root).resolve()

    user_key_raw = _resolve_user_key(repo_root_path)
    if not user_key_raw:
        return {
            "ok": True,
            "skipped": True,
            "reason": "missing_global_user_key",
            "provider": provider.provider_id,
        }

    try:
        user_key, global_root = resolve_global_root(repo_root_path, user_key_raw)
    except Exception as exc:
        return {"ok": False, "provider": provider.provider_id, "error": str(exc)}

    sessions_root_path = resolve_sessions_root(provider, sessions_root)
    files = iter_session_files(sessions_root_path, provider.session_globs)
    if not files:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_session_files",
            "provider": provider.provider_id,
            "sessions_root": str(sessions_root_path),
        }

    state_dir = global_root / "data" / "memory" / provider.provider_id / "_bridge_state"
    state_path = state_dir / "session_cursor.json"
    state = load_json(state_path)
    cursor_file = str(state.get("last_file") or "")
    cursor_line = int(state.get("last_line") or -1)
    if cursor_line < -1:
        cursor_line = -1

    known_files = {path.as_posix() for path in files}
    if cursor_file and cursor_file not in known_files:
        cursor_file = ""
        cursor_line = -1

    events, last_seen_file, last_seen_line = collect_new_events(
        files,
        cursor_file=cursor_file,
        cursor_line=cursor_line,
        provider=provider,
    )
    first_run = not bool(cursor_file)
    if first_run and len(events) > max(0, int(bootstrap_limit)):
        events = events[-max(0, int(bootstrap_limit)) :]
    if len(events) > max(0, int(max_write)):
        events = events[-max(0, int(max_write)) :]

    memory_dir = global_root / "data" / "memory" / "general" / provider.memory_category
    written = write_memory_entries(memory_dir, events, user_key=user_key, provider=provider)

    save_json(
        state_path,
        {
            "last_file": last_seen_file,
            "last_line": last_seen_line,
            "updated_at": now_iso(),
            "user_key": user_key,
            "sessions_root": str(sessions_root_path),
            "provider": provider.provider_id,
            "written_last_run": written,
        },
    )

    return {
        "ok": True,
        "provider": provider.provider_id,
        "written": written,
        "events_seen": len(events),
        "global_root": str(global_root),
        "memory_dir": str(memory_dir),
        "state_path": str(state_path),
        "sessions_root": str(sessions_root_path),
    }


def main(argv: list[str] | None = None, default_provider: str | None = None) -> int:
    if default_provider:
        provider = get_provider(default_provider)
        description = f"Mirror {provider.provider_id} CLI session messages into global memory."
    else:
        description = "Mirror supported CLI session messages into global memory."

    parser = argparse.ArgumentParser(description=description)
    if default_provider:
        provider_id = default_provider
    else:
        parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    parser.add_argument("--repo-root", default="", help="agent-factory root path")
    parser.add_argument("--sessions-root", default="", help="override provider sessions root")
    parser.add_argument("--bootstrap-limit", type=int, default=120, help="max entries to import on first run")
    parser.add_argument("--max-write", type=int, default=240, help="max entries to write in one run")
    args = parser.parse_args(argv)
    if not default_provider:
        provider_id = args.provider

    repo_root = Path(args.repo_root).resolve() if str(args.repo_root).strip() else Path(__file__).resolve().parents[1]
    result = run_bridge(
        provider_id=provider_id,
        repo_root=repo_root,
        sessions_root=args.sessions_root,
        bootstrap_limit=args.bootstrap_limit,
        max_write=args.max_write,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok", False) else 1


def main_for_provider(provider_id: str, argv: list[str] | None = None) -> int:
    return main(argv=argv, default_provider=provider_id)


if __name__ == "__main__":
    raise SystemExit(main())
