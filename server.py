#!/usr/bin/env python3
"""TheTVDB NFO Crawler - FastAPI + MCP Server

提供完整 Emby NFO 所需的 read / 生成 / 下載 / 任務管理 API，並同步暴露為 MCP tools。

端點分組:
  A. Series metadata (9)
  B. Season / Episode metadata (6)
  C. NFO generators (3)
  D. Task management (5)
  E. Artwork download (4)
  F. 既有：search / crawl / status (3) + 前端 / (1) + MCP /mcp
"""

import sys, io, os, re, time, threading, shutil, mimetypes
from pathlib import Path
from typing import List, Optional, Dict, Any

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response, PlainTextResponse
from pydantic import BaseModel, Field
from fastapi_mcp import FastApiMCP

from tvdb_crawler import (
    search_series, resolve_slug, scrape_series_page, scrape_season_page,
    scrape_episode_page, generate_tvshow_nfo, generate_season_nfo,
    generate_episode_nfo, make_xml_declaration,
    download_image, scrape_season_images, get_episode_image_url,
    pick_translation, DEFAULT_PRIORITY,
)

app = FastAPI(
    title="TheTVDB NFO Crawler",
    description="從 TheTVDB 爬取動漫元資料、產 Emby/Jellyfin NFO；提供完整 read/生成/下載 API + MCP。",
    version="2.0.0",
)

OUTPUT_BASE = Path("./output")
TASKS: Dict[str, Dict[str, Any]] = {}
TASK_LOCK = threading.Lock()

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


def _get_slug(series_id: str) -> str:
    cached = _SLUG_CACHE.get(series_id)
    if cached:
        return cached
    slug = resolve_slug(series_id)
    if not slug:
        raise HTTPException(status_code=404, detail=f"找不到 series_id={series_id} 對應的網址")
    _SLUG_CACHE[series_id] = slug
    return slug


def _get_series(series_id: str) -> Dict[str, Any]:
    cached = _cache_get(_SERIES_CACHE, series_id)
    if cached:
        return cached
    slug = _get_slug(series_id)
    try:
        data = scrape_series_page(slug)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"爬系列頁失敗: {e}")
    data["_slug"] = slug
    _cache_set(_SERIES_CACHE, series_id, data)
    return data


def _get_season(series_id: str, season_num: int) -> Dict[str, Any]:
    key = f"{series_id}/{season_num}"
    cached = _cache_get(_SEASON_CACHE, key)
    if cached:
        return cached
    sd = _get_series(series_id)
    s_info = next((s for s in sd.get("seasons", []) if s["number"] == season_num), None)
    if not s_info:
        raise HTTPException(status_code=404, detail=f"季 {season_num} 不存在")
    if not s_info.get("url"):
        raise HTTPException(status_code=404, detail=f"季 {season_num} 沒有 URL")
    try:
        season_data = scrape_season_page(s_info["url"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"爬季頁失敗: {e}")
    # 回填到 s_info 並合併
    s_info["tvdb_id"] = season_data.get("tvdb_id", "")
    s_info["overviews"] = season_data.get("overviews", {})
    s_info["title_translations"] = season_data.get("title_translations", {})
    merged = {**s_info, "episodes": season_data["episodes"]}
    _cache_set(_SEASON_CACHE, key, merged)
    return merged


def _get_episode(series_id: str, episode_id: str) -> Dict[str, Any]:
    key = f"{series_id}/{episode_id}"
    cached = _cache_get(_EPISODE_CACHE, key)
    if cached:
        return cached
    # 找到該集所在的季 → URL
    sd = _get_series(series_id)
    target_ep = None
    for s_info in sd.get("seasons", []):
        if not s_info.get("url"):
            continue
        try:
            season_data = scrape_season_page(s_info["url"])
        except Exception:
            continue
        for ep in season_data["episodes"]:
            if ep.get("id") == str(episode_id):
                target_ep = {**ep, "season_number": s_info["number"]}
                break
        if target_ep:
            break
    if not target_ep or not target_ep.get("url"):
        raise HTTPException(status_code=404, detail=f"集 {episode_id} 不存在")
    try:
        ep_detail = scrape_episode_page(target_ep["url"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"爬集頁失敗: {e}")
    merged = {**target_ep, **ep_detail}
    _cache_set(_EPISODE_CACHE, key, merged)
    return merged


# ─── 背景爬蟲 ──────────────────────────────────────────────────────────
def run_crawl(task_id, series_id, lang, lang_priority=None):
    if lang_priority is None:
        lang_priority = list(DEFAULT_PRIORITY)
    try:
        with TASK_LOCK:
            TASKS[task_id]["status"] = "running"

        def log(msg):
            with TASK_LOCK:
                TASKS[task_id]["logs"].append(msg)

        slug = resolve_slug(series_id)
        log(f"TVDB 網址: https://www.thetvdb.com/series/{slug}")

        log("爬取系列資訊 ...")
        series_data = scrape_series_page(slug)
        title = series_data.get("title", slug)
        actors = series_data.get("actors", [])
        log(f"  標題: {title}    季數: {len(series_data.get('seasons', []))}    演員: {len(actors)} 位")

        episodes_by_season = {}
        seasons = series_data.get("seasons", [])
        for s_info in seasons:
            sn = s_info["number"]
            s_url = s_info["url"]
            if not s_url:
                continue
            log(f"爬取 {s_info['name']} ({s_info['episode_count']} 集) ...")
            season_data = scrape_season_page(s_url)
            s_info["tvdb_id"] = season_data.get("tvdb_id", "")
            s_info["overviews"] = season_data.get("overviews", {})
            s_info["title_translations"] = season_data.get("title_translations", {})
            eps = season_data["episodes"]
            episodes_by_season[sn] = eps
            for ep in eps:
                ep_url = ep.get("url", "")
                if not ep_url:
                    continue
                log(f"  取得 {ep.get('name', '?')} ...")
                try:
                    ep_detail = scrape_episode_page(ep_url)
                    ovs = ep_detail.get("overviews", {})
                    ep["overview"] = pick_translation(ovs, priority=lang_priority) or (list(ovs.values())[0] if ovs else "")
                    ep["directors"] = ep_detail.get("directors", [])
                    ep["writers"] = ep_detail.get("writers", [])
                    ep["imdb_id"] = ep_detail.get("imdb_id", "")
                    ep["tmdb_id"] = ep_detail.get("tmdb_id", "")
                    if ep_detail.get("aired"): ep["aired"] = ep_detail["aired"]
                    if ep_detail.get("runtime"): ep["runtime"] = ep_detail["runtime"]
                except Exception as e:
                    log(f"    警告: {e}")
                time.sleep(0.5)

        safe_title = "".join(c for c in title if c not in r'\/:*?"<>|')
        output_dir = OUTPUT_BASE / task_id / safe_title
        output_dir.mkdir(parents=True, exist_ok=True)

        images = series_data.get("images", {})
        log("下載系列圖片 ...")
        if images.get("poster") and download_image(images["poster"], output_dir / "poster.jpg"):
            series_data["poster_path"] = "poster.jpg"
        if images.get("fanart") and download_image(images["fanart"], output_dir / "fanart.jpg"):
            series_data["fanart_path"] = "fanart.jpg"
        if images.get("clearlogo"):
            ext = ".png" if images["clearlogo"].lower().endswith(".png") else ".jpg"
            if download_image(images["clearlogo"], output_dir / f"clearlogo{ext}"):
                series_data["clearlogo_path"] = f"clearlogo{ext}"
        if images.get("banner") and download_image(images["banner"], output_dir / "banner.jpg"):
            series_data["banner_path"] = "banner.jpg"

        for s_info in seasons:
            sn = s_info["number"]
            season_dir = output_dir / ("Specials" if sn == 0 else f"Season {sn:02d}")
            season_dir.mkdir(parents=True, exist_ok=True)
            simg = scrape_season_images(s_info.get("url", ""), series_id, sn)
            poster_name = f"season{sn:02d}-poster.jpg" if sn > 0 else "season-specials-poster.jpg"
            if simg.get("poster"):
                # 季海報放在系列根目錄（跟範例一致）
                download_image(simg["poster"], output_dir / poster_name)
            time.sleep(0.3)

        for s_info in seasons:
            sn = s_info["number"]
            eps = episodes_by_season.get(sn, [])
            season_dir = output_dir / ("Specials" if sn == 0 else f"Season {sn:02d}")
            season_dir.mkdir(parents=True, exist_ok=True)
            for ep in eps:
                ep_thumb = get_episode_image_url(series_id, ep.get("id", ""))
                thumb_name = f"S{ep.get('seasonNumber', sn):02d}E{ep.get('number', 0):02d}-thumb.jpg"
                if download_image(ep_thumb, season_dir / thumb_name):
                    ep["thumb_local"] = thumb_name
                time.sleep(0.2)

        log("產生 tvshow.nfo ...")
        tvshow_root = generate_tvshow_nfo(series_data, seasons, episodes_by_season, actors, lang, lang_priority)
        (output_dir / "tvshow.nfo").write_text(make_xml_declaration(tvshow_root), encoding="utf-8")

        for s_info in seasons:
            sn = s_info["number"]
            eps = episodes_by_season.get(sn, [])
            if not eps:
                continue
            season_dir = output_dir / ("Specials" if sn == 0 else f"Season {sn:02d}")
            season_dir.mkdir(parents=True, exist_ok=True)
            (season_dir / "season.nfo").write_text(
                make_xml_declaration(generate_season_nfo(s_info, eps, lang_priority)), encoding="utf-8")
            for ep in sorted(eps, key=lambda e: int(e.get("number", 0))):
                ep_num = ep.get("number", 0)
                ep_season = ep.get("seasonNumber", sn)
                ep_filename = f"S{ep_season:02d}E{ep_num:02d}.nfo"
                (season_dir / ep_filename).write_text(
                    make_xml_declaration(generate_episode_nfo(ep, series_data, lang_priority)), encoding="utf-8")

        log(f"==== 完成！NFO 輸出到: {output_dir.resolve()} ====")

        with TASK_LOCK:
            TASKS[task_id]["status"] = "done"
            TASKS[task_id]["output"] = str(output_dir.resolve())
            TASKS[task_id]["title"] = title
            # 留下 series_data 與 episodes_by_season 供 regenerate 使用
            TASKS[task_id]["_series_data"] = series_data
            TASKS[task_id]["_episodes_by_season"] = episodes_by_season
            TASKS[task_id]["_lang"] = lang
            TASKS[task_id]["_lang_priority"] = lang_priority
    except Exception as e:
        with TASK_LOCK:
            TASKS[task_id]["status"] = "error"
            TASKS[task_id]["logs"].append(f"錯誤: {e}")


# ═══════════════════════════════════════════════════════════════════════
# Pydantic models
# ═══════════════════════════════════════════════════════════════════════
class SearchRequest(BaseModel):
    name: str = Field(..., description="動漫名稱、TheTVDB 系列網址、或數字 TVDB ID")
    lang: str = Field("zho", description="搜尋語言代碼 (zho/jpn/eng)")


class SearchResult(BaseModel):
    id: str
    name: str
    year: Optional[str] = ""
    overview: Optional[str] = ""


class SearchResponse(BaseModel):
    results: List[SearchResult]


class CrawlRequest(BaseModel):
    id: str = Field(..., description="TheTVDB 系列數字 ID")
    lang: str = Field("zho", description="主要語言代碼")
    lang_priority: List[str] = Field(default_factory=lambda: list(DEFAULT_PRIORITY))


class CrawlResponse(BaseModel):
    task_id: str


class TaskStatus(BaseModel):
    id: str
    status: str
    logs: List[str]
    output: str = ""
    title: str = ""


class TaskSummary(BaseModel):
    id: str
    status: str
    title: str = ""
    output: str = ""
    log_count: int = 0


class Actor(BaseModel):
    name: str
    role: str = ""
    type: str = "Actor"
    tvdbid: str = ""
    tmdbid: str = ""
    thumb: str = ""


class Person(BaseModel):
    name: str
    tvdbid: str = ""


class SeriesMeta(BaseModel):
    series_id: str
    slug: str
    title: str
    title_translations: Dict[str, str] = {}
    overviews: Dict[str, str] = {}
    first_aired: str = ""
    status: str = ""
    network: str = ""
    runtime: str = ""
    country: str = ""
    language: str = ""
    rating: str = "0"
    mpaa: str = ""
    imdb_id: str = ""
    tmdb_id: str = ""
    tvrage_id: str = ""
    genres: List[str] = []
    tags: List[str] = []
    trailers: List[str] = []
    images: Dict[str, str] = {}
    actors: List[Actor] = []
    season_count: int = 0


class TranslationsResponse(BaseModel):
    title_translations: Dict[str, str]
    overviews: Dict[str, str]


class ExternalIds(BaseModel):
    tvdb: str = ""
    imdb: str = ""
    tmdb: str = ""
    tvrage: str = ""


class RatingsResponse(BaseModel):
    rating: str
    mpaa: str
    content_ratings: List[str] = []


class ArtworkResponse(BaseModel):
    type: str
    urls: List[str]


class SeasonMeta(BaseModel):
    number: int
    name: str
    tvdb_id: str = ""
    title_translations: Dict[str, str] = {}
    overviews: Dict[str, str] = {}
    from_: str = Field("", alias="from")
    to: str = ""
    episode_count: int = 0
    url: str = ""

    class Config:
        populate_by_name = True


class EpisodeMeta(BaseModel):
    id: str
    number: int
    seasonNumber: int
    name: str
    aired: str = ""
    runtime: str = ""
    url: str = ""
    overviews: Dict[str, str] = {}
    overview: str = ""
    directors: List[Person] = []
    writers: List[Person] = []
    imdb_id: str = ""
    tmdb_id: str = ""


class NfoRequest(BaseModel):
    series_id: str
    lang: str = "zho"
    lang_priority: List[str] = Field(default_factory=lambda: list(DEFAULT_PRIORITY))


class SeasonNfoRequest(NfoRequest):
    season: int


class EpisodeNfoRequest(NfoRequest):
    episode_id: str


class FileEntry(BaseModel):
    path: str
    name: str
    size: int
    is_dir: bool


class ArtworkDownloadRequest(BaseModel):
    url: str
    task_id: str
    rel_path: str = Field(..., description="輸出目錄下的相對路徑")


# ═══════════════════════════════════════════════════════════════════════
# F. 既有 3 個（保持 operation_id 不變）
# ═══════════════════════════════════════════════════════════════════════
@app.post("/api/search", operation_id="search_tvdb_series", response_model=SearchResponse, tags=["F. Search & Crawl"])
def api_search(req: SearchRequest):
    """根據名稱、URL 或數字 ID 在 TheTVDB 搜尋系列。"""
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "請輸入動漫名稱")
    if name.startswith("http"):
        m = re.search(r"/series/([^/]+)", name)
        slug = m.group(1) if m else ""
        if slug.isdigit():
            results = [{"id": slug, "name": slug}]
        else:
            sd = scrape_series_page(slug)
            sid = sd.get("series_id", "")
            results = [{"id": sid, "name": sd.get("title", slug)}] if sid else []
    elif name.isdigit():
        results = [{"id": name, "name": name}]
    else:
        raw = search_series(name, req.lang)
        if not raw:
            raw = search_series(name, "en")
        results = [
            {"id": r["id"], "name": r["name"], "year": r.get("firstAired", "")[:4],
             "overview": r.get("overview", "")[:100]}
            for r in raw
        ]
    return SearchResponse(results=[SearchResult(**r) for r in results])


@app.post("/api/crawl", operation_id="start_tvdb_crawl", response_model=CrawlResponse, tags=["F. Search & Crawl"])
def api_crawl(req: CrawlRequest):
    """啟動非同步爬蟲：抓系列+季+集 metadata、下載所有圖、產 NFO。回傳 task_id。"""
    sid = req.id.strip()
    if not sid:
        raise HTTPException(400, "請提供 Series ID")
    task_id = f"{int(time.time())}-{sid}"
    with TASK_LOCK:
        TASKS[task_id] = {"id": task_id, "status": "pending", "logs": [], "output": "", "title": ""}
    threading.Thread(target=run_crawl, args=(task_id, sid, req.lang, req.lang_priority), daemon=True).start()
    return CrawlResponse(task_id=task_id)


@app.get("/api/status/{task_id}", operation_id="get_crawl_status", response_model=TaskStatus, tags=["F. Search & Crawl"])
def api_status(task_id: str):
    """查詢爬取任務狀態與累積 log。"""
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskStatus(id=task["id"], status=task["status"], logs=task["logs"],
                      output=task.get("output", ""), title=task.get("title", ""))


# ═══════════════════════════════════════════════════════════════════════
# A. 系列 metadata (9)
# ═══════════════════════════════════════════════════════════════════════
@app.get("/api/series/{series_id}", operation_id="get_series", response_model=SeriesMeta, tags=["A. Series metadata"])
def get_series(series_id: str):
    """取得系列完整 metadata（NFO 所有欄位的來源）。"""
    d = _get_series(series_id)
    return SeriesMeta(
        series_id=d.get("series_id", series_id), slug=d.get("_slug", ""),
        title=d.get("title", ""),
        title_translations=d.get("title_translations", {}),
        overviews=d.get("overviews", {}),
        first_aired=d.get("first_aired", ""), status=d.get("status", ""),
        network=d.get("network", ""), runtime=d.get("runtime", ""),
        country=d.get("country", ""), language=d.get("language", ""),
        rating=str(d.get("rating", "0") or "0"), mpaa=d.get("mpaa", ""),
        imdb_id=d.get("imdb_id", ""), tmdb_id=d.get("tmdb_id", ""), tvrage_id=d.get("tvrage_id", ""),
        genres=d.get("genres", []), tags=d.get("tags", []),
        trailers=d.get("trailers", []), images=d.get("images", {}),
        actors=[Actor(**a) for a in d.get("actors", [])],
        season_count=len(d.get("seasons", [])),
    )


@app.get("/api/series/{series_id}/translations", operation_id="get_series_translations",
         response_model=TranslationsResponse, tags=["A. Series metadata"])
def get_series_translations(series_id: str):
    """取得系列所有語言的標題與簡介。"""
    d = _get_series(series_id)
    return TranslationsResponse(
        title_translations=d.get("title_translations", {}),
        overviews=d.get("overviews", {}),
    )


@app.get("/api/series/{series_id}/cast", operation_id="get_series_cast",
         response_model=List[Actor], tags=["A. Series metadata"])
def get_series_cast(series_id: str):
    """取得演員清單（含 tvdbid 與 thumb URL）。"""
    d = _get_series(series_id)
    return [Actor(**a) for a in d.get("actors", [])]


@app.get("/api/series/{series_id}/genres", operation_id="get_series_genres",
         response_model=List[str], tags=["A. Series metadata"])
def get_series_genres(series_id: str):
    """取得類型清單。"""
    return _get_series(series_id).get("genres", [])


@app.get("/api/series/{series_id}/tags", operation_id="get_series_tags",
         response_model=List[str], tags=["A. Series metadata"])
def get_series_tags(series_id: str):
    """取得標籤 / 關鍵字清單。"""
    return _get_series(series_id).get("tags", [])


@app.get("/api/series/{series_id}/ratings", operation_id="get_series_ratings",
         response_model=RatingsResponse, tags=["A. Series metadata"])
def get_series_ratings(series_id: str):
    """取得評分與 Content Rating。"""
    d = _get_series(series_id)
    return RatingsResponse(
        rating=str(d.get("rating", "0") or "0"),
        mpaa=d.get("mpaa", ""),
        content_ratings=d.get("content_ratings", []),
    )


@app.get("/api/series/{series_id}/external-ids", operation_id="get_series_external_ids",
         response_model=ExternalIds, tags=["A. Series metadata"])
def get_series_external_ids(series_id: str):
    """取得 IMDb / TMDb / TVRage 等外部 ID。"""
    d = _get_series(series_id)
    return ExternalIds(
        tvdb=d.get("series_id", ""), imdb=d.get("imdb_id", ""),
        tmdb=d.get("tmdb_id", ""), tvrage=d.get("tvrage_id", ""),
    )


@app.get("/api/series/{series_id}/trailers", operation_id="get_series_trailers",
         response_model=List[str], tags=["A. Series metadata"])
def get_series_trailers(series_id: str):
    """取得 YouTube 預告片連結清單。"""
    return _get_series(series_id).get("trailers", [])


@app.get("/api/series/{series_id}/artwork", operation_id="get_series_artwork",
         response_model=ArtworkResponse, tags=["A. Series metadata"])
def get_series_artwork(
    series_id: str,
    type: str = Query("poster", description="poster / fanart / clearlogo / banner"),
):
    """取得指定類型的系列圖片 URL。"""
    d = _get_series(series_id)
    imgs = d.get("images", {})
    val = imgs.get(type)
    return ArtworkResponse(type=type, urls=[val] if val else [])


@app.get("/api/series/{series_id}/full", operation_id="get_series_full",
         tags=["A. Series metadata"])
def get_series_full(
    series_id: str,
    include_episodes: bool = Query(True, description="是否連每集 metadata 一起抓（會慢 5-30 秒）"),
):
    """一次拿系列完整包（系列 metadata + 各季 + 每集 metadata），JSON 格式。

    典型 workflow：
      1. POST /api/search {name:"女王之刃"} → 拿多個候選 (id + name + year + overview)
      2. 使用者選一個 id
      3. GET /api/series/{id}/full → 一次回全部（系列基本欄位、cast、genres、
         artwork、各季、各集 title/aired/overview/...），第三方 client 可直接渲染。

    效能：
      - include_episodes=false（快）→ 只回系列 + 季列表，不爬每季 episodes 頁
      - include_episodes=true（慢）→ 對每季各爬一次 episode 列表
      - 結果有記憶體 cache，同 series_id 重複呼叫走 cache
    """
    series = _get_series(series_id)
    seasons_out = []
    for s in series.get("seasons", []) or []:
        sn = s.get("number")
        if include_episodes and sn is not None:
            try:
                seasons_out.append(_get_season(series_id, sn))
                continue
            except HTTPException:
                pass
        seasons_out.append({**s, "episodes": []})
    return {"series": series, "seasons": seasons_out}


# ═══════════════════════════════════════════════════════════════════════
# B. Season / Episode metadata (6)
# ═══════════════════════════════════════════════════════════════════════
@app.get("/api/series/{series_id}/seasons", operation_id="list_seasons",
         response_model=List[SeasonMeta], tags=["B. Seasons & Episodes"])
def list_seasons(series_id: str):
    """列出系列的所有季。"""
    d = _get_series(series_id)
    return [
        SeasonMeta(
            number=s["number"], name=s["name"], tvdb_id=s.get("tvdb_id", ""),
            title_translations=s.get("title_translations", {}),
            overviews=s.get("overviews", {}),
            **{"from": s.get("from", "")},
            to=s.get("to", ""), episode_count=s.get("episode_count", 0),
            url=s.get("url", ""),
        )
        for s in d.get("seasons", [])
    ]


@app.get("/api/series/{series_id}/seasons/{season}", operation_id="get_season",
         response_model=SeasonMeta, tags=["B. Seasons & Episodes"])
def get_season(series_id: str, season: int):
    """取得單季詳細資訊（含 plot / tvdb_id / 多語標題）。"""
    s = _get_season(series_id, season)
    return SeasonMeta(
        number=s["number"], name=s["name"], tvdb_id=s.get("tvdb_id", ""),
        title_translations=s.get("title_translations", {}),
        overviews=s.get("overviews", {}),
        **{"from": s.get("from", "")},
        to=s.get("to", ""), episode_count=s.get("episode_count", 0),
        url=s.get("url", ""),
    )


@app.get("/api/series/{series_id}/seasons/{season}/artwork", operation_id="get_season_artwork",
         response_model=ArtworkResponse, tags=["B. Seasons & Episodes"])
def get_season_artwork(series_id: str, season: int):
    """取得季海報 URL。"""
    s = _get_season(series_id, season)
    art = scrape_season_images(s.get("url", ""), series_id, season)
    return ArtworkResponse(type="poster", urls=[art["poster"]] if art.get("poster") else [])


@app.get("/api/series/{series_id}/seasons/{season}/episodes", operation_id="list_episodes",
         response_model=List[EpisodeMeta], tags=["B. Seasons & Episodes"])
def list_episodes(series_id: str, season: int):
    """列出指定季的所有集。"""
    s = _get_season(series_id, season)
    return [EpisodeMeta(**ep) for ep in s["episodes"]]


@app.get("/api/series/{series_id}/episodes/{episode_id}", operation_id="get_episode",
         response_model=EpisodeMeta, tags=["B. Seasons & Episodes"])
def get_episode(series_id: str, episode_id: str):
    """取得單集詳細資訊（含 director/writer 含 tvdbid、多語 plot）。"""
    e = _get_episode(series_id, episode_id)
    return EpisodeMeta(
        id=e.get("id", ""), number=e.get("number", 0),
        seasonNumber=e.get("seasonNumber", e.get("season_number", 0)),
        name=e.get("name", "") or e.get("title", ""),
        aired=e.get("aired", ""), runtime=e.get("runtime", ""),
        url=e.get("url", ""), overviews=e.get("overviews", {}),
        overview=e.get("overview", ""),
        directors=[Person(**d) if isinstance(d, dict) else Person(name=d) for d in e.get("directors", [])],
        writers=[Person(**w) if isinstance(w, dict) else Person(name=w) for w in e.get("writers", [])],
        imdb_id=e.get("imdb_id", ""), tmdb_id=e.get("tmdb_id", ""),
    )


@app.get("/api/series/{series_id}/episodes/{episode_id}/thumb", operation_id="get_episode_thumb",
         response_model=ArtworkResponse, tags=["B. Seasons & Episodes"])
def get_episode_thumb(series_id: str, episode_id: str):
    """取得集縮圖 URL。"""
    url = get_episode_image_url(series_id, episode_id)
    return ArtworkResponse(type="thumb", urls=[url] if url else [])


# ═══════════════════════════════════════════════════════════════════════
# C. NFO generators (3) — 直接回傳 XML 字串，不寫檔
# ═══════════════════════════════════════════════════════════════════════
@app.post("/api/nfo/tvshow", operation_id="generate_tvshow_nfo_api",
          response_class=PlainTextResponse, tags=["C. NFO generators"])
def gen_tvshow_nfo(req: NfoRequest):
    """產生 tvshow.nfo 的 XML 字串（不寫檔）。"""
    sd = _get_series(req.series_id)
    seasons = sd.get("seasons", [])
    actors = sd.get("actors", [])
    root = generate_tvshow_nfo(sd, seasons, {}, actors, req.lang, req.lang_priority)
    return PlainTextResponse(make_xml_declaration(root), media_type="application/xml")


@app.post("/api/nfo/season", operation_id="generate_season_nfo_api",
          response_class=PlainTextResponse, tags=["C. NFO generators"])
def gen_season_nfo(req: SeasonNfoRequest):
    """產生 season.nfo 的 XML 字串。"""
    s = _get_season(req.series_id, req.season)
    root = generate_season_nfo(s, s.get("episodes", []), req.lang_priority)
    return PlainTextResponse(make_xml_declaration(root), media_type="application/xml")


@app.post("/api/nfo/episode", operation_id="generate_episode_nfo_api",
          response_class=PlainTextResponse, tags=["C. NFO generators"])
def gen_episode_nfo(req: EpisodeNfoRequest):
    """產生單集 NFO 的 XML 字串。"""
    sd = _get_series(req.series_id)
    e = _get_episode(req.series_id, req.episode_id)
    e["overview"] = pick_translation(e.get("overviews", {}), priority=req.lang_priority) or e.get("overview", "")
    root = generate_episode_nfo(e, sd, req.lang_priority)
    return PlainTextResponse(make_xml_declaration(root), media_type="application/xml")


# ═══════════════════════════════════════════════════════════════════════
# D. Task management (5)
# ═══════════════════════════════════════════════════════════════════════
@app.get("/api/tasks", operation_id="list_tasks",
         response_model=List[TaskSummary], tags=["D. Tasks"])
def list_tasks():
    """列出所有任務（pending/running/done/error）。"""
    with TASK_LOCK:
        return [
            TaskSummary(id=t["id"], status=t["status"], title=t.get("title", ""),
                        output=t.get("output", ""), log_count=len(t.get("logs", [])))
            for t in TASKS.values()
        ]


@app.delete("/api/tasks/{task_id}", operation_id="delete_task", tags=["D. Tasks"])
def delete_task(task_id: str, remove_output: bool = Query(False, description="是否一併刪除輸出目錄")):
    """從清單移除任務；可選擇是否一併刪除輸出目錄。"""
    with TASK_LOCK:
        task = TASKS.pop(task_id, None)
    if not task:
        raise HTTPException(404, "Task not found")
    out = task.get("output", "")
    if remove_output and out:
        try:
            p = Path(out)
            if p.exists() and OUTPUT_BASE.resolve() in p.resolve().parents:
                shutil.rmtree(p)
        except Exception as e:
            return {"ok": True, "warning": f"刪除輸出失敗: {e}"}
    return {"ok": True}


@app.get("/api/tasks/{task_id}/files", operation_id="list_task_files",
         response_model=List[FileEntry], tags=["D. Tasks"])
def list_task_files(task_id: str):
    """列出任務輸出目錄底下所有檔案（含 NFO / 圖）。"""
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    out = task.get("output", "")
    if not out:
        return []
    root = Path(out)
    if not root.exists():
        raise HTTPException(404, "Output dir 不存在")
    entries = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        entries.append(FileEntry(
            path=rel, name=p.name,
            size=p.stat().st_size if p.is_file() else 0,
            is_dir=p.is_dir(),
        ))
    return entries


@app.get("/api/tasks/{task_id}/file", operation_id="download_task_file", tags=["D. Tasks"])
def download_task_file(task_id: str, path: str = Query(..., description="任務輸出目錄下的相對路徑")):
    """下載 / 預覽任務輸出目錄下的個別檔案。"""
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    out = task.get("output", "")
    if not out:
        raise HTTPException(404, "Task 尚未完成")
    root = Path(out).resolve()
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, "Path 越界")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "檔案不存在")
    mime, _ = mimetypes.guess_type(target.name)
    return FileResponse(str(target), media_type=mime or "application/octet-stream",
                        filename=target.name)


@app.post("/api/tasks/{task_id}/regenerate", operation_id="regenerate_task_nfo",
          tags=["D. Tasks"])
def regenerate_task_nfo(task_id: str):
    """用任務快取的資料重新產生 NFO（不再連網）。"""
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    sd = task.get("_series_data")
    eps_map = task.get("_episodes_by_season")
    out = task.get("output", "")
    if not (sd and eps_map and out):
        raise HTTPException(400, "Task 沒有可重產的快取資料（請等爬蟲完成）")
    lang = task.get("_lang", "zho")
    lang_priority = task.get("_lang_priority", list(DEFAULT_PRIORITY))
    seasons = sd.get("seasons", [])
    actors = sd.get("actors", [])
    out_dir = Path(out)

    tvshow = generate_tvshow_nfo(sd, seasons, eps_map, actors, lang, lang_priority)
    (out_dir / "tvshow.nfo").write_text(make_xml_declaration(tvshow), encoding="utf-8")

    written = ["tvshow.nfo"]
    for s_info in seasons:
        sn = s_info["number"]
        eps = eps_map.get(sn, [])
        if not eps:
            continue
        season_dir = out_dir / ("Specials" if sn == 0 else f"Season {sn:02d}")
        season_dir.mkdir(parents=True, exist_ok=True)
        (season_dir / "season.nfo").write_text(
            make_xml_declaration(generate_season_nfo(s_info, eps, lang_priority)), encoding="utf-8")
        written.append(f"{season_dir.name}/season.nfo")
        for ep in sorted(eps, key=lambda e: int(e.get("number", 0))):
            fn = f"S{ep.get('seasonNumber', sn):02d}E{ep.get('number', 0):02d}.nfo"
            (season_dir / fn).write_text(
                make_xml_declaration(generate_episode_nfo(ep, sd, lang_priority)), encoding="utf-8")
            written.append(f"{season_dir.name}/{fn}")
    return {"ok": True, "files_written": written}


# ═══════════════════════════════════════════════════════════════════════
# E. Artwork download (4)
# ═══════════════════════════════════════════════════════════════════════
def _safe_task_path(task_id: str, rel_path: str) -> Path:
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    out = task.get("output", "")
    if not out:
        # 還沒完成 → 用 output/{task_id} 為根
        out = str((OUTPUT_BASE / task_id).resolve())
        Path(out).mkdir(parents=True, exist_ok=True)
    root = Path(out).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, "rel_path 越界")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@app.post("/api/artwork/download", operation_id="download_artwork", tags=["E. Artwork"])
def download_artwork(req: ArtworkDownloadRequest):
    """下載任意 URL 的圖到指定任務目錄下的相對路徑。"""
    target = _safe_task_path(req.task_id, req.rel_path)
    ok = download_image(req.url, target)
    if not ok:
        raise HTTPException(502, "下載失敗")
    return {"ok": True, "saved_to": str(target)}


@app.post("/api/artwork/series/{series_id}", operation_id="download_series_artwork", tags=["E. Artwork"])
def download_series_artwork(
    series_id: str,
    task_id: str = Query(..., description="輸出目標的 task_id"),
    types: List[str] = Query(["poster", "fanart", "clearlogo", "banner"]),
):
    """下載系列圖片（poster/fanart/clearlogo/banner）到任務目錄。"""
    d = _get_series(series_id)
    imgs = d.get("images", {})
    saved = []
    for t in types:
        url = imgs.get(t)
        if not url:
            continue
        ext = ".png" if t == "clearlogo" and url.lower().endswith(".png") else ".jpg"
        rel = f"{t}{ext}" if t != "poster" else "poster.jpg"
        # 統一檔名
        rel = {"poster": "poster.jpg", "fanart": "fanart.jpg",
               "banner": "banner.jpg", "clearlogo": f"clearlogo{ext}"}.get(t, f"{t}.jpg")
        target = _safe_task_path(task_id, rel)
        if download_image(url, target):
            saved.append(rel)
    return {"ok": True, "saved": saved}


@app.post("/api/artwork/season/{series_id}/{season}",
          operation_id="download_season_artwork", tags=["E. Artwork"])
def download_season_artwork(series_id: str, season: int, task_id: str = Query(...)):
    """下載指定季的海報。"""
    s = _get_season(series_id, season)
    art = scrape_season_images(s.get("url", ""), series_id, season)
    url = art.get("poster")
    if not url:
        return {"ok": False, "message": "找不到季海報"}
    rel = f"season{season:02d}-poster.jpg" if season > 0 else "season-specials-poster.jpg"
    target = _safe_task_path(task_id, rel)
    ok = download_image(url, target)
    return {"ok": ok, "saved_to": str(target) if ok else ""}


@app.post("/api/artwork/episode/{series_id}/{episode_id}",
          operation_id="download_episode_thumb", tags=["E. Artwork"])
def download_episode_thumb_api(
    series_id: str, episode_id: str,
    task_id: str = Query(...),
    season: Optional[int] = Query(None),
    number: Optional[int] = Query(None),
):
    """下載指定集的縮圖。"""
    url = get_episode_image_url(series_id, episode_id)
    if season is None or number is None:
        e = _get_episode(series_id, episode_id)
        season = season if season is not None else e.get("seasonNumber", 0)
        number = number if number is not None else e.get("number", 0)
    season_dir = "Specials" if season == 0 else f"Season {season:02d}"
    rel = f"{season_dir}/S{season:02d}E{number:02d}-thumb.jpg"
    target = _safe_task_path(task_id, rel)
    ok = download_image(url, target)
    return {"ok": ok, "saved_to": str(target) if ok else ""}


# ═══════════════════════════════════════════════════════════════════════
# 前端
# ═══════════════════════════════════════════════════════════════════════
@app.get("/", include_in_schema=False)
def index():
    return FileResponse("index.html")


# ═══════════════════════════════════════════════════════════════════════
# MCP server — 自動把所有有 operation_id 的端點變成 MCP tools
# ═══════════════════════════════════════════════════════════════════════
try:
    mcp = FastApiMCP(
        app,
        name="TheTVDB Crawler",
        description="完整 TheTVDB 抓取/查詢/NFO 生成/任務管理 API（Emby 相容）。",
    )
    mcp.mount_http()  # 用 Streamable HTTP transport（取代 deprecated 的 SSE）
except TypeError as e:
    # fastapi-mcp / mcp SDK 版本 mismatch 時降級為純 REST（MCP 端點失效但 UI 可用）
    import logging
    logging.warning(f"MCP mount 失敗，降級為純 REST API: {e}")


if __name__ == "__main__":
    import uvicorn
    OUTPUT_BASE.mkdir(exist_ok=True)
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    print(f"TheTVDB NFO Crawler 啟動: http://{host}:{port}  (MCP: /mcp)")
    uvicorn.run(app, host=host, port=port)
