from core.synergy.process import OmoDetector


def test_build_argv_extracts_leading_env_assignments():
    cmd = {
        "kind": "shell",
        "cmd": "AGENT_PROJECT_ID=minesweeper AGENT_FORCE_MODEL=models/gemini-3-flash-preview python scripts/ultrawork.py",
    }
    argv, env = OmoDetector.build_argv(cmd, "task payload")
    assert argv[0] == "python"
    assert argv[1] == "scripts/ultrawork.py"
    assert argv[-1] == "task payload"
    assert env["AGENT_PROJECT_ID"] == "minesweeper"
    assert env["AGENT_FORCE_MODEL"] == "models/gemini-3-flash-preview"


def test_build_argv_without_env_prefix_is_unchanged():
    cmd = {"kind": "shell", "cmd": "python scripts/ultrawork.py"}
    argv, env = OmoDetector.build_argv(cmd, "task payload")
    assert argv == ["python", "scripts/ultrawork.py", "task payload"]
    assert env == {}

