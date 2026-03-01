"""
Agent Factory Web Interface — FastAPI Server
비개발자를 위한 프리미엄 웹 인터페이스.
"""
import os
import sys
import json
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

# ── 경로 설정 ──────────────────────────────────────────────────────────────
WEB_DIR = Path(__file__).parent
ROOT_DIR = WEB_DIR.parent  # agent-factory root
sys.path.insert(0, str(ROOT_DIR))

# .env 로드 (agent-factory 루트의 .env)
load_dotenv(ROOT_DIR / ".env")

# ── API 라우터 임포트 ──────────────────────────────────────────────────────
from api.agents import router as agents_router
from api.run import router as run_router
from api.settings import router as settings_router

# ── Lifespan ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"\n🏭 Agent Factory Web v1.0 starting...")
    print(f"   Root: {ROOT_DIR}")
    print(f"   Web:  {WEB_DIR}")
    yield
    print("🏭 Agent Factory Web shutting down.")


# ── App 생성 ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agent Factory Web",
    version="1.0.0",
    lifespan=lifespan,
)

# Static files
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

# API Routes
app.include_router(agents_router, prefix="/api")
app.include_router(run_router, prefix="/api")
app.include_router(settings_router, prefix="/api")


# ── i18n 엔드포인트 ────────────────────────────────────────────────────────
@app.get("/api/i18n/{lang}")
async def get_i18n(lang: str):
    """한국어/영어 번역 파일 반환."""
    lang = lang if lang in ("ko", "en") else "en"
    i18n_path = WEB_DIR / "i18n" / f"{lang}.json"
    if i18n_path.exists():
        return json.loads(i18n_path.read_text(encoding="utf-8"))
    return {}


# ── SPA 메인 페이지 ────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    """메인 SPA 페이지."""
    html_path = WEB_DIR / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ── 진입점 ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(WEB_DIR)],
    )
