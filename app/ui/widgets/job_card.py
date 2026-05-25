"""JobCard — live (read-only) view of a single Job.

Shows status pill, prompt, model summary, progress bar, per-state details,
elapsed timer, and the Runway task_id (copyable, for manual debug).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import flet as ft

from app.core.job import Job, JobStatus
from app.runway.models import by_task_type
from app.ui import theme


# ── helpers ──────────────────────────────────────────────────────────


def _format_seconds(s: float | int | None) -> str:
    if s is None:
        return "—"
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _format_bytes(n: int | None) -> str:
    if n is None or n <= 0:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


def _wrap_text(text: str, *, max_chars: int = 200) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _live_elapsed(job: Job) -> str:
    """Best-effort live elapsed timer based on current state."""
    st = job.status
    if st in (JobStatus.QUEUED, JobStatus.SUBMITTING):
        if job.submitted_at:
            secs = (_now_utc() - job.submitted_at).total_seconds()
            return _format_seconds(secs)
        return "just now"
    if st == JobStatus.GENERATING:
        if job.started_at:
            secs = (_now_utc() - job.started_at).total_seconds()
            return _format_seconds(secs)
        return "just started"
    if st == JobStatus.DOWNLOADING:
        if job.completed_at:
            secs = (_now_utc() - job.completed_at).total_seconds()
            return _format_seconds(secs)
        return "—"
    if st == JobStatus.DONE:
        return _format_seconds(job.total_seconds)
    return "—"


# ── public class ─────────────────────────────────────────────────────


class JobCard:
    """Owns a Container root; swap content on `update_from()` or `tick()`."""

    def __init__(
        self,
        job: Job,
        page: ft.Page,
        *,
        on_open_folder: Callable[[Job], None] | None = None,
        on_retry: Callable[[Job], None] | None = None,
        on_cancel: Callable[[Job], None] | None = None,
    ) -> None:
        self.job = job
        self.page = page
        self.on_open_folder = on_open_folder
        self.on_retry = on_retry
        self.on_cancel = on_cancel

        self.root = ft.Container(content=self._render(), animate=ft.Animation(150, "easeOut"))

    @property
    def control(self) -> ft.Control:
        return self.root

    def update_from(self, job: Job) -> None:
        self.job = job
        self.root.content = self._render()
        try:
            self.root.update()
        except Exception:
            pass

    def tick(self) -> None:
        """Lightweight re-render to refresh the live timer for active states."""
        if self.job.status.is_active:
            self.root.content = self._render()
            try:
                self.root.update()
            except Exception:
                pass

    # ── render ───────────────────────────────────────────────────

    def _render(self) -> ft.Control:
        color, icon, label = theme.status_visual(self.job.status.value)

        status_pill = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, color=color, size=16),
                    ft.Text(label, size=12, weight=ft.FontWeight.W_700, color=color),
                ],
                spacing=6,
            ),
            bgcolor=ft.Colors.with_opacity(0.10, color),
            border=ft.border.all(1, ft.Colors.with_opacity(0.30, color)),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
        )

        progress_value = max(0.0, min(1.0, self.job.progress_ratio))
        progress_bar = ft.ProgressBar(
            value=progress_value if self.job.status not in (JobStatus.QUEUED, JobStatus.SUBMITTING) else None,
            color=color,
            bgcolor=theme.Colors.surface_3,
            bar_height=6,
        )

        return theme.card_container(
            ft.Column(
                [
                    ft.Row([status_pill, ft.Container(expand=True), *self._action_buttons()], spacing=8),
                    ft.Container(height=10),
                    ft.Text(
                        _wrap_text(self.job.prompt, max_chars=180),
                        size=13,
                        color=theme.Colors.text_primary,
                        selectable=True,
                    ),
                    ft.Container(height=8),
                    ft.Row(
                        [
                            ft.Text(
                                f"{by_task_type(self.job.model_task_type).display_name}  •  "
                                f"{self.job.duration}s  •  "
                                f"{self.job.aspect_ratio}  •  "
                                f"{'audio' if self.job.audio else 'no audio'}",
                                size=12,
                                color=theme.Colors.text_dim,
                            ),
                            ft.Container(expand=True),
                            self._task_id_chip(),
                        ],
                        spacing=8,
                    ),
                    ft.Container(height=12),
                    progress_bar,
                    ft.Container(height=8),
                    self._status_detail(),
                ],
                spacing=0,
            ),
            padding=ft.padding.all(16),
        )

    def _task_id_chip(self) -> ft.Control:
        """Small chip showing the Runway task_id with a copy-to-clipboard button."""
        if not self.job.runway_task_id:
            return ft.Container(width=0)
        short = self.job.runway_task_id[:8] + "…"

        def copy_id(_: ft.ControlEvent) -> None:
            try:
                self.page.set_clipboard(self.job.runway_task_id)
                self.page.snack_bar = ft.SnackBar(
                    ft.Text(f"Task ID copied: {self.job.runway_task_id}"),
                    bgcolor=theme.Colors.surface_2,
                )
                self.page.snack_bar.open = True
                self.page.update()
            except Exception:
                pass

        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.TAG, size=12, color=theme.Colors.text_dim),
                    ft.Text(
                        short,
                        size=11,
                        color=theme.Colors.text_secondary,
                        font_family="monospace",
                        selectable=True,
                    ),
                    ft.Container(width=2),
                    ft.Icon(ft.Icons.CONTENT_COPY, size=12, color=theme.Colors.text_dim),
                ],
                spacing=4,
            ),
            bgcolor=theme.Colors.surface_3,
            border=ft.border.all(1, theme.Colors.border),
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=8, vertical=3),
            on_click=copy_id,
            ink=True,
            tooltip=f"Click to copy:\n{self.job.runway_task_id}",
        )

    def _status_detail(self) -> ft.Control:
        st = self.job.status
        color, _icon, _label = theme.status_visual(st.value)

        if st in (JobStatus.QUEUED, JobStatus.SUBMITTING):
            eta = self.job.estimated_start_seconds
            eta_text = (
                f"≈ {_format_seconds(eta)} until start" if eta and eta > 0
                else "in queue (relax mode)"
            )
            elapsed = _live_elapsed(self.job)
            return ft.Row(
                [
                    ft.Text(eta_text, size=12, color=color),
                    ft.Container(expand=True),
                    ft.Icon(ft.Icons.SCHEDULE, size=12, color=theme.Colors.text_dim),
                    ft.Text(f"waiting {elapsed}", size=12, color=theme.Colors.text_dim),
                ],
                spacing=4,
            )

        if st == JobStatus.GENERATING:
            queue_time = _format_seconds(self.job.queue_seconds) if self.job.queue_seconds else "—"
            elapsed = _live_elapsed(self.job)
            return ft.Row(
                [
                    ft.Text(
                        f"{int(self.job.progress_ratio * 100)}%",
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color=color,
                    ),
                    ft.Container(expand=True),
                    ft.Icon(ft.Icons.SCHEDULE, size=12, color=theme.Colors.text_dim),
                    ft.Text(
                        f"queue {queue_time}  •  generating {elapsed}",
                        size=12,
                        color=theme.Colors.text_dim,
                    ),
                ],
                spacing=4,
            )

        if st == JobStatus.DOWNLOADING:
            done_mb = _format_bytes(self.job.download_bytes_done)
            total_mb = _format_bytes(self.job.file_size_bytes)
            return ft.Row(
                [
                    ft.Text(
                        f"{done_mb} / {total_mb}",
                        size=13,
                        weight=ft.FontWeight.W_500,
                        color=color,
                    ),
                    ft.Container(expand=True),
                    ft.Text(
                        f"{int(self.job.progress_ratio * 100)}%",
                        size=12,
                        color=theme.Colors.text_dim,
                    ),
                ]
            )

        if st == JobStatus.DONE:
            return ft.Row(
                [
                    ft.Icon(ft.Icons.FOLDER_OPEN, size=14, color=theme.Colors.text_secondary),
                    ft.Text(
                        self.job.output_path or "—",
                        size=12,
                        color=theme.Colors.text_secondary,
                        no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                    ),
                    ft.Text(
                        f"total {_format_seconds(self.job.total_seconds)}",
                        size=12,
                        color=theme.Colors.text_dim,
                    ),
                ],
                spacing=6,
            )

        if st == JobStatus.FAILED:
            return ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER, size=14, color=color),
                    ft.Text(
                        self.job.error_reason or "Unknown error",
                        size=12,
                        color=color,
                        selectable=True,
                        expand=True,
                    ),
                ],
                spacing=6,
            )

        if st == JobStatus.CANCELLED:
            return ft.Text("cancelled", size=12, color=theme.Colors.text_dim)

        return ft.Container()

    def _action_buttons(self) -> list[ft.Control]:
        buttons: list[ft.Control] = []
        if self.job.status == JobStatus.DONE and self.on_open_folder is not None:
            buttons.append(
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN,
                    icon_color=theme.Colors.text_secondary,
                    tooltip="Open output folder",
                    on_click=lambda _: self.on_open_folder(self.job),
                )
            )
        if self.job.status == JobStatus.FAILED and self.on_retry is not None:
            buttons.append(
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    icon_color=theme.Colors.text_secondary,
                    tooltip="Retry",
                    on_click=lambda _: self.on_retry(self.job),
                )
            )
        if self.job.status.is_active and self.on_cancel is not None:
            buttons.append(
                ft.IconButton(
                    icon=ft.Icons.STOP_CIRCLE_OUTLINED,
                    icon_color=theme.Colors.text_dim,
                    tooltip="Cancel this job",
                    on_click=lambda _: self.on_cancel(self.job),
                )
            )
        return buttons
