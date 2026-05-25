"""Downloads view — lists mp4s served from the web /downloads/ static tree."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import flet as ft
from loguru import logger

from app.paths import is_web_mode, output_dir
from app.ui import theme
from app.ui.state import AppState


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


def _scan_downloads() -> list[tuple[Path, int, datetime]]:
    """Return (path, size_bytes, mtime) for every regular file under output_dir(),
    newest first. Hidden files and `.part` (in-flight downloads) are skipped."""
    rows: list[tuple[Path, int, datetime]] = []
    root = output_dir()
    try:
        for entry in root.iterdir():
            if not entry.is_file():
                continue
            if entry.name.startswith(".") or entry.suffix == ".part":
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            rows.append((entry, st.st_size, datetime.fromtimestamp(st.st_mtime)))
    except FileNotFoundError:
        return []
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows


def _row_for(page: ft.Page, path: Path, size: int, mtime: datetime) -> ft.Control:
    # In web mode the file is served via /downloads/<name>; in desktop mode the
    # row is informational only (no static server in front of it).
    url = f"/downloads/{quote(path.name)}" if is_web_mode() else None

    def do_download(_: ft.ControlEvent) -> None:
        if url:
            page.launch_url(url)

    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.MOVIE_OUTLINED, size=18, color=theme.Colors.text_secondary),
                ft.Column(
                    [
                        ft.Text(
                            path.name,
                            size=13,
                            color=theme.Colors.text_primary,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            selectable=True,
                        ),
                        ft.Text(
                            f"{_format_bytes(size)}  •  {mtime.strftime('%Y-%m-%d %H:%M')}",
                            size=11,
                            color=theme.Colors.text_dim,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                    tight=True,
                ),
                ft.FilledButton(
                    text="Download",
                    icon=ft.Icons.CLOUD_DOWNLOAD,
                    on_click=do_download,
                    disabled=url is None,
                    tooltip=None if url else "Available only in web mode",
                    style=ft.ButtonStyle(
                        color={"": theme.Colors.bg},
                        bgcolor={"": theme.Colors.state_done},
                        shape=ft.RoundedRectangleBorder(radius=8),
                    ),
                ),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=theme.Colors.surface_2,
        border_radius=8,
        border=ft.border.all(1, theme.Colors.border),
        padding=ft.padding.symmetric(horizontal=14, vertical=10),
    )


def build_downloads_view(page: ft.Page, state: AppState) -> ft.View:
    list_column = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

    empty_state = ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.FOLDER_OFF, size=48, color=theme.Colors.text_dim),
                ft.Container(height=10),
                ft.Text("No downloads yet", size=14, color=theme.Colors.text_secondary),
                ft.Text(
                    "Finished videos will appear here once a job completes.",
                    size=12,
                    color=theme.Colors.text_dim,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.alignment.center,
        expand=True,
    )

    def reload() -> None:
        try:
            rows = _scan_downloads()
        except Exception as e:  # noqa: BLE001
            logger.warning("scan downloads failed: {}", e)
            rows = []
        if not rows:
            list_column.controls = [empty_state]
        else:
            list_column.controls = [_row_for(page, p, s, m) for p, s, m in rows]
        try:
            list_column.update()
        except Exception:
            pass

    def go_back(_: ft.ControlEvent) -> None:
        page.go("/jobs")

    def refresh(_: ft.ControlEvent) -> None:
        reload()

    header = ft.Container(
        content=ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK_ROUNDED,
                    icon_color=theme.Colors.text_secondary,
                    on_click=go_back,
                    tooltip="Back",
                ),
                ft.Text(
                    "Downloads",
                    size=18,
                    weight=ft.FontWeight.W_700,
                    color=theme.Colors.text_primary,
                ),
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

    reload()

    return ft.View(
        route="/downloads",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[ft.Column([header, body], spacing=0, expand=True)],
    )
