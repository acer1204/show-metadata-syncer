"""背景爬蟲任務：抓整個系列 → 下載圖片 → 產生 Emby NFO 檔到 output/。"""
import threading
import time
from pathlib import Path
from typing import Any, Dict

from .config import settings, lang_priority_list
from .clients.tvdb import (
    scrape_series_page, scrape_season_page, scrape_episode_page,
    pick_translation, generate_tvshow_nfo, generate_season_nfo,
    generate_episode_nfo, make_xml_declaration, download_image,
    scrape_season_images, get_episode_image_url,
)
from .sources.tvdb import get_slug

TASKS: Dict[str, Dict[str, Any]] = {}
TASK_LOCK = threading.Lock()


def output_base() -> Path:
    return Path(settings.output_dir)


def run_crawl(task_id, series_id, lang, lang_priority=None):
    if lang_priority is None:
        lang_priority = lang_priority_list()
    try:
        with TASK_LOCK:
            TASKS[task_id]["status"] = "running"

        def log(msg):
            with TASK_LOCK:
                TASKS[task_id]["logs"].append(msg)

        slug = get_slug(series_id)
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
                time.sleep(settings.episode_delay)

        safe_title = "".join(c for c in title if c not in r'\/:*?"<>|')
        output_dir = output_base() / task_id / safe_title
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
                # 季海報放在系列根目錄（跟 Emby 慣例一致）
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
