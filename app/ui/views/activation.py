"""Activation view — paste license string + Activate button.

Shown automatically when verify_or_die() raises LicenseError (no license
file, expired, or signature doesn't match the embedded public key).
"""
from __future__ import annotations

from collections.abc import Callable

import flet as ft
from loguru import logger

from app.security.license import LicenseError, install_license
from app.ui import theme


def build_activation_view(
    page: ft.Page,
    *,
    error_hint: str | None = None,
    on_activated: Callable[[], None] | None = None,
) -> ft.View:
    license_field = ft.TextField(
        label="Paste your license key here",
        autofocus=True,
        multiline=True,
        min_lines=4,
        max_lines=8,
        bgcolor=theme.Colors.surface_2,
        border_color=theme.Colors.border,
        focused_border_color=theme.Colors.primary,
        color=theme.Colors.text_primary,
        label_style=ft.TextStyle(color=theme.Colors.text_secondary),
        cursor_color=theme.Colors.primary,
        text_size=12,
        text_style=ft.TextStyle(font_family="monospace"),
    )
    error_text = ft.Text(
        error_hint or "",
        color=theme.Colors.state_failed,
        size=12,
        selectable=True,
    )
    progress = ft.ProgressRing(
        width=18, height=18, stroke_width=2,
        color=theme.Colors.primary, visible=False,
    )

    def do_activate(_: ft.ControlEvent) -> None:
        s = (license_field.value or "").strip()
        if not s:
            error_text.value = "Please paste your license key."
            error_text.update()
            return
        progress.visible = True
        error_text.value = ""
        page.update()
        try:
            lic = install_license(s)
        except LicenseError as e:
            error_text.value = f"License rejected: {e}"
            progress.visible = False
            page.update()
            return
        except Exception as e:  # noqa: BLE001
            error_text.value = f"Activation failed: {e}"
            progress.visible = False
            page.update()
            logger.exception("install_license error")
            return

        progress.visible = False
        page.snack_bar = ft.SnackBar(
            ft.Text(f"Activated for: {lic.issued_to}"),
            bgcolor=theme.Colors.surface_2,
        )
        page.snack_bar.open = True
        page.update()
        if on_activated is not None:
            on_activated()

    activate_btn = theme.primary_button(
        "Activate",
        on_click=do_activate,
        icon=ft.Icons.KEY,
        width=320,
    )

    card = theme.card_container(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.LOCK_OUTLINE, color=theme.Colors.primary, size=28),
                        ft.Text(
                            "Activate Runway Automation",
                            size=20,
                            weight=ft.FontWeight.W_700,
                            color=theme.Colors.text_primary,
                        ),
                    ],
                    spacing=10,
                ),
                ft.Container(height=4),
                theme.subheading(
                    "This copy needs a license key before it can run. "
                    "Paste the key you received from the developer."
                ),
                ft.Container(height=20),
                license_field,
                ft.Container(height=8),
                ft.Row([progress, error_text], spacing=10),
                ft.Container(height=12),
                activate_btn,
            ],
            spacing=0,
        ),
        padding=ft.padding.all(28),
    )

    return ft.View(
        route="/activate",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[
            ft.Container(
                content=ft.Column(
                    [
                        ft.Container(height=80),
                        ft.Container(content=card, width=560),
                        ft.Container(height=24),
                        theme.author_footer(page),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                expand=True,
                alignment=ft.alignment.top_center,
            )
        ],
    )
