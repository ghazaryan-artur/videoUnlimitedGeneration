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
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
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
