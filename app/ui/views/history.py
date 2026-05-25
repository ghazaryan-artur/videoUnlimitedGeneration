"""History view — all past jobs (DONE / FAILED / CANCELLED / running) with their task IDs."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import flet as ft
from loguru import logger

from app.core.job import Job, JobStatus
from app.core.storage import list_recent_jobs
from app.ui import theme
from app.ui.state import AppState


def _open_path(path: str | Path) -> None:
    p = Path(path)
    target = p.parent if p.is_file() else p
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not open {}: {}", target, e)


def _format_when(job: Job) -> str:
    return job.created_at.strftime("%Y-%m-%d %H:%M")


def _row_for(job: Job, page: ft.Page) -> ft.Control:
    color, icon, label = theme.status_visual(job.status.value)

    def copy_id(_: ft.ControlEvent) -> None:
        if not job.runway_task_id:
            return
        try:
            page.set_clipboard(job.runway_task_id)
            page.snack_bar = ft.SnackBar(
                ft.Text(f"Task ID copied: {job.runway_task_id}"),
                bgcolor=theme.Colors.surface_2,
            )
            page.snack_bar.open = True
            page.update()
        except Exception:
            pass

    def open_video(_: ft.ControlEvent) -> None:
        if job.output_path and Path(job.output_path).exists():
            try:
                if sys.platform == "win32":
                    os.startfile(job.output_path)  # type: ignore[attr-defined]
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", job.output_path])
            except Exception:
                pass

    def open_folder(_: ft.ControlEvent) -> None:
        target = job.output_path or job.output_dir
        if target:
            _open_path(target)

    actions: list[ft.Control] = []
    if job.runway_task_id:
        actions.append(
            ft.IconButton(
                icon=ft.Icons.CONTENT_COPY,
                icon_color=theme.Colors.text_dim,
                tooltip=f"Copy task_id\n{job.runway_task_id}",
                on_click=copy_id,
                icon_size=16,
            )
        )
    if job.status == JobStatus.DONE and job.output_path:
        actions.append(
            ft.IconButton(
                icon=ft.Icons.PLAY_ARROW,
                icon_color=theme.Colors.text_secondary,
                tooltip="Play",
                on_click=open_video,
                icon_size=16,
            )
        )
        actions.append(
            ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN,
                icon_color=theme.Colors.text_secondary,
                tooltip="Open folder",
                on_click=open_folder,
                icon_size=16,
            )
        )

    pill = ft.Container(
        content=ft.Row(
            [ft.Icon(icon, color=color, size=14),
             ft.Text(label, size=11, weight=ft.FontWeight.W_700, color=color)],
            spacing=4,
        ),
        bgcolor=ft.Colors.with_opacity(0.10, color),
        border=ft.border.all(1, ft.Colors.with_opacity(0.30, color)),
        border_radius=5,
        padding=ft.padding.symmetric(horizontal=6, vertical=2),
        width=110,
    )

    return ft.Container(
        content=ft.Row(
            [
                ft.Text(_format_when(job), size=11, color=theme.Colors.text_dim, width=120),
                pill,
                ft.Text(
                    job.prompt[:90] + ("…" if len(job.prompt) > 90 else ""),
                    size=12,
                    color=theme.Colors.text_primary,
                    expand=True,
                    no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    job.runway_task_id[:12] + "…" if job.runway_task_id else "—",
                    size=11,
                    color=theme.Colors.text_secondary,
                    font_family="monospace",
                    width=120,
                    selectable=True,
                ),
                *actions,
            ],
            spacing=10,
        ),
        bgcolor=theme.Colors.surface_2,
        border_radius=8,
        border=ft.border.all(1, theme.Colors.border),
        padding=ft.padding.symmetric(horizontal=12, vertical=10),
    )


def build_history_view(page: ft.Page, state: AppState) -> ft.View:
    list_column = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

    empty_state = ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.HISTORY_TOGGLE_OFF, size=48, color=theme.Colors.text_dim),
                ft.Container(height=10),
                ft.Text("No jobs yet", size=14, color=theme.Colors.text_secondary),
                ft.Text("Generate something to see it here", size=12, color=theme.Colors.text_dim),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.alignment.center,
        expand=True,
    )

    async def load() -> None:
        try:
            jobs = await list_recent_jobs(limit=500)
        except Exception as e:  # noqa: BLE001
            logger.warning("list_recent_jobs failed: {}", e)
            return
        if not jobs:
            list_column.controls = [empty_state]
        else:
            list_column.controls = [_row_for(j, page) for j in jobs]
        try:
            list_column.update()
        except Exception:
            pass

    def go_back(_: ft.ControlEvent) -> None:
        page.go("/jobs")

    def refresh(_: ft.ControlEvent) -> None:
        page.run_task(load)

    header = ft.Container(
        content=ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK_ROUNDED,
                    icon_color=theme.Colors.text_secondary,
                    on_click=go_back,
                    tooltip="Back",
                ),
                ft.Text("History", size=18, weight=ft.FontWeight.W_700,
                        color=theme.Colors.text_primary),
                ft.Container(expand=True),
                theme.secondary_button("Refresh", on_click=refresh, icon=ft.Icons.REFRESH),
            ],
            spacing=8,
        ),
        bgcolor=theme.Colors.surface,
        padding=ft.padding.symmetric(horizontal=24, vertical=14),
        border=ft.border.only(bottom=ft.BorderSide(1, theme.Colors.border)),
    )

    body = ft.Container(
        content=list_column,
        padding=ft.padding.symmetric(horizontal=24, vertical=18),
        expand=True,
    )

    page.run_task(load)

    return ft.View(
        route="/history",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[ft.Column([header, body], spacing=0, expand=True)],
    )
