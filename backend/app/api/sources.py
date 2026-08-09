"""列出平台目前註冊的 metadata 來源與其可用狀態。

client 可以先打這支，決定要對哪些來源查詢、或提示使用者去設定 key。
"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..sources import SOURCES, is_enabled

router = APIRouter(prefix="/api/sources", tags=["sources"])

# 支援 NFO 爬蟲任務（/api/crawl）的來源
_NFO_CRAWL = {"tvdb"}


class SourceStatus(BaseModel):
    name: str
    enabled: bool        # 設定頁的啟用開關（停用的來源不參與搜尋）
    ready: bool          # 現在就能查（TMDB 沒設 key 時為 false）
    requires_key: bool   # 是否需要 API key
    nfo_crawl: bool      # 是否支援 /api/crawl 產生 NFO 目錄


@router.get("", response_model=list[SourceStatus], operation_id="list_sources")
def list_sources():
    """回傳所有已註冊來源。ready=false 代表已註冊但缺設定（例如 TMDB 沒 key）。"""
    out = []
    for name, mod in SOURCES.items():
        ready_fn = getattr(mod, "ready", None)
        is_ready = bool(ready_fn()) if callable(ready_fn) else True
        out.append(SourceStatus(
            name=name,
            enabled=is_enabled(name),
            ready=is_ready,
            requires_key=bool(getattr(mod, "REQUIRES_KEY", False)),
            nfo_crawl=name in _NFO_CRAWL,
        ))
    return out
