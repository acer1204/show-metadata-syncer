"""Source registry.

要新增來源（例如 TMDB）：
  1. 新增 sources/tmdb.py，實作 base.py 說明的 NAME / search / full / empty
  2. 在下面的 SOURCES 註冊

/api/preview 與 /api/metadata 的 `source` 參數（all / tvdb / ...）
會自動涵蓋所有註冊的來源。
"""
from . import tvdb
from . import tmdb

SOURCES = {
    tvdb.NAME: tvdb,
    tmdb.NAME: tmdb,
}


def get_source(name: str):
    return SOURCES.get((name or "").lower())


def source_names() -> list[str]:
    return list(SOURCES.keys())


def enabled_names() -> list[str]:
    """設定裡啟用且有註冊的來源；停用的來源不參與搜尋。"""
    from ..config import settings
    out = [s.strip().lower() for s in settings.enabled_sources.split(",") if s.strip()]
    return [s for s in out if s in SOURCES]


def is_enabled(name: str) -> bool:
    return (name or "").lower() in enabled_names()
