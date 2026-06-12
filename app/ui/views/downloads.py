"""Downloads view — lists mp4s served from the web /downloads/ static tree.

Layout
------
Root view: one card per top-level subfolder of downloads/ (with the FIRST
(oldest) video's thumbnail as preview, the count, the total size, and the
latest mtime), plus any loose files saved directly into downloads/ root.

Click a folder card → "inside a folder" view shows individual video rows
(thumbnail, name, size+date, model, generation time, download link).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import flet as ft
from loguru import logger

from app.core.job import Job, PromptDraft, safe_folder_name
from app.core.storage import list_recent_jobs
from app.paths import is_web_mode, output_dir
from app.runway.models import by_task_type
from app.ui import theme
from app.ui.state import AppState


# ── data types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FileEntry:
    path: Path
    rel_path: str         # path relative to downloads/, using forward slashes
    size: int
    mtime: datetime


# ── formatting helpers ────────────────────────────────────────────────


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


def _format_duration_seconds(s: float | None) -> str | None:
    if s is None or s <= 0:
        return None
    if s < 60:
        return f"{int(round(s))}s"
    mins, secs = divmod(int(round(s)), 60)
    return f"{mins}m {secs:02d}s"


def _model_display_name(task_type: str) -> str:
    try:
        return by_task_type(task_type).display_name
    except KeyError:
        return task_type


# ── filesystem scan ───────────────────────────────────────────────────


def _scan_downloads_grouped(
    *, relative_root: str | None = None,
) -> tuple[list[tuple[str, list[FileEntry]]], list[FileEntry]]:
    """Walk a single level under downloads/<relative_root>/.

    When `relative_root` is None the scan starts from downloads/ itself
    (legacy behaviour). When given, we descend into downloads/<relative_root>/
    and group its immediate subdirectories — so for an author named "Anna"
    the view at /downloads shows her subfolders (cats, dogs, …) directly,
    without a useless "Anna" wrapper card.

    rel_path on every FileEntry is always anchored at downloads/ (the path
    served by the /downloads/<rel> static route), so URLs and thumbnails
    keep working regardless of the scan root.

    Returns:
        folders : list of (subfolder_name, list_of_FileEntry)
                  inner list sorted newest-first; outer list sorted by
                  newest contained mtime, newest folders first.
        loose   : list of FileEntry for files directly inside the scan
                  root (no further nesting), newest first.
    """
    root = output_dir()
    base = root if relative_root is None else (root / relative_root)
    folders_map: dict[str, list[FileEntry]] = {}
    loose: list[FileEntry] = []

    def _visit_dir(folder_name: str, dir_path: Path) -> None:
        bucket = folders_map.setdefault(folder_name, [])
        for sub in dir_path.iterdir():
            try:
                if sub.is_file():
                    if sub.name.startswith(".") or sub.suffix == ".part":
                        continue
                    st = sub.stat()
                    rel = sub.relative_to(root).as_posix()
                    bucket.append(FileEntry(
                        path=sub, rel_path=rel,
                        size=st.st_size,
                        mtime=datetime.fromtimestamp(st.st_mtime),
                    ))
                elif sub.is_dir() and not sub.name.startswith("."):
                    _visit_dir(folder_name, sub)
            except OSError:
                continue

    try:
        for entry in base.iterdir():
            if entry.is_file():
                if entry.name.startswith(".") or entry.suffix == ".part":
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                rel = entry.relative_to(root).as_posix()
                loose.append(FileEntry(
                    path=entry, rel_path=rel,
                    size=st.st_size,
                    mtime=datetime.fromtimestamp(st.st_mtime),
                ))
            elif entry.is_dir() and not entry.name.startswith("."):
                _visit_dir(entry.name, entry)
    except FileNotFoundError:
        return [], []

    # Sort inside each folder: newest first
    for files in folders_map.values():
        files.sort(key=lambda f: f.mtime, reverse=True)

    # Sort folder list by newest contained file
    folders = sorted(
        ((name, files) for name, files in folders_map.items() if files),
        key=lambda kv: kv[1][0].mtime,
        reverse=True,
    )
    loose.sort(key=lambda f: f.mtime, reverse=True)
    return folders, loose


# ── thumbnail URL / placeholder ──────────────────────────────────────


_THUMB_W = 128
_THUMB_H = 72
_FOLDER_THUMB_W = 180
_FOLDER_THUMB_H = 100


def _placeholder(width: int, height: int, icon: str = ft.Icons.MOVIE_OUTLINED) -> ft.Control:
    return ft.Container(
        content=ft.Icon(icon, size=28, color=theme.Colors.text_dim),
        alignment=ft.alignment.center,
        bgcolor=theme.Colors.surface_3,
        border_radius=6,
        width=width,
        height=height,
    )


def _thumb_image(rel_path: str, width: int, height: int) -> ft.Control:
    """Image control showing /thumbs/<rel_path>.jpg, or a placeholder.

    In desktop mode there's no FastAPI server, so always show a placeholder.
    """
    if not is_web_mode():
        return _placeholder(width, height)
    # urlquote keeps "/" by default — multi-segment subpaths work as-is
    thumb_url = f"/thumbs/{quote(rel_path)}.jpg"
    return ft.Image(
        src=thumb_url,
        width=width,
        height=height,
        fit=ft.ImageFit.COVER,
        border_radius=6,
        error_content=_placeholder(width, height),
    )


# ── rows ──────────────────────────────────────────────────────────────


def _file_row(
    page: ft.Page,
    entry: FileEntry,
    job: Job | None,
    on_copy_prompt=None,
    on_duplicate_as_draft=None,
    on_delete=None,
) -> ft.Control:
    url = f"/downloads/{quote(entry.rel_path)}" if is_web_mode() else None

    def do_download(_: ft.ControlEvent) -> None:
        if url:
            page.launch_url(url)

    title = (job.name if (job and job.name) else entry.path.name).strip() or entry.path.name
    subtitle_parts: list[str] = [
        _format_bytes(entry.size),
        entry.mtime.strftime("%Y-%m-%d %H:%M"),
    ]
    if job is not None:
        subtitle_parts.append(_model_display_name(job.model_task_type))
        gen = _format_duration_seconds(job.generation_seconds)
        if gen is not None:
            subtitle_parts.append(f"⏱ {gen}")

    action_row: list[ft.Control] = []
    if job is not None and (job.prompt or "").strip() and on_copy_prompt is not None:
        action_row.append(ft.IconButton(
            icon=ft.Icons.CONTENT_COPY,
            icon_color=theme.Colors.text_secondary,
            tooltip="Copy prompt text to clipboard",
            on_click=lambda _: on_copy_prompt(job),
        ))
    if job is not None and on_duplicate_as_draft is not None:
        action_row.append(ft.IconButton(
            icon=ft.Icons.FILE_COPY,
            icon_color=theme.Colors.text_secondary,
            tooltip="Duplicate as draft (same settings)",
            on_click=lambda _: on_duplicate_as_draft(job),
        ))
    action_row.append(ft.FilledButton(
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
    ))
    if on_delete is not None:
        action_row.append(ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            icon_color=theme.Colors.state_failed,
            tooltip="Delete video (file + DB record)",
            on_click=lambda _: on_delete(entry, job),
        ))

    return ft.Container(
        content=ft.Row(
            [
                _thumb_image(entry.rel_path, _THUMB_W, _THUMB_H),
                ft.Column(
                    [
                        ft.Text(
                            title,
                            size=13,
                            color=theme.Colors.text_primary,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            selectable=True,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Text(
                            entry.path.name,
                            size=11,
                            color=theme.Colors.text_dim,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            selectable=True,
                        ),
                        ft.Text(
                            "  •  ".join(subtitle_parts),
                            size=11,
                            color=theme.Colors.text_secondary,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                    tight=True,
                ),
                *action_row,
            ],
            spacing=14,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=theme.Colors.surface_2,
        border_radius=8,
        border=ft.border.all(1, theme.Colors.border),
        padding=ft.padding.symmetric(horizontal=14, vertical=10),
    )


def _folder_card(
    folder_name: str,
    files: list[FileEntry],
    on_open,
    *,
    page: ft.Page | None = None,
    folder_rel: str | None = None,
) -> ft.Control:
    # "First video" = chronologically earliest by mtime → last after desc sort
    preview = files[-1]
    total_size = sum(f.size for f in files)
    latest = files[0].mtime
    n = len(files)

    # "Download all" — only meaningful in web mode and when we know which
    # folder to bundle. Clicking the button must NOT also enter the folder
    # (event bubbling), so we wrap it in a GestureDetector / contain it as
    # a separate Container with its own on_click and stop propagation by
    # leaving the outer Container without on_click on this child path.
    right_controls: list[ft.Control] = []
    if page is not None and folder_rel and is_web_mode():
        zip_url = f"/downloads-zip/{quote(folder_rel)}/"

        def _download_all(_e: ft.ControlEvent) -> None:
            page.launch_url(zip_url)

        right_controls.append(
            ft.IconButton(
                icon=ft.Icons.DOWNLOAD_FOR_OFFLINE,
                icon_color=theme.Colors.state_done,
                tooltip=f"Download all {n} as zip",
                on_click=_download_all,
            )
        )
    right_controls.append(
        ft.Icon(
            ft.Icons.CHEVRON_RIGHT_ROUNDED,
            size=24,
            color=theme.Colors.text_dim,
        )
    )

    return ft.Container(
        content=ft.Row(
            [
                _thumb_image(preview.rel_path, _FOLDER_THUMB_W, _FOLDER_THUMB_H),
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.FOLDER_ROUNDED, size=18, color=theme.Colors.primary),
                                ft.Text(
                                    folder_name,
                                    size=14,
                                    weight=ft.FontWeight.W_600,
                                    color=theme.Colors.text_primary,
                                    no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    selectable=True,
                                ),
                            ],
                            spacing=6,
                        ),
                        ft.Text(
                            f"{n} video{'s' if n != 1 else ''}  •  "
                            f"{_format_bytes(total_size)}  •  "
                            f"latest {latest.strftime('%Y-%m-%d %H:%M')}",
                            size=12,
                            color=theme.Colors.text_secondary,
                        ),
                    ],
                    spacing=4,
                    expand=True,
                    tight=True,
                ),
                *right_controls,
            ],
            spacing=14,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=theme.Colors.surface_2,
        border_radius=10,
        border=ft.border.all(1, theme.Colors.border),
        padding=ft.padding.symmetric(horizontal=14, vertical=12),
        ink=True,
        on_click=lambda _e: on_open(folder_name),
    )


def _breadcrumb(folder_name: str, on_back) -> ft.Control:
    return ft.Container(
        content=ft.Row(
            [
                ft.TextButton(
                    text="All folders",
                    icon=ft.Icons.ARROW_BACK_ROUNDED,
                    on_click=lambda _e: on_back(),
                    style=ft.ButtonStyle(
                        color={"": theme.Colors.text_secondary},
                        shape=ft.RoundedRectangleBorder(radius=6),
                    ),
                ),
                ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=18, color=theme.Colors.text_dim),
                ft.Icon(ft.Icons.FOLDER_ROUNDED, size=16, color=theme.Colors.primary),
                ft.Text(
                    folder_name,
                    size=14,
                    weight=ft.FontWeight.W_600,
                    color=theme.Colors.text_primary,
                    selectable=True,
                ),
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.padding.only(left=4, right=4, top=2, bottom=8),
    )


# ── view ──────────────────────────────────────────────────────────────


def build_downloads_view(page: ft.Page, state: AppState) -> ft.View:
    list_column = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
    current_folder: dict[str, str | None] = {"name": None}  # None = root view

    def _toast(msg: str) -> None:
        page.snack_bar = ft.SnackBar(
            ft.Text(msg),
            bgcolor=theme.Colors.surface_2,
        )
        page.snack_bar.open = True
        page.update()

    def copy_job_prompt(job: Job) -> None:
        text = (job.prompt or "").strip()
        if not text:
            _toast("Prompt is empty — nothing to copy.")
            return
        try:
            page.set_clipboard(text)
        except Exception:
            pass
        _toast("Prompt copied to clipboard.")

    def duplicate_job_as_draft(job: Job) -> None:
        bare_name = (job.name or "").split(" #")[0].strip() or None
        new_draft = PromptDraft(
            prompt=job.prompt,
            model_task_type=job.model_task_type,
            duration=job.duration,
            aspect_ratio=job.aspect_ratio,
            resolution=job.resolution,
            audio=job.audio,
            name=bare_name,
            output_dir=job.output_dir,
            count=1,
        )
        state.drafts.append(new_draft)
        _toast("Draft added — taking you to Jobs.")
        page.go("/jobs")

    def confirm_delete(entry: FileEntry, job: Job | None) -> None:
        """Two-step delete: confirm, then unlink the file and drop the
        matching DB row (so it disappears from History too).

        The thumbnail cache file (if any) is also removed — otherwise the
        next scan would still pick up a stale .thumbs/<filename>.jpg.
        """
        async def _do_delete() -> None:
            from app.core import storage
            # File first; if anything below errors out we still want the
            # video gone from disk (the visible part for the user).
            try:
                entry.path.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("delete file failed {}: {}", entry.path, e)
                _toast(f"Could not delete file: {e}")
                return
            # Thumbnail cache (best-effort)
            try:
                thumb = output_dir() / ".thumbs" / (entry.path.name + ".jpg")
                thumb.unlink(missing_ok=True)
            except Exception:
                pass
            # DB row — only if we have a job for this file
            if job is not None:
                try:
                    await storage.delete_job(job.id)
                except Exception as e:  # noqa: BLE001
                    logger.warning("delete_job {} failed: {}", job.id, e)
            page.close(dialog)
            _toast("Video deleted.")
            await reload()

        def _on_confirm(_e: ft.ControlEvent) -> None:
            page.run_task(_do_delete)

        title_text = (job.name if (job and job.name) else entry.path.name).strip() \
            or entry.path.name
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.Colors.surface,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.DELETE_FOREVER, color=theme.Colors.state_failed, size=22),
                    ft.Text(
                        "Delete video?",
                        color=theme.Colors.text_primary,
                        size=16, weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                width=440,
                content=ft.Column(
                    [
                        ft.Text(
                            title_text,
                            size=13,
                            color=theme.Colors.text_primary,
                            weight=ft.FontWeight.W_500,
                            selectable=True,
                        ),
                        ft.Container(height=6),
                        ft.Text(
                            "The file will be removed from disk and the matching "
                            "record from the database. This cannot be undone.",
                            size=12, color=theme.Colors.text_dim,
                        ),
                    ],
                    spacing=0, tight=True,
                ),
            ),
            actions=[
                ft.Row(
                    [
                        theme.secondary_button("Cancel", on_click=lambda _e: page.close(dialog)),
                        ft.FilledButton(
                            text="Delete",
                            icon=ft.Icons.DELETE_OUTLINE,
                            on_click=_on_confirm,
                            style=ft.ButtonStyle(
                                color={"": theme.Colors.bg},
                                bgcolor={"": theme.Colors.state_failed},
                                shape=ft.RoundedRectangleBorder(radius=8),
                            ),
                        ),
                    ],
                    spacing=4,
                ),
            ],
        )
        page.open(dialog)

    empty_state_root = ft.Container(
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

    empty_state_folder = ft.Container(
        content=ft.Text("Folder is empty.", size=12, color=theme.Colors.text_dim),
        alignment=ft.alignment.center,
        padding=ft.padding.symmetric(vertical=40),
    )

    async def reload() -> None:
        # Mine-only Downloads: scan directly inside downloads/<author>/, so
        # the root view shows that author's subfolders (cats, dogs, …)
        # without an extra "Anna" wrapper card. Without a selected author
        # nothing is shown — Active is shared, Downloads is per-author.
        if not state.selected_author:
            folders: list[tuple[str, list[FileEntry]]] = []
            loose: list[FileEntry] = []
            author_seg: str | None = None
        else:
            author_seg = safe_folder_name(state.selected_author)
            try:
                folders, loose = _scan_downloads_grouped(relative_root=author_seg)
            except Exception as e:  # noqa: BLE001
                logger.warning("scan downloads failed: {}", e)
                folders, loose = [], []

        jobs_by_filename: dict[str, Job] = {}
        try:
            jobs = await list_recent_jobs(limit=1000)
            for j in jobs:
                if j.output_path:
                    jobs_by_filename.setdefault(Path(j.output_path).name, j)
        except Exception as e:  # noqa: BLE001
            logger.warning("load jobs for /downloads failed: {}", e)

        controls: list[ft.Control] = []
        cur = current_folder["name"]

        if cur is None:
            for fname, files in folders:
                folder_rel = f"{author_seg}/{fname}" if author_seg else fname
                controls.append(_folder_card(
                    fname, files, enter_folder,
                    page=page, folder_rel=folder_rel,
                ))
            for entry in loose:
                controls.append(_file_row(
                    page, entry, jobs_by_filename.get(entry.path.name),
                    on_copy_prompt=copy_job_prompt,
                    on_duplicate_as_draft=duplicate_job_as_draft,
                    on_delete=confirm_delete,
                ))
            if not controls:
                controls.append(empty_state_root)
        else:
            matched = next((files for n, files in folders if n == cur), None)
            controls.append(_breadcrumb(cur, back_to_root))
            if not matched:
                controls.append(empty_state_folder)
            else:
                for entry in matched:
                    controls.append(_file_row(
                    page, entry, jobs_by_filename.get(entry.path.name),
                    on_copy_prompt=copy_job_prompt,
                    on_duplicate_as_draft=duplicate_job_as_draft,
                    on_delete=confirm_delete,
                ))

        list_column.controls = controls
        try:
            list_column.update()
        except Exception:
            pass

    def enter_folder(name: str) -> None:
        current_folder["name"] = name
        page.run_task(reload)

    def back_to_root() -> None:
        current_folder["name"] = None
        page.run_task(reload)

    def go_back(_: ft.ControlEvent) -> None:
        # Top-bar Back button: if inside a folder, go to root; otherwise leave page.
        if current_folder["name"] is not None:
            back_to_root()
        else:
            page.go("/jobs")

    def refresh(_: ft.ControlEvent) -> None:
        page.run_task(reload)

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

    page.run_task(reload)

    return ft.View(
        route="/downloads",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[ft.Column([header, body], spacing=0, expand=True)],
    )
