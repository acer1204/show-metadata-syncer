"""TheTVDB source adapter — 把 clients/tvdb.py 的爬蟲包成統一的
search() / full() 介面，並附帶 30 分鐘記憶體快取。"""
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from rapidfuzz import fuzz

from ..config import settings, lang_priority_list
from ..clients.tvdb import (
    search_series, resolve_slug, scrape_series_page, scrape_season_page,
    scrape_episode_page, pick_translation, fmt_date, fmt_year,
    get_episode_image_url,
)
from .base import empty_detail

NAME = "tvdb"
REQUIRES_KEY = False


def ready() -> bool:
    """TVDB 純爬蟲，不需 key，永遠可用。"""
    return True

# ─── 簡易快取（避免重複爬同一個系列） ─────────────────────────────────
_SERIES_CACHE: Dict[str, Dict[str, Any]] = {}
_SLUG_CACHE: Dict[str, str] = {}
_SEASON_CACHE: Dict[str, Dict[str, Any]] = {}
_EPISODE_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 1800  # 30 分鐘


def _cache_get(cache, key):
    with _CACHE_LOCK:
        item = cache.get(key)
        if item and time.time() - item["ts"] < _CACHE_TTL:
            return item["data"]
    return None


def _cache_set(cache, key, data):
    with _CACHE_LOCK:
        cache[key] = {"ts": time.time(), "data": data}


def get_slug(series_id: str, hint: str = "") -> str:
    """series_id → slug。search 結果若已帶 slug 可用 hint 直接種進快取。"""
    cached = _SLUG_CACHE.get(series_id)
    if cached:
        return cached
    slug = hint or resolve_slug(series_id)
    if not slug:
        raise HTTPException(status_code=404, detail=f"找不到 series_id={series_id} 對應的網址")
    _SLUG_CACHE[series_id] = slug
    return slug


def get_series(series_id: str, slug_hint: str = "") -> Dict[str, Any]:
    cached = _cache_get(_SERIES_CACHE, series_id)
    if cached:
        return cached
    slug = get_slug(series_id, slug_hint)
    try:
        data = scrape_series_page(slug)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"爬系列頁失敗: {e}")
    data["_slug"] = slug
    _cache_set(_SERIES_CACHE, series_id, data)
    return data


def get_season(series_id: str, season_num: int) -> Dict[str, Any]:
    key = f"{series_id}/{season_num}"
    cached = _cache_get(_SEASON_CACHE, key)
    if cached:
        return cached
    sd = get_series(series_id)
    s_info = next((s for s in sd.get("seasons", []) if s["number"] == season_num), None)
    if not s_info:
        raise HTTPException(status_code=404, detail=f"季 {season_num} 不存在")
    if not s_info.get("url"):
        raise HTTPException(status_code=404, detail=f"季 {season_num} 沒有 URL")
    try:
        season_data = scrape_season_page(s_info["url"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"爬季頁失敗: {e}")
    s_info["tvdb_id"] = season_data.get("tvdb_id", "")
    s_info["overviews"] = season_data.get("overviews", {})
    s_info["title_translations"] = season_data.get("title_translations", {})
    merged = {**s_info, "episodes": season_data["episodes"]}
    _cache_set(_SEASON_CACHE, key, merged)
    return merged


def get_episode_detail(ep: Dict[str, Any]) -> Dict[str, Any]:
    """單集詳細頁（plot / directors / writers / 外部 ID），以 episode id 快取。"""
    ep_id = str(ep.get("id", ""))
    if not ep_id or not ep.get("url"):
        return {}
    cached = _cache_get(_EPISODE_CACHE, ep_id)
    if cached is not None:
        return cached
    detail = scrape_episode_page(ep["url"])
    _cache_set(_EPISODE_CACHE, ep_id, detail)
    return detail


def find_episode(series_id: str, episode_id: str) -> Dict[str, Any]:
    """在整個系列裡找某集（含季號），回傳合併後的完整資料。"""
    sd = get_series(series_id)
    for s_info in sd.get("seasons", []):
        sn = s_info.get("number")
        if sn is None or not s_info.get("url"):
            continue
        try:
            season = get_season(series_id, sn)
        except HTTPException:
            continue
        for ep in season.get("episodes", []):
            if str(ep.get("id")) == str(episode_id):
                detail = get_episode_detail(ep)
                return {**ep, **detail, "season_number": sn}
    raise HTTPException(status_code=404, detail=f"集 {episode_id} 不存在")


# ─── search() ──────────────────────────────────────────────────────────
def search(q: str, limit: int = 5) -> List[dict]:
    """TheTVDB Algolia 搜尋 → preview 項目，fuzzy score 用原始 query 重排。"""
    hits = search_series(q, settings.search_lang)
    if not hits:
        hits = search_series(q, "en")
    q_lo = q.lower()
    out = []
    for h in hits[:limit]:
        tr = h.get("translations", {}) or {}
        title_cn = tr.get("zhtw") or tr.get("zho") or ""
        title_native = tr.get("jpn") or ""
        title_english = tr.get("eng") or h.get("name_en") or ""
        candidates = [h.get("name", ""), title_cn, title_native, title_english] + list(h.get("aliases", []))
        score = max((fuzz.WRatio(q_lo, str(c).lower()) for c in candidates if c), default=0)
        out.append({
            "source": NAME,
            "id": str(h["id"]),
            "title_cn": title_cn or (h.get("name") or ""),
            "title_native": title_native,
            "title_english": title_english,
            "year": h.get("firstAired", ""),
            "url": f"https://www.thetvdb.com/dereferrer/series/{h['id']}",
            "cover": h.get("image", ""),
            "score": round(score, 1),
            "overview": (h.get("overview", "") or "")[:200],
            "aliases": h.get("aliases", []),
            "hint": h.get("slug", ""),
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# ─── full() ────────────────────────────────────────────────────────────
def empty() -> dict:
    return empty_detail(NAME)


def _canonical_episode(ep: Dict[str, Any], series_id: str, season_number: int,
                       priority: List[str], full_detail: bool) -> Dict[str, Any]:
    plot = ep.get("overview", "") or ""
    overviews = ep.get("overviews", {}) or {}
    directors: list = []
    writers: list = []
    imdb_id = ep.get("imdb_id", "")
    tmdb_id = ep.get("tmdb_id", "")
    runtime = ep.get("runtime", "")
    aired = ep.get("aired", "")
    if full_detail:
        try:
            detail = get_episode_detail(ep)
        except Exception:
            detail = {}
        overviews = detail.get("overviews", overviews) or overviews
        plot = pick_translation(overviews, priority=priority) if overviews else plot
        directors = detail.get("directors", [])
        writers = detail.get("writers", [])
        imdb_id = detail.get("imdb_id", "") or imdb_id
        tmdb_id = detail.get("tmdb_id", "") or tmdb_id
        runtime = detail.get("runtime", "") or runtime
        aired = detail.get("aired", "") or aired
    return {
        "id": str(ep.get("id", "")),
        "number": ep.get("number", 0),
        "season_number": ep.get("seasonNumber", season_number),
        "title": ep.get("name", ""),
        "plot": plot,
        "overviews": overviews,
        "aired": fmt_date(aired),
        "runtime": runtime,
        "directors": directors,
        "writers": writers,
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "thumb": get_episode_image_url(series_id, ep.get("id", "")) if ep.get("id") else "",
        "url": ep.get("url", ""),
    }


def full(item_id: str, episodes: str = "none",
         priority: Optional[List[str]] = None, hint: str = "") -> Dict[str, Any]:
    """組出一個系列的 canonical detail。

    episodes:
      - "none": 只有系列欄位 + 季列表（不含集）
      - "list": 各季爬集數列表（title/aired/runtime/縮圖 URL，快）
      - "full": 每集再爬詳細頁（plot/導演/編劇/外部 ID，慢，但有快取）
    """
    if priority is None:
        priority = lang_priority_list()
    sd = get_series(item_id, hint)

    tr = sd.get("title_translations", {})
    ov = sd.get("overviews", {})
    title = pick_translation(tr, sd.get("title", ""), priority=priority)
    original_title = tr.get("jpn") or sd.get("title", "")
    plot = pick_translation(ov, priority=priority)

    try:
        score = float(sd.get("rating", "0") or 0)
    except ValueError:
        score = 0.0

    d = empty()
    d.update({
        "id": str(sd.get("series_id", item_id) or item_id),
        "url": sd.get("url", ""),
        "match_score": 100,
        "title": title,
        "original_title": original_title,
        "title_translations": tr,
        "plot": plot,
        "overviews": ov,
        "year": fmt_year(sd.get("first_aired", "")),
        "premiered": fmt_date(sd.get("first_aired", "")),
        "status": sd.get("status", ""),
        "studio": sd.get("network", ""),
        "runtime": sd.get("runtime", ""),
        "country": sd.get("country", ""),
        "language": sd.get("language", ""),
        "mpaa": sd.get("mpaa", ""),
        "genres": sd.get("genres", []),
        "tags": sd.get("tags", []),
        "rating": {"score": score if score > 0 else None, "votes": None},
        "unique_ids": {
            "tvdb": str(sd.get("series_id", "") or ""),
            "imdb": sd.get("imdb_id", ""),
            "tmdb": sd.get("tmdb_id", ""),
            "tvrage": sd.get("tvrage_id", ""),
        },
        "trailers": sd.get("trailers", []),
        "actors": sd.get("actors", []),
        "images": {**{"poster": "", "fanart": "", "clearlogo": "", "banner": ""},
                   **(sd.get("images", {}) or {})},
        "season_count": len(sd.get("seasons", [])),
        "episode_count": sum(s.get("episode_count", 0) for s in sd.get("seasons", [])),
    })

    sid = d["unique_ids"]["tvdb"] or item_id
    seasons_out = []
    all_aired: List[str] = []
    for s in sd.get("seasons", []) or []:
        sn = s.get("number")
        season_row = {
            "number": sn,
            "name": s.get("name", ""),
            "title": "",
            "plot": "",
            "title_translations": s.get("title_translations", {}) or {},
            "overviews": s.get("overviews", {}) or {},
            "tvdb_id": s.get("tvdb_id", ""),
            "from": s.get("from", ""),
            "to": s.get("to", ""),
            "episode_count": s.get("episode_count", 0),
            "poster": f"https://artworks.thetvdb.com/banners/seasons/{sid}-{sn}.jpg" if sid else "",
            "url": s.get("url", ""),
            "episodes": [],
        }
        if episodes in ("list", "full") and sn is not None and s.get("url"):
            try:
                merged = get_season(item_id, sn)
                season_row["tvdb_id"] = merged.get("tvdb_id", "")
                season_row["title_translations"] = merged.get("title_translations", {}) or {}
                season_row["overviews"] = merged.get("overviews", {}) or {}
                eps = merged.get("episodes", [])
                want_full = episodes == "full"
                out_eps = []
                for i, ep in enumerate(eps):
                    out_eps.append(_canonical_episode(ep, sid, sn, priority, want_full))
                    if want_full and i < len(eps) - 1:
                        time.sleep(settings.episode_delay)
                season_row["episodes"] = out_eps
                all_aired.extend(e["aired"] for e in out_eps if e.get("aired"))
            except HTTPException:
                pass
        season_row["title"] = (
            pick_translation(season_row["title_translations"], priority=priority)
            if season_row["title_translations"]
            else ("特別篇" if sn == 0 else f"季別 {sn}")
        )
        season_row["plot"] = (
            pick_translation(season_row["overviews"], priority=priority)
            if season_row["overviews"] else ""
        )
        seasons_out.append(season_row)

    d["seasons"] = seasons_out
    if all_aired:
        d["end_date"] = sorted(all_aired)[-1]
    return d
