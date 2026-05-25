"""HTTP server for the hosted (web) build.

Wraps the Flet app inside a FastAPI app so we can own the HTTP layer:
the `/downloads/<filename>` route streams finished mp4s with
`Content-Disposition: attachment`, which makes browsers fire a Save dialog
instead of playing the video inline. Everything else falls through to Flet.

One process, one port — no nginx, no env vars, no extra system services.
"""
from __future__ import annotations

from typing import Callable

import flet as ft
import flet.fastapi as flet_fastapi
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from loguru import logger

from app.paths import output_dir


SessionHandler = Callable[[ft.Page], "object"]


def _safe_download_path(filename: str):
    """Return an existing regular file inside output_dir() — or raise 404/400.

    Defends against path traversal: only bare filenames (no slashes, no `..`)
    are accepted, and we re-check that the resolved path stays inside the
    downloads root.
    """
    if not filename or any(c in filename for c in ("/", "\\", "\x00")):
        raise HTTPException(status_code=400, detail="bad filename")
    if filename in (".", "..") or filename.startswith("."):
        raise HTTPException(status_code=400, detail="bad filename")

    root = output_dir().resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad filename") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return path


def build_app(target: SessionHandler) -> FastAPI:
    api = FastAPI(title="RunwayAutomation")

    @api.get("/downloads/{filename}")
    async def download(filename: str) -> FileResponse:
        path = _safe_download_path(filename)
        # media_type=octet-stream + filename= → Content-Disposition: attachment;
        # filename="...". Forces the browser to download rather than play inline.
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=path.name,
        )

    @api.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Mount Flet last so our explicit routes win over its catch-all.
    api.mount("/", flet_fastapi.app(target))
    return api


def run_web(target: SessionHandler, *, host: str = "0.0.0.0", port: int = 8551) -> None:
    """Block forever serving the hosted app on `host:port`."""
    logger.info("Starting web server on {}:{}", host, port)
    uvicorn.run(build_app(target), host=host, port=port, log_level="info")
