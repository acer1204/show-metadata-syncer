#!/usr/bin/env python3
"""CLI shim — 爬蟲本體已移到 backend/app/clients/tvdb.py。

使用方式（跟以前一樣）:
    py tvdb_crawler.py "一騎当千"              # 搜尋並輸出 NFO
    py tvdb_crawler.py -u "https://..."        # 直接給 TVDB 網址
    py tvdb_crawler.py -i 80158                # 直接用 TVDB ID
    py tvdb_crawler.py --auto-thumb DIR        # ffmpeg 補縮圖
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.clients.tvdb import main

if __name__ == "__main__":
    main()
