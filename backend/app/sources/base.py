"""Canonical schema shared by every metadata source.

每個來源模組（sources/tvdb.py、之後的 sources/tmdb.py …）都必須提供：

    NAME: str                                   # 來源代號，如 "tvdb"
    def search(q, limit=5) -> list[dict]        # preview 項目（含 fuzzy score）
    def full(item_id, episodes="none",
             priority=None, hint="") -> dict    # canonical detail
    def empty() -> dict                         # 該來源的全空 canonical detail

canonical detail 的空值規則（同 comic-metadata-syncer）：
  - str  -> ""
  - num  -> None
  - list -> []

preview 項目建議欄位：source / id / title_cn / title_native / title_english /
year / url / cover / score / overview / aliases / hint（hint 是傳回給
full() 的來源內部提示，例如 tvdb 的 slug，避免多一次反查）。
"""

CANONICAL_FIELDS = (
    "source", "id", "url",
    "match_score",              # fuzzy 相似度，給客戶端排序/挑選用
    "media_type",               # series / movie
    "title", "original_title", "title_translations",
    "plot", "overviews",
    "year", "premiered", "end_date", "status",
    "studio", "runtime", "country", "language", "mpaa",
    "genres", "tags", "rating",
    "unique_ids", "trailers", "actors", "images",
    "season_count", "episode_count", "seasons",
)


def empty_detail(source: str) -> dict:
    """Return an all-empty canonical detail for a given source."""
    return {
        "source": source,
        "id": "",
        "url": "",
        "match_score": 0,
        "media_type": "series",
        "title": "",
        "original_title": "",
        "title_translations": {},
        "plot": "",
        "overviews": {},
        "year": "",
        "premiered": "",
        "end_date": "",
        "status": "",
        "studio": "",
        "runtime": "",
        "country": "",
        "language": "",
        "mpaa": "",
        "genres": [],
        "tags": [],
        "rating": {"score": None, "votes": None},
        "unique_ids": {"tvdb": "", "imdb": "", "tmdb": "", "tvrage": ""},
        "trailers": [],
        "actors": [],
        "images": {"poster": "", "fanart": "", "clearlogo": "", "banner": ""},
        "season_count": None,
        "episode_count": None,
        "seasons": [],
    }
