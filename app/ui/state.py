"""AppState — the per-session object every view reads from / writes to.

In desktop mode, AppState owns its own token, RunwayClient, BatchRunner,
active_jobs map and drafts list — exactly as before. Behaviour is
unchanged.

In web mode, those same attributes are exposed as properties that delegate
to the process-wide `runtime` singleton (see app/core/runtime.py). That
way a closed browser tab does not tear down the worker pool, and two
visitors see the same queue. Views continue to read `state.runner`,
`state.active_jobs`, `state.drafts`, etc. — they don't need to know which
mode they're in.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import flet as ft

from app.core.job import Job, PromptDraft
from app.core.queue import BatchRunner
from app.paths import is_web_mode
from app.runway.auth import StoredToken, load_token
from app.runway.client import RunwayClient


JobListener = Callable[[Job], None]


class AppState:
    """Lives for the lifetime of one app instance (desktop) or one
    browser session (web)."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._web = is_web_mode()

        # Per-session unsubscribe handle for runtime listener (web only).
        self._runtime_unsub: Callable[[], None] | None = None

        # Drafts are always per-session — even in web mode. A draft is one
        # user's in-progress edit; other visitors should not see it until
        # "Start batch" turns it into shared jobs.
        self._drafts: list[PromptDraft] = []

        # Selected author for THIS session. Each visitor picks one on first
        # arrival; the choice is not persisted across reloads. The list of
        # available authors itself is shared (lives in SQLite).
        self.selected_author: str | None = None

        if self._web:
            # Auth / client / runner / active_jobs live in runtime — nothing
            # else to init here.
            return

        # ── desktop: owns its own state, identical to the previous build ──

        # Auth
        self._token: StoredToken | None = None

        # HTTP client (created on login)
        self._client: RunwayClient | None = None

        # Active batch runner
        self._runner: BatchRunner | None = None

        # Active jobs being processed (id → Job)
        self._active_jobs: dict[str, Job] = {}

        # Per-session subscribers (desktop pub/sub)
        self._job_listeners: list[JobListener] = []

    # ── shared-vs-local field access ─────────────────────────────────

    @property
    def token(self) -> StoredToken | None:
        if self._web:
            from app.core.runtime import runtime
            return runtime.token
        return self._token

    @token.setter
    def token(self, value: StoredToken | None) -> None:
        if self._web:
            from app.core.runtime import runtime
            # In web, the only path that nulls the token is logout. Route
            # it through runtime.logout() so the runner + client get torn
            # down too. Direct assignment of a non-None token is not used
            # — login goes through install_client(token).
            #
            # Use page.run_task — Flet dispatches sync UI handlers (like
            # do_logout) on a thread pool that has no running event loop,
            # so asyncio.create_task would raise RuntimeError and the
            # caller's page.go("/login") would never run.
            if value is None:
                self.page.run_task(runtime.logout)
            else:
                runtime.token = value
            return
        self._token = value

    @property
    def client(self) -> RunwayClient | None:
        if self._web:
            from app.core.runtime import runtime
            return runtime.client
        return self._client

    @client.setter
    def client(self, value: RunwayClient | None) -> None:
        if self._web:
            # Web doesn't allow replacing the shared client from a session.
            return
        self._client = value

    @property
    def runner(self) -> BatchRunner | None:
        if self._web:
            from app.core.runtime import runtime
            return runtime.runner
        return self._runner

    @runner.setter
    def runner(self, value: BatchRunner | None) -> None:
        if self._web:
            # Web ignores per-session runner assignment — runtime owns it.
            return
        self._runner = value

    @property
    def active_jobs(self) -> dict[str, Job]:
        if self._web:
            from app.core.runtime import runtime
            return runtime.active_jobs
        return self._active_jobs

    @property
    def drafts(self) -> list[PromptDraft]:
        # Per-session in both modes — see __init__ for the why.
        return self._drafts

    # ── token / client lifecycle ──────────────────────────────────

    def load_token_from_disk(self) -> bool:
        """Loads JWT and creates the HTTP client.

        Desktop: as before — fills self._token and self._client.
        Web: a no-op — runtime.startup() already did this at process start.
        Returns True when a usable token is available.
        """
        if self._web:
            from app.core.runtime import runtime
            return runtime.has_token()

        self._token = load_token()
        if self._token is not None:
            self._client = RunwayClient(token=self._token.token)
            return True
        return False

    def install_client(self, token: StoredToken) -> None:
        if self._web:
            from app.core.runtime import runtime
            # Fire-and-forget — runtime builds client + runner and resumes
            # unfinished jobs. Use page.run_task so this works whether the
            # caller is sync or async (asyncio.create_task would raise from
            # a sync UI handler with no running loop).
            self.page.run_task(runtime.install_login, token)
            return

        self._token = token
        if self._client is not None:
            asyncio.create_task(self._client.aclose())
        self._client = RunwayClient(token=self._token.token)

    def ensure_client(self) -> bool:
        """Make sure self.client is usable. Re-create from self.token if the
        previous client was closed (transient websocket disconnect path)
        or never existed. Returns False only when there is no token to use."""
        if self._web:
            from app.core.runtime import runtime
            return runtime.ensure_client()

        if self._client is not None and not self._client.is_closed:
            return True
        if self._token is None:
            return False
        self._client = RunwayClient(token=self._token.token)
        return True

    async def teardown(self) -> None:
        """Called by app.py on_disconnect.

        Desktop: closes the per-session runner + client (unchanged).
        Web: ONLY unsubscribes this page from the runtime listener list.
        The runner + client are process-global and keep running.
        """
        if self._web:
            if self._runtime_unsub is not None:
                try:
                    self._runtime_unsub()
                except Exception:  # noqa: BLE001
                    pass
                self._runtime_unsub = None
            return

        # NOTE: we intentionally drop the references after aclose. Flet may
        # fire on_disconnect on a transient websocket flicker and then keep
        # using the same AppState — without this, the next interaction would
        # pick up a stopped runner / closed client and blow up with
        # "Cannot send a request, as the client has been closed".
        if self._runner is not None:
            try:
                await self._runner.aclose()
            except Exception:
                pass
            self._runner = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    # ── job updates pub/sub ───────────────────────────────────────

    def subscribe_job_updates(self, listener: JobListener) -> Callable[[], None]:
        if self._web:
            from app.core.runtime import runtime
            return runtime.subscribe(listener)

        self._job_listeners.append(listener)
        def _unsub() -> None:
            try:
                self._job_listeners.remove(listener)
            except ValueError:
                pass
        return _unsub

    def emit_job_update(self, job: Job) -> None:
        if self._web:
            # In web mode, the runtime's own _emit() is the only legitimate
            # source of job updates — it both writes runtime.active_jobs
            # and fans out to every subscribed session. Calling _emit() from
            # here would re-enter the listener loop we are already inside
            # (on_runner_update was invoked *by* _emit), so it just records
            # the new state into the shared map and returns.
            from app.core.runtime import runtime
            runtime.active_jobs[job.id] = job
            return

        # Desktop path: update local map + fan out to per-session listeners.
        self._active_jobs[job.id] = job
        for fn in list(self._job_listeners):
            try:
                fn(job)
            except Exception:  # noqa: BLE001
                pass

    def install_session_listener(self, fn: JobListener) -> None:
        """Wire a page-bound job-update callback.

        Desktop: no-op — the per-session BatchRunner already calls this
        callback directly via its `on_update=` constructor arg, so wiring
        it again would double-emit.

        Web: registers the callback with the shared runtime singleton (so
        this page gets notified whenever any job advances) and stores the
        unsubscribe handle on self so teardown() can clean it up. Idempotent
        — calling it twice in one session does not stack listeners.
        """
        if not self._web:
            return
        if self._runtime_unsub is not None:
            return
        from app.core.runtime import runtime
        self._runtime_unsub = runtime.subscribe(fn)
