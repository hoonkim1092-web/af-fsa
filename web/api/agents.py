"""
Agent catalog/create/edit API for web UI.
"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["agents"])

# model_utils를 경로 추가 후 import (웹 서버는 web/ 하위에서 실행)
_ROOT_DIR = Path(__file__).parent.parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

try:
    from model_utils import resolve_preferred_model as _resolve_model
except Exception as _e:
    # import 실패 시 graceful fallback
    def _resolve_model(role: str) -> str:  # type: ignore
        return ""

AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"

EDITABLE_CATEGORIES = [
    {"key": "name", "label": "Agent Name"},
    {"key": "role", "label": "Role Summary"},
    {"key": "tone", "label": "Tone"},
    {"key": "traits", "label": "Traits"},
    {"key": "system_ko", "label": "System Prompt"},
    {"key": "signature_lines", "label": "Signature Lines"},
    {"key": "runtime_rules.preferred_model", "label": "Preferred Model"},
    {"key": "runtime_rules.codex_directive", "label": "Codex Directive"},
]


class AgentCreateRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    tone: str = ""
    traits: list[str] = Field(default_factory=list)
    system_ko: str = ""
    signature_lines: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    runtime_rules: dict = Field(default_factory=dict)


class AgentPatchRequest(BaseModel):
    updates: dict = Field(default_factory=dict)


def _safe_id(text: str) -> str:
    t = (text or "").strip().lower()
    out = []
    for ch in t:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    while "__" in s:
        s = s.replace("__", "_")
    return s or "agent"


def _project_root(project_id: str | None = None) -> Path | None:
    pid = _safe_id(project_id or "")
    if pid:
        return _ROOT_DIR / "projects" / pid
    project_root = str(os.getenv("AGENT_PROJECT_ROOT", "") or "").strip()
    return Path(project_root) if project_root else None


def _project_agents_dir(project_id: str | None = None) -> Path | None:
    project_root = _project_root(project_id)
    if project_root is None:
        return None
    return project_root / "agents"


def _agent_file(agent_id: str, project_id: str | None = None) -> Path:
    project_dir = _project_agents_dir(project_id)
    base_dir = project_dir if project_dir is not None else AGENTS_DIR
    return base_dir / f"{_safe_id(agent_id)}.yaml"


def _scan_agent_items(base_dir: Path) -> dict[str, Path]:
    items: dict[str, Path] = {}
    if not base_dir.exists():
        return items
    for item in sorted(base_dir.iterdir()):
        if item.suffix in (".yaml", ".yml"):
            items[item.stem] = item
        elif item.is_dir() and not item.name.startswith(".") and (item / "agent.yaml").exists():
            items[item.name] = item
    return items


def _effective_agent_items(project_id: str | None = None) -> list[Path]:
    items = _scan_agent_items(AGENTS_DIR)
    project_dir = _project_agents_dir(project_id)
    if project_dir is not None:
        items.update(_scan_agent_items(project_dir))
    return [items[key] for key in sorted(items)]


def _resolve_agent_file(agent_id: str, project_id: str | None = None) -> Path:
    target = _agent_file(agent_id, project_id)
    if target.exists():
        return target
    fallback = AGENTS_DIR / f"{_safe_id(agent_id)}.yaml"
    if fallback.exists():
        return fallback
    return target


def _ensure_editable_agent_file(agent_id: str, project_id: str | None = None) -> Path:
    target = _agent_file(agent_id, project_id)
    if target.exists():
        return target
    if project_id:
        source = AGENTS_DIR / f"{_safe_id(agent_id)}.yaml"
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return target


def _extract_role(data: dict, default: str = "") -> str:
    if not isinstance(data, dict):
        return default
    if data.get("role"):
        return str(data.get("role"))
    ident = data.get("identity", {})
    if isinstance(ident, dict) and ident.get("role_summary"):
        return str(ident.get("role_summary"))
    return default


def _extract_system_prompt(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    direct = str(data.get("system_ko", "")).strip()
    if direct:
        return direct
    prompt = data.get("prompt", {})
    if isinstance(prompt, dict):
        return str(prompt.get("system_ko", "")).strip()
    return ""


def _to_catalog_item(path: Path, data: dict) -> dict:
    runtime_rules = data.get("runtime_rules", {}) if isinstance(data.get("runtime_rules"), dict) else {}
    traits = data.get("traits", []) if isinstance(data.get("traits"), list) else []
    signatures = data.get("signature_lines", []) if isinstance(data.get("signature_lines"), list) else []
    skills = data.get("skills", []) if isinstance(data.get("skills"), list) else []

    return {
        "id": path.stem,
        "name": str(data.get("name", path.stem)),
        "role": _extract_role(data, path.stem),
        "tone": str(data.get("tone", "")),
        "traits": [str(x) for x in traits],
        "signature_lines": [str(x) for x in signatures],
        "system_ko": _extract_system_prompt(data),
        "skills": [str(x) for x in skills],
        "runtime_rules": runtime_rules,
        "summary": {
            "skills_count": len(skills),
            "codex_enabled": bool(runtime_rules.get("codex_enabled", False)),
            "preferred_model": str(runtime_rules.get("preferred_model", "")),
        },
        "editable_categories": EDITABLE_CATEGORIES,
    }


def _load_agent_yaml(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return {
            "id": path.stem,
            "name": data.get("name", path.stem),
            "type": data.get("type", "unknown"),
            "level": data.get("level", 0),
            "tagline": data.get("identity", {}).get("tagline", ""),
            "role": data.get("identity", {}).get("role_summary", "") or data.get("role", ""),
            "preferred_model": data.get("runtime_rules", {}).get("preferred_model", ""),
        }
    except Exception:
        return None


def _load_agent_dir(path: Path) -> dict | None:
    yaml_file = path / "agent.yaml"
    if yaml_file.exists():
        info = _load_agent_yaml(yaml_file)
        if info:
            info["id"] = path.name
            return info
    return None


@router.get("/agents")
async def list_agents(project_id: str | None = None):
    agents = []
    items = _effective_agent_items(project_id)
    if not items:
        return {"agents": []}

    for item in items:
        if item.suffix in (".yaml", ".yml"):
            info = _load_agent_yaml(item)
            if info:
                agents.append(info)
        elif item.is_dir() and not item.name.startswith("."):
            info = _load_agent_dir(item)
            if info:
                agents.append(info)

    return {"agents": agents, "count": len(agents)}


@router.get("/agents/catalog")
async def list_agent_catalog(project_id: str | None = None):
    yaml_paths = [path for path in _effective_agent_items(project_id) if path.suffix in (".yaml", ".yml")]
    if not yaml_paths:
        return {"agents": [], "count": 0, "editable_categories": EDITABLE_CATEGORIES}

    items = []
    for path in yaml_paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                items.append(_to_catalog_item(path, data))
        except Exception:
            continue
    return {"agents": items, "count": len(items), "editable_categories": EDITABLE_CATEGORIES}


@router.post("/agents")
async def create_agent(req: AgentCreateRequest, project_id: str | None = None):
    path = _agent_file(req.agent_id, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise HTTPException(status_code=409, detail=f"agent already exists: {path.stem}")

    # runtime_rules 준비
    rr = req.runtime_rules if isinstance(req.runtime_rules, dict) else {}

    # ── 선호 모델 자동 결정 (수동 입력이 없을 때만) ──────────────────────────
    manual_model = str(rr.get("preferred_model", "")).strip()
    if not manual_model:
        auto_model = _resolve_model(req.role)
        if auto_model:
            rr["preferred_model"] = auto_model
    # ─────────────────────────────────────────────────────────────────────────

    payload = {
        "name": req.name,
        "role": req.role,
        "tone": req.tone,
        "traits": [str(x) for x in req.traits if str(x).strip()],
        "system_ko": req.system_ko,
        "signature_lines": [str(x) for x in req.signature_lines if str(x).strip()],
        "skills": [str(x) for x in req.skills if str(x).strip()],
        "runtime_rules": rr,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assigned = rr.get("preferred_model", "")
    return {"ok": True, "agent_id": path.stem, "preferred_model": assigned}


@router.patch("/agents/{agent_id}")
async def patch_agent(agent_id: str, req: AgentPatchRequest, project_id: str | None = None):
    path = _ensure_editable_agent_file(agent_id, project_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"agent not found: {_safe_id(agent_id)}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        data = {}

    updates = req.updates if isinstance(req.updates, dict) else {}

    if "name" in updates:
        data["name"] = str(updates.get("name", "")).strip()
    if "role" in updates:
        data["role"] = str(updates.get("role", "")).strip()
    if "tone" in updates:
        data["tone"] = str(updates.get("tone", "")).strip()
    if "traits" in updates and isinstance(updates.get("traits"), list):
        data["traits"] = [str(x) for x in updates.get("traits", []) if str(x).strip()]
    if "system_ko" in updates:
        data["system_ko"] = str(updates.get("system_ko", "")).strip()
    if "signature_lines" in updates and isinstance(updates.get("signature_lines"), list):
        data["signature_lines"] = [str(x) for x in updates.get("signature_lines", []) if str(x).strip()]

    rr = data.get("runtime_rules", {}) if isinstance(data.get("runtime_rules"), dict) else {}
    if "runtime_rules" in updates and isinstance(updates.get("runtime_rules"), dict):
        rr.update(updates["runtime_rules"])
    if "preferred_model" in updates:
        rr["preferred_model"] = str(updates.get("preferred_model", "")).strip()
    if "codex_directive" in updates:
        rr["codex_directive"] = str(updates.get("codex_directive", "")).strip()
    data["runtime_rules"] = rr

    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": True, "agent_id": path.stem}


# ─── 엔진 ID → 사람이 읽기 좋은 이름 매핑 ───────────────────────────────────
_ENGINE_LABELS: dict[str, str] = {
    "architect_claude":  "Claude Opus  (아키텍터)",
    "coder_claude":      "Claude Sonnet (코더)",
    "researcher_gemini": "Gemini Pro   (리서처)",
    "gemini_flash":      "Gemini Flash  (경량)",
    "manager_gpt":       "GPT-4o       (매니저)",
    "reasoner_o":        "GPT o-series  (추론)",
    "codex":             "Codex         (자동화)",
}


@router.get("/agents/{agent_id}/model-info")
async def get_agent_model_info(agent_id: str, project_id: str | None = None):
    """에이전트의 역할 · 추론 엔진 · 선호 모델을 반환한다."""
    path = _resolve_agent_file(agent_id, project_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"agent not found: {_safe_id(agent_id)}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    role = str(
        data.get("role", "")
        or (data.get("identity", {}) or {}).get("role_summary", "")
    ).strip()

    rr = data.get("runtime_rules", {}) if isinstance(data.get("runtime_rules"), dict) else {}
    preferred_model = str(rr.get("preferred_model", "")).strip()

    try:
        from model_utils import _infer_engine_id
        engine_id = _infer_engine_id(role or agent_id)
    except Exception:
        engine_id = "gemini_flash"

    # preferred_model 없으면 자동 결정
    auto_assigned = False
    if not preferred_model:
        preferred_model = _resolve_model(role or agent_id)
        auto_assigned = True

    return {
        "agent_id": path.stem,
        "name": str(data.get("name", path.stem)),
        "role": role,
        "engine_id": engine_id,
        "engine_label": _ENGINE_LABELS.get(engine_id, engine_id),
        "preferred_model": preferred_model,
        "auto_assigned": auto_assigned,
    }


@router.patch("/agents/{agent_id}/model")
async def update_agent_model(agent_id: str, body: dict, project_id: str | None = None):
    """preferred_model만 빠르게 교체한다. body: {preferred_model: str}"""
    new_model = str(body.get("preferred_model", "")).strip()
    if not new_model:
        raise HTTPException(status_code=422, detail="preferred_model is required")

    path = _ensure_editable_agent_file(agent_id, project_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"agent not found: {_safe_id(agent_id)}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rr = data.get("runtime_rules", {}) if isinstance(data.get("runtime_rules"), dict) else {}
    rr["preferred_model"] = new_model
    data["runtime_rules"] = rr
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": True, "agent_id": path.stem, "preferred_model": new_model}


@router.post("/agents/backfill")

async def backfill_preferred_models(project_id: str | None = None):
    """
    기존 에이전트 YAML 중 preferred_model이 없는 항목에 role 기반으로 자동 설정.
    웹 UI 설정 페이지에서 "일괄 자동 설정" 버튼으로 호출할 수 있다.
    """
    items = [path for path in _effective_agent_items(project_id) if path.suffix in (".yaml", ".yml")]
    if not items:
        return {"ok": True, "updated": 0, "skipped": 0, "results": []}

    updated, skipped = 0, 0
    results = []

    for raw_path in items:
        try:
            path = _ensure_editable_agent_file(raw_path.stem, project_id) if project_id else raw_path
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                skipped += 1
                continue

            rr = data.get("runtime_rules", {}) if isinstance(data.get("runtime_rules"), dict) else {}
            existing = str(rr.get("preferred_model", "")).strip()

            if existing:
                # 이미 설정됨 → 건너뜀
                results.append({"agent_id": path.stem, "status": "skipped", "preferred_model": existing})
                skipped += 1
                continue

            # role 추출
            role = str(
                data.get("role", "")
                or (data.get("identity", {}) or {}).get("role_summary", "")
            ).strip()

            auto_model = _resolve_model(role or path.stem)
            if not auto_model:
                results.append({"agent_id": path.stem, "status": "skipped_no_model"})
                skipped += 1
                continue

            rr["preferred_model"] = auto_model
            data["runtime_rules"] = rr
            data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            results.append({"agent_id": path.stem, "status": "updated", "preferred_model": auto_model})
            updated += 1

        except Exception as e:
            results.append({"agent_id": path.stem, "status": "error", "detail": str(e)})
            skipped += 1

    return {"ok": True, "updated": updated, "skipped": skipped, "results": results}
