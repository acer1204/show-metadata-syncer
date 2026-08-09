"""NFO generators — 直接回傳 Emby/Jellyfin 相容的 XML 字串，不寫檔。"""
from typing import List, Optional

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..config import settings, lang_priority_list
from ..clients.tvdb import (
    generate_tvshow_nfo, generate_season_nfo, generate_episode_nfo,
    make_xml_declaration, pick_translation,
)
from ..sources.tvdb import get_series, get_season, find_episode

router = APIRouter(prefix="/api/nfo", tags=["nfo"])


class NfoRequest(BaseModel):
    series_id: str
    lang: Optional[str] = None
    lang_priority: Optional[List[str]] = Field(default=None, description="預設用系統設定")


class SeasonNfoRequest(NfoRequest):
    season: int


class EpisodeNfoRequest(NfoRequest):
    episode_id: str


def _resolve(req: NfoRequest) -> tuple[str, List[str]]:
    return (req.lang or settings.search_lang,
            req.lang_priority or lang_priority_list())


@router.post("/tvshow", operation_id="generate_tvshow_nfo_api", response_class=PlainTextResponse)
def gen_tvshow_nfo(req: NfoRequest):
    """產生 tvshow.nfo 的 XML 字串（不寫檔）。"""
    lang, priority = _resolve(req)
    sd = get_series(req.series_id)
    seasons = sd.get("seasons", [])
    actors = sd.get("actors", [])
    root = generate_tvshow_nfo(sd, seasons, {}, actors, lang, priority)
    return PlainTextResponse(make_xml_declaration(root), media_type="application/xml")


@router.post("/season", operation_id="generate_season_nfo_api", response_class=PlainTextResponse)
def gen_season_nfo(req: SeasonNfoRequest):
    """產生 season.nfo 的 XML 字串。"""
    _, priority = _resolve(req)
    s = get_season(req.series_id, req.season)
    root = generate_season_nfo(s, s.get("episodes", []), priority)
    return PlainTextResponse(make_xml_declaration(root), media_type="application/xml")


@router.post("/episode", operation_id="generate_episode_nfo_api", response_class=PlainTextResponse)
def gen_episode_nfo(req: EpisodeNfoRequest):
    """產生單集 NFO 的 XML 字串。"""
    _, priority = _resolve(req)
    sd = get_series(req.series_id)
    e = find_episode(req.series_id, req.episode_id)
    e["overview"] = pick_translation(e.get("overviews", {}), priority=priority) or e.get("overview", "")
    e.setdefault("seasonNumber", e.get("season_number", 0))
    root = generate_episode_nfo(e, sd, priority)
    return PlainTextResponse(make_xml_declaration(root), media_type="application/xml")
