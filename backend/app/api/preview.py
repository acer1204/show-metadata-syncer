"""Lightweight preview: titles + cover + score, no detail fetch.

Use this when you only need name/cover/id for a candidate picker or autocomplete.
For full metadata, hit /api/metadata instead.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from fastapi import HTTPException

from ..sources import get_source, enabled_names, is_enabled

router = APIRouter(prefix="/api/preview", tags=["preview"])


class PreviewItem(BaseModel):
    source: str
    id: str
    title_cn: str | None = None
    title_native: str | None = None
    title_english: str | None = None
    year: str | None = None
    url: str | None = None
    cover: str | None = None
    score: float = 0
    overview: str = ""          # 該來源附的簡介節錄
    aliases: list[str] = []
    hint: str = ""              # 傳回給 /api/metadata 的來源內部提示（tvdb=slug）


@router.get("", response_model=list[PreviewItem], operation_id="preview_search")
async def preview(
    q: str,
    source: str = "all",
    min_score: Optional[float] = Query(None, ge=0, le=100,
                                       description="選填的分數下限；不給則不過濾（fuzz_threshold 僅供 client 參考）"),
):
    """快速搜尋候選（不抓詳細頁）。source 可為 all 或單一來源代號。

    預設不過濾（跟 comic 版一致）；client 可自行帶 min_score 過濾。
    """
    if source == "all":
        mods = [get_source(n) for n in enabled_names()]
    else:
        if get_source(source) and not is_enabled(source):
            raise HTTPException(400, f"來源 {source} 已停用（設定頁可重新啟用）")
        mods = [m for m in [get_source(source)] if m]
    results = await asyncio.gather(
        *(asyncio.to_thread(m.search, q) for m in mods), return_exceptions=True
    )
    hits: list[PreviewItem] = []
    for items in results:
        if isinstance(items, BaseException):
            continue
        hits.extend(PreviewItem(**it) for it in items
                    if min_score is None or it.get("score", 0) >= min_score)
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits
