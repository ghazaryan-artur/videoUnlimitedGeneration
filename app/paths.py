"""Filesystem paths.

In dev: everything sits next to the source.
In a packaged build: data goes under %LOCALAPPDATA%\\RunwayAutomation\\.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def project_root() -> Path:
    """Source root in dev, install root in frozen builds."""
    if _is_frozen():
        # PyInstaller --onedir: sys.executable lives in the install folder
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def user_data_root() -> Path:
    """Per-user writable storage. Roams across reinstalls."""
    if _is_frozen():
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        path = base / "RunwayAutomation"
    else:
        path = project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def jwt_file() -> Path:
    return user_data_root() / "jwt.json"


def db_file() -> Path:
    return user_data_root() / "jobs.db"


def license_file() -> Path:
    return user_data_root() / "license.lic"


def logs_dir() -> Path:
    p = user_data_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_web_mode() -> bool:
    """True when running as a hosted web app.

    Resolution order:
      1. app._build_mode.MODE  — written by scripts/build_server.py into the
         tarball so the deployed bundle needs zero env config.
      2. RUNWAY_UI_MODE env var — manual override for ad-hoc dev/test.
      3. default "desktop".
    """
    try:
        from app import _build_mode  # type: ignore[attr-defined]
        return str(getattr(_build_mode, "MODE", "")).lower() == "web"
    except ImportError:
        pass
    return os.environ.get("RUNWAY_UI_MODE", "desktop").lower() == "web"


def output_dir() -> Path:
    """Where finished mp4s are saved by default."""
    if is_web_mode():
        # Hosted deployment: a flat folder served by app/ui/server.py via the
        # /downloads/<name>.mp4 FastAPI route.
        videos = project_root() / "downloads"
    elif _is_frozen():
        # User-friendly: into Videos folder
        videos = Path.home() / "Videos" / "RunwayAutomation"
    else:
        videos = project_root() / "output"
    videos.mkdir(parents=True, exist_ok=True)
    return videos


def resolve_output_dir(user_path: str | None) -> Path:
    """Resolve a user-supplied destination folder safely.

    In WEB mode, the result is guaranteed to live inside output_dir()
    (the downloads root). The user's input is treated as a path relative
    to that root; absolute paths, parent-references (..), and Windows
    drive letters are stripped before joining, so there is no way to
    escape upward. An empty/blank input returns the root itself.

    In DESKTOP mode the user's input is used as-is — they own the host
    and can write wherever they like.
    """
    root = output_dir()
    if not user_path or not user_path.strip():
        return root

    raw = user_path.strip()

    if not is_web_mode():
        return Path(raw)

    # Web: keep only "safe" path segments (drop "..", absolute roots,
    # drive letters), then resolve and double-check containment.
    parts = []
    for seg in Path(raw).parts:
        if seg in ("", "/", "\\", "..", "."):
            continue
        # Windows drive letters arrive as "C:\\"; skip them
        if len(seg) >= 2 and seg[1] == ":":
            continue
        parts.append(seg)
    relative = Path(*parts) if parts else Path()

    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return root_resolved
    return candidate


def browser_profile_dir() -> Path:
    """Persistent Chromium profile for the browser-login fallback."""
    p = user_data_root() / "browser_profile"
    p.mkdir(parents=True, exist_ok=True)
    return p
