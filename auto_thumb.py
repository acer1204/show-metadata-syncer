#!/usr/bin/env python3
"""auto_thumb.py — 從影片自動截圖補缺失的 -thumb.jpg

對指定目錄遞迴掃描 .mkv/.mp4/.m4v/.avi/.ts/.m2ts，
若沒有同主檔名的 -thumb.jpg（或檔案 < 1 KB 是假圖/壞圖），
用 ffmpeg 截取指定 % 處的畫面當預覽圖。

純 stdlib + ffmpeg/ffprobe 外部命令，無需 pip 安裝任何套件。

使用範例:
    python auto_thumb.py "E:/Anime/武裝少女 (2017)"
    python auto_thumb.py "E:/Anime/某動漫" --pct 0.33 --width 1920
    python auto_thumb.py "E:/Anime" -r       # 遞迴掃整個 Anime 庫
"""

import argparse
import shutil
import subprocess
import sys
import io
from pathlib import Path

VIDEO_EXTS = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts")
EXCLUDE_DIRS = {"Backdrops", "Extras"}  # 不對 OP/ED 等附加片做 thumb


def ensure_utf8_stdout():
    if hasattr(sys.stdout, "buffer"):
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except Exception:
            pass


def find_ffmpeg():
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("XX 找不到 ffmpeg / ffprobe")
        print("   安裝：")
        print("     Windows: winget install Gyan.FFmpeg")
        print("     macOS:   brew install ffmpeg")
        print("     Linux:   sudo apt install ffmpeg")
        return None, None
    return ffmpeg, ffprobe


def get_duration(ffprobe, video_path):
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error",
             "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def make_thumb(ffmpeg, video_path, thumb_path, timestamp_sec, width, jpeg_q):
    try:
        subprocess.run(
            [ffmpeg, "-loglevel", "error",
             "-ss", str(timestamp_sec),         # -ss 在 -i 前 = fast seek
             "-i", str(video_path),
             "-vframes", "1",
             "-vf", f"scale={width}:-1",        # 保比例縮放
             "-q:v", str(jpeg_q),               # JPEG 品質
             "-y", str(thumb_path)],
            check=True, timeout=60,
        )
        return True
    except subprocess.SubprocessError:
        return False


def collect_videos(root):
    """遞迴找影片，排除 Backdrops/Extras。"""
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXTS:
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        yield p


def process(directory, time_pct=0.25, width=1280, jpeg_q=2,
            overwrite_small=True, dry_run=False):
    target = Path(directory)
    if not target.exists():
        print(f"XX 找不到目錄: {target}")
        return 1

    ffmpeg, ffprobe = find_ffmpeg()
    if not ffmpeg:
        return 1

    videos = list(collect_videos(target))
    if not videos:
        print(f"在 {target} 沒找到任何影片")
        return 0

    print(f"=== 掃描 {target} ===")
    print(f"  影片: {len(videos)} 個")
    print(f"  截圖位置: {time_pct*100:.0f}% 處")
    print(f"  寬度: {width}px")
    print(f"  JPEG 品質: {jpeg_q}（1=最高品質，31=最差）")
    if dry_run:
        print(f"  *** DRY RUN（不實際截圖）***")
    print()

    ok = skip = fail = 0

    for v in videos:
        thumb = v.with_name(v.stem + "-thumb.jpg")

        # 跳過判斷
        if thumb.exists():
            size = thumb.stat().st_size
            if size >= 1024:
                skip += 1
                continue
            if not overwrite_small:
                skip += 1
                continue
            # 壞圖 / 假圖（JSON 偽裝）→ 刪掉重截
            if not dry_run:
                thumb.unlink()

        # 取時長
        duration = get_duration(ffprobe, v)
        if duration is None:
            print(f"  XX 無法取時長: {v.relative_to(target)}")
            fail += 1
            continue

        timestamp = round(duration * time_pct, 2)

        if dry_run:
            print(f"  [DRY] {v.relative_to(target)} → 會截 {timestamp}s 處")
            ok += 1
            continue

        # 截圖
        if not make_thumb(ffmpeg, v, thumb, timestamp, width, jpeg_q):
            print(f"  XX ffmpeg 失敗: {v.relative_to(target)}")
            fail += 1
            continue

        if not thumb.exists() or thumb.stat().st_size < 1024:
            print(f"  XX 截圖檔案異常: {v.relative_to(target)}")
            if thumb.exists():
                thumb.unlink()
            fail += 1
            continue

        size = thumb.stat().st_size
        rel = v.relative_to(target)
        print(f"  OK {rel.parent}/{thumb.name} ({size} bytes @ {timestamp}s)")
        ok += 1

    print()
    print(f"=== 完成 ===")
    print(f"  截圖成功: {ok}")
    print(f"  已有跳過: {skip}")
    print(f"  失敗:     {fail}")
    print(f"  總計:     {len(videos)}")
    return 0 if fail == 0 else 2


def main():
    ensure_utf8_stdout()
    p = argparse.ArgumentParser(
        description="從影片自動截圖補缺失的 -thumb.jpg（純 stdlib + ffmpeg）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("directory", help="要掃描的目錄（會遞迴）")
    p.add_argument("--pct", type=float, default=0.25,
                   help="截圖位置（0.0-1.0），預設 0.25（25%% 處，避開 OP/ED）")
    p.add_argument("--width", type=int, default=1280,
                   help="輸出寬度（高度按比例自動），預設 1280")
    p.add_argument("--quality", type=int, default=2,
                   help="JPEG 品質 1-31（越小越好），預設 2")
    p.add_argument("--no-overwrite-small",
                   action="store_true",
                   help="既有 thumb < 1KB 時不刪除重截（預設會刪掉，避免假圖殘留）")
    p.add_argument("--dry-run", action="store_true",
                   help="只列出會做什麼，不實際截圖")

    args = p.parse_args()
    return process(
        args.directory,
        time_pct=args.pct,
        width=args.width,
        jpeg_q=args.quality,
        overwrite_small=not args.no_overwrite_small,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
