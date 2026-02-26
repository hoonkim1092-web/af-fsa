import hashlib
import json
import os
import shutil
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

from core.config_paths import BASE_DIR, PROJECT_ROOT


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)].rstrip() + "..."


class SynergyBridge:
    """Bridges Agent Factory with optional Oh My OpenCode (OmO) runtime."""

    def __init__(self):
        self.omo_path = self._detect_omo()
        self.ultrawork_cmd = self._resolve_ultrawork_cmd()

    def _detect_omo(self) -> str | None:
        raw = str(os.getenv("OMO_PATH", "")).strip()
        candidates = []
        if raw:
            candidates.append(raw)
        parent = os.path.dirname(BASE_DIR)
        candidates.extend(
            [
                os.path.join(parent, "oh-my-opencode-dev"),
                os.path.join(parent, "oh-my-opencode"),
                os.path.join(BASE_DIR, "tmp_oh_my_opencode_review"),
            ]
        )
        for c in candidates:
            if c and os.path.isdir(c):
                return os.path.abspath(c)
        return None

    def _resolve_ultrawork_cmd(self):
        if not self.omo_path:
            return None

        env_cmd = str(os.getenv("OMO_ULTRAWORK_CMD", "")).strip()
        if env_cmd:
            return {"kind": "shell", "cmd": env_cmd}

        py_candidates = [
            os.path.join(self.omo_path, "scripts", "ultrawork.py"),
            os.path.join(self.omo_path, "ultrawork.py"),
            os.path.join(self.omo_path, "bin", "ultrawork.py"),
        ]
        for p in py_candidates:
            if os.path.isfile(p):
                return {"kind": "argv", "cmd": [sys.executable, p]}

        package_json = os.path.join(self.omo_path, "package.json")
        try:
            if os.path.isfile(package_json):
                pkg = json.loads(Path(package_json).read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
                if isinstance(scripts, dict) and "ultrawork" in scripts:
                    if shutil.which("bun"):
                        return {"kind": "argv", "cmd": ["bun", "run", "ultrawork", "--"]}
                    if shutil.which("npm"):
                        return {"kind": "argv", "cmd": ["npm", "run", "ultrawork", "--"]}
        except Exception:
            pass
        return None

    def run_ultrawork(self, task: str, timeout_sec: int = 120) -> dict:
        if not self.omo_path:
            return {"ok": False, "error": "omo_not_detected"}
        if not self.ultrawork_cmd:
            return {"ok": False, "error": "ultrawork_command_not_resolved", "omo_path": self.omo_path}

        text = str(task or "").strip()
        if not text:
            return {"ok": False, "error": "missing_task"}

        try:
            if self.ultrawork_cmd.get("kind") == "shell":
                raw = str(self.ultrawork_cmd.get("cmd") or "").strip()
                # Prefer argv execution to avoid shell interpolation of task text.
                argv = shlex.split(raw, posix=False)
                if argv:
                    argv.append(text)
                    proc = subprocess.run(
                        argv,
                        cwd=self.omo_path,
                        capture_output=True,
                        text=True,
                        timeout=max(5, int(timeout_sec)),
                        shell=False,
                        check=False,
                    )
                else:
                    return {"ok": False, "error": "invalid_ultrawork_command", "omo_path": self.omo_path}
            else:
                argv = list(self.ultrawork_cmd.get("cmd") or [])
                argv.append(text)
                proc = subprocess.run(
                    argv,
                    cwd=self.omo_path,
                    capture_output=True,
                    text=True,
                    timeout=max(5, int(timeout_sec)),
                    check=False,
                )
            return {
                "ok": proc.returncode == 0,
                "mode": "omo_ultrawork",
                "returncode": int(proc.returncode),
                "stdout": _truncate(proc.stdout, 2400),
                "stderr": _truncate(proc.stderr, 1200),
                "omo_path": self.omo_path,
            }
        except Exception as e:
            return {"ok": False, "mode": "omo_ultrawork", "error": str(e), "omo_path": self.omo_path}

    def run_hash_edit(self, path: str, find: str, replace: str, count: int = 1, expected_sha256: str = "") -> dict:
        rel = str(path or "").strip()
        if not rel:
            return {"ok": False, "error": "missing_path"}
        if str(find or "") == "":
            return {"ok": False, "error": "missing_find"}

        project_root = Path(PROJECT_ROOT).resolve()
        target = Path(rel)
        if not target.is_absolute():
            target = (project_root / target).resolve()
        else:
            target = target.resolve()

        if project_root not in [target, *target.parents]:
            return {"ok": False, "error": "path_outside_project", "path": str(target)}
        if not target.exists() or not target.is_file():
            return {"ok": False, "error": "file_not_found", "path": str(target)}

        try:
            before = target.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"read_failed:{e}", "path": str(target)}

        before_hash = hashlib.sha256(before.encode("utf-8")).hexdigest()
        expected = str(expected_sha256 or "").strip().lower()
        if expected and expected != before_hash:
            return {
                "ok": False,
                "error": "sha256_mismatch",
                "expected_sha256": expected,
                "actual_sha256": before_hash,
                "path": str(target),
            }

        n = before.count(find)
        if n == 0:
            return {"ok": False, "error": "find_not_found", "path": str(target)}

        reps = max(1, int(count or 1))
        after = before.replace(find, str(replace or ""), reps)
        after_hash = hashlib.sha256(after.encode("utf-8")).hexdigest()
        try:
            target.write_text(after, encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"write_failed:{e}", "path": str(target)}

        return {
            "ok": True,
            "mode": "omo_hash_edit",
            "path": str(target),
            "replacements": min(reps, n),
            "sha256_before": before_hash,
            "sha256_after": after_hash,
        }


def get_synergy_context(tool_names: list[str] | None = None) -> str:
    if tool_names is None:
        names = ["synergy_omo_hash_edit", "synergy_omo_ultrawork"]
    else:
        names = [str(n).strip() for n in tool_names if str(n).strip()]
    if not names:
        return (
            "\n\n[Synergy Briefing]\n"
            "OmO synergy tools are not attached in this run."
        )
    rendered = ", ".join([f"`{n}`" for n in names])
    return (
        "\n\n[Synergy & Stability Briefing]\n"
        "코드 수정 시 발생할 수 있는 라인 밀림(Harness Problem) 방지를 위해\n"
        "`get_file_with_hashes`로 파일을 읽고 `apply_edit`을 사용하는 것을 강력 추천합니다.\n"
        f"사용 가능한 시너지 도구: {rendered}\n"
        "Hash-Anchored 편집은 다중 에이전트 환경에서 가장 안전한 협업 수단입니다."
    )


def build_synergy_tools(policy: dict | None = None, is_allowed_fn: Callable | None = None) -> list[Callable]:
    bridge = SynergyBridge()
    skill_id = "synergy_runner"
    tools: list[Callable] = []

    def synergy_omo_hash_edit(path: str, find: str, replace: str, count: int = 1, expected_sha256: str = "") -> dict:
        """Apply deterministic file edit with optional SHA-256 guard."""
        return bridge.run_hash_edit(path=path, find=find, replace=replace, count=count, expected_sha256=expected_sha256)

    def synergy_omo_ultrawork(task: str, timeout_sec: int = 120) -> dict:
        """Delegate complex implementation task to OmO ultrawork runtime if available."""
        return bridge.run_ultrawork(task=task, timeout_sec=timeout_sec)

    for fn in [synergy_omo_hash_edit, synergy_omo_ultrawork]:
        fn._skill_id = skill_id  # type: ignore[attr-defined]
        if is_allowed_fn and policy is not None:
            if not is_allowed_fn(policy, skill_id, fn.__name__):
                continue
        tools.append(fn)
    return tools
