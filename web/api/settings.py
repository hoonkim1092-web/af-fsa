"""
/api/settings — API 키 관리 및 엔진 상태 조회
"""
import os
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["settings"])

ROOT_DIR = Path(__file__).parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"

ENGINES = [
    {"id": "google",    "name": "Google Gemini", "env_key": "GOOGLE_API_KEY"},
    {"id": "openai",    "name": "OpenAI",        "env_key": "OPENAI_API_KEY"},
    {"id": "anthropic", "name": "Anthropic",     "env_key": "ANTHROPIC_API_KEY"},
]


@router.get("/settings/engines")
async def get_engine_status():
    """각 엔진의 API 키 등록 여부 반환."""
    result = []
    for engine in ENGINES:
        key = os.getenv(engine["env_key"], "")
        result.append({
            "id": engine["id"],
            "name": engine["name"],
            "env_key": engine["env_key"],
            "registered": bool(key),
            "masked_key": f"{key[:8]}...{key[-4:]}" if len(key) > 12 else ("****" if key else ""),
        })
    return {"engines": result}


class KeyUpdate(BaseModel):
    env_key: str
    value: str


@router.post("/settings/keys")
async def update_api_key(req: KeyUpdate):
    """API 키를 .env에 저장하고 환경변수에 반영."""
    # 환경변수에 즉시 반영
    os.environ[req.env_key] = req.value

    # .env 파일에 영구 저장
    lines = []
    found = False
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{req.env_key}="):
            new_lines.append(f"{req.env_key}={req.value}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{req.env_key}={req.value}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return {
        "success": True,
        "env_key": req.env_key,
        "registered": bool(req.value),
    }
