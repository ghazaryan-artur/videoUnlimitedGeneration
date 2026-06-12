"""Authors — the shared list of video creators.

Each session picks one author before it can start a batch; that author's
name becomes the first folder under downloads/, so the Downloads view
naturally groups everything by creator.

The list is process-wide (lives in SQLite, visible to every connected
session). Selection is per-session (see AppState.selected_author).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from app.core.storage import connection
from app.core.job import safe_folder_name


def normalize_author(name: str) -> str:
    """Trim whitespace and clamp to a filesystem-safe form.

    We store the visible name as-is (after trim) but the folder name used
    on disk is derived from this via safe_folder_name — so an author
    "Anna / Test" displays as "Anna / Test" but lands in downloads/Anna - Test/.
    """
    return (name or "").strip()


async def list_authors() -> list[str]:
    async with connection() as db:
        cur = await db.execute("SELECT name FROM authors ORDER BY name COLLATE NOCASE")
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def add_author(name: str) -> bool:
    """Insert if absent. Returns True on insert, False if it already existed
    or the name was empty after normalisation."""
    n = normalize_author(name)
    if not n:
        return False
    async with connection() as db:
        try:
            await db.execute(
                "INSERT INTO authors (name, created_at) VALUES (?, ?)",
                (n, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def delete_author(name: str) -> None:
    n = normalize_author(name)
    if not n:
        return
    async with connection() as db:
        await db.execute("DELETE FROM authors WHERE name = ?", (n,))
        await db.commit()


def author_folder_segment(name: str) -> str:
    """The on-disk first-segment for this author's videos."""
    return safe_folder_name(name)
