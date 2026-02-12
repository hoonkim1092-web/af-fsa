# agent_factory_lite_secure.py
import os
import re
import time
import json
import ast
import yaml
import hashlib
import subprocess
import sys
from datetime import datetime
from dotenv import load_dotenv

import google.generativeai as genai

def safe_generate(model, prompt, **kwargs):
    for i in range(5):
        try:
            return model.generate_content(prompt, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "quota" in msg:
                wait_sec = 20 * (i + 1)
                print(f"[Warn] Quota hit. Waiting {wait_sec}s... ({i+1}/5)")
                time.sleep(wait_sec)
                continue
            raise e
    raise RuntimeError("Quota exceeded after retries")

# =============================================================================
# 0) ENV / PATH
# =============================================================================
load_dotenv(override=True)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY not found in env/.env")
genai.configure(api_key=GOOGLE_API_KEY)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.join(BASE_DIR, "agents")
SKILLS_DIR = os.path.join(BASE_DIR, "skills")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
DATA_DIR = os.path.join(BASE_DIR, "data")
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")

REGISTRY_PATH = os.path.join(SKILLS_DIR, "registry.yaml")
WORKFLOW_PATH = os.path.join(SKILLS_DIR, "workflow_registry.yaml")

for d in [AGENTS_DIR, SKILLS_DIR, RUNS_DIR, DATA_DIR, ARTIFACTS_DIR]:
    os.makedirs(d, exist_ok=True)

MAX_ITERATIONS = 3
TEST_TIMEOUT_SEC = 10
CHILD_ENV_PASSTHROUGH = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}

# =============================================================================
# Utils
# =============================================================================
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return (t[:60] if t else "skill")

def strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json|python)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()

def safe_json_load(s: str) -> dict:
    s = strip_code_fences(s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        return json.loads(m.group(0)) if m else {}

def read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def write_yaml(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

def write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def ensure_registry_files():
    if not os.path.exists(REGISTRY_PATH):
        write_yaml(REGISTRY_PATH, {"skills": {}, "install_candidates": {}})
    if not os.path.exists(WORKFLOW_PATH):
        write_yaml(WORKFLOW_PATH, {"capability_to_skill": {}, "updated_at": now_iso()})

# =============================================================================
# Utils
# =============================================================================
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")

def safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return (t[:60] if t else "skill")

def strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json|python)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()

def safe_json_load(s: str) -> dict:
    s = strip_code_fences(s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        return json.loads(m.group(0)) if m else {}

def read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def write_yaml(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

def write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def ensure_registry_files():
    if not os.path.exists(REGISTRY_PATH):
        write_yaml(REGISTRY_PATH, {"skills": {}, "install_candidates": {}})
    if not os.path.exists(WORKFLOW_PATH):
        write_yaml(WORKFLOW_PATH, {"capability_to_skill": {}, "updated_at": now_iso()})

# =============================================================================
# 1) Model Router (Lite)
# =============================================================================
class ModelRouter:
    def pick(self, stage: str) -> str:
        # ?붽뎄遺꾩꽍/鍮뚮뜑??pro, ?섎㉧吏 flash
        if stage in ("requirement", "builder"):
            return "models/gemini-2.0-flash"
        return "models/gemini-2.0-flash"

# =============================================================================
# 2) Quick Guard (AST) - 移섎챸 ?꾧뎄 ?뺤닔
# =============================================================================
BANNED_IMPORT_TOPS = {
    "os", "sys", "subprocess", "shutil", "importlib",
    "pathlib", "glob",
    "ctypes",
    "multiprocessing", "threading", "concurrent", "asyncio",
}

BANNED_CALLS = {"eval", "exec", "__import__", "compile", "input"}
# open? ?덉슜(???runner?먯꽌 寃쎈줈 ?듭젣)

def quick_guard(code: str) -> tuple[bool, list[str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"SyntaxError: {e}"]

    vios: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in BANNED_IMPORT_TOPS:
                    vios.append(f"Forbidden import: {alias.name}")

        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in BANNED_IMPORT_TOPS:
                vios.append(f"Forbidden import: {node.module}")

        elif isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in BANNED_CALLS:
                vios.append(f"Forbidden call: {name}")

    return (len(vios) == 0), vios

def build_child_env() -> dict:
    child = {}
    for k in CHILD_ENV_PASSTHROUGH:
        v = os.environ.get(k)
        if v:
            child[k] = v
    return child

# =============================================================================
# 3) Isolated Run (Lite) - -I ?좎?, -S ?쒓굅(pandas ?덉슜)
# =============================================================================
def run_isolated(skill_py_path: str, timeout_sec: int = TEST_TIMEOUT_SEC) -> tuple[bool, dict, str]:
    skill_abs = os.path.abspath(skill_py_path)
    data_abs = os.path.abspath(DATA_DIR)
    art_abs = os.path.abspath(ARTIFACTS_DIR)

    # ??Windows ?ы븿 ?덉쟾 二쇱엯: repr ?ъ슜
    SKILL_PATH = repr(skill_abs)
    DATA_ROOT = repr(data_abs)
    ART_ROOT = repr(art_abs)

    runner = f"""
import json, os, sys, builtins, importlib.util

SKILL_PATH = {SKILL_PATH}
DATA_ROOT  = {DATA_ROOT}
ART_ROOT   = {ART_ROOT}

# 1) sys.path?먯꽌 CWD ?쒓굅(紐⑤뱢 ??꾩엵 諛⑹?)
try:
    cwd = os.getcwd()
    sys.path = [p for p in sys.path if p not in ("", ".", cwd)]
except Exception:
    pass

try:
    import socket as _socket
    AUDIT_PATH = os.path.join(ART_ROOT, "network_audit.log")

    def _audit(line: str):
        try:
            ts = __import__("datetime").datetime.utcnow().isoformat()
            with builtins.open(AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{{ts}}] {{line}}\\n")
        except Exception:
            pass

    _orig_connect = _socket.socket.connect
    def _logged_connect(self, address):
        _audit(f"socket.connect address={{address}}")
        return _orig_connect(self, address)
    _socket.socket.connect = _logged_connect

    _orig_create_connection = _socket.create_connection
    def _logged_create_connection(address, *args, **kwargs):
        _audit(f"socket.create_connection address={{address}}")
        return _orig_create_connection(address, *args, **kwargs)
    _socket.create_connection = _logged_create_connection
except Exception:
    pass

# 3) ?뚯씪 寃쎈줈 ?듭젣(Chroot-ish): data/artifacts 諛?open 李⑤떒
_real_open = builtins.open
BLOCK_EXT = (".py", ".pth", ".so", ".dll", ".exe")

def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))

DATA_N = _norm(DATA_ROOT)
ART_N  = _norm(ART_ROOT)

def _is_within(path: str, root_norm: str) -> bool:
    p = _norm(path)
    return p == root_norm or p.startswith(root_norm + os.sep)

def _safe_open(file, mode="r", *args, **kwargs):
    if isinstance(file, int):
        raise PermissionError("fd open blocked")
    path = _norm(str(file))

    # read: data/artifacts留?
    if not (_is_within(path, DATA_N) or _is_within(path, ART_N)):
        raise PermissionError(f"open blocked: {path}")

    # write: artifacts留?+ ?ㅽ뻾???뺤옣??李⑤떒
    if any(x in mode for x in ("w","a","x","+")):
        if not _is_within(path, ART_N):
            raise PermissionError(f"write blocked: {path}")
        if path.endswith(BLOCK_EXT):
            raise PermissionError(f"write ext blocked: {path}")

    return _real_open(path, mode, *args, **kwargs)

builtins.open = _safe_open

# 4) Load skill
try:
    spec = importlib.util.spec_from_file_location("skill", SKILL_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
except Exception as e:
    print(json.dumps({{"ok": False, "reason": "import_failed", "error": str(e)}}, ensure_ascii=False))
    raise SystemExit(1)

missing = [fn for fn in ("propose","apply","test") if not hasattr(m, fn)]
if missing:
    print(json.dumps({{"ok": False, "reason": "missing_interface", "missing": missing}}, ensure_ascii=False))
    raise SystemExit(2)

ctx = {{"dry_run": True, "data_dir": DATA_ROOT, "artifacts_dir": ART_ROOT}}

try:
    res = m.test(ctx)
    if not isinstance(res, dict):
        res = {{"ok": False, "reason": "return_not_dict"}}
    print(json.dumps(res, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"ok": False, "reason": "runtime_error", "error": str(e)}}, ensure_ascii=False))
"""

    try:
        # ??-I ?좎? / -S ?쒓굅 => ?꾩옱 ?섍꼍??site-packages(pandas/numpy) ?ъ슜 媛??
        p = subprocess.run(
            [sys.executable, "-I", "-c", runner],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=build_child_env(),   # 최소 allowlist만 전달
            cwd=ARTIFACTS_DIR,        # ???묒뾽 ?붾젆?좊━ 怨좎젙
        )
    except subprocess.TimeoutExpired:
        return False, {"ok": False, "reason": "timeout"}, "timeout"
    except Exception as e:
        return False, {"ok": False, "reason": "runner_error", "error": str(e)}, str(e)

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if not out:
        return False, {"ok": False, "reason": "empty_output"}, err

    try:
        j = json.loads(out.splitlines()[-1])
        return bool(j.get("ok")), j, err
    except Exception:
        return False, {"ok": False, "reason": "non_json_output", "stdout": out}, err

# =============================================================================
# 4) Agent / Requirements
# =============================================================================
class AgentManager:
    def __init__(self, mr: ModelRouter):
        self.mr = mr

    def _agent_path(self, role_spec: str) -> str:
        return os.path.join(AGENTS_DIR, f"{safe_id(role_spec)}.yaml")

    def get_or_create(self, role_spec: str) -> dict:
        path = self._agent_path(role_spec)
        if os.path.exists(path):
            return read_yaml(path)

        model = genai.GenerativeModel(self.mr.pick("agent_create"))
        prompt = f"""
ROLE_SPEC: "{role_spec}"
JSON留?異쒕젰:
{{"name":"...", "role":"...", "tone":"immutable", "traits":"immutable"}}
"""
        res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        data = safe_json_load(res.text)
        data["name"] = data.get("name") or f"agent_{safe_id(role_spec)}"
        data["role"] = data.get("role") or role_spec
        data["created_at"] = now_iso()
        write_yaml(path, data)
        return data

    def install_skills(self, role_spec: str, skill_ids: list[str]) -> list[str]:
        if not skill_ids:
            return []
        path = self._agent_path(role_spec)
        agent = read_yaml(path) if os.path.exists(path) else self.get_or_create(role_spec)
        current = [safe_id(str(s)) for s in (agent.get("skills") or []) if str(s).strip()]
        merged = list(dict.fromkeys(current + [safe_id(s) for s in skill_ids]))
        agent["skills"] = merged
        agent["updated_at"] = now_iso()
        write_yaml(path, agent)
        return merged

class RequirementAnalyzer:
    def __init__(self, mr: ModelRouter):
        self.mr = mr

    def _fallback_missing_skills(self, task_input: str, role_text: str) -> list[str]:
        text = f"{task_input} {role_text}".lower()
        picks: list[str] = []
        rules = [
            ("research_assistant", ["research", "리서치", "검증", "후보", "라이브러리"]),
            ("issue_tracker", ["이슈", "추적", "ticket", "issue", "책임", "audit", "로그"]),
            ("data_visualize", ["시각화", "대시보드", "차트", "그래프", "요약"]),
        ]
        for sid, kws in rules:
            if any(k in text for k in kws):
                picks.append(sid)
        if not picks:
            picks.append("research_assistant")
        return list(dict.fromkeys([safe_id(s) for s in picks]))[:5]

    def analyze(self, agent: dict, task_input: str) -> dict:
        model = genai.GenerativeModel(self.mr.pick("requirement"))
        prompt = f"""
AgentRole: {agent.get("role")}
Task: {task_input}

JSON留?異쒕젰:
{{
  "goal": "??臾몄옣",
  "missing_skills": ["snake_case_0to5"],
  "constraints": ["network_allowed", "no_system_tools", "data_io_allowed"],
  "risk_level": "normal|elevated|strict"
}}

洹쒖튃:
- missing_skills 0~5媛?
- ?ㅽ궗紐?snake_case
- data 遺꾩꽍?대㈃ needs_pandas 異붽?
"""
        try:
            res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            data = safe_json_load(res.text)
        except Exception as e:
            data = {
                "goal": task_input,
                "missing_skills": self._fallback_missing_skills(task_input, str(agent.get("role", ""))),
                "constraints": ["network_allowed", "no_system_tools", "data_io_allowed"],
                "risk_level": "normal",
                "analysis_fallback": f"llm_unavailable:{type(e).__name__}",
            }
        data.setdefault("goal", task_input)
        data.setdefault("missing_skills", [])
        data.setdefault("constraints", ["network_allowed", "no_system_tools", "data_io_allowed"])
        rl = data.get("risk_level", "normal")
        if rl not in ("normal", "elevated", "strict"):
            data["risk_level"] = "normal"

        data["missing_skills"] = [safe_id(str(s)) for s in (data["missing_skills"] or []) if str(s).strip()]
        return data

class HimariResearchAgent:
    def __init__(self, mr: ModelRouter):
        self.mr = mr

    def _registry_skill_index(self) -> dict:
        reg = read_yaml(REGISTRY_PATH)
        items = reg.get("skills", {}) if isinstance(reg, dict) else {}
        idx: dict = {}
        for sid, meta in items.items():
            key = safe_id(str(sid))
            caps = [safe_id(str(c)) for c in (meta.get("capabilities") or [])]
            idx[key] = {
                "id": key,
                "name": meta.get("name") or sid,
                "capabilities": caps,
                "meta": meta,
            }
        return idx

    def _fallback_match(self, need: str, idx: dict) -> list[str]:
        need_tokens = set(t for t in safe_id(need).split("_") if t)
        picked: list[str] = []
        for sid, item in idx.items():
            corpus = " ".join([sid, safe_id(item.get("name", ""))] + item.get("capabilities", []))
            tokens = set(t for t in corpus.split("_") if t)
            if need_tokens and (need_tokens & tokens):
                picked.append(sid)
        return picked[:3]

    def _score_candidate(self, need: str, item: dict) -> tuple[int, dict]:
        need_tokens = set(t for t in safe_id(need).split("_") if t)
        caps = [safe_id(str(c)) for c in (item.get("capabilities") or [])]
        corpus = " ".join([safe_id(item.get("id", "")), safe_id(item.get("name", ""))] + caps)
        tokens = set(t for t in corpus.split("_") if t)
        overlap = sorted(list(need_tokens & tokens))

        meta = item.get("meta", {}) if isinstance(item.get("meta"), dict) else {}
        path = str(meta.get("path", ""))
        meta_path = str(meta.get("meta_path", ""))
        exists_py = os.path.exists(path) if path else os.path.exists(os.path.join(SKILLS_DIR, item["id"], "skill.py"))
        exists_meta = os.path.exists(meta_path) if meta_path else os.path.exists(os.path.join(SKILLS_DIR, item["id"], "meta.yaml"))
        last_test_ok = bool(meta.get("last_test_ok", False))

        score = 0
        score += min(len(overlap) * 25, 60)
        if exists_py:
            score += 20
        if exists_meta:
            score += 10
        if last_test_ok:
            score += 10
        score = max(0, min(100, score))
        verify = {
            "exists_skill_py": exists_py,
            "exists_meta_yaml": exists_meta,
            "last_test_ok": last_test_ok,
            "token_overlap": overlap,
        }
        return score, verify

    def research(self, agent: dict, reqs: dict, build_targets: list[str] | None = None) -> dict:
        missing = [safe_id(str(s)) for s in (build_targets or reqs.get("missing_skills") or []) if str(s).strip()]
        idx = self._registry_skill_index()
        if not missing:
            return {"suggestions": {}, "all_candidates": [], "evidence_pack": {"targets": {}}}

        skill_catalog = []
        for sid, item in idx.items():
            skill_catalog.append({
                "id": sid,
                "name": item["name"],
                "capabilities": item["capabilities"],
            })

        model = genai.GenerativeModel(self.mr.pick("requirement"))
        prompt = f"""
너는 리서치 에이전트 Himari다.
목표: missing_skills에 대해 설치 가능한 로컬 스킬 후보를 추천한다.

AgentRole: {agent.get("role")}
Goal: {reqs.get("goal")}
MissingSkills: {missing}
LocalSkillCatalog(JSON): {json.dumps(skill_catalog, ensure_ascii=False)}

출력은 JSON만:
{{
  "suggestions": {{
    "missing_skill_id": ["candidate_skill_id_1", "candidate_skill_id_2"]
  }}
}}
"""
        suggestions: dict[str, list[str]] = {}
        try:
            res = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            payload = safe_json_load(res.text)
            raw = payload.get("suggestions", {}) if isinstance(payload, dict) else {}
            if isinstance(raw, dict):
                for need, cands in raw.items():
                    k = safe_id(str(need))
                    values = [safe_id(str(c)) for c in (cands or []) if safe_id(str(c)) in idx]
                    if values:
                        suggestions[k] = list(dict.fromkeys(values))
        except Exception:
            suggestions = {}

        for need in missing:
            if need not in suggestions:
                fallback = self._fallback_match(need, idx)
                if fallback:
                    suggestions[need] = fallback

        all_candidates = []
        for arr in suggestions.values():
            for sid in arr:
                if sid not in all_candidates:
                    all_candidates.append(sid)

        targets: dict = {}
        for need in missing:
            ranked = []
            for sid in suggestions.get(need, []):
                item = idx.get(sid)
                if not item:
                    continue
                score, verify = self._score_candidate(need, item)
                ranked.append({
                    "candidate_skill_id": sid,
                    "candidate_name": item.get("name", sid),
                    "score": score,
                    "verification": verify,
                    "capabilities": item.get("capabilities", []),
                })
            ranked.sort(key=lambda x: x["score"], reverse=True)
            best = ranked[0] if ranked else None
            targets[need] = {
                "need_skill_id": need,
                "top_candidate": (best or {}).get("candidate_skill_id"),
                "top_score": (best or {}).get("score", 0),
                "verified": bool(best and best["verification"]["exists_skill_py"]),
                "candidates": ranked,
            }

        evidence_pack = {
            "generated_at": now_iso(),
            "agent_role": agent.get("role"),
            "goal": reqs.get("goal"),
            "targets": targets,
        }
        return {"suggestions": suggestions, "all_candidates": all_candidates, "evidence_pack": evidence_pack}

    def notify_candidates(self, candidates: list[str], idx: dict):
        if not candidates:
            print("\n[Himari] 설치 추천 후보가 없습니다.")
            return
        print("\n[Himari] 리서치 결과 - 설치 후보")
        for i, sid in enumerate(candidates, start=1):
            item = idx.get(sid, {})
            caps = item.get("capabilities", [])
            print(f"  {i}. {sid} | name={item.get('name', sid)} | capabilities={caps}")

# =============================================================================
# 5) Builder
# =============================================================================
class SandboxedBuilder:
    def __init__(self, mr: ModelRouter):
        self.mr = mr

    def build_skill(
        self,
        agent: dict,
        skill_name: str,
        reqs: dict,
        run_id: str,
        evidence_pack: dict,
    ) -> tuple[bool, str | None, dict]:
        skill_id = safe_id(skill_name)
        skill_dir = os.path.join(SKILLS_DIR, skill_id)
        os.makedirs(skill_dir, exist_ok=True)
        code_path = os.path.join(skill_dir, "skill.py")
        meta_path = os.path.join(skill_dir, "meta.yaml")

        run_dir = os.path.join(RUNS_DIR, run_id)
        os.makedirs(run_dir, exist_ok=True)

        evidence_targets = (evidence_pack or {}).get("targets", {}) if isinstance(evidence_pack, dict) else {}
        target_evidence = evidence_targets.get(skill_id)
        if not target_evidence:
            fail_meta = {
                "id": skill_id,
                "name": skill_name,
                "status": "disabled",
                "version": "0.1.0",
                "capabilities": [skill_name],
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "last_test_ok": False,
                "last_test_detail": {"ok": False, "reason": "missing_evidence_pack"},
            }
            write_yaml(meta_path, fail_meta)
            return False, None, fail_meta

        model = genai.GenerativeModel(self.mr.pick("builder"))
        base_prompt = f"""
?덈뒗 ?뚯씠???ㅽ궗 紐⑤뱢???묒꽦?쒕떎.
Skill: "{skill_name}"
AgentRole: {agent.get("role")}
Goal: {reqs.get("goal")}
Constraints: {reqs.get("constraints")}
Evidence(JSON): {json.dumps(target_evidence, ensure_ascii=False)}

?꾩닔:
- ?⑥닔 3媛? propose(ctx)->dict, apply(ctx)->dict, test(ctx)->dict(諛섎뱶??ok ???ы븿)
- ?곗씠???낅젰: ctx["data_dir"] ?꾨옒 ?뚯씪???쎈뒗??
- ?곗텧臾???? ctx["artifacts_dir"] ?꾨옒濡???ν빐???섏?留? 媛?ν븯硫?dict濡?諛섑솚.
湲덉?:
- os/sys/subprocess/shutil/importlib/pathlib/glob/ctypes ???ъ슜 湲덉?
- eval/exec/__import__/compile/input 湲덉?
異쒕젰:
- 留덊겕?ㅼ슫 ?놁씠 ?뚯씠??肄붾뱶留?
"""

        last = {"ok": False, "reason": "not_started"}
        for i in range(MAX_ITERATIONS):
            res = model.generate_content(base_prompt)
            code = strip_code_fences(res.text)

            ok, vios = quick_guard(code)
            if not ok:
                last = {"ok": False, "reason": "guard_block", "violations": vios}
                continue

            write_text(code_path, code)

            t_ok, t_json, t_err = run_isolated(code_path, timeout_sec=TEST_TIMEOUT_SEC)
            last = {"test_ok": t_ok, "test_json": t_json, "stderr": (t_err or "")[:500]}

            if t_ok:
                meta = {
                    "id": skill_id,
                    "name": skill_name,
                    "status": "active",
                    "version": "0.1.0",
                    "capabilities": [skill_name],
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                    "code_hash": sha256_text(code),
                    "last_test_ok": True,
                    "last_test_detail": t_json,
                }
                write_yaml(meta_path, meta)
                write_text(os.path.join(run_dir, f"{skill_id}_skill.py"), code)
                write_yaml(os.path.join(run_dir, f"{skill_id}_meta.yaml"), meta)
                return True, code_path, meta

        fail_meta = {
            "id": skill_id,
            "name": skill_name,
            "status": "disabled",
            "version": "0.1.0",
            "capabilities": [skill_name],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "last_test_ok": False,
            "last_test_detail": last,
        }
        write_yaml(meta_path, fail_meta)
        return False, None, fail_meta

    def get_model_priority_queue(self):
        # 1순위부터 순차적으로 시도할 모델 목록 (2026.02 Update)
        return [
            "models/gemini-2.0-flash-lite-001",
            "models/gemini-1.5-flash",
            "models/gemini-1.0-pro"
        ]

# =============================================================================
# 6) Registry / Workflow / Git
# =============================================================================
class RegistryManager:
    def __init__(self):
        ensure_registry_files()

    def register_built(self, meta: dict, skill_dir: str):
        reg = read_yaml(REGISTRY_PATH)
        reg.setdefault("skills", {})
        reg["skills"][meta["id"]] = {
            "id": meta["id"],
            "name": meta.get("name"),
            "status": meta.get("status"),
            "version": meta.get("version"),
            "capabilities": meta.get("capabilities", []),
            "path": os.path.join(skill_dir, "skill.py"),
            "meta_path": os.path.join(skill_dir, "meta.yaml"),
            "updated_at": now_iso(),
            "last_test_ok": bool(meta.get("last_test_ok", False)),
        }
        write_yaml(REGISTRY_PATH, reg)

    def workflow_apply(self, metas: list[dict]):
        wf = read_yaml(WORKFLOW_PATH)
        wf.setdefault("capability_to_skill", {})
        mapping = wf["capability_to_skill"]
        for meta in metas:
            sid = meta["id"]
            for cap in meta.get("capabilities", []):
                k = safe_id(str(cap))
                mapping.setdefault(k, [])
                if sid not in mapping[k]:
                    mapping[k].append(sid)
        wf["updated_at"] = now_iso()
        write_yaml(WORKFLOW_PATH, wf)

class GitManager:
    def push_gate(self) -> bool:
        ans = input("\n?뵶 Git commit ?좊옒? (yes/no): ").strip().lower()
        return ans == "yes"

    def commit(self):
        # ?섍꼍留덈떎 ?ㅻⅤ??理쒖냼留?
        subprocess.run(["git", "add", "skills/", "runs/"], check=False)
        subprocess.run(["git", "commit", "-m", "feat: auto-generated skills"], check=False)

# =============================================================================
# 7) Factory
# =============================================================================
class AgentFactory:
    def __init__(self):
        self.mr = ModelRouter()
        self.agent_mgr = AgentManager(self.mr)
        self.req = RequirementAnalyzer(self.mr)
        self.research = HimariResearchAgent(self.mr)
        self.builder = SandboxedBuilder(self.mr)
        self.registry = RegistryManager()
        self.git = GitManager()

    def _missing_local_skill_files(self, agent: dict) -> list[str]:
        missing: list[str] = []
        for sid_raw in (agent.get("skills") or []):
            sid = safe_id(str(sid_raw))
            if not sid:
                continue
            skill_py = os.path.join(SKILLS_DIR, sid, "skill.py")
            if not os.path.exists(skill_py):
                missing.append(sid)
        return list(dict.fromkeys(missing))

    def run(self, task_input: str, role_spec: str = "General"):
        run_id = f"run_{int(time.time())}"
        print(f"\n?룺 RUN={run_id}")
        print(f"- Role: {role_spec}")
        print(f"- Task: {task_input}")

        agent = self.agent_mgr.get_or_create(role_spec)
        reqs = self.req.analyze(agent, task_input)
        file_missing = self._missing_local_skill_files(agent)

        skills = reqs.get("missing_skills", [])
        initial_targets = list(dict.fromkeys([safe_id(s) for s in skills] + file_missing))
        if not initial_targets:
            print("??missing_skills ?놁쓬. 醫낅즺.")
            return

        print("\n?뱦 Needed skills:", initial_targets)
        research = self.research.research(agent, reqs, build_targets=initial_targets)
        reg_idx = self.research._registry_skill_index()
        candidates = research.get("all_candidates", [])
        self.research.notify_candidates(candidates, reg_idx)
        if candidates:
            merged = self.agent_mgr.install_skills(role_spec, candidates)
            print(f"[Install] 승인 없이 자동 설치 적용됨: {merged}")

        covered = set()
        sugg = research.get("suggestions", {}) if isinstance(research, dict) else {}
        for need, cands in sugg.items():
            for c in (cands or []):
                skill_py = os.path.join(SKILLS_DIR, safe_id(c), "skill.py")
                if os.path.exists(skill_py):
                    covered.add(safe_id(need))
                    break

        skills = [s for s in initial_targets if safe_id(s) not in covered]
        if skills:
            print(f"[Build] 설치로 커버되지 않은 스킬은 생성 진행: {skills}")
        else:
            print("[Build] 설치된 스킬로 모두 커버되었습니다.")

        if not skills:
            print("[Build] 생성할 스킬이 없습니다. 종료합니다.")
            return

        built_metas: list[dict] = []
        built_dirs: list[str] = []

        evidence_pack = research.get("evidence_pack", {}) if isinstance(research, dict) else {}
        for s in skills:
            ok, path, meta = self.builder.build_skill(agent, s, reqs, run_id=run_id, evidence_pack=evidence_pack)
            if ok and path:
                skill_dir = os.path.dirname(path)
                built_dirs.append(skill_dir)
                built_metas.append(meta)
                self.registry.register_built(meta, skill_dir)
                print(f"??built: {s} -> {path}")
            else:
                print(f"??build failed: {s}")

        if built_metas:
            self.registry.workflow_apply(built_metas)
            print(f"\n?㎨ Registry: {REGISTRY_PATH}")
            print(f"?㎛ Workflow: {WORKFLOW_PATH}")

        if built_dirs and self.git.push_gate():
            self.git.commit()
            print("?? committed (push???덇? ?뚯븘????")

# =============================================================================
# Example 뭐지 왜 적용이 안되지 ㅇ
# =============================================================================
if __name__ == "__main__":
    AgentFactory().run(
        task_input="Read sales.csv from data_dir and write monthly summary to artifacts_dir/summary.json",
        role_spec="Data Analyst",
    )





