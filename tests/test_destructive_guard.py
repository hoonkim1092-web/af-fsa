from pathlib import Path


def test_inject_destructive_guard_contract_is_idempotent():
    from core.destructive_guard import inject_destructive_guard_contract

    base = "system prompt"
    combined = inject_destructive_guard_contract(base)

    assert "system prompt" in combined
    assert "[Destructive Action Guard]" in combined
    assert inject_destructive_guard_contract(combined) == combined


def test_merge_claude_destructive_guard_deduplicates_rules():
    from core.destructive_guard import merge_claude_destructive_guard

    merged = merge_claude_destructive_guard(
        {"permissions": {"deny": ["Bash(rm:*)", "Bash(git clean:*)"]}}
    )

    deny = merged["permissions"]["deny"]
    assert deny.count("Bash(rm:*)") == 1
    assert "Bash(git reset --hard:*)" in deny
    assert "Bash(Remove-Item:*)" in deny


def test_write_gemini_destructive_policy_contains_shell_deny_rules(tmp_path: Path):
    from core.destructive_guard import write_gemini_destructive_policy

    policy_path = write_gemini_destructive_policy(tmp_path / "destructive_guard.toml")
    content = policy_path.read_text(encoding="utf-8")

    assert 'include_tools = ["run_shell_command"]' in content
    assert 'pattern = "rm"' in content
    assert 'pattern = "git"' in content
    assert "reset" in content
    assert "--hard" in content


def test_detect_destructive_process_covers_cmd_and_git_paths():
    from core.destructive_guard import detect_destructive_process

    assert detect_destructive_process("git", ["git", "reset", "--hard"]) == "git reset --hard"
    assert detect_destructive_process("cmd", ["cmd", "/c", "git clean -fd"]) == "git clean"
    assert detect_destructive_process("powershell", ["powershell", "-Command", "Remove-Item foo -Force"]) == "Remove-Item"
    assert detect_destructive_process("cmd", ["cmd", "/c", "echo hello"]) is None
