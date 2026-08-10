"""canonical detail → 既有 NFO 產生器吃的資料形狀。

讓任何回傳 canonical schema 的來源（TMDB、之後的新來源）都能重用
clients/tvdb.py 的 generate_tvshow_nfo / generate_season_nfo /
generate_episode_nfo，不用每個來源各寫一套 NFO 產生器。
"""
from typing import Any, Dict, List, Tuple


def canonical_to_legacy(d: dict, source: str) -> Tuple[dict, List[dict], Dict[int, list], List[dict]]:
    """回傳 (series_data, seasons, episodes_by_season, actors)。

    episode 的 `id` 欄位在 NFO 裡會被寫成 uniqueid type="tvdb"，
    所以只有 tvdb 來源才保留；其他來源的集 id 走 tmdb_id。
    """
    ids = d.get("unique_ids", {}) or {}
    plot = d.get("plot") or ""
    overviews = d.get("overviews") or ({"zho": plot} if plot else {})
    rating_score = (d.get("rating") or {}).get("score")

    series_data: Dict[str, Any] = {
        "title": d.get("original_title") or d.get("title", ""),
        "title_translations": d.get("title_translations", {}) or {},
        "overviews": overviews,
        "first_aired": d.get("premiered", ""),
        "status": d.get("status", ""),
        "network": d.get("studio", ""),
        "runtime": d.get("runtime", ""),
        "country": d.get("country", ""),
        "language": d.get("language", ""),
        "rating": str(rating_score) if rating_score else "0",
        "mpaa": d.get("mpaa", ""),
        "content_ratings": [],
        "series_id": ids.get("tvdb", ""),
        "imdb_id": ids.get("imdb", ""),
        "tmdb_id": ids.get("tmdb", ""),
        "tvrage_id": ids.get("tvrage", ""),
        "genres": d.get("genres", []) or [],
        "tags": d.get("tags", []) or [],
        "trailers": d.get("trailers", []) or [],
        "actors": d.get("actors", []) or [],
        "images": d.get("images", {}) or {},
    }

    seasons: List[dict] = []
    episodes_by_season: Dict[int, list] = {}
    for s in d.get("seasons", []) or []:
        sn = s.get("number")
        if sn is None:
            continue
        seasons.append({
            "number": sn,
            "name": s.get("title") or s.get("name", ""),
            "from": s.get("from", ""),
            "to": s.get("to", ""),
            "episode_count": s.get("episode_count", 0),
            "url": s.get("url", ""),
            "tvdb_id": s.get("tvdb_id", ""),
            "overviews": s.get("overviews", {}) or ({"zho": s["plot"]} if s.get("plot") else {}),
            "title_translations": s.get("title_translations", {}) or {},
            "_poster_url": s.get("poster", ""),
        })
        eps = []
        for ep in s.get("episodes", []) or []:
            eps.append({
                "id": str(ep.get("id", "")) if source == "tvdb" else "",
                "number": ep.get("number", 0),
                "seasonNumber": ep.get("season_number", sn),
                "name": ep.get("title", ""),
                "aired": ep.get("aired", ""),
                "runtime": ep.get("runtime", ""),
                "overview": ep.get("plot", ""),
                "overviews": ep.get("overviews", {}) or {},
                "directors": ep.get("directors", []) or [],
                "writers": ep.get("writers", []) or [],
                "imdb_id": ep.get("imdb_id", ""),
                "tmdb_id": ep.get("tmdb_id", ""),
                "_thumb_url": ep.get("thumb", ""),
            })
        episodes_by_season[sn] = eps

    series_data["seasons"] = seasons
    return series_data, seasons, episodes_by_season, series_data["actors"]
