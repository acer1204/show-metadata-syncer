"""TMDB source adapter — 官方 API v3（免費，需 API key）。

Key 從設定頁貼上（/api/settings 的 tmdb_api_key）或環境變數 TMDB_API_KEY。
沒設 key 時 search() 回空清單、full() 回 empty()，不會擋到其他來源。

跟 TVDB 不同：TMDB 的 season 端點一次就含每集 overview + crew，
所以 episodes="list" 與 "full" 成本相同，行為一致。
"""
import threading
import time
from typing import Any, Dict, List, Optional

import requests
from fastapi import HTTPException
from rapidfuzz import fuzz

from ..config import settings, lang_priority_list

NAME = "tmdb"
REQUIRES_KEY = True
IMG = "https://image.tmdb.org/t/p"


def ready() -> bool:
    """需要 API key（設定頁或 env TMDB_API_KEY）。"""
    return bool(settings.tmdb_api_key)

# 我們的語言代碼 → TMDB language 參數
LANG_MAP = {"zhtw": "zh-TW", "zho": "zh-CN", "jpn": "ja-JP", "eng": "en-US"}

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 1800


def _cache_get(key):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item and time.time() - item["ts"] < _CACHE_TTL:
            return item["data"]
    return None


def _cache_set(key, data):
    with _CACHE_LOCK:
        _CACHE[key] = {"ts": time.time(), "data": data}


def _get(path: str, **params) -> dict:
    """v4 讀取權杖（eyJ 開頭的 JWT）走 Authorization header；
    v3 API key 走 query 參數（TMDB 官方兩種都支援）。"""
    key = settings.tmdb_api_key
    headers = {"Accept": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    r = requests.get(f"{settings.tmdb_url}{path}", params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def _primary_lang(priority: Optional[List[str]] = None) -> str:
    pr = priority or lang_priority_list()
    return LANG_MAP.get(pr[0], "zh-TW") if pr else "zh-TW"


def _img(path: str, size: str = "original") -> str:
    return f"{IMG}/{size}{path}" if path else ""


# ─── search() ──────────────────────────────────────────────────────────
def search(q: str, limit: int = 5) -> List[dict]:
    if not settings.tmdb_api_key:
        return []
    lang = _primary_lang()
    try:
        data = _get("/search/tv", query=q, language=lang, include_adult="true")
    except Exception:
        return []
    q_lo = q.lower()
    out = []
    for r in (data.get("results") or [])[:limit]:
        name = r.get("name") or ""
        orig = r.get("original_name") or ""
        score = max(
            fuzz.WRatio(q_lo, name.lower()),
            fuzz.WRatio(q_lo, orig.lower()),
        )
        is_zh = lang.startswith("zh")
        out.append({
            "source": NAME,
            "id": str(r.get("id", "")),
            "title_cn": name if is_zh else "",
            "title_native": orig,
            "title_english": name if lang.startswith("en") else "",
            "year": (r.get("first_air_date") or "")[:4],
            "url": f"https://www.themoviedb.org/tv/{r.get('id')}",
            "cover": _img(r.get("poster_path"), "w342"),
            "score": round(score, 1),
            "overview": (r.get("overview") or "")[:200],
            "aliases": [],
            "hint": "",
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# ─── full() ────────────────────────────────────────────────────────────
def empty() -> dict:
    from .base import empty_detail
    return empty_detail(NAME)


def _translations_maps(raw: dict) -> tuple[Dict[str, str], Dict[str, str]]:
    """TMDB translations → 我們的 {zhtw/zho/jpn/eng: value} dicts。"""
    titles: Dict[str, str] = {}
    overviews: Dict[str, str] = {}
    for t in ((raw.get("translations") or {}).get("translations") or []):
        iso1 = t.get("iso_639_1", "")
        iso2 = t.get("iso_3166_1", "")
        data = t.get("data") or {}
        if iso1 == "zh":
            code = "zhtw" if iso2 in ("TW", "HK") else "zho"
        elif iso1 == "ja":
            code = "jpn"
        elif iso1 == "en":
            code = "eng"
        else:
            continue
        if data.get("name") and code not in titles:
            titles[code] = data["name"]
        if data.get("overview") and code not in overviews:
            overviews[code] = data["overview"]
    return titles, overviews


def _mpaa(raw: dict) -> str:
    ratings = ((raw.get("content_ratings") or {}).get("results") or [])
    by_cc = {r.get("iso_3166_1"): r.get("rating", "") for r in ratings}
    return by_cc.get("TW") or by_cc.get("US") or (ratings[0].get("rating", "") if ratings else "")


def _episode_row(ep: dict, season_number: int) -> dict:
    directors = [{"name": c.get("name", ""), "tmdbid": str(c.get("id", ""))}
                 for c in (ep.get("crew") or []) if c.get("job") == "Director"]
    writers = [{"name": c.get("name", ""), "tmdbid": str(c.get("id", ""))}
               for c in (ep.get("crew") or []) if c.get("department") == "Writing"]
    return {
        "id": str(ep.get("id", "")),
        "number": ep.get("episode_number", 0),
        "season_number": ep.get("season_number", season_number),
        "title": ep.get("name", ""),
        "plot": ep.get("overview", "") or "",
        "overviews": {},
        "aired": ep.get("air_date", "") or "",
        "runtime": str(ep.get("runtime") or ""),
        "directors": directors,
        "writers": writers,
        "imdb_id": "",
        "tmdb_id": str(ep.get("id", "")),
        "thumb": _img(ep.get("still_path"), "w780"),
        "url": "",
    }


def full(item_id: str, episodes: str = "none",
         priority: Optional[List[str]] = None, hint: str = "") -> Dict[str, Any]:
    from .base import empty_detail
    if not settings.tmdb_api_key:
        return empty_detail(NAME)
    if priority is None:
        priority = lang_priority_list()
    lang = _primary_lang(priority)

    cache_key = f"tv/{item_id}/{lang}"
    raw = _cache_get(cache_key)
    if raw is None:
        try:
            raw = _get(f"/tv/{item_id}", language=lang,
                       append_to_response="external_ids,credits,content_ratings,videos,translations")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return empty_detail(NAME)
            raise HTTPException(502, f"TMDB 錯誤: {e}")
        except Exception as e:
            raise HTTPException(502, f"TMDB 錯誤: {e}")
        _cache_set(cache_key, raw)

    titles, overviews = _translations_maps(raw)
    ext = raw.get("external_ids") or {}
    networks = raw.get("networks") or []
    runtimes = raw.get("episode_run_time") or []
    videos = ((raw.get("videos") or {}).get("results") or [])
    trailers = [f"https://www.youtube.com/watch?v={v['key']}"
                for v in videos if v.get("site") == "YouTube" and v.get("key")]
    actors = [{
        "name": c.get("name", ""),
        "role": c.get("character", ""),
        "type": "Actor",
        "tvdbid": "",
        "tmdbid": str(c.get("id", "")),
        "thumb": _img(c.get("profile_path"), "w185"),
    } for c in ((raw.get("credits") or {}).get("cast") or [])]

    d = empty_detail(NAME)
    d.update({
        "id": str(raw.get("id", item_id) or item_id),
        "url": f"https://www.themoviedb.org/tv/{raw.get('id', item_id)}",
        "match_score": 100,
        "title": raw.get("name", "") or "",
        "original_title": raw.get("original_name", "") or "",
        "title_translations": titles,
        "plot": raw.get("overview", "") or "",
        "overviews": overviews,
        "year": (raw.get("first_air_date") or "")[:4],
        "premiered": raw.get("first_air_date", "") or "",
        "end_date": raw.get("last_air_date", "") or "",
        "status": raw.get("status", "") or "",
        "studio": networks[0].get("name", "") if networks else "",
        "runtime": str(runtimes[0]) if runtimes else "",
        "country": ",".join(raw.get("origin_country") or []),
        "language": raw.get("original_language", "") or "",
        "mpaa": _mpaa(raw),
        "genres": [g.get("name", "") for g in (raw.get("genres") or [])],
        "tags": [],
        "rating": {
            "score": raw.get("vote_average") or None,
            "votes": raw.get("vote_count") or None,
        },
        "unique_ids": {
            "tvdb": str(ext.get("tvdb_id") or ""),
            "imdb": ext.get("imdb_id") or "",
            "tmdb": str(raw.get("id", "") or ""),
            "tvrage": str(ext.get("tvrage_id") or ""),
        },
        "trailers": trailers,
        "actors": actors,
        "images": {
            "poster": _img(raw.get("poster_path")),
            "fanart": _img(raw.get("backdrop_path")),
            "clearlogo": "",
            "banner": "",
        },
        "season_count": raw.get("number_of_seasons"),
        "episode_count": raw.get("number_of_episodes"),
    })

    seasons_out = []
    for s in raw.get("seasons") or []:
        sn = s.get("season_number")
        season_row = {
            "number": sn,
            "name": s.get("name", ""),
            "title": s.get("name", "") or ("特別篇" if sn == 0 else f"季別 {sn}"),
            "plot": s.get("overview", "") or "",
            "title_translations": {},
            "overviews": {},
            "tvdb_id": "",
            "from": s.get("air_date", "") or "",
            "to": "",
            "episode_count": s.get("episode_count", 0),
            "poster": _img(s.get("poster_path"), "w342"),
            "url": f"https://www.themoviedb.org/tv/{d['id']}/season/{sn}",
            "episodes": [],
        }
        if episodes in ("list", "full") and sn is not None:
            skey = f"tv/{item_id}/season/{sn}/{lang}"
            sdata = _cache_get(skey)
            if sdata is None:
                try:
                    sdata = _get(f"/tv/{item_id}/season/{sn}", language=lang)
                    _cache_set(skey, sdata)
                except Exception:
                    sdata = None
            if sdata:
                season_row["episodes"] = [
                    _episode_row(ep, sn) for ep in (sdata.get("episodes") or [])
                ]
                if sdata.get("overview"):
                    season_row["plot"] = sdata["overview"]
        seasons_out.append(season_row)
    d["seasons"] = seasons_out
    return d
