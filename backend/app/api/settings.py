"""Read / write runtime settings via the UI.

GET returns the effective values.
PUT accepts a partial body; only fields present are touched.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import runtime_settings
from ..config import KNOWN_LANGS
from ..sources import source_names

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    search_lang: str
    lang_priority: List[str]
    episode_delay: float
    output_dir: str
    tmdb_api_key_set: bool       # key never returned; just a presence flag
    fuzz_threshold: int
    enabled_sources: List[str]


class SettingsIn(BaseModel):
    search_lang: Optional[str] = None
    lang_priority: Optional[List[str]] = None
    episode_delay: Optional[float] = Field(default=None, ge=0, le=10)
    tmdb_api_key: Optional[str] = None   # None = leave unchanged; "" = clear
    fuzz_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    enabled_sources: Optional[List[str]] = None


@router.get("", response_model=SettingsOut, operation_id="get_settings")
def get_settings():
    return SettingsOut(**runtime_settings.current())


@router.put("", response_model=SettingsOut, operation_id="update_settings")
def update_settings(body: SettingsIn):
    updates = body.model_dump(exclude_none=True)
    if "lang_priority" in updates:
        bad = [p for p in updates["lang_priority"] if p not in KNOWN_LANGS]
        if bad:
            raise HTTPException(400, f"未知語言代碼: {bad}（可用: {list(KNOWN_LANGS)}）")
        updates["lang_priority"] = ",".join(updates["lang_priority"])
    if "enabled_sources" in updates:
        bad = [s for s in updates["enabled_sources"] if s not in source_names()]
        if bad:
            raise HTTPException(400, f"未知來源: {bad}（可用: {source_names()}）")
        updates["enabled_sources"] = ",".join(updates["enabled_sources"])
    runtime_settings.update(updates)
    return SettingsOut(**runtime_settings.current())
