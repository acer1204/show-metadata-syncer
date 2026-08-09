#!/usr/bin/env python3
"""
TheTVDB NFO Crawler (純爬蟲版，不需 API Key)
從 TheTVDB 網站直接爬取動漫資訊，產生 Emby/Jellyfin 相容的 NFO 檔案

使用方式:
    py tvdb_crawler.py "一騎当千"              # 搜尋並輸出 NFO
    py tvdb_crawler.py -u "https://..."        # 直接給 TVDB 網址
    py tvdb_crawler.py -i 80158                # 直接用 TVDB ID
    py tvdb_crawler.py "名稱" -o ./output      # 指定輸出目錄
"""

import sys
import io
import re
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from lxml import etree

BASE_URL = "https://www.thetvdb.com"
LEGACY_API = "https://thetvdb.com/api"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8,ja;q=0.7",
})


def http_get(url, **kw):
    resp = SESSION.get(url, timeout=30, **kw)
    resp.raise_for_status()
    return resp


ARTWORK_BASE = "https://artworks.thetvdb.com"

LANG_GROUPS = {
    "zhtw": ["zhtw", "zh-Hant"],
    "zho":  ["zho", "zhs", "zhcn", "zh-Hans"],
    "jpn":  ["jpn"],
    "eng":  ["eng"],
}
DEFAULT_PRIORITY = ["zhtw", "zho", "jpn", "eng"]


def download_image(url, filepath, timeout=30):
    if not url:
        return False
    try:
        resp = SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        filepath.write_bytes(resp.content)
        return True
    except Exception:
        return False


def scrape_series_images(soup, series_id):
    result = {"poster": "", "fanart": "", "clearlogo": "", "banner": ""}
    poster_img = soup.select_one("img.img-responsive")
    if poster_img:
        src = poster_img.get("src", "")
        if "artworks" in src:
            result["poster"] = src
    if not result["poster"]:
        result["poster"] = f"{ARTWORK_BASE}/banners/posters/{series_id}-2.jpg"
    for num in range(1, 5):
        url = f"{ARTWORK_BASE}/banners/fanart/original/{series_id}-{num}.jpg"
        try:
            r = SESSION.head(url, timeout=5)
            if r.status_code == 200:
                result["fanart"] = url
                break
        except Exception:
            pass
    for a in soup.select('a[href*="artworks.thetvdb.com"]'):
        href = a.get("href", "")
        if "clearlogo" in href and not result["clearlogo"]:
            result["clearlogo"] = href
        elif "graphical" in href and not result["banner"]:
            result["banner"] = href
        elif "fanart" in href and not result["fanart"]:
            result["fanart"] = href
        elif "poster" in href and not result["poster"]:
            result["poster"] = href
    if not result["banner"]:
        result["banner"] = f"{ARTWORK_BASE}/banners/graphical/{series_id}-g.jpg"
    return result


def scrape_season_images(season_url, series_id, season_num):
    result = {"poster": f"{ARTWORK_BASE}/banners/seasons/{series_id}-{season_num}.jpg"}
    try:
        resp = http_get(season_url)
        soup = BeautifulSoup(resp.text, "html5lib")
        for a in soup.select('a[href*="artworks.thetvdb.com"]'):
            href = a.get("href", "")
            if ("season" in href.lower() or "seasons" in href) and ("poster" in href.lower() or href.endswith(".jpg")):
                result["poster"] = href
                break
    except Exception:
        pass
    return result


def get_episode_image_url(series_id, episode_id):
    return f"{ARTWORK_BASE}/banners/episodes/{series_id}/{episode_id}.jpg"


SEARCH_API = "https://api4.thetvdb.com/web/search/queries"


def search_series(name, lang="zh"):
    """透過 TheTVDB 主站的 Algolia proxy 搜尋系列/電影。

    2026 起原本的 legacy XML API (`/api/GetSeries.php`) 已 EOL；主站 SPA 改走
    api4.thetvdb.com/web/search/queries（Algolia InstantSearch batch queries 格式）。
    不需 API key — proxy 端已放憑證。
    """
    body = {
        "requests": [{
            "indexName": "TVDB",
            "params": {
                "query": name,
                "maxValuesPerFacet": 10,
                "page": 0,
                "analytics": False,
                "highlightPreTag": "__ais-highlight__",
                "highlightPostTag": "__/ais-highlight__",
                "filters": "NOT is_official=0",
                "facets": ["type", "year", "network", "status"],
            }
        }]
    }
    try:
        resp = SESSION.post(
            SEARCH_API,
            json=body,
            headers={
                "Origin": "https://thetvdb.com",
                "Referer": "https://thetvdb.com/search",
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    hits = (data.get("results") or [{}])[0].get("hits", [])
    results = []
    for h in hits:
        oid = h.get("objectID", "")
        # 只回 series（movie 走另一 endpoint，person 不相關）
        if not oid.startswith("series-"):
            continue
        sid = oid.split("series-", 1)[1]
        # 抓 translation 若有 lang preference
        title = h.get("name") or ""
        translations = h.get("translations") or {}
        if isinstance(translations, dict) and lang and lang != "en":
            title = translations.get(lang) or title
        image = h.get("image_url") or h.get("poster") or h.get("thumbnail") or ""
        results.append({
            "id": sid,
            "name": title,
            "name_en": h.get("name") or "",
            "translations": translations if isinstance(translations, dict) else {},
            "overview": h.get("overview", "") or "",
            "firstAired": str(h.get("year", "") or ""),
            "aliases": h.get("aliases", []) if isinstance(h.get("aliases"), list) else [],
            "slug": h.get("slug", "") or "",
            "image": image,
        })
    return results


def resolve_slug(series_id):
    """從 series ID 反查 slug。

    第一步：legacy redirect（對 v3 ID 有效）。
    第二步：失敗時用 search API 反查 — 用 NFO 邏輯：
            找到第一個 search 結果裡 id == series_id 的，再用該結果的 SeriesName 去搜尋頁面取 slug。
    """
    # 第一步：legacy redirect
    try:
        resp = SESSION.get(f"{BASE_URL}/", params={"tab": "series", "id": series_id},
                           allow_redirects=False, timeout=15)
        loc = resp.headers.get("Location", "")
        if "/series/" in loc:
            return loc.split("/series/")[-1].split("?")[0].split("#")[0]
    except Exception:
        pass
    # 第二步：對 v4 內部 ID（8+ 位數）沒救——這些 ID 在公開頁面沒有反查端點
    # 留 None，讓 server 端的 _get_slug 直接 raise 404 給使用者
    return None


def find_slug_by_name(name):
    """由動漫名搜尋並回傳第一筆 slug（用主站搜尋頁的 HTML）。
    legacy GetSeries.php 沒提供 slug，只能掃主站搜尋結果頁。
    """
    if not name:
        return None
    try:
        resp = SESSION.get(f"{BASE_URL}/search", params={"query": name}, timeout=15)
        soup = BeautifulSoup(resp.text, "html5lib")
        # 找第一個 /series/<slug> 連結（排除 /series/create）
        for a in soup.select("a[href*='/series/']"):
            href = a.get("href", "")
            m = re.search(r"/series/([a-z0-9-]+)", href)
            if m:
                slug = m.group(1)
                if slug not in ("create", "list", ""):
                    return slug
    except Exception:
        pass
    return None


def scrape_series_page(slug):
    url = f"{BASE_URL}/series/{slug}"
    resp = http_get(url)
    soup = BeautifulSoup(resp.text, "html5lib")
    data = {"slug": slug, "url": url}

    data["title"] = ""
    h1 = soup.select_one("h1.translated_title, h1#series_title")
    if h1:
        data["title"] = h1.text.strip()

    data.update({
        "series_id": "", "status": "", "first_aired": "", "network": "",
        "runtime": "", "genres": [], "country": "", "language": "",
        "imdb_id": "", "tmdb_id": "", "tvrage_id": "",
        "mpaa": "", "content_rating": "", "rating": "0", "votes": "",
    })

    info_block = soup.find(id="series_basic_info")
    if info_block:
        for li in info_block.select("li.list-group-item"):
            strong = li.find("strong")
            if not strong:
                continue
            label = strong.text.strip().rstrip(":")
            span = li.find("span")
            value = span.get_text(" ", strip=True) if span else ""
            if "Series ID" in label:
                data["series_id"] = re.sub(r"\D", "", value)
            elif "Status" in label:
                data["status"] = value
            elif "First Aired" in label:
                data["first_aired"] = value
            elif "Network" in label:
                net_span = li.select_one("span a")
                data["network"] = net_span.text.strip() if net_span else value
            elif "Average Runtime" in label:
                nums = re.findall(r"\d+", value)
                data["runtime"] = nums[0] if nums else ""
            elif "Genres" in label:
                data["genres"] = [a.text.strip() for a in li.select("a") if a.text.strip()]
            elif "Original Country" in label:
                data["country"] = value
            elif "Original Language" in label:
                data["language"] = value
            elif "Content Rating" in label or "Rating" in label and "Content" in label:
                if not data["mpaa"]:
                    data["mpaa"] = value
                    data["content_rating"] = value
            elif "Favorites" in label or "Followers" in label:
                pass  # ignore

    # External IDs from links
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "imdb.com/title/" in href and not data["imdb_id"]:
            m = re.search(r"(tt\d+)", href)
            if m:
                data["imdb_id"] = m.group(1)
        if "themoviedb.org" in href and not data["tmdb_id"]:
            m = re.search(r"/(?:tv|movie)/(\d+)", href)
            if m:
                data["tmdb_id"] = m.group(1)
        if "tvrage.com" in href and not data["tvrage_id"]:
            m = re.search(r"/shows/id-(\d+)", href) or re.search(r"/(\d+)", href)
            if m:
                data["tvrage_id"] = m.group(1)

    # Real rating (尋找頁面中的評分數字)
    for el in soup.select(".change_translation_text + *, .rating, [class*='rating']"):
        txt = el.get_text(" ", strip=True)
        m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:/|out of)\s*10", txt)
        if m:
            data["rating"] = m.group(1)
            break
    if data["rating"] == "0":
        # 嘗試找 "Rating 6.0" 或單獨數字
        rating_el = soup.find(string=re.compile(r"^\s*Rating\s*$", re.I))
        if rating_el and rating_el.parent:
            sib = rating_el.parent.find_next_sibling()
            if sib:
                m = re.search(r"(\d+(?:\.\d+)?)", sib.get_text(" ", strip=True))
                if m:
                    data["rating"] = m.group(1)

    # Tags / Keywords
    # TheTVDB 已不再有單獨的 Tags 區塊，改為 taxonomy 連結散落在 series_basic_info 各分類：
    #   /genres/<x>         (類型，但這個我們另外抓進 data["genres"])
    #   /taxonomy/series/<cat>/<term>  (Sub-Genre, Supernatural Beings, Geographic Location, ... )
    data["tags"] = []
    # 標籤類別（排除已抓的 Genres，避免重複）
    TAXONOMY_CATEGORIES = {
        "Sub-Genre", "Supernatural Beings", "Plot Characteristics",
        "Relationship Types", "Geographic Location", "Setting",
        "Time Period", "TV Type or Format", "Inspirations",
        "Keywords", "Tags",
    }
    if info_block:
        for li in info_block.select("li.list-group-item"):
            strong = li.find("strong")
            if not strong:
                continue
            label = strong.text.strip().rstrip(":")
            if label in TAXONOMY_CATEGORIES:
                for a in li.select("a"):
                    txt = a.get_text(strip=True)
                    if txt and txt not in data["tags"] and len(txt) < 60:
                        data["tags"].append(txt)
    # 後備：直接掃所有 /taxonomy/series/ 連結
    if not data["tags"]:
        for a in soup.select("a[href^='/taxonomy/series/']"):
            txt = a.get_text(strip=True)
            if txt and txt not in data["tags"] and len(txt) < 60:
                data["tags"].append(txt)

    # Overviews / title translations
    data["overviews"] = {}
    data["title_translations"] = {}
    for div in soup.select(".change_translation_text"):
        lang = div.get("data-language", "")
        tr_title = div.get("data-title", "")
        p = div.find("p")
        if p and p.text.strip():
            data["overviews"][lang] = p.text.strip()
        if tr_title:
            data["title_translations"][lang] = tr_title

    # Content rating
    data["content_ratings"] = []

    # Actors (含 tvdbid 與 thumb)
    data["actors"] = []
    cast_tab = soup.find(id="castcrew")
    if cast_tab:
        for item in cast_tab.select('[class*="col-xs-6"][class*="col-sm-3"]'):
            # Name 在 <h3>，但 <small> 是 role；要排除 small
            h3 = item.find("h3")
            name = ""
            role = ""
            if h3:
                small = h3.find("small")
                if small:
                    role_text = small.get_text(" ", strip=True)
                    # 移除 "* needs role-specific image" 之類
                    role_text = re.sub(r"\*?\s*needs role-specific image.*$", "", role_text).strip()
                    m = re.match(r"^\s*as\s+(.+?)$", role_text, re.I)
                    role = m.group(1).strip() if m else role_text
                    small.extract()  # 暫時拿掉算 name
                name = h3.get_text(" ", strip=True)
                name = re.sub(r"\s+", " ", name).strip()
            if not name:
                # fallback
                links = item.select("a")
                if links:
                    name = links[0].get_text(" ", strip=True)
            if not name or name in ("Add Person", "Add Character"):
                continue
            # thumb 與 person tvdbid 從 image URL: 支援兩種版本
            #   舊: /banners/person/{tvdbid}/xxx.jpg
            #   新: /banners/v4/actor/{tvdbid}/photo/xxx.jpg
            tvdbid = ""
            thumb = ""
            img = item.find("img")
            if img:
                thumb = img.get("src", "") or img.get("data-src", "")
                if thumb and not thumb.startswith("http"):
                    thumb = urljoin(BASE_URL, thumb)
                # 三種 URL 形式：
                #   /banners/person/{id}/...      (v3 中期)
                #   /banners/v4/actor/{id}/photo/ (v4 新版)
                #   /banners/actors/{id}.jpg      (v2 老格式，無末尾斜線)
                m = re.search(r"/banners/(?:person|v4/actor)/(\d+)/", thumb) \
                    or re.search(r"/banners/actors/(\d+)\.", thumb)
                if m:
                    tvdbid = m.group(1)
            data["actors"].append({
                "name": name, "role": role, "type": "Actor",
                "tvdbid": tvdbid, "tmdbid": "", "thumb": thumb,
            })

    # Seasons
    data["seasons"] = []
    seasons_tab = soup.find(id="seasons-official")
    if seasons_tab:
        for row in seasons_tab.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 4:
                continue
            link_el = cells[0].find("a")
            season_name = link_el.text.strip() if link_el else cells[0].text.strip()
            ep_count = cells[3].text.strip()
            if season_name in ("All Seasons", "Unassigned Episodes"):
                continue
            if not ep_count.isdigit() or ep_count == "0":
                continue
            season_url = link_el.get("href") if link_el else ""
            season_num = 0
            m = re.search(r"/official/(\d+)", season_url)
            if m:
                season_num = int(m.group(1))
            if "Specials" in season_name or "specials" in season_name.lower() or "special" in season_name.lower():
                season_num = 0

            from_date = cells[1].text.strip() if len(cells) > 1 else ""
            to_date = cells[2].text.strip() if len(cells) > 2 else ""

            data["seasons"].append({
                "number": season_num,
                "name": season_name,
                "from": from_date,
                "to": to_date,
                "episode_count": int(ep_count),
                "url": urljoin(BASE_URL, season_url) if season_url else "",
                "tvdb_id": "",
                "overviews": {},
                "title_translations": {},
            })

    # Trailers
    data["trailers"] = []
    for a in soup.select("a[href*='youtube.com'], a[href*='youtu.be']"):
        href = a.get("href", "")
        if href and href not in data["trailers"]:
            data["trailers"].append(href)

    # tags 已在上面抓過，這裡不要清空
    data["images"] = scrape_series_images(soup, data.get("series_id", ""))
    return data


def scrape_season_page(season_url):
    """Returns dict: {episodes, tvdb_id, overviews, title_translations}

    為向後相容，當作 list 用時仍然 iterable 經由 .episodes 屬性。
    """
    resp = http_get(season_url)
    soup = BeautifulSoup(resp.text, "html5lib")

    # Season tvdb id (from data attributes or meta)
    season_tvdb_id = ""
    for el in soup.select("[data-season-id], [data-id]"):
        sid = el.get("data-season-id") or el.get("data-id") or ""
        if sid.isdigit() and len(sid) >= 4:
            season_tvdb_id = sid
            break
    if not season_tvdb_id:
        # 從 canonical 連結找 /seasons/{id}
        for link in soup.select("link[rel='canonical'], meta[property='og:url']"):
            href = link.get("href", "") or link.get("content", "")
            m = re.search(r"/seasons/(?:official/)?(\d+)", href)
            if m and len(m.group(1)) >= 4:
                season_tvdb_id = m.group(1)
                break

    # Season translations (plot / title per language)
    overviews = {}
    title_translations = {}
    for div in soup.select(".change_translation_text"):
        lang = div.get("data-language", "")
        tr_title = div.get("data-title", "")
        p = div.find("p")
        if p and p.text.strip():
            overviews[lang] = p.text.strip()
        if tr_title:
            title_translations[lang] = tr_title

    episodes = []
    for table in soup.select("table.table"):
        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 4:
                continue
            link_el = cells[1].find("a") if len(cells) > 1 else None
            ep_title = link_el.text.strip() if link_el else cells[1].text.strip()
            aired = cells[2].text.strip() if len(cells) > 2 else ""
            runtime_str = cells[3].text.strip() if len(cells) > 3 else ""
            ep_link = link_el.get("href") if link_el else ""
            ep_id = ""
            m = re.search(r"/episodes/(\d+)", ep_link)
            if m:
                ep_id = m.group(1)
            ep_code = cells[0].text.strip()
            ep_num = 0
            ep_season = 0
            m2 = re.match(r"S(\d+)E(\d+)", ep_code)
            if m2:
                ep_season = int(m2.group(1))
                ep_num = int(m2.group(2))
            runtime_nums = re.findall(r"\d+", runtime_str)
            ep_runtime = runtime_nums[0] if runtime_nums else ""
            episodes.append({
                "id": ep_id,
                "number": ep_num,
                "seasonNumber": ep_season,
                "name": ep_title,
                "aired": aired,
                "runtime": ep_runtime,
                "url": urljoin(BASE_URL, ep_link) if ep_link else "",
            })
    return {
        "episodes": episodes,
        "tvdb_id": season_tvdb_id,
        "overviews": overviews,
        "title_translations": title_translations,
    }


def scrape_episode_page(ep_url):
    resp = http_get(ep_url)
    soup = BeautifulSoup(resp.text, "html5lib")
    data = {"url": ep_url}
    h1 = soup.select_one("h1")
    data["title"] = h1.text.strip() if h1 else ""
    data["overviews"] = {}
    data["directors"] = []
    data["writers"] = []
    data["imdb_id"] = ""
    data["tmdb_id"] = ""

    for li in soup.select("li.list-group-item"):
        strong = li.find("strong")
        if not strong:
            continue
        label = strong.text.strip().rstrip(":")
        span = li.find("span")
        value = span.get_text(" ", strip=True) if span else ""
        if "Originally Aired" in label:
            data["aired"] = value
        elif "Runtime" in label:
            nums = re.findall(r"\d+", value)
            data["runtime"] = nums[0] if nums else ""
        elif "Network" in label:
            data["network"] = value

    for div in soup.select(".change_translation_text"):
        lang = div.get("data-language", "")
        p = div.find("p")
        if p and p.text.strip():
            data["overviews"][lang] = p.text.strip()

    # Directors / Writers from castcrew table (含 tvdbid)
    cast_tab = soup.find(id="castcrew")
    if cast_tab:
        for row in cast_tab.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 2:
                continue
            name_link = cells[0].find("a")
            if not name_link:
                continue
            name = name_link.text.strip()
            if not name or name in ("Add Person", "Add Character"):
                continue
            href = name_link.get("href", "")
            tvdbid = ""
            m = re.search(r"/people/(\d+)", href)
            if m:
                tvdbid = m.group(1)
            person_type = cells[1].text.strip().lower()
            person = {"name": name, "tvdbid": tvdbid}
            if "director" in person_type:
                data["directors"].append(person)
            elif "writer" in person_type or "screenplay" in person_type:
                data["writers"].append(person)

    # External IDs
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "imdb.com/title/" in href and not data["imdb_id"]:
            m = re.search(r"(tt\d+)", href)
            if m:
                data["imdb_id"] = m.group(1)
        if "themoviedb.org" in href and not data["tmdb_id"]:
            m = re.search(r"/(?:tv|movie)/(\d+)", href)
            if m:
                data["tmdb_id"] = m.group(1)

    return data


def pick_translation(mapping, fallback="", priority=None):
    """依照優先序挑翻譯，若 priority 內全沒命中，**自動 fallback 到 DEFAULT_PRIORITY 剩下的順位**。

    這對 episode title 特別重要：episode 常常只有某些語言翻譯，
    缺繁中時不應該直接掉到日文，要先試簡中。
    """
    if priority is None:
        priority = DEFAULT_PRIORITY
    # 第一輪：使用者指定順序
    for lang_key in priority:
        for k in LANG_GROUPS.get(lang_key, [lang_key]):
            if mapping.get(k):
                return mapping[k]
    # 第二輪：DEFAULT_PRIORITY 補位（避免「沒繁中 → 直接日文」）
    for lang_key in DEFAULT_PRIORITY:
        if lang_key in priority:
            continue
        for k in LANG_GROUPS.get(lang_key, [lang_key]):
            if mapping.get(k):
                return mapping[k]
    # 最後保底：任意一個有值的
    for v in mapping.values():
        if v:
            return v
    return fallback


def fmt_date(text):
    if not text:
        return ""
    # 把所有空白（含換行/Tab）壓成單一空格
    text = re.sub(r"\s+", " ", str(text)).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    m2 = re.match(r"([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if m2:
        text2 = m2.group(1)
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(text2, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    # "July 2003" 之類（無日）
    m3 = re.match(r"([A-Z][a-z]+)\s+(\d{4})", text)
    if m3:
        for fmt in ("%B %Y", "%b %Y"):
            try:
                return datetime.strptime(m3.group(0), fmt).strftime("%Y-%m-01")
            except ValueError:
                continue
    return text


def fmt_year(text):
    m = re.search(r"(\d{4})", str(text or ""))
    return m.group(1) if m else ""


def safe_str(v):
    return str(v) if v else ""


def sub_el(parent, tag, text=None, attrib=None, cdata=False):
    el = etree.SubElement(parent, tag, attrib or {})
    if cdata and text:
        el.text = etree.CDATA(text)
    elif text is not None:
        el.text = str(text)
    return el


def make_xml_declaration(root):
    xml_bytes = etree.tostring(root, encoding="utf-8", xml_declaration=True,
                               pretty_print=True, standalone=True)
    return xml_bytes.decode("utf-8")


def generate_tvshow_nfo(series_data, seasons, episodes_by_season, actors, lang, lang_priority=None):
    root = etree.Element("tvshow")

    ov = series_data.get("overviews", {})
    overview = pick_translation(ov, priority=lang_priority)
    sub_el(root, "plot", overview, cdata=True)
    sub_el(root, "outline", overview, cdata=True)
    sub_el(root, "lockdata", "false")
    sub_el(root, "dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    base_title = series_data.get("title", "")
    tr = series_data.get("title_translations", {})
    title = pick_translation(tr, base_title, priority=lang_priority)
    orig_title = tr.get("jpn") or base_title
    sub_el(root, "title", title)
    sub_el(root, "originaltitle", orig_title)

    # Actors with tvdbid / tmdbid / thumb
    for actor in actors:
        ael = etree.SubElement(root, "actor")
        sub_el(ael, "name", actor.get("name", ""))
        sub_el(ael, "role", actor.get("role", ""))
        sub_el(ael, "type", actor.get("type", "Actor"))
        a_tvdbid = actor.get("tvdbid", "")
        a_tmdbid = actor.get("tmdbid", "")
        a_thumb = actor.get("thumb", "")
        if a_tvdbid:
            sub_el(ael, "tvdbid", a_tvdbid)
        if a_tmdbid:
            sub_el(ael, "tmdbid", a_tmdbid)
        if a_thumb:
            sub_el(ael, "thumb", a_thumb)

    for tr_url in series_data.get("trailers", []):
        if tr_url:
            sub_el(root, "trailer", tr_url)

    sub_el(root, "rating", str(series_data.get("rating", "0") or "0"))
    yr = fmt_year(series_data.get("first_aired", ""))
    sub_el(root, "year", yr)
    sub_el(root, "sorttitle", title)

    mpaa = series_data.get("mpaa", "") or (
        (series_data.get("content_ratings") or [""])[0] if isinstance(series_data.get("content_ratings"), list) else ""
    )
    if mpaa:
        sub_el(root, "mpaa", mpaa)

    sid = series_data.get("series_id", "")
    imdb = series_data.get("imdb_id", "")
    tmdb = series_data.get("tmdb_id", "")
    tvrage = series_data.get("tvrage_id", "")

    if imdb:
        sub_el(root, "imdb_id", imdb)
    if tmdb:
        sub_el(root, "tmdbid", tmdb)
    if sid:
        sub_el(root, "tvdbid", sid)

    premiered = fmt_date(series_data.get("first_aired", ""))
    if premiered:
        sub_el(root, "premiered", premiered)
        sub_el(root, "releasedate", premiered)

    all_eps = []
    for eps in episodes_by_season.values():
        all_eps.extend(eps)
    aired_dates = sorted([e.get("aired", "") for e in all_eps if e.get("aired")])
    if aired_dates:
        end_d = fmt_date(aired_dates[-1])
        if end_d:
            sub_el(root, "enddate", end_d)

    runtime = series_data.get("runtime", "")
    if runtime:
        sub_el(root, "runtime", runtime)

    for g in series_data.get("genres", []):
        sub_el(root, "genre", g)

    network = series_data.get("network", "")
    if network:
        sub_el(root, "studio", network)

    for t in series_data.get("tags", []):
        if t:
            sub_el(root, "tag", t)

    if imdb:
        sub_el(root, "uniqueid", imdb, {"type": "imdb"})
    if tmdb:
        sub_el(root, "uniqueid", tmdb, {"type": "tmdb"})
    if sid:
        sub_el(root, "uniqueid", sid, {"type": "tvdb"})
        sub_el(root, "tvdbid", sid)
    if tvrage:
        sub_el(root, "uniqueid", tvrage, {"type": "tvrage"})
        sub_el(root, "tvrageid", tvrage)

    # Artwork：用相對檔名（避免硬寫絕對路徑），讓 Emby 自己用同目錄
    art_el = etree.SubElement(root, "art")
    if series_data.get("poster_path"):
        sub_el(art_el, "poster", _basename_or(series_data["poster_path"], "poster.jpg"))
    if series_data.get("fanart_path"):
        sub_el(art_el, "fanart", _basename_or(series_data["fanart_path"], "fanart.jpg"))
    if series_data.get("clearlogo_path"):
        sub_el(art_el, "clearlogo", _basename_or(series_data["clearlogo_path"], "clearlogo.png"))
    if series_data.get("banner_path"):
        sub_el(art_el, "banner", _basename_or(series_data["banner_path"], "banner.jpg"))

    ep_guide = {}
    if imdb: ep_guide["imdb"] = imdb
    if tmdb: ep_guide["tmdb"] = tmdb
    if sid:  ep_guide["tvdb"] = sid
    if tvrage: ep_guide["tvrage"] = tvrage
    sub_el(root, "episodeguide", json.dumps(ep_guide, ensure_ascii=False))

    if sid:
        sub_el(root, "id", sid)
    sub_el(root, "season", "-1")
    sub_el(root, "episode", "-1")
    sub_el(root, "displayorder", "aired")
    sub_el(root, "status", series_data.get("status", ""))

    return root


def _basename_or(path_or_name, fallback):
    """從絕對路徑取檔名，沒有就回 fallback"""
    if not path_or_name:
        return fallback
    name = str(path_or_name).replace("\\", "/").rstrip("/").split("/")[-1]
    return name or fallback


def generate_season_nfo(season_info, season_episodes, lang_priority=None):
    root = etree.Element("season")
    sn = season_info.get("number", 0)

    # 標題本地化：優先用季 translations，再來中文化「季別 N」/「特別篇」
    tr = season_info.get("title_translations", {})
    name = pick_translation(tr, priority=lang_priority) if tr else ""
    if not name:
        if sn == 0:
            name = "特別篇"
        else:
            name = f"季別 {sn}"

    # 季 plot
    ov = season_info.get("overviews", {})
    overview = pick_translation(ov, priority=lang_priority) if ov else ""

    sub_el(root, "plot", overview, cdata=True)
    sub_el(root, "outline", overview, cdata=True)
    sub_el(root, "lockdata", "false")
    sub_el(root, "dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    sub_el(root, "title", name)

    aired_dates = [e.get("aired", "") for e in season_episodes if e.get("aired")]
    if aired_dates:
        sub_el(root, "year", fmt_year(sorted(aired_dates)[0]))
    elif season_info.get("from"):
        sub_el(root, "year", fmt_year(season_info["from"]))

    sub_el(root, "sorttitle", name)

    # season tvdbid：只有「看起來像 v3 ID」(<= 7 位數) 才寫，避免 8+ 位數新內部 ID
    season_tvdb_id = season_info.get("tvdb_id", "")
    if season_tvdb_id and len(str(season_tvdb_id)) <= 7:
        sub_el(root, "tvdbid", season_tvdb_id)

    # premiered：優先用「該季第一集的 aired」(精確日期)，最後才退到 season info 的 from(常常只有 年-月)
    first_ep_aired = sorted(aired_dates)[0] if aired_dates else ""
    season_from = season_info.get("from", "")
    # fmt_date 對單純 "年-月" 會 fallback 到 -01；要避免那個失真
    if first_ep_aired:
        premiered = fmt_date(first_ep_aired)
    elif season_from and re.match(r"\d{4}-\d{2}-\d{2}", season_from.strip()):
        premiered = fmt_date(season_from)
    else:
        premiered = ""  # 寧可空也不要寫 month-01 假值
    if premiered:
        sub_el(root, "premiered", premiered)
        sub_el(root, "releasedate", premiered)

    if season_tvdb_id and len(str(season_tvdb_id)) <= 7:
        sub_el(root, "uniqueid", season_tvdb_id, {"type": "tvdb"})

    # Art：相對檔名
    poster_name = f"season{sn:02d}-poster.jpg" if sn > 0 else "season-specials-poster.jpg"
    art_el = etree.SubElement(root, "art")
    sub_el(art_el, "poster", poster_name)

    sub_el(root, "seasonnumber", str(sn))
    return root


def generate_episode_nfo(ep, series_data, lang_priority=None):
    root = etree.Element("episodedetails")
    overview = ep.get("overview") or ""
    if not overview:
        ov = ep.get("overviews", {})
        overview = pick_translation(ov, priority=lang_priority) if ov else ""
        if not overview and ov:
            overview = list(ov.values())[0]
    sub_el(root, "plot", overview, cdata=True)
    sub_el(root, "outline", "")
    sub_el(root, "lockdata", "false")
    sub_el(root, "dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    sub_el(root, "title", ep.get("name", "") or ep.get("title", ""))
    if series_data.get("title"):
        sub_el(root, "showtitle", series_data.get("title", ""))

    # Directors / Writers：支援 dict (含 tvdbid) 或舊版 string
    for d in ep.get("directors", []):
        if isinstance(d, dict):
            attrib = {"tvdbid": d["tvdbid"]} if d.get("tvdbid") else {}
            sub_el(root, "director", d.get("name", ""), attrib)
        elif d:
            sub_el(root, "director", d)
    for w in ep.get("writers", []):
        if isinstance(w, dict):
            attrib = {"tvdbid": w["tvdbid"]} if w.get("tvdbid") else {}
            sub_el(root, "writer", w.get("name", ""), attrib)
        elif w:
            sub_el(root, "writer", w)

    sub_el(root, "rating", "0")
    aired = ep.get("aired", "")
    if aired:
        sub_el(root, "year", fmt_year(aired))
        sub_el(root, "aired", fmt_date(aired))
    sub_el(root, "sorttitle", ep.get("name", ""))
    ep_runtime = ep.get("runtime", "")
    if ep_runtime:
        sub_el(root, "runtime", ep_runtime)
    epid = ep.get("id", "")
    if epid:
        sub_el(root, "tvdbid", epid)
        sub_el(root, "uniqueid", epid, {"type": "tvdb"})
    ep_imdb = ep.get("imdb_id", "")
    ep_tmdb = ep.get("tmdb_id", "")
    if ep_imdb:
        sub_el(root, "imdbid", ep_imdb)
        sub_el(root, "uniqueid", ep_imdb, {"type": "imdb"})
    if ep_tmdb:
        sub_el(root, "tmdbid", ep_tmdb)
        sub_el(root, "uniqueid", ep_tmdb, {"type": "tmdb"})
    sub_el(root, "episode", str(ep.get("number", "")))
    sub_el(root, "season", str(ep.get("seasonNumber", "")))
    if ep.get("thumb_local"):
        art_el = etree.SubElement(root, "art")
        sub_el(art_el, "poster", ep["thumb_local"])
    return root


def run(args):
    series_id = args.id
    series_url = args.url
    series_name = args.name

    if not series_id and series_url:
        m = re.search(r"/series/([^/]+)", series_url)
        slug = m.group(1) if m else ""
        if slug.isdigit():
            series_id = slug
            slug = resolve_slug(series_id)
        else:
            # Get ID from the page
            try:
                sd = scrape_series_page(slug)
                series_id = sd.get("series_id", "")
            except Exception:
                pass

    if not series_id and series_name:
        print(f"搜尋「{series_name}」...")
        results = search_series(series_name, args.lang)
        if not results:
            results = search_series(series_name, "en")
        if not results:
            print("找不到任何結果。")
            return

        if len(results) == 1:
            r = results[0]
            series_id = r["id"]
            print(f"找到: {r['name']} (ID: {series_id})")
        else:
            print(f"\n找到 {len(results)} 個結果:")
            print("-" * 60)
            for i, r in enumerate(results):
                yr = r.get("firstAired", "")[:4]
                print(f"  [{i+1}] {r['name']} ({yr})  TVDB ID: {r['id']}")
                ov = r.get("overview", "")[:80]
                if ov:
                    print(f"      {ov}...")
            print("-" * 60)
            choice = input(f"請選擇 (1-{len(results)}, q=離開): ").strip()
            if choice.lower() == "q":
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(results):
                    series_id = results[idx]["id"]
                else:
                    print("無效選擇。")
                    return
            except ValueError:
                print("無效選擇。")
                return

    if not series_id:
        print("無法確定 Series ID。")
        return

    slug = resolve_slug(series_id)
    if not slug:
        print(f"無法解析 Series ID {series_id} 的網址。")
        return

    print(f"TVDB 網址: {BASE_URL}/series/{slug}")

    print("爬取系列資訊 ...")
    series_data = scrape_series_page(slug)
    title = series_data.get("title", slug)
    print(f"  標題: {title}")
    actors = series_data.get("actors", [])
    print(f"  季數: {len(series_data.get('seasons', []))}")
    print(f"  演員: {len(actors)} 位")

    episodes_by_season = {}
    if not args.no_episodes:
        seasons = series_data.get("seasons", [])
        for s_info in seasons:
            sn = s_info["number"]
            s_url = s_info["url"]
            if not s_url:
                continue
            print(f"爬取 {s_info['name']} ({s_info['episode_count']} 集) ...")
            season_data = scrape_season_page(s_url)
            eps = season_data["episodes"]
            s_info["tvdb_id"] = season_data.get("tvdb_id", "")
            s_info["overviews"] = season_data.get("overviews", {})
            s_info["title_translations"] = season_data.get("title_translations", {})
            episodes_by_season[sn] = eps
            for ep in eps:
                ep_url = ep.get("url", "")
                if not ep_url:
                    continue
                print(f"  取得 {ep.get('name', '?')} 的詳細資訊 ...")
                try:
                    ep_detail = scrape_episode_page(ep_url)
                    ep["overviews"] = ep_detail.get("overviews", {})
                    ovs = ep_detail.get("overviews", {})
                    ep["overview"] = pick_translation(ovs, priority=DEFAULT_PRIORITY) or (list(ovs.values())[0] if ovs else "")
                    ep["directors"] = ep_detail.get("directors", [])
                    ep["writers"] = ep_detail.get("writers", [])
                    ep["imdb_id"] = ep_detail.get("imdb_id", "")
                    ep["tmdb_id"] = ep_detail.get("tmdb_id", "")
                    if ep_detail.get("aired"):
                        ep["aired"] = ep_detail["aired"]
                    if ep_detail.get("runtime"):
                        ep["runtime"] = ep_detail["runtime"]
                except Exception as e:
                    print(f"    警告: {e}")
                time.sleep(0.5)

    output_dir = Path(args.output)
    safe_title = "".join(c for c in title if c not in r'\/:*?"<>|')
    if not args.url:
        output_dir = output_dir / safe_title
    output_dir.mkdir(parents=True, exist_ok=True)

    # Download series artwork
    images = series_data.get("images", {})
    sid_str = series_data.get("series_id", "")
    print("\n下載系列圖片 ...")
    if images.get("poster"):
        poster_path = output_dir / "poster.jpg"
        if download_image(images["poster"], poster_path):
            series_data["poster_path"] = str(poster_path.resolve())
            print(f"  poster.jpg")
    if images.get("fanart"):
        fanart_path = output_dir / "fanart.jpg"
        if download_image(images["fanart"], fanart_path):
            series_data["fanart_path"] = str(fanart_path.resolve())
            print(f"  fanart.jpg")
    if images.get("clearlogo"):
        ext = ".png" if images["clearlogo"].lower().endswith(".png") else ".jpg"
        clearlogo_path = output_dir / f"clearlogo{ext}"
        if download_image(images["clearlogo"], clearlogo_path):
            series_data["clearlogo_path"] = str(clearlogo_path.resolve())
            print(f"  clearlogo{ext}")
    if images.get("banner"):
        banner_path = output_dir / "banner.jpg"
        if download_image(images["banner"], banner_path):
            series_data["banner_path"] = str(banner_path.resolve())
            print(f"  banner.jpg")

    # Download season posters
    for s_info in series_data.get("seasons", []):
        sn = s_info["number"]
        season_dir_name = "Specials" if sn == 0 else f"Season {sn:02d}"
        season_dir = output_dir / season_dir_name
        season_dir.mkdir(parents=True, exist_ok=True)
        simg = scrape_season_images(s_info.get("url", ""), sid_str, sn)
        poster_name = f"season{sn:02d}-poster.jpg" if sn > 0 else "season-specials-poster.jpg"
        if simg.get("poster"):
            download_image(simg["poster"], season_dir / poster_name)
            s_info["poster_local"] = poster_name
        time.sleep(0.3)

    # Download episode thumbnails
    for s_info in series_data.get("seasons", []):
        sn = s_info["number"]
        eps = episodes_by_season.get(sn, [])
        season_dir_name = "Specials" if sn == 0 else f"Season {sn:02d}"
        season_dir = output_dir / season_dir_name
        season_dir.mkdir(parents=True, exist_ok=True)
        for ep in eps:
            ep_thumb = get_episode_image_url(sid_str, ep.get("id", ""))
            thumb_name = f"S{ep.get('seasonNumber', sn):02d}E{ep.get('number', 0):02d}-thumb.jpg"
            if download_image(ep_thumb, season_dir / thumb_name):
                ep["thumb_local"] = thumb_name
            time.sleep(0.2)

    print("\n產生 tvshow.nfo ...")
    tvshow_root = generate_tvshow_nfo(series_data, series_data.get("seasons", []),
                                       episodes_by_season, actors, args.lang, DEFAULT_PRIORITY)
    tvshow_xml = make_xml_declaration(tvshow_root)
    tvshow_path = output_dir / "tvshow.nfo"
    tvshow_path.write_text(tvshow_xml, encoding="utf-8")
    print(f"  -> {tvshow_path}")

    if args.no_episodes:
        print("完成！")
        return

    for s_info in series_data.get("seasons", []):
        sn = s_info["number"]
        eps = episodes_by_season.get(sn, [])
        if not eps:
            continue
        season_dir_name = "Specials" if sn == 0 else f"Season {sn:02d}"
        season_dir = output_dir / season_dir_name
        season_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{season_dir_name} ({len(eps)} 集)")

        season_root = generate_season_nfo(s_info, eps)
        season_xml = make_xml_declaration(season_root)
        nfo_path = season_dir / "season.nfo"
        nfo_path.write_text(season_xml, encoding="utf-8")
        print(f"  產生 season.nfo")

        for ep in sorted(eps, key=lambda e: int(e.get("number", 0))):
            ep_num = ep.get("number", 0)
            ep_season = ep.get("seasonNumber", sn)
            ep_filename = f"S{ep_season:02d}E{ep_num:02d}.nfo"
            ep_root = generate_episode_nfo(ep, series_data)
            ep_xml = make_xml_declaration(ep_root)
            ep_path = season_dir / ep_filename
            ep_path.write_text(ep_xml, encoding="utf-8")
            print(f"    {ep_filename}")

    print(f"\n{'=' * 55}")
    print(f"  完成！NFO 輸出到: {output_dir.resolve()}")
    print(f"{'=' * 55}")


def auto_thumb(directory, time_pct=0.25, width=1280, jpeg_q=2, overwrite_small=True):
    """從影片自動截圖補缺失的 -thumb.jpg

    對 directory 內所有 .mkv/.mp4，若沒有同主檔名的 -thumb.jpg（或檔案 < 1KB），
    用 ffmpeg 截取指定 time_pct 處的畫面當 thumb。

    參數:
        directory: 要掃描的目錄（會遞迴 Season XX/、Specials/、Backdrops/）
        time_pct: 截圖位置（0.25 = 25% 處，避開 OP/ED）
        width: 縮放寬度
        jpeg_q: JPEG 品質 1-31（越小越好）
        overwrite_small: 若既有 thumb < 1KB（壞圖/假圖），刪掉重截
    """
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("[auto-thumb] 錯誤：找不到 ffmpeg / ffprobe，請先安裝（winget install Gyan.FFmpeg）")
        return 1

    target = Path(directory)
    if not target.exists():
        print(f"[auto-thumb] 找不到目錄: {target}")
        return 1

    video_exts = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts")
    videos = [p for p in target.rglob("*") if p.suffix.lower() in video_exts and "Backdrops" not in p.parts]

    if not videos:
        print(f"[auto-thumb] {target} 沒有影片檔")
        return 0

    print(f"[auto-thumb] 掃描 {len(videos)} 個影片")
    ok = skip = fail = 0

    for v in videos:
        thumb = v.with_name(v.stem + "-thumb.jpg")

        # 跳過邏輯
        if thumb.exists():
            size = thumb.stat().st_size
            if size >= 1024:
                skip += 1
                continue
            if overwrite_small:
                thumb.unlink()  # 刪壞圖
            else:
                skip += 1
                continue

        # 取時長
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(v)],
                capture_output=True, text=True, timeout=30,
            )
            duration = float(r.stdout.strip())
        except (subprocess.SubprocessError, ValueError) as e:
            print(f"  XX 無法取時長 {v.name}: {e}")
            fail += 1
            continue

        timestamp = round(duration * time_pct, 2)

        # 截圖（-ss 放 -i 前面是 fast seek，差距 100 倍速度）
        try:
            subprocess.run(
                [ffmpeg, "-loglevel", "error", "-ss", str(timestamp),
                 "-i", str(v), "-vframes", "1",
                 "-vf", f"scale={width}:-1", "-q:v", str(jpeg_q),
                 "-y", str(thumb)],
                check=True, timeout=60,
            )
        except subprocess.SubprocessError as e:
            print(f"  XX ffmpeg 失敗 {v.name}: {e}")
            fail += 1
            continue

        if thumb.exists() and thumb.stat().st_size > 1024:
            print(f"  OK {thumb.name} ({thumb.stat().st_size} bytes @ {timestamp}s)")
            ok += 1
        else:
            print(f"  XX 截圖大小異常 {v.name}")
            if thumb.exists():
                thumb.unlink()
            fail += 1

    print(f"\n[auto-thumb] 完成：OK={ok}  Skip={skip}  Fail={fail}  Total={len(videos)}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="TheTVDB NFO Crawler (純爬蟲，不需 API Key)")
    parser.add_argument("name", nargs="?", help="動漫名稱")
    parser.add_argument("-u", "--url", help="TheTVDB 系列網址")
    parser.add_argument("-i", "--id", help="TheTVDB 系列 ID")
    parser.add_argument("-o", "--output", default=".", help="輸出目錄")
    parser.add_argument("-l", "--lang", default="zho", help="語言代碼 (zho/jpn/eng)")
    parser.add_argument("--no-episodes", action="store_true", help="只產生 tvshow.nfo")

    # 新增：ffmpeg auto-thumb 模式
    parser.add_argument("--auto-thumb", metavar="DIR",
                        help="獨立模式：對指定目錄內所有影片，缺 -thumb.jpg 的用 ffmpeg 截 25%% 處補上")
    parser.add_argument("--thumb-pct", type=float, default=0.25,
                        help="auto-thumb 的截圖位置（0.0-1.0），預設 0.25")
    parser.add_argument("--thumb-width", type=int, default=1280,
                        help="auto-thumb 的縮放寬度，預設 1280")
    parser.add_argument("--thumb-quality", type=int, default=2,
                        help="auto-thumb 的 JPEG 品質 1-31（越小越好），預設 2")
    args = parser.parse_args()

    # auto-thumb 獨立模式（不需要爬 TheTVDB）
    if args.auto_thumb:
        return auto_thumb(
            args.auto_thumb,
            time_pct=args.thumb_pct,
            width=args.thumb_width,
            jpeg_q=args.thumb_quality,
        )

    if not args.name and not args.url and not args.id:
        print("TheTVDB NFO Crawler (純爬蟲版)")
        print("=" * 55)
        while True:
            q = input("\n請輸入動漫名稱 / TVDB網址 / TVDB ID (q 離開): ").strip()
            if q.lower() == "q":
                break
            if not q:
                continue
            if q.startswith("http"):
                args.url = q
            elif q.isdigit():
                args.id = q
            else:
                args.name = q
            run(args)
            break
        return

    run(args)


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except Exception:
            pass
    main()
