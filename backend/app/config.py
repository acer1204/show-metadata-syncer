"""Configuration loaded from env (or .env file)."""
from pydantic_settings import BaseSettings, SettingsConfigDict

# TheTVDB 語言優先序可用的代碼（clients/tvdb.py 的 LANG_GROUPS keys）
KNOWN_LANGS = ("zhtw", "zho", "jpn", "eng")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    cors_origins: str = "*"

    # TheTVDB 搜尋用語言（Algolia translations 的 key）
    search_lang: str = "zho"

    # NFO / metadata 翻譯挑選優先序（逗號分隔）
    lang_priority: str = "zhtw,zho,jpn,eng"

    # 逐集爬詳細頁時的間隔秒數（對 TheTVDB 客氣一點）
    episode_delay: float = 0.5

    # TMDB（免費 API，https://www.themoviedb.org/settings/api 申請）
    tmdb_url: str = "https://api.themoviedb.org/3"
    tmdb_api_key: str = ""

    # Fuzzy match floor (used by future filters; rank is unaffected)
    fuzz_threshold: int = 75

    # 啟用的來源（逗號分隔）；停用的來源不參與搜尋
    enabled_sources: str = "tvdb,tmdb"

    # 目錄（Docker 內以 env 覆寫為 /app/output、/app/data）
    output_dir: str = "./output"
    data_dir: str = "./data"


settings = Settings()


def lang_priority_list() -> list[str]:
    """Parse the comma-separated priority into a clean list."""
    out = [p.strip() for p in settings.lang_priority.split(",") if p.strip()]
    return [p for p in out if p in KNOWN_LANGS] or list(KNOWN_LANGS)
