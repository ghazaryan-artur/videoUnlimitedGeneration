"""Windows toast notifications + system shutdown helper.

Lazy imports — works on non-Windows for development, no-ops if winotify is
unavailable.
"""
from __future__ import annotations

import os
import subprocess
import sys

from loguru import logger


def show_toast(title: str, message: str, *, duration: str = "short") -> None:
    """Best-effort Windows toast. duration: 'short' (~5s) or 'long' (~25s)."""
    if sys.platform != "win32":
        logger.info("[toast] {}: {}", title, message)
        return
    try:
        from winotify import Notification, audio  # type: ignore[import-not-found]
        toast = Notification(
            app_id="Runway Automation",
            title=title,
            msg=message,
            duration=duration,
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception as e:  # noqa: BLE001
        logger.warning("toast failed: {}", e)


def schedule_shutdown(seconds: int) -> bool:
    """Issue Windows `shutdown /s /t <seconds>`. Returns True if scheduled.

    Cancellation: call `cancel_shutdown()`.
    """
    if sys.platform != "win32":
        logger.info("[shutdown] would shutdown in {}s (no-op on non-Windows)", seconds)
        return False
    try:
        subprocess.run(
            ["shutdown", "/s", "/t", str(max(0, int(seconds)))],
            check=True,
            capture_output=True,
        )
        logger.info("Shutdown scheduled in {}s", seconds)
        return True
    except subprocess.CalledProcessError as e:
        logger.warning("shutdown command failed: {} stderr={}", e, e.stderr)
        return False
    except FileNotFoundError:
        logger.warning("shutdown.exe not found")
        return False


def cancel_shutdown() -> bool:
    if sys.platform != "win32":
        return False
    try:
        subprocess.run(["shutdown", "/a"], check=True, capture_output=True)
        logger.info("Shutdown cancelled")
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False
