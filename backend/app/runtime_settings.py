"""Runtime settings overlay.

Layer cake (lowest → highest precedence):
  1. defaults baked into pydantic Settings
  2. env vars / .env file (loaded by pydantic at startup)
  3. JSON file at {data_dir}/settings.json (written by the /api/settings PUT)

`load_from_disk()` applies layer 3 on top of `settings`. `update()` writes the
file and mutates `settings` in-place so the next request sees new values.
"""
import json
from pathlib import Path

from .config import settings

# Anything outside this set is rejected by update().
WRITABLE_KEYS = {"search_lang", "lang_priority", "episode_delay", "tmdb_api_key",
                 "fuzz_threshold", "enabled_sources"}


def _settings_file() -> Path:
    return Path(settings.data_dir) / "settings.json"


def load_from_disk() -> None:
    """Apply any saved overrides to the live settings singleton."""
    f = _settings_file()
    if not f.exists():
        return
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    for k, v in (data or {}).items():
        if k in WRITABLE_KEYS:
            setattr(settings, k, v)


def update(updates: dict) -> None:
    """Persist a partial update and live-apply it."""
    clean = {k: v for k, v in (updates or {}).items() if k in WRITABLE_KEYS}
    f = _settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if f.exists():
        try:
            existing = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(clean)
    f.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in clean.items():
        setattr(settings, k, v)


def current() -> dict:
    """Effective values for the UI."""
    from .config import lang_priority_list
    from .sources import enabled_names
    return {
        "search_lang": settings.search_lang,
        "lang_priority": lang_priority_list(),
        "episode_delay": settings.episode_delay,
        "output_dir": settings.output_dir,
        "tmdb_api_key_set": bool(settings.tmdb_api_key),
        "fuzz_threshold": settings.fuzz_threshold,
        "enabled_sources": enabled_names(),
    }
