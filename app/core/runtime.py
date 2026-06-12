"""Process-wide runtime singleton for the hosted (web) build.

In web mode the BatchRunner, RunwayClient, active jobs map and prompt drafts
must outlive any single browser session — otherwise closing the tab would
stop generations mid-flight, and a second visitor would see a different UI
than the first.

This module owns those objects for the lifetime of the uvicorn process.
FastAPI's lifespan hooks call `startup()` once on process start and
`shutdown()` once on process exit; sessions just subscribe / unsubscribe.

The desktop build does not import or call into this module.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from loguru import logger

from app.core import storage
from app.core.job import Job
from app.core.queue import BatchRunner
from app.core.storage import list_unfinished_jobs, make_db_log_sink, task_id_from_url
from app.runway.auth import StoredToken, clear_token, load_token
from app.runway.client import RunwayClient


JobListener = Callable[[Job], None]


class _Runtime:
    """Process-wide state. One instance, accessed via the module-level
    `runtime` singleton."""

    def __init__(self) -> None:
        self.token: StoredToken | None = None
        self.client: RunwayClient | None = None
        self.runner: BatchRunner | None = None

        # Shared across all connected sessions. Drafts are NOT shared —
        # one user's in-progress edit shouldn't appear in another's tab.
        # Per-session drafts live on AppState (see app/ui/state.py).
        self.active_jobs: dict[str, Job] = {}

        # Sessions register on connect, unregister on disconnect. Runner's
        # on_update is wired once to _emit, which fans out to all listeners.
        self._listeners: list[JobListener] = []

        self._started = False
        self._lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Called once by FastAPI lifespan on process start.

        If a JWT is on disk, build the client + runner immediately and
        resume any unfinished jobs from the DB. If there is no token yet,
        wait — `install_login()` will do the same wiring after the first
        successful login.
        """
        async with self._lock:
            if self._started:
                return
            self._started = True

            await storage.init_db()

            self.token = load_token()
            if self.token is None:
                logger.info("runtime.startup: no stored token — waiting for login")
                return

            await self._bring_up_locked()

    async def shutdown(self) -> None:
        """Called once by FastAPI lifespan on process exit."""
        async with self._lock:
            if self.runner is not None:
                with suppress(Exception):
                    await self.runner.aclose()
                self.runner = None
            if self.client is not None:
                with suppress(Exception):
                    await self.client.aclose()
                self.client = None
            self._listeners.clear()
            self._started = False

    async def install_login(self, token: StoredToken) -> None:
        """Called by the login view after a successful sign-in.

        Replaces any previously-loaded token, brings up a fresh client +
        runner, and triggers resume of unfinished jobs.
        """
        async with self._lock:
            # If a runner is already up under a different token, tear it
            # down cleanly before swapping.
            if self.runner is not None:
                with suppress(Exception):
                    await self.runner.aclose()
                self.runner = None
            if self.client is not None:
                with suppress(Exception):
                    await self.client.aclose()
                self.client = None

            self.token = token
            await self._bring_up_locked()

    async def logout(self) -> None:
        """Tear down auth + runner and wipe the JWT from disk.

        On a shared-account web deployment this affects every connected
        session — that's the point of "one shared account".
        """
        async with self._lock:
            if self.runner is not None:
                with suppress(Exception):
                    await self.runner.aclose()
                self.runner = None
            if self.client is not None:
                with suppress(Exception):
                    await self.client.aclose()
                self.client = None
            self.token = None
            self.active_jobs.clear()
            with suppress(Exception):
                clear_token()

    async def _bring_up_locked(self) -> None:
        """Build client + runner from self.token and resume unfinished jobs.

        Must be called with self._lock held.
        """
        assert self.token is not None
        self.client = RunwayClient(token=self.token.token)
        self.client.add_sink(make_db_log_sink(task_id_from_url))
        self.client._db_sink_attached = True  # type: ignore[attr-defined]

        self.runner = BatchRunner(self.client, on_update=self._emit)
        await self.runner.ensure_started()

        try:
            leftovers = await list_unfinished_jobs()
        except Exception as e:  # noqa: BLE001
            logger.warning("runtime.startup: list_unfinished_jobs failed: {}", e)
            leftovers = []

        if leftovers:
            for j in leftovers:
                self.active_jobs[j.id] = j
            try:
                await self.runner.resume_jobs(leftovers)
            except Exception as e:  # noqa: BLE001
                logger.warning("runtime.startup: resume_jobs failed: {}", e)
            try:
                summary = await self.runner.sync_with_runway(leftovers)
                logger.info("runtime.startup: synced with Runway: {}", summary)
            except Exception as e:  # noqa: BLE001
                logger.warning("runtime.startup: sync_with_runway failed: {}", e)

        logger.info(
            "runtime ready  user_id={}  resumed={}",
            self.token.user_id, len(leftovers),
        )

    # ── session pub/sub ──────────────────────────────────────────────

    def subscribe(self, fn: JobListener) -> Callable[[], None]:
        """Register a per-session listener; returns an unsubscribe fn."""
        self._listeners.append(fn)

        def _unsub() -> None:
            with suppress(ValueError):
                self._listeners.remove(fn)

        return _unsub

    def _emit(self, job: Job) -> None:
        """Called by the runner whenever it touches a job.

        Updates the shared active_jobs map first so any session that draws
        from snapshot data sees fresh state, then fans out to every
        connected session's UI callback. Exceptions in one listener must
        not stop the others.
        """
        self.active_jobs[job.id] = job
        for fn in list(self._listeners):
            try:
                fn(job)
            except Exception:  # noqa: BLE001
                logger.exception("runtime listener raised")

    # ── helpers used by AppState facade ──────────────────────────────

    def has_token(self) -> bool:
        return self.token is not None and not self.token.is_expired

    def ensure_client(self) -> bool:
        """Best-effort: if the client got dropped (shouldn't happen in web,
        but mirrored from AppState.ensure_client for symmetry), recreate
        it from self.token. Returns False only when there is no token."""
        if self.client is not None and not self.client.is_closed:
            return True
        if self.token is None:
            return False
        self.client = RunwayClient(token=self.token.token)
        self.client.add_sink(make_db_log_sink(task_id_from_url))
        self.client._db_sink_attached = True  # type: ignore[attr-defined]
        return True


runtime = _Runtime()
