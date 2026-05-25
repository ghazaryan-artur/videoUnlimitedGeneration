"""Debug view — live API log persisted to SQLite, filterable, exportable."""
from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime
from pathlib import Path

import flet as ft
from loguru import logger

from app.core.storage import recent_api_logs
from app.paths import logs_dir
from app.ui import theme
from app.ui.state import AppState


REFRESH_INTERVAL_SECONDS = 1.0


def _status_color(status: int | None) -> str:
    if status is None:
        return theme.Colors.state_failed
    if 200 <= status < 300:
        return theme.Colors.state_done
    if 300 <= status < 400:
        return theme.Colors.text_secondary
    if status == 429:
        return theme.Colors.accent_warm
    if 400 <= status < 500:
        return theme.Colors.state_failed
    if status >= 500:
        return theme.Colors.state_failed
    return theme.Colors.text_dim


def _short_url(url: str) -> str:
    if url.startswith("https://api.runwayml.com"):
        return url[len("https://api.runwayml.com"):]
    return url


def build_debug_view(page: ft.Page, state: AppState) -> ft.View:
    filter_field = ft.TextField(
        hint_text="Filter by URL or status (e.g.  /tasks  429  POST)",
        bgcolor=theme.Colors.surface_2,
        border_color=theme.Colors.border,
        focused_border_color=theme.Colors.primary,
        color=theme.Colors.text_primary,
        cursor_color=theme.Colors.primary,
        prefix_icon=ft.Icons.SEARCH,
        height=44,
        text_size=13,
        expand=True,
    )

    table = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
    detail_panel = ft.Container(
        bgcolor=theme.Colors.surface_2,
        border=ft.border.all(1, theme.Colors.border),
        border_radius=10,
        padding=ft.padding.all(14),
        content=ft.Text(
            "Click a log row to inspect the full request and response bodies.",
            size=12,
            color=theme.Colors.text_dim,
        ),
        height=260,
    )

    auto_refresh = ft.Switch(label="Auto-refresh", value=True, active_color=theme.Colors.primary)

    def make_row(entry) -> ft.Control:
        ts = entry.ts.split("T")[1][:12] if "T" in entry.ts else entry.ts[-12:]
        method_color = {
            "GET": theme.Colors.text_secondary,
            "POST": theme.Colors.state_generating,
            "PUT": theme.Colors.accent_warm,
            "DELETE": theme.Colors.state_failed,
        }.get(entry.method, theme.Colors.text_dim)

        status_label = str(entry.status) if entry.status is not None else "ERR"

        row_content = ft.Row(
            [
                ft.Text(ts, size=11, color=theme.Colors.text_dim, width=90),
                ft.Container(
                    content=ft.Text(entry.method, size=11, weight=ft.FontWeight.W_700, color=method_color),
                    width=54,
                ),
                ft.Text(
                    _short_url(entry.url),
                    size=12,
                    color=theme.Colors.text_primary,
                    expand=True,
                    no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Container(
                    content=ft.Text(
                        status_label,
                        size=11,
                        weight=ft.FontWeight.W_700,
                        color=_status_color(entry.status),
                    ),
                    width=46,
                    alignment=ft.alignment.center_right,
                ),
                ft.Text(
                    f"{entry.duration_ms:.0f}ms",
                    size=11,
                    color=theme.Colors.text_dim,
                    width=70,
                    text_align=ft.TextAlign.RIGHT,
                ),
            ],
            spacing=8,
        )

        def on_click(_e: ft.ControlEvent) -> None:
            show_detail(entry)

        return ft.Container(
            content=row_content,
            padding=ft.padding.symmetric(horizontal=12, vertical=6),
            border_radius=6,
            on_click=on_click,
            ink=True,
            tooltip="Click for full request/response",
        )

    def show_detail(entry) -> None:
        def pretty(body: str | None) -> str:
            if not body:
                return "(empty)"
            try:
                obj = json.loads(body)
                return json.dumps(obj, indent=2, ensure_ascii=False)
            except Exception:
                return body

        detail_panel.content = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            f"{entry.method}  {_short_url(entry.url)}",
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color=theme.Colors.text_primary,
                            expand=True,
                            selectable=True,
                        ),
                        ft.Text(
                            f"{entry.status if entry.status is not None else 'ERR'}  •  "
                            f"{entry.duration_ms:.0f}ms",
                            size=12,
                            color=_status_color(entry.status),
                            weight=ft.FontWeight.W_700,
                        ),
                    ]
                ),
                ft.Container(height=8),
                ft.Text("Request body", size=11, color=theme.Colors.text_dim),
                ft.Container(
                    content=ft.Text(
                        pretty(entry.request_body),
                        size=11,
                        font_family="monospace",
                        color=theme.Colors.text_secondary,
                        selectable=True,
                    ),
                    bgcolor=theme.Colors.surface_3,
                    border_radius=6,
                    padding=ft.padding.all(10),
                    height=80,
                ),
                ft.Container(height=6),
                ft.Text("Response body", size=11, color=theme.Colors.text_dim),
                ft.Container(
                    content=ft.Text(
                        pretty(entry.response_body) if entry.error is None else entry.error,
                        size=11,
                        font_family="monospace",
                        color=theme.Colors.text_secondary,
                        selectable=True,
                    ),
                    bgcolor=theme.Colors.surface_3,
                    border_radius=6,
                    padding=ft.padding.all(10),
                    height=80,
                ),
            ],
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
        )
        detail_panel.update()

    async def refresh_table() -> None:
        try:
            entries = await recent_api_logs(limit=500)
        except Exception as e:  # noqa: BLE001
            logger.warning("recent_api_logs failed: {}", e)
            return
        # Filter
        f = (filter_field.value or "").lower().strip()
        if f:
            def keep(e) -> bool:
                hay = f"{e.method} {e.url} {e.status}".lower()
                return all(part in hay for part in f.split())
            entries = [e for e in entries if keep(e)]
        # Latest at bottom
        table.controls = [make_row(e) for e in entries]
        try:
            table.update()
        except Exception:
            pass

    async def refresh_loop() -> None:
        while True:
            if auto_refresh.value:
                await refresh_table()
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS)

    # Export
    async def export_logs(_: ft.ControlEvent) -> None:
        entries = await recent_api_logs(limit=10000)
        out = logs_dir() / f"api_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
        with gzip.open(out, "wt", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps({
                    "ts": e.ts,
                    "method": e.method,
                    "url": e.url,
                    "status": e.status,
                    "duration_ms": e.duration_ms,
                    "request_body": e.request_body,
                    "response_body": e.response_body,
                    "error": e.error,
                    "related_task_id": e.related_task_id,
                }) + "\n")
        page.snack_bar = ft.SnackBar(
            ft.Text(f"Exported {len(entries)} entries to {out}"),
            bgcolor=theme.Colors.surface_2,
        )
        page.snack_bar.open = True
        page.update()

    def go_back(_: ft.ControlEvent) -> None:
        page.go("/jobs")

    filter_field.on_change = lambda _: page.run_task(refresh_table)

    header = ft.Container(
        content=ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK_ROUNDED,
                    icon_color=theme.Colors.text_secondary,
                    on_click=go_back,
                    tooltip="Back",
                ),
                ft.Text("Debug log", size=18, weight=ft.FontWeight.W_700,
                        color=theme.Colors.text_primary),
                ft.Container(expand=True),
                auto_refresh,
                ft.Container(width=12),
                theme.secondary_button("Export", on_click=export_logs, icon=ft.Icons.DOWNLOAD),
            ],
            spacing=8,
        ),
        bgcolor=theme.Colors.surface,
        padding=ft.padding.symmetric(horizontal=24, vertical=14),
        border=ft.border.only(bottom=ft.BorderSide(1, theme.Colors.border)),
    )

    body = ft.Container(
        content=ft.Column(
            [
                filter_field,
                ft.Container(height=10),
                ft.Container(
                    content=table,
                    bgcolor=theme.Colors.surface,
                    border=ft.border.all(1, theme.Colors.border),
                    border_radius=10,
                    padding=ft.padding.symmetric(horizontal=4, vertical=4),
                    expand=True,
                ),
                ft.Container(height=10),
                detail_panel,
            ],
            spacing=0,
            expand=True,
        ),
        padding=ft.padding.symmetric(horizontal=24, vertical=18),
        expand=True,
    )

    view = ft.View(
        route="/debug",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[ft.Column([header, body], spacing=0, expand=True)],
    )

    # Kick off refresh loop
    page.run_task(refresh_loop)

    return view
