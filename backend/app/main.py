from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from . import runtime_settings
from .api import preview, metadata as metadata_api, nfo, tasks, settings as settings_api, sources as sources_api

app = FastAPI(
    title="Show Metadata Syncer",
    description="從 TheTVDB 爬取電視節目元資料，回傳統一 canonical JSON（含圖片 URL），並可產生 Emby/Jellyfin 相容 NFO。",
    version="2.0.0",
)

# JSON 回應補 charset=utf-8：PowerShell 5.1 等舊 client 沒看到 charset 會用
# ISO-8859-1 解碼，中文全變亂碼
@app.middleware("http")
async def _json_charset(request, call_next):
    resp = await call_next(request)
    if resp.headers.get("content-type") == "application/json":
        resp.headers["content-type"] = "application/json; charset=utf-8"
    return resp


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sources_api.router)
app.include_router(preview.router)
app.include_router(metadata_api.router)
app.include_router(nfo.router)
app.include_router(tasks.router)
app.include_router(settings_api.router)


@app.on_event("startup")
def _load_overrides() -> None:
    runtime_settings.load_from_disk()
    Path(settings.output_dir).mkdir(parents=True, exist_ok=True)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ─── MCP server — 自動把所有有 operation_id 的端點變成 MCP tools ──────
try:
    from fastapi_mcp import FastApiMCP
    mcp = FastApiMCP(
        app,
        name="Show Metadata Syncer",
        description="TheTVDB 抓取/查詢/NFO 生成/任務管理 API（Emby 相容）。",
    )
    mcp.mount_http()  # Streamable HTTP transport at /mcp
except Exception as e:  # fastapi-mcp 未安裝或版本 mismatch 時降級為純 REST
    import logging
    logging.warning(f"MCP mount 失敗，降級為純 REST API: {e}")


# In Docker the built frontend is copied to /app/static.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if STATIC_DIR.is_dir():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # index.html 一律 no-cache：否則瀏覽器快取舊頁後，改版的 hashed bundle 不會被載入
    _NO_CACHE = {"Cache-Control": "no-cache"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api", "mcp")):
            raise HTTPException(status_code=404)
        file = STATIC_DIR / full_path
        if full_path and file.is_file():
            return FileResponse(file)
        return FileResponse(STATIC_DIR / "index.html", headers=_NO_CACHE)
else:
    @app.get("/", include_in_schema=False)
    def index():
        return {
            "service": "Show Metadata Syncer",
            "docs": "/docs",
            "hint": "前端未建置（開發模式請用 frontend/ 的 vite dev server）",
        }
