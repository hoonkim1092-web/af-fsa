import argparse
import base64
import fnmatch
import hashlib
import json
import os
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_EXCLUDE_GLOBS = (
    "docs/task.md",
)
REPO_ROOT_ALIASES = ("@repo", "@root", ".", "./", ".\\")


def load_dotenv_simple(root: Path) -> None:
    env_path = root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def load_dotenv_override(env_path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            loaded[k] = v
    return loaded


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(text: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in (text or "").strip().lower())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def normalize_match_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def sync_project_id(text: str) -> str:
    # Keep sync key stable across variants like:
    # logi-mind-v22 / logi_mind_v22 / logi_mind_v22+
    return safe_id(text).replace("-", "_")


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return False


def _project_sync_id_for_path(repo_root: Path, project_root: Path) -> str:
    repo_name_key = normalize_match_key(repo_root.name)
    project_name_key = normalize_match_key(project_root.name)
    projects_root = (repo_root / "projects").resolve()
    project_root_resolved = project_root.resolve()

    # Preserve the existing sync key for the workspace repo root.
    if _same_path(project_root_resolved, repo_root):
        return sync_project_id(repo_root.name)

    # Avoid colliding with the repo-root key when a local project shares the same name.
    if project_root_resolved.parent == projects_root and project_name_key == repo_name_key:
        return f"project_{sync_project_id(project_root.name)}"

    return sync_project_id(project_root.name)


def _is_repo_root_alias(repo_root: Path, raw: str) -> bool:
    token = str(raw or "").strip()
    if not token:
        return False
    if token.lower() in REPO_ROOT_ALIASES:
        return True
    try:
        candidate = Path(token).expanduser()
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        return candidate.exists() and candidate.is_dir() and _same_path(candidate, repo_root)
    except Exception:
        return False


def resolve_project_root(repo_root: Path, project_input: str) -> tuple[str, Path]:
    projects_root = repo_root / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)

    raw = str(project_input or "").strip()
    safe = safe_id(raw)
    key = normalize_match_key(raw)

    # 0) Explicit current repo root aliases only.
    if _is_repo_root_alias(repo_root, raw):
        return _project_sync_id_for_path(repo_root, repo_root), repo_root

    # 1) Existing path as-given or relative to the repo root.
    direct_candidates = []
    if raw:
        direct_path = Path(raw).expanduser()
        direct_candidates.append(direct_path if direct_path.is_absolute() else repo_root / direct_path)
    for cand in direct_candidates:
        if cand.exists() and cand.is_dir():
            resolved = cand.resolve()
            return _project_sync_id_for_path(repo_root, resolved), resolved

    # 2) Local projects/ first so a nested project can win over the repo-root name.
    exact_dir = projects_root / raw
    if raw and exact_dir.exists() and exact_dir.is_dir():
        return _project_sync_id_for_path(repo_root, exact_dir), exact_dir

    safe_dir = projects_root / safe
    if safe and safe_dir.exists() and safe_dir.is_dir():
        return _project_sync_id_for_path(repo_root, safe_dir), safe_dir

    if key:
        matches = []
        for p in projects_root.iterdir():
            if not p.is_dir():
                continue
            if normalize_match_key(p.name) == key:
                matches.append(p)
        if len(matches) == 1:
            return _project_sync_id_for_path(repo_root, matches[0]), matches[0]

    # 3) Sibling project directory support next (e.g. D:\logi-mind-v22).
    siblings_root = repo_root.parent
    sibling_candidates = []
    if raw:
        sibling_candidates.append(siblings_root / raw)
    if safe:
        sibling_candidates.append(siblings_root / safe)
    for cand in sibling_candidates:
        if cand.exists() and cand.is_dir() and not _same_path(cand, repo_root):
            return _project_sync_id_for_path(repo_root, cand), cand

    if key:
        sibling_matches = []
        for p in siblings_root.iterdir():
            if not p.is_dir():
                continue
            if _same_path(p, repo_root):
                continue
            if normalize_match_key(p.name) == key:
                sibling_matches.append(p)
        if len(sibling_matches) == 1:
            return _project_sync_id_for_path(repo_root, sibling_matches[0]), sibling_matches[0]

    # 4) Current repo root is the last fallback when only the name matches.
    if key and key == normalize_match_key(repo_root.name):
        return _project_sync_id_for_path(repo_root, repo_root), repo_root

    # 5) Create canonical local directory if nothing matched.
    chosen = projects_root / safe
    chosen.mkdir(parents=True, exist_ok=True)
    return _project_sync_id_for_path(repo_root, chosen), chosen


def resolve_global_root(repo_root: Path, user_key: str) -> tuple[str, Path]:
    projects_root = repo_root / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)

    safe_user = safe_id(user_key)
    if not safe_user:
        raise ValueError("invalid_user_key")

    env_root = str(os.getenv("AGENT_GLOBAL_PROJECT_ROOT", "")).strip()
    if env_root:
        root = Path(env_root).expanduser().resolve()
    else:
        root = (projects_root / f"global_{safe_user}").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return safe_user, root


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    raise UnicodeDecodeError("unknown", raw, 0, 1, "unsupported text encoding")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def b64e(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def b64d(text: str) -> str:
    return base64.b64decode(text.encode("ascii")).decode("utf-8")


def http_json(method: str, url: str, headers: dict, payload: dict | list | None = None):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def try_read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _include_text_file(project_root: Path, files: dict[str, str], rel: str) -> None:
    p = project_root / rel
    if p.exists() and p.is_file():
        if rel in files:
            return
        files[rel] = b64e(read_text(p))


def _is_excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    rel_norm = str(rel).replace("\\", "/").lower()
    return any(fnmatch.fnmatch(rel_norm, pat) for pat in patterns)


def _include_tree(
    project_root: Path,
    files: dict[str, str],
    root_rel: str,
    exts: tuple[str, ...],
    exclude_globs: tuple[str, ...] = (),
) -> None:
    root = project_root / root_rel
    if not root.exists():
        return
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        rel = p.relative_to(project_root).as_posix()
        if rel in files or _is_excluded(rel, exclude_globs):
            continue
        files[rel] = b64e(read_text(p))


def _include_root_files(
    project_root: Path,
    files: dict[str, str],
    exts: tuple[str, ...],
    exclude_globs: tuple[str, ...] = (),
) -> None:
    for p in project_root.glob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        rel = p.relative_to(project_root).as_posix()
        if rel in files or _is_excluded(rel, exclude_globs):
            continue
        files[rel] = b64e(read_text(p))


def _parse_exclude_globs() -> tuple[str, ...]:
    raw = str(os.getenv("CONTEXT_SYNC_EXCLUDE", "")).strip()
    parsed = [p.strip().replace("\\", "/").lower() for p in raw.split(",") if p.strip()]
    return tuple(dict.fromkeys([*DEFAULT_EXCLUDE_GLOBS, *parsed]))


def _apply_exclude_globs(files: dict[str, str], patterns: tuple[str, ...]) -> dict[str, str]:
    if not files or not patterns:
        return files
    kept: dict[str, str] = {}
    for rel, content in files.items():
        rel_norm = str(rel).replace("\\", "/")
        if _is_excluded(rel_norm, patterns):
            continue
        kept[rel_norm] = content
    return kept


def _collect_run_files(project_root: Path, files: dict[str, str], run_limit: int, agent_id: str | None) -> None:
    runs_root = project_root / "runs"
    if not runs_root.exists():
        return
    if agent_id is None:
        # Project scope: include all known run artifacts.
        run_dirs = sorted([p for p in runs_root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
        for run_dir in run_dirs:
            for f in run_dir.iterdir():
                if not f.is_file():
                    continue
                if f.name in ("chat_trace.json", "state.json") or f.name.endswith("_meta.yaml") or f.name.endswith("_skill.py"):
                    rel = f.relative_to(project_root).as_posix()
                    if rel in files:
                        continue
                    files[rel] = b64e(read_text(f))
        return

    # Agent scope: include recent runs for this agent only.
    candidates = sorted(runs_root.glob("*/chat_trace.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    kept = 0
    for trace_path in candidates:
        if kept >= max(0, run_limit):
            break
        trace_json = try_read_json(trace_path)
        trace_agent = safe_id(trace_json.get("agent_name") or trace_json.get("agent_role"))
        if agent_id and trace_agent != agent_id:
            continue
        run_dir = trace_path.parent
        for name in ["chat_trace.json", "state.json"]:
            f = run_dir / name
            if f.exists() and f.is_file():
                rel = f.relative_to(project_root).as_posix()
                if rel in files:
                    continue
                files[rel] = b64e(read_text(f))
        kept += 1


def collect_snapshot(project_root: Path, project_id: str, agent_id: str | None, run_limit: int) -> dict:
    files: dict[str, str] = {}
    exclude_globs = _parse_exclude_globs()

    # Project shared context files.
    for rel in [
        "context_schema.yaml",
        "dashboard.json",
        "policies.yaml",
        "settings.yaml",
        "skill-lock.yaml",
        "workflow.yaml",
    ]:
        _include_text_file(project_root, files, rel)

    # Agent profile file (project-local).
    if agent_id:
        rel = f"agents/{agent_id}.yaml"
        if not _is_excluded(rel, exclude_globs):
            _include_text_file(project_root, files, rel)
    else:
        _include_tree(project_root, files, "agents", (".yaml", ".yml", ".md", ".txt"), exclude_globs=exclude_globs)

    # Memory: agent-scoped by default, fallback legacy for migration.
    mem_root = project_root / "data" / "memory"
    if mem_root.exists():
        if agent_id:
            agent_mem = mem_root / agent_id
            roots = [agent_mem]
        else:
            roots = [mem_root]

        seen = set()
        for root in roots:
            if not root.exists():
                continue
            for p in root.rglob("*.json"):
                rel = p.relative_to(project_root).as_posix()
                if rel in seen:
                    continue
                files[rel] = b64e(read_text(p))
                seen.add(rel)
        if agent_id:
            # Backward compatibility: include only legacy flat layout files.
            for p in mem_root.rglob("*.json"):
                rel_from_mem = p.relative_to(mem_root).as_posix()
                depth = len(rel_from_mem.split("/"))
                if depth != 2:
                    continue
                rel = p.relative_to(project_root).as_posix()
                if rel in seen:
                    continue
                files[rel] = b64e(read_text(p))
                seen.add(rel)

    _collect_run_files(project_root, files, run_limit=run_limit, agent_id=agent_id)

    # Project artifacts (work outputs) in text formats.
    _include_tree(project_root, files, "artifacts", (".json", ".yaml", ".yml", ".md", ".txt", ".csv", ".log", ".sql", ".py"), exclude_globs=exclude_globs)
    _include_tree(project_root, files, "data", (".json", ".yaml", ".yml", ".md", ".txt", ".csv", ".log", ".sql", ".py"), exclude_globs=exclude_globs)
    _include_tree(project_root, files, "runs", (".json", ".yaml", ".yml", ".md", ".txt", ".csv", ".log", ".sql", ".py"), exclude_globs=exclude_globs)
    _include_tree(project_root, files, "docs", (".json", ".yaml", ".yml", ".md", ".txt"), exclude_globs=exclude_globs)
    _include_tree(project_root, files, "planning", (".json", ".yaml", ".yml", ".md", ".txt"), exclude_globs=exclude_globs)
    _include_tree(project_root, files, "syncCompyne", (".json", ".yaml", ".yml", ".md", ".txt", ".py", ".csv", ".log", ".sql"), exclude_globs=exclude_globs)
    _include_tree(project_root, files, "inbox", (".json", ".yaml", ".yml", ".md", ".txt", ".csv"), exclude_globs=exclude_globs)
    _include_tree(project_root, files, "processed", (".json", ".yaml", ".yml", ".md", ".txt", ".csv"), exclude_globs=exclude_globs)
    _include_root_files(project_root, files, (".md", ".json", ".yaml", ".yml", ".txt", ".log"), exclude_globs=exclude_globs)
    files = _apply_exclude_globs(files, exclude_globs)

    digest_src = json.dumps(files, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(digest_src).hexdigest()
    return {
        "schema_version": 2,
        "captured_at": now_iso(),
        "project_id": project_id,
        "agent_id": agent_id,
        "scope": "agent" if agent_id else "project",
        "file_count": len(files),
        "sha256": digest,
        "files": files,
    }


def collect_global_snapshot(global_root: Path, user_key: str, run_limit: int) -> dict:
    files: dict[str, str] = {}
    exclude_globs = _parse_exclude_globs()

    # Global memory files are the primary source of cross-project continuity.
    _include_tree(global_root, files, "data/memory", (".json",), exclude_globs=exclude_globs)

    # Optional lightweight global artifacts/notes.
    _include_tree(global_root, files, "artifacts/global", (".json", ".yaml", ".yml", ".md", ".txt"), exclude_globs=exclude_globs)
    _include_tree(global_root, files, "docs/global", (".json", ".yaml", ".yml", ".md", ".txt"), exclude_globs=exclude_globs)

    # Include recent run traces only for global profile (bounded by run_limit).
    runs_root = global_root / "runs"
    if runs_root.exists():
        traces = sorted(runs_root.glob("*/chat_trace.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        kept = 0
        for trace in traces:
            if kept >= max(0, run_limit):
                break
            run_dir = trace.parent
            for name in ("chat_trace.json", "state.json"):
                f = run_dir / name
                if not f.exists() or not f.is_file():
                    continue
                rel = f.relative_to(global_root).as_posix()
                if rel in files or _is_excluded(rel, exclude_globs):
                    continue
                files[rel] = b64e(read_text(f))
            kept += 1

    files = _apply_exclude_globs(files, exclude_globs)
    digest_src = json.dumps(files, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(digest_src).hexdigest()
    return {
        "schema_version": 1,
        "captured_at": now_iso(),
        "scope": "global",
        "user_key": safe_id(user_key),
        "file_count": len(files),
        "sha256": digest,
        "files": files,
    }


def write_snapshot(project_root: Path, payload: dict, overwrite: bool = True) -> tuple[int, int]:
    files = payload.get("files", {}) if isinstance(payload, dict) else {}
    written = 0
    skipped = 0
    root_resolved = project_root.resolve()
    for rel, encoded in files.items():
        rel_norm = str(rel).replace("\\", "/")
        target = (project_root / rel_norm).resolve()
        if root_resolved not in [target, *target.parents]:
            skipped += 1
            continue
        if (not overwrite) and target.exists():
            skipped += 1
            continue
        content = b64d(str(encoded))
        write_text(target, content)
        written += 1
    return written, skipped


def make_scope_key(project_id: str, agent_id: str | None) -> str:
    if agent_id:
        return f"{project_id}__agent__{agent_id}"
    return project_id


def parse_project_inputs(single: str, multi: str) -> list[str]:
    items: list[str] = []
    if str(single or "").strip():
        items.append(str(single).strip())
    if str(multi or "").strip():
        for token in str(multi).split(","):
            t = token.strip()
            if t:
                items.append(t)
    dedup: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return dedup


def main():
    parser = argparse.ArgumentParser(description="Sync project/agent context via Supabase.")
    parser.add_argument("--project", "-p", default="", help="single project id")
    parser.add_argument("--projects", default="", help="multiple project ids (comma separated)")
    parser.add_argument("--agent", "-a", default="", help="optional agent id/name for agent-scoped sync")
    parser.add_argument("--scope", choices=["default", "project", "agent", "global"], default="default", help="sync scope")
    parser.add_argument("--user-key", default="", help="global user key (used with --scope global)")
    parser.add_argument("--mode", "-m", choices=["push", "pull"], required=True, help="sync mode")
    parser.add_argument("--table", default="", help="Supabase table name")
    parser.add_argument("--run-limit", type=int, default=100, help="max run chat traces to include on push")
    parser.add_argument("--no-overwrite", action="store_true", help="do not overwrite local files on pull")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv_simple(repo_root)
    agent_id = safe_id(args.agent) if str(args.agent or "").strip() else None
    scope_mode = str(args.scope or "default").strip().lower()
    if scope_mode == "default":
        scope_mode = "agent" if agent_id else "project"
    if scope_mode == "agent" and not agent_id:
        raise SystemExit("--scope agent requires --agent")

    def _resolve_supabase_fields(env_map: dict[str, str]) -> tuple[str, str, str, str]:
        sb_url = str(env_map.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))).strip().rstrip("/")
        sb_key = str(env_map.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", ""))).strip()
        table = (
            str(args.table or "").strip()
            or str(env_map.get("CONTEXT_SYNC_TABLE", "")).strip()
            or str(os.getenv("CONTEXT_SYNC_TABLE", "")).strip()
            or "project_context_sync"
        )
        machine = str(env_map.get("SYNC_MACHINE_ID", os.getenv("SYNC_MACHINE_ID", socket.gethostname()))).strip()
        return sb_url, sb_key, table, machine

    results: list[dict] = []

    if scope_mode == "global":
        user_key_input = str(args.user_key or os.getenv("AGENT_GLOBAL_USER_KEY", "")).strip()
        if not user_key_input:
            raise SystemExit("--scope global requires --user-key or AGENT_GLOBAL_USER_KEY")

        try:
            user_key, global_root = resolve_global_root(repo_root, user_key_input)
        except Exception as e:
            print(json.dumps({"ok": False, "items": [{"ok": False, "mode": args.mode, "scope": "global", "error": str(e)}]}, ensure_ascii=False, indent=2))
            return

        scope_key = f"user:{user_key}"
        env_map = load_dotenv_override(global_root / ".env")
        sb_url, sb_key, table, machine = _resolve_supabase_fields(env_map)
        if not sb_url or not sb_key:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "items": [
                            {
                                "ok": False,
                                "mode": args.mode,
                                "scope": "global",
                                "user_key": user_key,
                                "scope_key": scope_key,
                                "error": "missing_supabase_env",
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        headers = {
            "apikey": sb_key,
            "Authorization": f"Bearer {sb_key}",
            "Content-Type": "application/json",
        }

        if args.mode == "push":
            try:
                payload = collect_global_snapshot(global_root=global_root, user_key=user_key, run_limit=args.run_limit)
                row = {
                    "project_id": scope_key,
                    "source_machine": machine,
                    "updated_at": now_iso(),
                    "payload": payload,
                    "payload_hash": payload.get("sha256"),
                }
                upsert_headers = dict(headers)
                upsert_headers["Prefer"] = "resolution=merge-duplicates,return=representation"
                url = f"{sb_url}/rest/v1/{table}?on_conflict=project_id"
                res = http_json("POST", url, upsert_headers, [row])
                results.append(
                    {
                        "ok": True,
                        "mode": "push",
                        "scope": "global",
                        "user_key": user_key,
                        "scope_key": scope_key,
                        "global_root": str(global_root),
                        "result": res,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "ok": False,
                        "mode": "push",
                        "scope": "global",
                        "user_key": user_key,
                        "scope_key": scope_key,
                        "error": str(e),
                    }
                )
        else:
            try:
                q = urllib.parse.quote(scope_key, safe="")
                url = f"{sb_url}/rest/v1/{table}?project_id=eq.{q}&select=project_id,source_machine,updated_at,payload&limit=1"
                rows = http_json("GET", url, headers, None) or []
                if not rows:
                    results.append(
                        {
                            "ok": False,
                            "mode": "pull",
                            "scope": "global",
                            "user_key": user_key,
                            "scope_key": scope_key,
                            "error": "not_found",
                        }
                    )
                else:
                    row = rows[0]
                    payload = row.get("payload", {}) if isinstance(row, dict) else {}
                    written, skipped = write_snapshot(global_root, payload, overwrite=(not args.no_overwrite))
                    results.append(
                        {
                            "ok": True,
                            "mode": "pull",
                            "scope": "global",
                            "user_key": user_key,
                            "scope_key": scope_key,
                            "global_root": str(global_root),
                            "source_machine": row.get("source_machine"),
                            "updated_at": row.get("updated_at"),
                            "written": written,
                            "skipped": skipped,
                        }
                    )
            except Exception as e:
                results.append(
                    {
                        "ok": False,
                        "mode": "pull",
                        "scope": "global",
                        "user_key": user_key,
                        "scope_key": scope_key,
                        "error": str(e),
                    }
                )

        print(json.dumps({"ok": all(r.get("ok", False) for r in results), "items": results}, ensure_ascii=False, indent=2))
        return

    project_inputs = parse_project_inputs(args.project, args.projects)
    if not project_inputs:
        raise SystemExit("provide --project or --projects")

    for project_input in project_inputs:
        project_id, project_root = resolve_project_root(repo_root, project_input)
        snapshot_agent = agent_id if scope_mode == "agent" else None
        scope_key = make_scope_key(project_id, snapshot_agent)

        env_map = load_dotenv_override(project_root / ".env")
        sb_url, sb_key, table, machine = _resolve_supabase_fields(env_map)
        if not sb_url or not sb_key:
            results.append(
                {
                    "ok": False,
                    "mode": args.mode,
                    "scope": scope_mode,
                    "project_id": project_id,
                    "agent_id": snapshot_agent,
                    "scope_key": scope_key,
                    "error": "missing_supabase_env",
                }
            )
            continue

        headers = {
            "apikey": sb_key,
            "Authorization": f"Bearer {sb_key}",
            "Content-Type": "application/json",
        }

        if args.mode == "push":
            try:
                payload = collect_snapshot(project_root=project_root, project_id=project_id, agent_id=snapshot_agent, run_limit=args.run_limit)
                row = {
                    "project_id": scope_key,  # stored as scope key for backward-compatible schema
                    "source_machine": machine,
                    "updated_at": now_iso(),
                    "payload": payload,
                    "payload_hash": payload.get("sha256"),
                }
                upsert_headers = dict(headers)
                upsert_headers["Prefer"] = "resolution=merge-duplicates,return=representation"
                url = f"{sb_url}/rest/v1/{table}?on_conflict=project_id"
                res = http_json("POST", url, upsert_headers, [row])
                results.append(
                    {
                        "ok": True,
                        "mode": "push",
                        "scope": scope_mode,
                        "project_id": project_id,
                        "agent_id": snapshot_agent,
                        "scope_key": scope_key,
                        "result": res,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "ok": False,
                        "mode": "push",
                        "scope": scope_mode,
                        "project_id": project_id,
                        "agent_id": snapshot_agent,
                        "scope_key": scope_key,
                        "error": str(e),
                    }
                )
            continue

        try:
            q = urllib.parse.quote(scope_key, safe="")
            url = f"{sb_url}/rest/v1/{table}?project_id=eq.{q}&select=project_id,source_machine,updated_at,payload&limit=1"
            rows = http_json("GET", url, headers, None) or []
            if not rows:
                results.append(
                    {
                        "ok": False,
                        "mode": "pull",
                        "scope": scope_mode,
                        "project_id": project_id,
                        "agent_id": snapshot_agent,
                        "scope_key": scope_key,
                        "error": "not_found",
                    }
                )
                continue
            row = rows[0]
            payload = row.get("payload", {}) if isinstance(row, dict) else {}
            written, skipped = write_snapshot(project_root, payload, overwrite=(not args.no_overwrite))
            results.append(
                {
                    "ok": True,
                    "mode": "pull",
                    "scope": scope_mode,
                    "project_id": project_id,
                    "agent_id": snapshot_agent,
                    "scope_key": scope_key,
                    "source_machine": row.get("source_machine"),
                    "updated_at": row.get("updated_at"),
                    "written": written,
                    "skipped": skipped,
                }
            )
        except Exception as e:
            results.append(
                {
                    "ok": False,
                    "mode": "pull",
                    "scope": scope_mode,
                    "project_id": project_id,
                    "agent_id": snapshot_agent,
                    "scope_key": scope_key,
                    "error": str(e),
                }
            )

    print(json.dumps({"ok": all(r.get("ok", False) for r in results), "items": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
