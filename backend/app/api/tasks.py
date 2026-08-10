"""爬蟲任務管理 + 輸出檔案存取 + 圖片下載。

POST /api/crawl 啟動背景任務（抓整個系列 → 下載圖 → 產 NFO 到 output/），
其餘端點查狀態、列檔案、下載檔案、重產 NFO。
"""
import mimetypes
import os
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from ..config import settings, lang_priority_list
from ..clients.tvdb import (
    download_image, scrape_season_images, get_episode_image_url,
    generate_tvshow_nfo, generate_season_nfo, generate_episode_nfo,
    make_xml_declaration,
)
from ..crawl import TASKS, TASK_LOCK, run_crawl, output_base
from ..sources.tvdb import get_series, get_season, find_episode

router = APIRouter(prefix="/api", tags=["tasks"])


class CrawlRequest(BaseModel):
    id: str = Field(..., description="TheTVDB 系列數字 ID")
    source: str = Field("tvdb", description="來源（目前僅支援 tvdb）")
    lang: Optional[str] = None
    lang_priority: Optional[List[str]] = None


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


class FileEntry(BaseModel):
    path: str
    name: str
    size: int
    is_dir: bool


class ArtworkDownloadRequest(BaseModel):
    url: str
    task_id: str
    rel_path: str = Field(..., description="輸出目錄下的相對路徑")


@router.post("/crawl", operation_id="start_tvdb_crawl", response_model=CrawlResponse)
def api_crawl(req: CrawlRequest):
    """啟動非同步爬蟲：抓系列+季+集 metadata、下載所有圖、產 NFO。回傳 task_id。"""
    if req.source != "tvdb":
        raise HTTPException(400, "目前只有 tvdb 支援 NFO 爬蟲任務")
    sid = req.id.strip()
    if not sid:
        raise HTTPException(400, "請提供 Series ID")
    task_id = f"{int(time.time())}-{sid}"
    with TASK_LOCK:
        TASKS[task_id] = {"id": task_id, "status": "pending", "logs": [], "output": "", "title": ""}
    lang = req.lang or settings.search_lang
    priority = req.lang_priority or lang_priority_list()
    threading.Thread(target=run_crawl, args=(task_id, sid, lang, priority), daemon=True).start()
    return CrawlResponse(task_id=task_id)


@router.get("/status/{task_id}", operation_id="get_crawl_status", response_model=TaskStatus)
def api_status(task_id: str):
    """查詢爬取任務狀態與累積 log。"""
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskStatus(id=task["id"], status=task["status"], logs=task["logs"],
                      output=task.get("output", ""), title=task.get("title", ""))


@router.get("/tasks", operation_id="list_tasks", response_model=List[TaskSummary])
def list_tasks():
    """列出所有任務（pending/running/done/error）。"""
    with TASK_LOCK:
        return [
            TaskSummary(id=t["id"], status=t["status"], title=t.get("title", ""),
                        output=t.get("output", ""), log_count=len(t.get("logs", [])))
            for t in TASKS.values()
        ]


@router.delete("/tasks/{task_id}", operation_id="delete_task")
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
            if p.exists() and output_base().resolve() in p.resolve().parents:
                shutil.rmtree(p)
        except Exception as e:
            return {"ok": True, "warning": f"刪除輸出失敗: {e}"}
    return {"ok": True}


@router.get("/tasks/{task_id}/files", operation_id="list_task_files", response_model=List[FileEntry])
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


@router.get("/tasks/{task_id}/file", operation_id="download_task_file")
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


@router.get("/tasks/{task_id}/zip", operation_id="download_task_zip")
def download_task_zip(task_id: str):
    """把任務的整個輸出資料夾（NFO + 圖片）打包成 ZIP 下載。

    ZIP 內是一層 {系列名}/ 資料夾，解壓即為 Emby 影集資料夾。
    """
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    out = task.get("output", "")
    if not out:
        raise HTTPException(400, "Task 尚未完成")
    root = Path(out)
    if not root.exists():
        raise HTTPException(404, "Output dir 不存在")

    # 先壓到暫存檔再串流（輸出目錄可能上百 MB，不放記憶體），回應後自動刪除
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                z.write(p, arcname=f"{root.name}/{p.relative_to(root).as_posix()}")
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=f"{root.name}.zip",
        background=BackgroundTask(os.unlink, tmp.name),
    )


@router.post("/tasks/{task_id}/regenerate", operation_id="regenerate_task_nfo")
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
    lang = task.get("_lang", settings.search_lang)
    lang_priority = task.get("_lang_priority", lang_priority_list())
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


# ─── Artwork download ──────────────────────────────────────────────────
def _safe_task_path(task_id: str, rel_path: str) -> Path:
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    out = task.get("output", "")
    if not out:
        # 還沒完成 → 用 output/{task_id} 為根
        out = str((output_base() / task_id).resolve())
        Path(out).mkdir(parents=True, exist_ok=True)
    root = Path(out).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, "rel_path 越界")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@router.post("/artwork/download", operation_id="download_artwork", tags=["artwork"])
def download_artwork(req: ArtworkDownloadRequest):
    """下載任意 URL 的圖到指定任務目錄下的相對路徑。"""
    target = _safe_task_path(req.task_id, req.rel_path)
    ok = download_image(req.url, target)
    if not ok:
        raise HTTPException(502, "下載失敗")
    return {"ok": True, "saved_to": str(target)}


@router.post("/artwork/series/{series_id}", operation_id="download_series_artwork", tags=["artwork"])
def download_series_artwork(
    series_id: str,
    task_id: str = Query(..., description="輸出目標的 task_id"),
    types: List[str] = Query(["poster", "fanart", "clearlogo", "banner"]),
):
    """下載系列圖片（poster/fanart/clearlogo/banner）到任務目錄。"""
    d = get_series(series_id)
    imgs = d.get("images", {})
    saved = []
    for t in types:
        url = imgs.get(t)
        if not url:
            continue
        ext = ".png" if t == "clearlogo" and url.lower().endswith(".png") else ".jpg"
        rel = {"poster": "poster.jpg", "fanart": "fanart.jpg",
               "banner": "banner.jpg", "clearlogo": f"clearlogo{ext}"}.get(t, f"{t}.jpg")
        target = _safe_task_path(task_id, rel)
        if download_image(url, target):
            saved.append(rel)
    return {"ok": True, "saved": saved}


@router.post("/artwork/season/{series_id}/{season}", operation_id="download_season_artwork", tags=["artwork"])
def download_season_artwork(series_id: str, season: int, task_id: str = Query(...)):
    """下載指定季的海報。"""
    s = get_season(series_id, season)
    art = scrape_season_images(s.get("url", ""), series_id, season)
    url = art.get("poster")
    if not url:
        return {"ok": False, "message": "找不到季海報"}
    rel = f"season{season:02d}-poster.jpg" if season > 0 else "season-specials-poster.jpg"
    target = _safe_task_path(task_id, rel)
    ok = download_image(url, target)
    return {"ok": ok, "saved_to": str(target) if ok else ""}


@router.post("/artwork/episode/{series_id}/{episode_id}", operation_id="download_episode_thumb", tags=["artwork"])
def download_episode_thumb_api(
    series_id: str, episode_id: str,
    task_id: str = Query(...),
    season: Optional[int] = Query(None),
    number: Optional[int] = Query(None),
):
    """下載指定集的縮圖。"""
    url = get_episode_image_url(series_id, episode_id)
    if season is None or number is None:
        e = find_episode(series_id, episode_id)
        season = season if season is not None else e.get("seasonNumber", e.get("season_number", 0))
        number = number if number is not None else e.get("number", 0)
    season_dir = "Specials" if season == 0 else f"Season {season:02d}"
    rel = f"{season_dir}/S{season:02d}E{number:02d}-thumb.jpg"
    target = _safe_task_path(task_id, rel)
    ok = download_image(url, target)
    return {"ok": ok, "saved_to": str(target) if ok else ""}
