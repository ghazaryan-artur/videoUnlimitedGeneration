"""BatchRunner — persistent worker pool that turns Jobs into downloaded videos.

Concurrency model
-----------------
N worker tasks (default 2 to mirror Runway relax-mode capacity) pull from a
shared `asyncio.Queue` and run jobs through their full lifecycle. Workers
live for the entire app session — you can `add_jobs(...)` at any time, even
while previous jobs are still running.

On 429
------
Workers do their own patient retry: 30s → 60s → 180s → 240s → 300s and
then 300s indefinitely. The job stays in QUEUED state and never silently
fails just because Runway is at capacity.

Crash recovery
--------------
`resume_jobs()` accepts the leftover non-terminal jobs from disk and feeds
them into the same queue. `_run_one` branches on whether `runway_task_id`
is set: fresh jobs go through submit → poll → download; resumed jobs skip
submit and pick up at polling (or directly at download).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.core import storage
from app.core.job import Batch, Job, JobStatus
from app.paths import output_dir
from app.runway.client import ApiLogEntry, RunwayApiError, RunwayClient
from app.runway.models import by_task_type
from app.runway.tasks import (
    SessionContext,
    download_artifact,
    ensure_session,
    get_task_status,
    poll_task,
    register_task_in_ui,
    submit_task,
)
from app.settings import settings


JobUpdateCallback = Callable[[Job], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── small helpers ────────────────────────────────────────────────────


def _backoff_for_attempt(attempt: int) -> float:
    """Patient backoff: 30, 60, 120, 180, 240, then 300 forever (5min ceiling)."""
    if attempt == 0:
        return 30.0
    if attempt == 1:
        return 60.0
    return float(min(60 * (attempt + 1), 300))


async def _wait_for_429_capacity(attempt: int) -> None:
    delay = _backoff_for_attempt(attempt)
    logger.info("Runway 429 — waiting {:.0f}s before retrying (attempt {})", delay, attempt + 1)
    await asyncio.sleep(delay)


# ── BatchRunner ──────────────────────────────────────────────────────


class BatchRunner:
    """Persistent worker pool. Add jobs anytime via `add_jobs()`."""

    def __init__(
        self,
        client: RunwayClient,
        *,
        max_concurrent: int | None = None,
        on_update: JobUpdateCallback | None = None,
    ) -> None:
        self._client = client
        self._max_concurrent = max_concurrent or settings.max_concurrent_jobs
        self._on_update = on_update

        self._queue: asyncio.Queue[Job | None] = asyncio.Queue()
        self._workers: list[asyncio.Task[Any]] = []
        self._inflight: dict[str, asyncio.Task[Any]] = {}   # job.id → task running it
        self._cancel_event = asyncio.Event()           # soft shutdown — workers exit, no CANCELLED marking
        self._explicit_cancel = False                  # user clicked Cancel — mark jobs CANCELLED
        self._started = False
        # Loop is captured LATER, in ensure_started(), where we are guaranteed
        # to be running on the same loop that owns the workers + the queue.
        # Capturing in __init__ is unsafe — Flet may construct objects from a
        # context where get_running_loop() raises or returns a different loop
        # than the one workers will run on.
        self._loop: asyncio.AbstractEventLoop | None = None

        # Lazily created when the first fresh submission needs it
        self._session_ctx: SessionContext | None = None
        self._artifacts_cache: dict[str, list[dict[str, Any]]] = {}

        # All jobs we've ever taken responsibility for (for is_idle check)
        self._known_ids: set[str] = set()

    # ── public API ───────────────────────────────────────────────────

    async def ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        # Capture the loop here — workers + queue + cancel_* all share it.
        self._loop = asyncio.get_running_loop()
        # If a previous aclose() set the cancel event, clear it so newly
        # spawned workers don't exit immediately.
        self._cancel_event.clear()
        self._explicit_cancel = False
        for i in range(self._max_concurrent):
            t = asyncio.create_task(self._worker_loop(i), name=f"runway-worker-{i}")
            self._workers.append(t)
        logger.info("BatchRunner started with {} workers", self._max_concurrent)

    async def add_jobs(
        self,
        jobs: list[Job],
        *,
        batch_name: str = "",
        is_resume: bool = False,
    ) -> None:
        """Push a list of jobs into the queue. Workers pick them up as they free up.

        Returns immediately — does NOT wait for jobs to finish.
        """
        if not jobs:
            return
        await self.ensure_started()

        if not is_resume:
            batch = Batch(name=batch_name or f"batch_{datetime.now(timezone.utc).isoformat()}",
                          job_ids=[j.id for j in jobs])
            await storage.insert_batch(batch)
            for j in jobs:
                j.batch_id = batch.id
                await storage.upsert_job(j)
                self._notify(j)
        else:
            # Resume: fix up half-states and just notify the UI
            for j in jobs:
                if j.status == JobStatus.SUBMITTING and j.runway_task_id is None:
                    j.transition_to(JobStatus.PENDING)
                    await storage.upsert_job(j)
                self._notify(j)

        for j in jobs:
            if j.id in self._known_ids:
                # Avoid double-queuing — happens if resume races with explicit add
                continue
            self._known_ids.add(j.id)
            await self._queue.put(j)

    # Backward-compat aliases — keep run_batch / resume_jobs wrappers
    async def run_batch(self, jobs: list[Job], *, batch_name: str = "") -> None:
        await self.add_jobs(jobs, batch_name=batch_name, is_resume=False)

    async def resume_jobs(self, jobs: list[Job]) -> None:
        await self.add_jobs(jobs, is_resume=True)

    @property
    def is_idle(self) -> bool:
        return self._queue.empty() and not self._inflight

    def cancel_all(self) -> None:
        """Explicit user cancel — mark every job CANCELLED.

        Safe to call from any thread; the actual work is dispatched onto the
        runner's event loop. Flet invokes sync `on_click` handlers in a
        thread-pool worker, so we cannot touch asyncio primitives directly.
        """
        if self._loop is None:
            # Workers never started — nothing to cancel
            return
        self._loop.call_soon_threadsafe(self._do_cancel_all)

    def _do_cancel_all(self) -> None:
        self._explicit_cancel = True
        self._cancel_event.set()
        # Drain queued (un-started) jobs and mark them cancelled.
        while True:
            try:
                queued = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if queued is not None:
                asyncio.create_task(self._mark_cancelled(queued))
        # Cancel in-flight worker tasks; their CancelledError handler marks
        # the job CANCELLED because _explicit_cancel is set.
        for t in self._inflight.values():
            if not t.done():
                t.cancel()

    def cancel_one(self, job_id: str) -> bool:
        """Cancel a single in-flight job. Safe to call from any thread.

        Returns True if a cancel was scheduled, False if the job is not
        currently in-flight (already finished, or still queued).
        """
        if self._loop is None or job_id not in self._inflight:
            return False
        self._loop.call_soon_threadsafe(self._do_cancel_one, job_id)
        return True

    def _do_cancel_one(self, job_id: str) -> None:
        self._explicit_cancel = True  # so the about-to-fire CancelledError marks CANCELLED
        t = self._inflight.get(job_id)
        if t is not None and not t.done():
            t.cancel()

    async def aclose(self) -> None:
        """Graceful shutdown — used by AppState.teardown.

        Importantly we DO NOT mark in-flight jobs CANCELLED here. They stay
        in their current QUEUED/GENERATING/DOWNLOADING state in the DB so the
        next session resumes them. This makes Flet web page-reloads
        non-destructive.
        """
        self._cancel_event.set()  # _explicit_cancel stays False — soft mode
        # Drain any queued (un-started) jobs back to PENDING (no-op since DB
        # already has them PENDING).
        # Send sentinels
        for _ in self._workers:
            with suppress(asyncio.QueueFull):
                self._queue.put_nowait(None)
        for w in self._workers:
            try:
                await asyncio.wait_for(w, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                w.cancel()
        self._workers.clear()
        self._started = False

    # ── worker loop ──────────────────────────────────────────────────

    async def _worker_loop(self, worker_index: int) -> None:
        while not self._cancel_event.is_set():
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if job is None:
                # sentinel
                return
            current = asyncio.current_task()
            if current is not None:
                self._inflight[job.id] = current
            try:
                await self._run_one(job)
            except asyncio.CancelledError:
                # _run_one handled marking based on explicit vs soft mode
                raise
            except Exception:  # noqa: BLE001
                logger.exception("worker {} crashed on job {}", worker_index, job.id)
            finally:
                self._inflight.pop(job.id, None)

    # ── per-job lifecycle ────────────────────────────────────────────

    async def _run_one(self, job: Job) -> None:
        if self._cancel_event.is_set():
            await self._mark_cancelled(job)
            return
        try:
            # Branch: fresh submission vs. resume
            if job.runway_task_id is None:
                # Fresh: submit → poll → download
                await self._submit_with_retry(job)
                # _submit_with_retry can exit without raising if it failed
                # (FAILED) or was cancelled mid-submit (CANCELLED, no task id).
                # Either way, there's nothing left to poll.
                if job.runway_task_id is None:
                    return
                await self._poll_until_terminal(job)
                if job.status != JobStatus.DOWNLOADING:
                    return
                await self._download(job)
            else:
                # Resume — task already exists server-side
                if job.status in (
                    JobStatus.QUEUED,
                    JobStatus.GENERATING,
                    JobStatus.SUBMITTING,
                ):
                    await self._poll_until_terminal(job)
                    if job.status != JobStatus.DOWNLOADING:
                        return
                    await self._download(job)
                elif job.status == JobStatus.DOWNLOADING:
                    # Crashed mid-download. Refetch artifact URL and stream again.
                    try:
                        status = await get_task_status(self._client, job.runway_task_id)
                    except Exception as e:  # noqa: BLE001
                        await self._fail(job, f"resume status fetch failed: {e}")
                        return
                    if status.is_success and status.artifacts:
                        self._artifacts_cache[job.id] = status.artifacts
                        await self._download(job)
                    elif status.status == "FAILED":
                        await self._fail(job, status.error or "task failed")
                    else:
                        # Server might still be generating despite our DOWNLOADING flag
                        job.transition_to(JobStatus.GENERATING)
                        await storage.upsert_job(job)
                        self._notify(job)
                        await self._poll_until_terminal(job)
                        if job.status == JobStatus.DOWNLOADING:
                            await self._download(job)
                else:
                    logger.warning("Unexpected resume state for job {}: {}", job.id, job.status)
        except asyncio.CancelledError:
            if self._explicit_cancel:
                with suppress(Exception):
                    await self._mark_cancelled(job)
            # else: soft shutdown — leave the job in its current state for resume
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Unexpected error on job {}: {}", job.id, e)
            await self._fail(job, f"internal: {type(e).__name__}: {e}")

    async def _submit_with_retry(self, job: Job) -> None:
        # Lazily create session ctx on first fresh submission (resume jobs skip this).
        if self._session_ctx is None:
            self._session_ctx = await ensure_session(self._client)
        profile = by_task_type(job.model_task_type)
        job.transition_to(JobStatus.SUBMITTING, submitted_at=_now())
        await storage.upsert_job(job)
        self._notify(job)

        attempt = 0
        while True:
            if self._cancel_event.is_set():
                await self._mark_cancelled(job)
                return
            try:
                runway_task_id = await submit_task(
                    self._client,
                    ctx=self._session_ctx,
                    profile=profile,
                    name=job.display_name,
                    prompt=job.prompt,
                    duration=job.duration,
                    aspect_ratio=job.aspect_ratio,
                    resolution=job.resolution,
                    audio=job.audio,
                )
            except RunwayApiError as e:
                if e.status == 429:
                    # Reflect "waiting for capacity" in the UI
                    if job.status != JobStatus.QUEUED:
                        job.transition_to(
                            JobStatus.QUEUED,
                            error_reason=None,
                            estimated_start_seconds=int(_backoff_for_attempt(attempt)),
                        )
                        await storage.upsert_job(job)
                        self._notify(job)
                    await _wait_for_429_capacity(attempt)
                    attempt += 1
                    continue   # retry forever — Runway will free a slot eventually
                if e.status == 400:
                    # Likely content policy on prompt OR validation issue
                    await self._fail(job, f"rejected by Runway (400): {e.body[:200]}")
                    return
                raise

            # success
            job.transition_to(
                JobStatus.QUEUED,
                runway_task_id=runway_task_id,
                runway_session_id=self._session_ctx.session_id,
                runway_asset_group_id=self._session_ctx.asset_group_id,
            )
            await storage.upsert_job(job)
            self._notify(job)

            # Make the task visible in app.runwayml.com — best-effort
            await register_task_in_ui(
                self._client,
                ctx=self._session_ctx,
                runway_task_id=runway_task_id,
                name=job.display_name,
                prompt=job.prompt,
                duration=job.duration,
                audio=job.audio,
            )
            return

    async def _poll_until_terminal(self, job: Job) -> None:
        assert job.runway_task_id is not None
        async for status in poll_task(self._client, job.runway_task_id):
            if self._cancel_event.is_set():
                return

            # Map Runway status → our JobStatus
            new_status = job.status
            if status.status == "RUNNING":
                if job.status != JobStatus.GENERATING:
                    new_status = JobStatus.GENERATING
                    job.started_at = _now()
            elif status.status == "THROTTLED":
                new_status = JobStatus.QUEUED
            elif status.status == "PENDING":
                new_status = JobStatus.QUEUED
            elif status.status == "SUCCEEDED":
                new_status = JobStatus.DOWNLOADING
                job.completed_at = _now()
            elif status.status == "FAILED":
                await self._fail(job, status.error or "Runway returned FAILED with no error reason")
                return
            elif status.status == "CANCELLED":
                await self._mark_cancelled(job)
                return

            changed = (
                new_status != job.status
                or job.progress_ratio != status.progress_ratio
                or job.estimated_start_seconds != status.estimated_start_seconds
            )
            job.status = new_status
            job.progress_ratio = status.progress_ratio
            job.estimated_start_seconds = status.estimated_start_seconds

            # On terminal-success branch, capture artifacts
            if new_status == JobStatus.DOWNLOADING and status.artifacts:
                # store the first artifact URL via runway_session_id field? no —
                # we re-fetch in download step. just stash via in-memory cache:
                self._artifacts_cache[job.id] = status.artifacts

            if changed:
                await storage.upsert_job(job)
                self._notify(job)

            if new_status in (JobStatus.DOWNLOADING,):
                return

    _artifacts_cache: dict[str, list[dict[str, Any]]] = {}

    async def _download(self, job: Job) -> None:
        artifacts = self._artifacts_cache.pop(job.id, None) or []
        if not artifacts:
            await self._fail(job, "completed but no artifacts to download")
            return
        art = artifacts[0]
        try:
            file_size = int(art.get("fileSize") or 0) or None
        except (TypeError, ValueError):
            file_size = None

        job.file_size_bytes = file_size

        def progress(written: int, total: int | None) -> None:
            job.download_bytes_done = written
            if total:
                job.progress_ratio = min(1.0, written / total)
            self._notify(job)

        # Per-job output dir override (from PromptDraft.output_dir)
        dest_dir = Path(job.output_dir) if job.output_dir else output_dir()
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            await self._fail(job, f"output folder unavailable: {e}")
            return

        try:
            dest: Path = await download_artifact(
                self._client,
                art,
                dest_dir,
                on_progress=progress,
            )
        except Exception as e:  # noqa: BLE001
            await self._fail(job, f"download failed: {type(e).__name__}: {e}")
            return

        job.transition_to(
            JobStatus.DONE,
            output_path=str(dest),
            downloaded_at=_now(),
            progress_ratio=1.0,
        )
        await storage.upsert_job(job)
        self._notify(job)

    # ── terminal helpers ─────────────────────────────────────────────

    async def _fail(self, job: Job, reason: Any) -> None:
        # Defensive: error_reason is a str column, never let dicts/objects through
        if reason is None:
            reason_str = "unknown error"
        elif isinstance(reason, str):
            reason_str = reason
        elif isinstance(reason, dict):
            import json as _json
            reason_str = (
                reason.get("message")
                or reason.get("error")
                or _json.dumps(reason, ensure_ascii=False)[:500]
            )
        else:
            reason_str = str(reason)[:500]
        job.transition_to(JobStatus.FAILED, error_reason=reason_str, completed_at=_now())
        await storage.upsert_job(job)
        self._notify(job)
        logger.warning("Job {} FAILED: {}", job.id, reason_str)

    async def _mark_cancelled(self, job: Job) -> None:
        job.transition_to(JobStatus.CANCELLED, completed_at=_now())
        with suppress(Exception):
            await storage.upsert_job(job)
        self._notify(job)

    def _notify(self, job: Job) -> None:
        if self._on_update is not None:
            with suppress(Exception):
                self._on_update(job)
