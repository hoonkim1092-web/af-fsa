import json
from pathlib import Path

import pytest

from scripts.session_bridge import get_provider, run_bridge


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_provider_registry_exposes_codex_claude_and_gemini():
    codex = get_provider("codex")
    claude = get_provider("claude")
    gemini = get_provider("gemini")

    assert codex.provider_id == "codex"
    assert codex.memory_category == "codex_chat"
    assert codex.source_name == "codex_session_bridge"

    assert claude.provider_id == "claude"
    assert claude.memory_category == "claude_chat"
    assert claude.source_name == "claude_session_bridge"

    assert gemini.provider_id == "gemini"
    assert gemini.memory_category == "gemini_chat"
    assert gemini.source_name == "gemini_session_bridge"


def test_run_bridge_mirrors_codex_sessions_into_global_memory(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    sessions_root = tmp_path / "codex-sessions"
    session_file = sessions_root / "2026" / "03" / "09" / "rollout-2026-03-09T01-02-03.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "timestamp": "2026-03-09T01:02:03Z",
                "type": "response_item",
                "payload": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello from codex"}],
                },
            },
            {
                "timestamp": "2026-03-09T01:02:04Z",
                "type": "response_item",
                "payload": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hi from codex"}],
                },
            },
        ],
    )
    monkeypatch.setenv("AGENT_GLOBAL_USER_KEY", "hoon_main")

    result = run_bridge("codex", repo_root=repo_root, sessions_root=sessions_root)

    assert result["ok"] is True
    assert result["written"] == 2

    memory_dir = repo_root / "projects" / "global_hoon_main" / "data" / "memory" / "general" / "codex_chat"
    records = [_read_json(path) for path in memory_dir.glob("*.json")]
    texts = {record["details"]["text"] for record in records}
    assert "[user] hello from codex" in texts
    assert "[assistant] hi from codex" in texts


def test_run_bridge_recovers_from_stale_cursor(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    sessions_root = tmp_path / "codex-sessions"
    session_file = sessions_root / "2026" / "03" / "09" / "rollout-2026-03-09T04-05-06.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "timestamp": "2026-03-09T04:05:06Z",
                "type": "response_item",
                "payload": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "cursor recovery"}],
                },
            }
        ],
    )
    monkeypatch.setenv("AGENT_GLOBAL_USER_KEY", "hoon_main")

    state_path = repo_root / "projects" / "global_hoon_main" / "data" / "memory" / "codex" / "_bridge_state" / "session_cursor.json"
    _write_json(
        state_path,
        {
            "last_file": "missing/rollout-old.jsonl",
            "last_line": 999,
        },
    )

    result = run_bridge("codex", repo_root=repo_root, sessions_root=sessions_root)

    assert result["ok"] is True
    assert result["written"] == 1

    state = _read_json(state_path)
    assert state["last_file"].endswith("rollout-2026-03-09T04-05-06.jsonl")
    assert state["last_line"] == 0


@pytest.mark.parametrize(
    ("provider_id", "memory_category"),
    [
        ("claude", "claude_chat"),
        ("gemini", "gemini_chat"),
    ],
)
def test_run_bridge_supports_generic_role_content_jsonl(
    tmp_path: Path,
    monkeypatch,
    provider_id: str,
    memory_category: str,
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    sessions_root = tmp_path / f"{provider_id}-sessions"
    session_file = sessions_root / "2026" / "03" / "09" / "session-1.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "timestamp": "2026-03-09T09:10:11Z",
                "role": "user",
                "content": f"hello from {provider_id}",
            }
        ],
    )
    monkeypatch.setenv("AGENT_GLOBAL_USER_KEY", "hoon_main")

    result = run_bridge(provider_id, repo_root=repo_root, sessions_root=sessions_root)

    assert result["ok"] is True
    assert result["written"] == 1

    memory_dir = repo_root / "projects" / "global_hoon_main" / "data" / "memory" / "general" / memory_category
    records = [_read_json(path) for path in memory_dir.glob("*.json")]
    assert len(records) == 1
    assert records[0]["details"]["text"] == f"[user] hello from {provider_id}"
