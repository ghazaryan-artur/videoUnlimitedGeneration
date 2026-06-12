"""HTTP server for the hosted (web) build.

Wraps the Flet app inside a FastAPI app so we can own the HTTP layer:
the `/downloads/<filename>` route streams finished mp4s with
`Content-Disposition: attachment`, which makes browsers fire a Save dialog
instead of playing the video inline. Everything else falls through to Flet.

One process, one port — no nginx, no env vars, no extra system services.

The FastAPI lifespan also brings up `app.core.runtime` once at process
start, so the BatchRunner + RunwayClient + active-jobs map live for the
whole process — not just for the duration of a single browser session.
"""
from __future__ import annotations

import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

import flet as ft
import flet.fastapi as flet_fastapi
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from loguru import logger

from app.core.runtime import runtime
from app.paths import output_dir


SessionHandler = Callable[[ft.Page], "object"]


def _safe_download_path(rel_path: str):
    """Return an existing regular file inside output_dir() — or raise 404/400.

    Defends against path traversal. Multi-segment paths separated by `/`
    are accepted (so files in subfolders can be served), but every segment
    is checked against `..`, leading dots, and the resolved absolute path
    is re-verified to stay inside the downloads root.
    """
    if not rel_path:
        raise HTTPException(status_code=400, detail="bad filename")
    if any(c in rel_path for c in ("\\", "\x00")):
        raise HTTPException(status_code=400, detail="bad filename")

    segments = [s for s in rel_path.split("/") if s]
    if not segments:
        raise HTTPException(status_code=400, detail="bad filename")
    for seg in segments:
        if seg in (".", "..") or seg.startswith("."):
            raise HTTPException(status_code=400, detail="bad filename")

    root = output_dir().resolve()
    path = root.joinpath(*segments).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad filename") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return path


def _safe_directory_path(rel_path: str) -> Path:
    """Same checks as _safe_download_path but resolves to a directory.

    Used by the /downloads-zip/<rel>/ endpoint to bundle a whole folder.
    """
    if not rel_path:
        raise HTTPException(status_code=400, detail="bad path")
    if any(c in rel_path for c in ("\\", "\x00")):
        raise HTTPException(status_code=400, detail="bad path")

    segments = [s for s in rel_path.split("/") if s]
    if not segments:
        raise HTTPException(status_code=400, detail="bad path")
    for seg in segments:
        if seg in (".", "..") or seg.startswith("."):
            raise HTTPException(status_code=400, detail="bad path")

    root = output_dir().resolve()
    path = root.joinpath(*segments).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad path") from None
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="folder not found")
    return path


def _build_folder_zip(folder: Path) -> Path:
    """Bundle every (non-hidden, non-.part) file under `folder` into a new
    temp .zip and return its path.

    Stored without compression (ZIP_STORED) — mp4s are already compressed,
    so deflate would burn CPU for no size win. arcname is relative to
    `folder`, so a download of downloads/Anna/cats/ produces a zip whose
    top-level contains cats/ contents (no Anna/ prefix in the archive).
    Caller is responsible for unlinking the returned file.
    """
    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="runway_bundle_")
    import os
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for sub in sorted(folder.rglob("*")):
                if not sub.is_file():
                    continue
                if sub.name.startswith(".") or sub.suffix == ".part":
                    continue
                if any(p.startswith(".") for p in sub.relative_to(folder).parts):
                    continue
                zf.write(sub, arcname=sub.relative_to(folder).as_posix())
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Process-level startup / shutdown for the shared runtime.

    On startup: load the stored JWT (if any), build the BatchRunner and
    HTTP client, resume unfinished jobs from the DB. On shutdown: close
    the runner + client cleanly.
    """
    await runtime.startup()
    try:
        yield
    finally:
        await runtime.shutdown()


def build_app(target: SessionHandler) -> FastAPI:
    api = FastAPI(title="RunwayAutomation", lifespan=_lifespan)

    @api.middleware("http")
    async def downloads_intercept(request: Request, call_next):
        """Serve /downloads/<file> BEFORE routing reaches Flet's mount.

        Plain FastAPI routes don't work here: `api.mount("/", flet_fastapi.app(...))`
        is a Mount whose Starlette matcher claims every path, and the mounted
        Flet app responds to anything it doesn't know with its SPA index.html
        — including `/downloads/<file>`. Middleware runs ahead of the router,
        so this short-circuits the request before Flet sees it.
        """
        path = request.url.path
        if path.startswith("/downloads-zip/") and len(path) > len("/downloads-zip/"):
            # Bundle an entire folder into a zip. Used by the "Download all"
            # button on a folder card. We use a temp file (not in-memory) so
            # large bundles don't pin the whole archive in RAM, and clean
            # it up via a background task after the response finishes.
            rel = path[len("/downloads-zip/"):].rstrip("/")
            try:
                folder = _safe_directory_path(rel)
            except HTTPException as e:
                return PlainTextResponse(
                    str(e.detail or ""), status_code=e.status_code,
                )
            try:
                zip_path = _build_folder_zip(folder)
            except Exception as e:  # noqa: BLE001
                logger.warning("zip build failed for {}: {}", folder, e)
                return PlainTextResponse(
                    "could not build archive", status_code=500,
                )
            from starlette.background import BackgroundTask

            def _cleanup(p: Path) -> None:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

            return FileResponse(
                zip_path,
                media_type="application/zip",
                filename=f"{folder.name}.zip",
                background=BackgroundTask(_cleanup, zip_path),
            )
        if path.startswith("/downloads/") and len(path) > len("/downloads/"):
            # ASGI scope path is already URL-decoded by Starlette, so spaces
            # arrive as ' ' (not %20).
            filename = path[len("/downloads/"):]
            try:
                file_path = _safe_download_path(filename)
            except HTTPException as e:
                return PlainTextResponse(
                    str(e.detail or ""), status_code=e.status_code,
                )
            return FileResponse(
                file_path,
                media_type="application/octet-stream",
                filename=file_path.name,
            )
        if path.startswith("/thumbs/") and len(path) > len("/thumbs/"):
            # /thumbs/<video_name>.jpg — first frame of <video_name>, generated
            # lazily by ffmpeg and cached to <output_dir>/.thumbs/.
            thumb_filename = path[len("/thumbs/"):]
            if not thumb_filename.endswith(".jpg"):
                return PlainTextResponse("bad filename", status_code=400)
            video_filename = thumb_filename[:-4]
            try:
                video_path = _safe_download_path(video_filename)
            except HTTPException as e:
                return PlainTextResponse(
                    str(e.detail or ""), status_code=e.status_code,
                )
            from app.core.thumbs import ensure_thumb
            thumb = ensure_thumb(video_path)
            if thumb is None or not thumb.exists():
                return PlainTextResponse("no thumbnail", status_code=404)
            return FileResponse(thumb, media_type="image/jpeg")
        if path == "/healthz":
            return Response(content='{"status":"ok"}',
                            media_type="application/json")
        return await call_next(request)

    # Flet owns everything else.
    api.mount("/", flet_fastapi.app(target))
    return api


def run_web(target: SessionHandler, *, host: str = "0.0.0.0", port: int = 8551) -> None:
    """Block forever serving the hosted app on `host:port`."""
    logger.info("Starting web server on {}:{}", host, port)
    uvicorn.run(build_app(target), host=host, port=port, log_level="info")
