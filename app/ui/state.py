"""AppState — the shared object every view reads from / writes to."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import flet as ft

from app.core.job import Job, PromptDraft
from app.core.queue import BatchRunner
from app.runway.auth import StoredToken, load_token
from app.runway.client import RunwayClient


JobListener = Callable[[Job], None]


class AppState:
    """Lives for the lifetime of one app instance."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page

        # Auth
        self.token: StoredToken | None = None

        # HTTP client (created on login)
        self.client: RunwayClient | None = None

        # Active batch runner
        self.runner: BatchRunner | None = None

        # Editable drafts (PromptDraft, not yet submitted)
        self.drafts: list[PromptDraft] = []

        # Active jobs being processed (id → Job)
        self.active_jobs: dict[str, Job] = {}

        # Subscribers
        self._job_listeners: list[JobListener] = []

    # ── token / client lifecycle ──────────────────────────────────

    def load_token_from_disk(self) -> bool:
        """Loads JWT from %LOCALAPPDATA%/data/jwt.json AND creates the HTTP client.

        Without creating the client here, every UI action that uses state.client
        would short-circuit to /login — so users would see "asks to login each
        launch" even though the JWT is fine.
        """
        self.token = load_token()
        if self.token is not None:
            self.client = RunwayClient(token=self.token.token)
            return True
        return False

    def install_client(self, token: StoredToken) -> None:
        self.token = token
        if self.client is not None:
            asyncio.create_task(self.client.aclose())
        self.client = RunwayClient(token=self.token.token)

    def ensure_client(self) -> bool:
        """Make sure self.client is usable. Re-create from self.token if the
        previous client was closed (transient websocket disconnect path)
        or never existed. Returns False only when there is no token to use."""
        if self.client is not None and not self.client.is_closed:
            return True
        if self.token is None:
            return False
        self.client = RunwayClient(token=self.token.token)
        return True

    async def teardown(self) -> None:
        # NOTE: we intentionally drop the references after aclose. Flet may
        # fire on_disconnect on a transient websocket flicker and then keep
        # using the same AppState — without this, the next interaction would
        # pick up a stopped runner / closed client and blow up with
        # "Cannot send a request, as the client has been closed".
        if self.runner is not None:
            try:
                await self.runner.aclose()
            except Exception:
                pass
            self.runner = None
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:
                pass
            self.client = None

    # ── job updates pub/sub ───────────────────────────────────────

    def subscribe_job_updates(self, listener: JobListener) -> Callable[[], None]:
        self._job_listeners.append(listener)
        def _unsub() -> None:
            try:
                self._job_listeners.remove(listener)
            except ValueError:
                pass
        return _unsub

    def emit_job_update(self, job: Job) -> None:
        # Update local map
        self.active_jobs[job.id] = job
        for fn in list(self._job_listeners):
            try:
                fn(job)
            except Exception:  # noqa: BLE001
                pass
