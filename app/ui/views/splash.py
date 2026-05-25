"""Splash screen — shown briefly during app boot while DB and JWT load."""
from __future__ import annotations

import flet as ft

from app.ui import theme


def build_splash_view(page: ft.Page) -> ft.View:
    return ft.View(
        route="/_splash",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[
            ft.Container(
                content=ft.Column(
                    [
                        ft.Container(height=80),
                        ft.Icon(
                            ft.Icons.AUTO_AWESOME,
                            color=theme.Colors.primary,
                            size=72,
                        ),
                        ft.Container(height=18),
                        ft.Text(
                            "Runway Automation",
                            size=26,
                            weight=ft.FontWeight.W_700,
                            color=theme.Colors.text_primary,
                        ),
                        ft.Container(height=6),
                        ft.Text(
                            "Loading…",
                            size=13,
                            color=theme.Colors.text_dim,
                        ),
                        ft.Container(height=24),
                        ft.ProgressRing(
                            width=28,
                            height=28,
                            stroke_width=3,
                            color=theme.Colors.primary,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                alignment=ft.alignment.top_center,
                expand=True,
            )
        ],
    )
