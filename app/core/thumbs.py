"""Thumbnail extraction for downloaded videos.

Used by the web /downloads listing to show a preview per file. Thumbnails are
~320px-wide JPGs, cached to <output_dir>/.thumbs/<video_name>.jpg.

ffmpeg is located in this order:
  1. system `ffmpeg` on PATH (preferred — fastest, no extra dependency)
  2. `imageio-ffmpeg` Python package, which bundles its own binary

If neither is available, ensure_thumb() returns None and the UI falls back to
a placeholder icon.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from loguru import logger


THUMB_DIR_NAME = ".thumbs"
THUMB_WIDTH = 320
EXTRACT_AT_SECONDS = 1.0  # seek 1 second in to skip black intros


_ffmpeg_cached: str | None | tuple = ()  # () = unresolved, None = absent


def _ffmpeg_binary() -> str | None:
    global _ffmpeg_cached
    if _ffmpeg_cached != ():
        return _ffmpeg_cached  # type: ignore[return-value]

    p = shutil.which("ffmpeg")
    if p:
        _ffmpeg_cached = p
        logger.info("Thumbnails will use system ffmpeg at {}", p)
        return p

    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]
        path = imageio_ffmpeg.get_ffmpeg_exe()
        _ffmpeg_cached = path
        logger.info("Thumbnails will use imageio-ffmpeg at {}", path)
        return path
    except Exception as e:  # noqa: BLE001
        logger.warning("No ffmpeg available — thumbnails disabled ({})", e)
        _ffmpeg_cached = None
        return None


def thumb_path_for(video: Path) -> Path:
    """Where a thumb for `video` lives. Does not check existence."""
    return video.parent / THUMB_DIR_NAME / f"{video.name}.jpg"


def ensure_thumb(video: Path) -> Path | None:
    """Return path to a JPG thumb for `video`, generating it if missing.

    Returns None if ffmpeg is unavailable or extraction fails. Cached: a
    second call for the same video is a stat() and a return.
    """
    if not video.is_file():
        return None
    thumb = thumb_path_for(video)
    if thumb.is_file() and thumb.stat().st_size > 0:
        return thumb

    ffmpeg = _ffmpeg_binary()
    if ffmpeg is None:
        return None

    thumb.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-ss", str(EXTRACT_AT_SECONDS),
        "-i", str(video),
        "-vframes", "1",
        "-vf", f"scale={THUMB_WIDTH}:-2",
        "-q:v", "5",
        str(thumb),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=30, capture_output=True)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode(errors="ignore")[:300]
        logger.warning("ffmpeg failed for {}: {}", video.name, stderr)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out for {}", video.name)
        return None

    if not thumb.is_file() or thumb.stat().st_size == 0:
        return None
    return thumb
