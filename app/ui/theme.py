"""Flet theme — modern dark Material 3 with a purple-cyan accent.

Inspired by Runway's own brand (cool dark with a vibrant primary). All colour
tokens live here so the rest of the UI stays declarative.
"""
from __future__ import annotations

import flet as ft


# ── colour tokens ─────────────────────────────────────────────────────


class Colors:
    # Surfaces
    bg = "#0E0F13"
    surface = "#161821"
    surface_2 = "#1E2230"
    surface_3 = "#262B3C"
    surface_done = "#1F3027"   # subtle green tint over surface_2 — "already downloaded" marker
    border = "#2D3346"
    border_done = "#2F5A45"    # matching border for surface_done

    # Text
    text_primary = "#F4F5FA"
    text_secondary = "#A0A6B8"
    text_dim = "#6B7186"

    # Brand
    primary = "#A88BFF"        # soft violet
    primary_hover = "#BFA9FF"
    primary_dim = "#5A4D88"

    accent = "#36E1C5"          # mint/teal
    accent_warm = "#FFB259"     # amber

    # State colours (matched to JobStatus)
    state_queued = "#5A8BFF"        # blue
    state_generating = "#A88BFF"    # purple
    state_downloading = "#36E1C5"   # mint
    state_done = "#54D27A"          # green
    state_failed = "#FF6E7A"        # red
    state_cancelled = "#6B7186"     # grey


# ── status helpers ────────────────────────────────────────────────────


# Maps app.core.job.JobStatus → (color, icon, label)
STATUS_VISUALS: dict[str, tuple[str, str, str]] = {
    "PENDING":     (Colors.text_dim,           ft.Icons.HOURGLASS_EMPTY,   "Pending"),
    "SUBMITTING":  (Colors.state_queued,       ft.Icons.UPLOAD,            "Submitting"),
    "QUEUED":      (Colors.state_queued,       ft.Icons.SCHEDULE,          "Queued"),
    "GENERATING":  (Colors.state_generating,   ft.Icons.MOVIE_FILTER,      "Generating"),
    "DOWNLOADING": (Colors.state_downloading,  ft.Icons.CLOUD_DOWNLOAD,    "Downloading"),
    "DONE":        (Colors.state_done,         ft.Icons.CHECK_CIRCLE,      "Done"),
    "FAILED":      (Colors.state_failed,       ft.Icons.ERROR,             "Failed"),
    "CANCELLED":   (Colors.state_cancelled,    ft.Icons.BLOCK,             "Cancelled"),
}


def status_visual(status: str) -> tuple[str, str, str]:
    return STATUS_VISUALS.get(status, (Colors.text_dim, ft.Icons.HELP, status))


# ── apply theme to a Flet page ────────────────────────────────────────


def apply(page: ft.Page) -> None:
    page.title = "Runway Automation"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = Colors.bg
    page.padding = 0
    page.window.min_width = 1100
    page.window.min_height = 720
    page.window.width = 1280
    page.window.height = 820

    page.theme = ft.Theme(
        color_scheme_seed=Colors.primary,
        color_scheme=ft.ColorScheme(
            primary=Colors.primary,
            on_primary="#0E0F13",
            secondary=Colors.accent,
            surface=Colors.surface,
            on_surface=Colors.text_primary,
            background=Colors.bg,
            on_background=Colors.text_primary,
            error=Colors.state_failed,
            outline=Colors.border,
        ),
        font_family="Inter",
        visual_density=ft.VisualDensity.COMFORTABLE,
        use_material3=True,
    )

    page.fonts = {
        "Inter": "https://rsms.me/inter/font-files/InterVariable.ttf",
    }


# ── reusable text styles ──────────────────────────────────────────────


def heading(text: str, *, size: int = 24) -> ft.Text:
    return ft.Text(text, size=size, weight=ft.FontWeight.W_600, color=Colors.text_primary)


def subheading(text: str) -> ft.Text:
    return ft.Text(text, size=14, weight=ft.FontWeight.W_500, color=Colors.text_secondary)


def caption(text: str) -> ft.Text:
    return ft.Text(text, size=12, color=Colors.text_dim)


def primary_button(text: str, on_click=None, *, icon: str | None = None,
                   width: int | None = None) -> ft.FilledButton:
    return ft.FilledButton(
        text=text,
        icon=icon,
        on_click=on_click,
        width=width,
        style=ft.ButtonStyle(
            color={"": Colors.bg},
            bgcolor={"": Colors.primary, "hovered": Colors.primary_hover},
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.padding.symmetric(horizontal=24, vertical=14),
            text_style=ft.TextStyle(weight=ft.FontWeight.W_600, size=14),
        ),
    )


def secondary_button(text: str, on_click=None, *, icon: str | None = None) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        text=text,
        icon=icon,
        on_click=on_click,
        style=ft.ButtonStyle(
            color={"": Colors.text_primary},
            side={"": ft.BorderSide(1, Colors.border)},
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=ft.padding.symmetric(horizontal=20, vertical=14),
            text_style=ft.TextStyle(weight=ft.FontWeight.W_500, size=14),
        ),
    )


def card_container(content: ft.Control, *, padding: int | ft.Padding = 20,
                   bgcolor: str = Colors.surface) -> ft.Container:
    return ft.Container(
        content=content,
        bgcolor=bgcolor,
        border=ft.border.all(1, Colors.border),
        border_radius=14,
        padding=padding,
    )


# ── author / credits ──────────────────────────────────────────────────


AUTHOR_NAME = "Artur Ghazaryan"
AUTHOR_GITHUB_URL = "https://github.com/ghazaryan-artur"
AUTHOR_GITHUB_HANDLE = "github.com/ghazaryan-artur"


def author_footer(page: ft.Page) -> ft.Control:
    """Small "Made by NAME · github.com/HANDLE" footer with a clickable link.

    Drop into login / activation views (or anywhere else) for credit.
    """
    return ft.Row(
        [
            ft.Text("Made by ", size=11, color=Colors.text_dim),
            ft.Text(
                AUTHOR_NAME,
                size=11,
                weight=ft.FontWeight.W_500,
                color=Colors.text_secondary,
                selectable=True,
            ),
            ft.Text("  ·  ", size=11, color=Colors.text_dim),
            ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.OPEN_IN_NEW, size=11, color=Colors.primary),
                        ft.Text(
                            AUTHOR_GITHUB_HANDLE,
                            size=11,
                            color=Colors.primary,
                            weight=ft.FontWeight.W_500,
                        ),
                    ],
                    spacing=4,
                    tight=True,
                ),
                on_click=lambda _: page.launch_url(AUTHOR_GITHUB_URL),
                tooltip="Open the author's GitHub profile",
                padding=ft.padding.symmetric(horizontal=4, vertical=2),
                border_radius=4,
                ink=True,
            ),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=0,
        tight=True,
    )
