"""Full metadata: returns canonical-shape details for the top-N candidates of
each source, globally ranked by fuzzy score against the query.

Response shape:
    {
      "query": "...",
      "sources": [
        { "source": "tvdb", "id": "...", "match_score": 100, ...canonical fields },
        ...
      ]
    }

`source` accepts "all" (default) or a registered source name — same semantics
as /api/preview. A source with zero hits still contributes one empty entry so
the client knows the source was tried.

`episodes` 控制每個 detail 要抓多深:
  - none: 系列欄位 + 季列表（預設，最快）
  - list: 各季的集數列表（title/aired/縮圖 URL）
  - full: 每集詳細頁（plot/導演/編劇），很慢，建議只對單一 id 使用
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..sources import get_source, source_names, enabled_names, is_enabled

router = APIRouter(prefix="/api/metadata", tags=["metadata"])

CANDIDATES_PER_SOURCE = 3  # how many top hits to return per source

EPISODES_MODES = ("none", "list", "full")


def _check_episodes(episodes: str) -> str:
    if episodes not in EPISODES_MODES:
        raise HTTPException(400, f"episodes 必須是 {EPISODES_MODES} 之一")
    return episodes


@router.get("", operation_id="metadata_search")
async def metadata(
    q: str,
    source: str = "all",
    episodes: str = Query("none", description="none / list / full"),
    min_score: Optional[float] = Query(None, ge=0, le=100,
                                       description="選填的分數下限；不給則不過濾（fuzz_threshold 僅供 client 參考）"),
):
    _check_episodes(episodes)
    if source == "all":
        mods = [get_source(n) for n in enabled_names()]
    else:
        if get_source(source) and not is_enabled(source):
            raise HTTPException(400, f"來源 {source} 已停用（設定頁可重新啟用）")
        mods = [m for m in [get_source(source)] if m]
        if not mods:
            raise HTTPException(400, f"unknown source: {source!r} (expected: all, {', '.join(source_names())})")

    async def _one_source(mod):
        try:
            hits = await asyncio.to_thread(mod.search, q)
        except Exception:
            hits = []
        # 預設不過濾；client 有帶 min_score 時才先濾（也省下抓 detail 的時間）
        if min_score is not None:
            hits = [h for h in hits if h.get("score", 0) >= min_score]
        hits = hits[:CANDIDATES_PER_SOURCE]

        async def _fetch(h):
            try:
                d = await asyncio.to_thread(
                    mod.full, h["id"], episodes, None, h.get("hint", ""))
                d["match_score"] = h.get("score", 0)
                return d
            except Exception:
                return None

        details = [d for d in await asyncio.gather(*(_fetch(h) for h in hits)) if d]
        return details or [mod.empty()]

    per_source = await asyncio.gather(*(_one_source(m) for m in mods))
    sources = [d for group in per_source for d in group]
    sources.sort(key=lambda x: x["match_score"], reverse=True)
    return {"query": q, "sources": sources}


@router.get("/{source}/{item_id}", operation_id="metadata_by_id", summary="Metadata by id")
async def metadata_by_id(
    source: str,
    item_id: str,
    episodes: str = Query("list", description="none / list / full"),
):
    """Fetch one item's full canonical detail directly by (source, id).

    Useful when the client already picked a candidate from /api/preview and
    just wants its complete metadata. Returns the canonical detail object
    flat (no `sources` wrapper). `match_score` is set to 100 because the
    caller has confirmed this is the right pick.

    你的 Emby-like client 走這條即可，例如:
      GET /api/metadata/tvdb/87491?episodes=full
    """
    _check_episodes(episodes)
    mod = get_source(source)
    if mod is None:
        raise HTTPException(400, f"unknown source: {source!r} (expected: {', '.join(source_names())})")
    if not is_enabled(source):
        raise HTTPException(400, f"來源 {source} 已停用（設定頁可重新啟用）")
    detail = await asyncio.to_thread(mod.full, item_id, episodes, None, "")
    if not detail.get("id"):
        raise HTTPException(404, f"{source} id {item_id} not found")
    detail["match_score"] = 100
    return detail
