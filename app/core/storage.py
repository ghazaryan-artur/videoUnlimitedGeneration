"""SQLite storage for jobs + persisted API debug log.

Plain aiosqlite — no ORM, just typed wrappers. Schema is created on first run,
migrations applied idempotently.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from app.core.job import Batch, Job, JobStatus
from app.paths import db_file, output_dir
from app.runway.client import ApiLogEntry
from app.settings import settings


# ── schema ────────────────────────────────────────────────────────────


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id                       TEXT PRIMARY KEY,
        batch_id                 TEXT NOT NULL,
        model_task_type          TEXT NOT NULL,
        prompt                   TEXT NOT NULL,
        duration                 INTEGER NOT NULL,
        aspect_ratio             TEXT NOT NULL,
        resolution               TEXT NOT NULL,
        audio                    INTEGER NOT NULL,
        name                     TEXT,
        output_dir               TEXT,
        runway_task_id           TEXT,
        runway_session_id        TEXT,
        runway_asset_group_id    TEXT,
        status                   TEXT NOT NULL,
        progress_ratio           REAL NOT NULL DEFAULT 0,
        estimated_start_seconds  INTEGER,
        error_reason             TEXT,
        output_path              TEXT,
        file_size_bytes          INTEGER,
        download_bytes_done      INTEGER NOT NULL DEFAULT 0,
        created_at               TEXT NOT NULL,
        submitted_at             TEXT,
        started_at               TEXT,
        completed_at             TEXT,
        downloaded_at            TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_batch     ON jobs(batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status    ON jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_runway_id ON jobs(runway_task_id)",
    """
    CREATE TABLE IF NOT EXISTS batches (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              TEXT NOT NULL,
        method          TEXT NOT NULL,
        url             TEXT NOT NULL,
        status          INTEGER,
        duration_ms     REAL NOT NULL,
        request_body    TEXT,
        response_body   TEXT,
        error           TEXT,
        related_task_id TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_api_log_ts        ON api_log(ts)",
    "CREATE INDEX IF NOT EXISTS idx_api_log_status    ON api_log(status)",
    "CREATE INDEX IF NOT EXISTS idx_api_log_related   ON api_log(related_task_id)",
    """
    CREATE TABLE IF NOT EXISTS authors (
        name        TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS downloaded_files (
        rel_path         TEXT PRIMARY KEY,
        first_downloaded_at TEXT NOT NULL,
        download_count   INTEGER NOT NULL DEFAULT 1
    )
    """,
)


# ── connection ────────────────────────────────────────────────────────


_DB_PATH: Path | None = None


def db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = db_file()
    return _DB_PATH


_MIGRATIONS: tuple[tuple[str, str], ...] = (
    # (column_name, ALTER stmt) — applied if column doesn't exist on jobs.
    ("output_dir", "ALTER TABLE jobs ADD COLUMN output_dir TEXT"),
    ("queue_order", "ALTER TABLE jobs ADD COLUMN queue_order INTEGER NOT NULL DEFAULT 0"),
    ("author", "ALTER TABLE jobs ADD COLUMN author TEXT"),
)


async def _apply_migrations(db: aiosqlite.Connection) -> None:
    cur = await db.execute("PRAGMA table_info(jobs)")
    rows = await cur.fetchall()
    existing_columns = {r[1] for r in rows}  # row[1] is column name
    for column, stmt in _MIGRATIONS:
        if column not in existing_columns:
            logger.info("Applying migration: {}", stmt)
            await db.execute(stmt)


async def _import_legacy_download_marks(db: aiosqlite.Connection) -> None:
    """Carry over "downloaded" marks written by the older implementation,
    which stamped `jobs.user_downloaded_at` (keyed by absolute output_path)
    instead of the `downloaded_files` table. INSERT OR IGNORE — marks are
    permanent, so re-running on every startup is harmless."""
    cur = await db.execute("PRAGMA table_info(jobs)")
    if "user_downloaded_at" not in {r[1] for r in await cur.fetchall()}:
        return
    cur = await db.execute(
        "SELECT output_path, user_downloaded_at FROM jobs "
        "WHERE user_downloaded_at IS NOT NULL AND output_path IS NOT NULL"
    )
    rows = await cur.fetchall()
    if not rows:
        return
    roots = {output_dir(), output_dir().resolve()}
    for output_path, stamped_at in rows:
        rel = None
        for root in roots:
            for candidate in (Path(output_path), Path(output_path).resolve()):
                try:
                    rel = candidate.relative_to(root).as_posix()
                    break
                except ValueError:
                    continue
            if rel:
                break
        if rel:
            await db.execute(
                "INSERT OR IGNORE INTO downloaded_files (rel_path, first_downloaded_at) "
                "VALUES (?, ?)",
                (rel, stamped_at),
            )


async def init_db() -> None:
    """Create schema if missing — safe to call every startup."""
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(p)) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA foreign_keys = ON")
        for stmt in SCHEMA_STATEMENTS:
            await db.execute(stmt)
        await _apply_migrations(db)
        await _import_legacy_download_marks(db)
        await db.commit()
    logger.info("DB ready at {}", p)


@asynccontextmanager
async def connection() -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(str(db_path())) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


# ── (de)serialization helpers ─────────────────────────────────────────


def _dt_to_str(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _str_to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _job_to_row(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "batch_id": job.batch_id,
        "model_task_type": job.model_task_type,
        "prompt": job.prompt,
        "duration": job.duration,
        "aspect_ratio": job.aspect_ratio,
        "resolution": job.resolution,
        "audio": int(job.audio),
        "name": job.name,
        "output_dir": job.output_dir,
        "author": job.author,
        "runway_task_id": job.runway_task_id,
        "runway_session_id": job.runway_session_id,
        "runway_asset_group_id": job.runway_asset_group_id,
        "status": job.status.value,
        "progress_ratio": job.progress_ratio,
        "estimated_start_seconds": job.estimated_start_seconds,
        "error_reason": job.error_reason,
        "queue_order": job.queue_order,
        "output_path": job.output_path,
        "file_size_bytes": job.file_size_bytes,
        "download_bytes_done": job.download_bytes_done,
        "created_at": _dt_to_str(job.created_at),
        "submitted_at": _dt_to_str(job.submitted_at),
        "started_at": _dt_to_str(job.started_at),
        "completed_at": _dt_to_str(job.completed_at),
        "downloaded_at": _dt_to_str(job.downloaded_at),
    }


def _row_to_job(row: aiosqlite.Row) -> Job:
    d = dict(row)
    d["audio"] = bool(d["audio"])
    d["status"] = JobStatus(d["status"])
    for k in ("created_at", "submitted_at", "started_at", "completed_at", "downloaded_at"):
        d[k] = _str_to_dt(d.get(k))
    return Job(**d)


# ── jobs CRUD ─────────────────────────────────────────────────────────


_INSERT_JOB_SQL = """
INSERT INTO jobs (
    id, batch_id, model_task_type, prompt, duration, aspect_ratio, resolution,
    audio, name, output_dir, author, runway_task_id, runway_session_id, runway_asset_group_id,
    status, progress_ratio, estimated_start_seconds, error_reason, queue_order,
    output_path, file_size_bytes, download_bytes_done,
    created_at, submitted_at, started_at, completed_at, downloaded_at
) VALUES (
    :id, :batch_id, :model_task_type, :prompt, :duration, :aspect_ratio, :resolution,
    :audio, :name, :output_dir, :author, :runway_task_id, :runway_session_id, :runway_asset_group_id,
    :status, :progress_ratio, :estimated_start_seconds, :error_reason, :queue_order,
    :output_path, :file_size_bytes, :download_bytes_done,
    :created_at, :submitted_at, :started_at, :completed_at, :downloaded_at
)
"""

_UPDATE_JOB_SQL = """
UPDATE jobs SET
    batch_id = :batch_id,
    model_task_type = :model_task_type,
    prompt = :prompt,
    duration = :duration,
    aspect_ratio = :aspect_ratio,
    resolution = :resolution,
    audio = :audio,
    name = :name,
    output_dir = :output_dir,
    author = :author,
    runway_task_id = :runway_task_id,
    runway_session_id = :runway_session_id,
    runway_asset_group_id = :runway_asset_group_id,
    status = :status,
    progress_ratio = :progress_ratio,
    estimated_start_seconds = :estimated_start_seconds,
    error_reason = :error_reason,
    queue_order = :queue_order,
    output_path = :output_path,
    file_size_bytes = :file_size_bytes,
    download_bytes_done = :download_bytes_done,
    submitted_at = :submitted_at,
    started_at = :started_at,
    completed_at = :completed_at,
    downloaded_at = :downloaded_at
WHERE id = :id
"""


async def insert_job(job: Job) -> None:
    async with connection() as db:
        await db.execute(_INSERT_JOB_SQL, _job_to_row(job))
        await db.commit()


async def upsert_job(job: Job) -> None:
    async with connection() as db:
        cur = await db.execute(_UPDATE_JOB_SQL, _job_to_row(job))
        if cur.rowcount == 0:
            await db.execute(_INSERT_JOB_SQL, _job_to_row(job))
        await db.commit()


async def load_job(job_id: str) -> Job | None:
    async with connection() as db:
        cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
    return _row_to_job(row) if row else None


async def list_jobs_by_batch(batch_id: str) -> list[Job]:
    async with connection() as db:
        cur = await db.execute(
            "SELECT * FROM jobs WHERE batch_id = ? ORDER BY created_at",
            (batch_id,),
        )
        rows = await cur.fetchall()
    return [_row_to_job(r) for r in rows]


async def list_unfinished_jobs() -> list[Job]:
    """Used at startup for crash recovery."""
    placeholders = ",".join("?" * 5)
    statuses = (
        JobStatus.PENDING.value,
        JobStatus.SUBMITTING.value,
        JobStatus.QUEUED.value,
        JobStatus.GENERATING.value,
        JobStatus.DOWNLOADING.value,
    )
    async with connection() as db:
        cur = await db.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) "
            "ORDER BY queue_order, created_at",
            statuses,
        )
        rows = await cur.fetchall()
    return [_row_to_job(r) for r in rows]


async def list_recent_jobs(limit: int = 200) -> list[Job]:
    """All jobs ever recorded, newest first — for History view."""
    async with connection() as db:
        cur = await db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    return [_row_to_job(r) for r in rows]


async def find_job_by_runway_id(runway_task_id: str) -> Job | None:
    async with connection() as db:
        cur = await db.execute(
            "SELECT * FROM jobs WHERE runway_task_id = ? LIMIT 1",
            (runway_task_id,),
        )
        row = await cur.fetchone()
    return _row_to_job(row) if row else None


async def delete_terminal_jobs() -> int:
    """Wipe DONE / FAILED / CANCELLED jobs. Returns rows removed."""
    async with connection() as db:
        cur = await db.execute(
            "DELETE FROM jobs WHERE status IN (?, ?, ?)",
            (JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value),
        )
        await db.commit()
    return cur.rowcount


async def delete_job(job_id: str) -> bool:
    async with connection() as db:
        cur = await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await db.commit()
    return cur.rowcount > 0


# ── downloaded-files tracking ────────────────────────────────────────
# Records that a viewer actually fetched a finished video through the
# /downloads/<file> (or /downloads-zip/<folder>/) route — as opposed to
# `Job.downloaded_at`, which marks when *the app itself* pulled the file
# down from Runway onto the server. Used by the Downloads view to tint a
# video's card green once it has been saved at least once, by anyone.
#
# Marks are permanent by design: keyed by file path (not by job row), so
# "Clear completed", deleting a job, or deleting the file never un-marks
# a video. There is intentionally no delete function.


async def mark_file_downloaded(rel_path: str) -> None:
    async with connection() as db:
        await db.execute(
            """
            INSERT INTO downloaded_files (rel_path, first_downloaded_at, download_count)
            VALUES (?, ?, 1)
            ON CONFLICT(rel_path) DO UPDATE SET
                download_count = download_count + 1
            """,
            (rel_path, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def list_downloaded_rel_paths() -> set[str]:
    async with connection() as db:
        cur = await db.execute("SELECT rel_path FROM downloaded_files")
        rows = await cur.fetchall()
    return {r[0] for r in rows}



# ── batches ──────────────────────────────────────────────────────────


async def insert_batch(batch: Batch) -> None:
    async with connection() as db:
        await db.execute(
            "INSERT INTO batches (id, name, created_at) VALUES (?, ?, ?)",
            (batch.id, batch.name, batch.created_at.isoformat()),
        )
        await db.commit()


# ── api log ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StoredApiLog:
    id: int
    ts: str
    method: str
    url: str
    status: int | None
    duration_ms: float
    request_body: str | None
    response_body: str | None
    error: str | None
    related_task_id: str | None


async def insert_api_log(entry: ApiLogEntry, *, related_task_id: str | None = None) -> None:
    """Used as a sink subscribed to RunwayClient — writes every API call."""
    async with connection() as db:
        await db.execute(
            """
            INSERT INTO api_log
                (ts, method, url, status, duration_ms, request_body, response_body, error, related_task_id)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                entry.ts,
                entry.method,
                entry.url,
                entry.status,
                entry.duration_ms,
                entry.request_body,
                entry.response_body,
                entry.error,
                related_task_id,
            ),
        )
        await db.commit()


def make_db_log_sink(related_task_id_provider=lambda url: None):
    """Returns a sink callable suitable for RunwayClient(sinks=[...]).

    `related_task_id_provider(url)` lets us extract a task_id from URLs that
    contain it (e.g. /v1/tasks/<id>) so the UI can filter logs per job.
    """

    async def _sink(entry: ApiLogEntry) -> None:
        try:
            await insert_api_log(entry, related_task_id=related_task_id_provider(entry.url))
        except Exception as e:  # noqa: BLE001
            logger.warning("DB sink failed: {}", e)

    return _sink


async def recent_api_logs(limit: int = 500, related_task_id: str | None = None) -> list[StoredApiLog]:
    sql = "SELECT * FROM api_log "
    args: list[Any] = []
    if related_task_id:
        sql += "WHERE related_task_id = ? "
        args.append(related_task_id)
    sql += "ORDER BY id DESC LIMIT ?"
    args.append(limit)

    async with connection() as db:
        cur = await db.execute(sql, args)
        rows = await cur.fetchall()
    rows.reverse()  # oldest-first for UI
    return [StoredApiLog(**dict(r)) for r in rows]


async def purge_old_api_logs(days: int | None = None) -> int:
    """Delete api_log rows older than retention. Returns rows removed."""
    days = days if days is not None else settings.api_log_retention_days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with connection() as db:
        cur = await db.execute("DELETE FROM api_log WHERE ts < ?", (cutoff,))
        await db.commit()
    return cur.rowcount


# ── helpers ──────────────────────────────────────────────────────────


def task_id_from_url(url: str) -> str | None:
    """Extract a task UUID from URLs like /v1/tasks/<uuid>(/...)?"""
    import re
    m = re.search(r"/v1/tasks/([0-9a-f-]{36})", url)
    return m.group(1) if m else None
