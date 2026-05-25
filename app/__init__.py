"""App package init — runs before any submodule import.

Two side-effects matter here:
  1. Force UTF-8 stdio on Windows so rich/loguru never trips on cp1252.
  2. When running from a PyInstaller bundle, point Playwright at the
     ms-playwright/ folder we copied next to the .exe — otherwise it
     fails with "Looks like Playwright was just installed or updated.
     Please run: playwright install".
"""
import os
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

if getattr(sys, "frozen", False):
    _bundled = Path(sys.executable).parent / "ms-playwright"
    if _bundled.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_bundled))
