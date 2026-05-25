"""Login view — email + password (primary) + browser fallback (secondary)."""
from __future__ import annotations

from collections.abc import Callable

import flet as ft
from loguru import logger

from app.runway.auth import api_login, browser_login
from app.runway.client import RunwayApiError
from app.ui import theme
from app.ui.state import AppState


def build_login_view(
    page: ft.Page,
    state: AppState,
    *,
    on_logged_in: Callable[[], None],
) -> ft.View:
    username = ft.TextField(
        label="Email",
        autofocus=True,
        keyboard_type=ft.KeyboardType.EMAIL,
        bgcolor=theme.Colors.surface_2,
        border_color=theme.Colors.border,
        focused_border_color=theme.Colors.primary,
        color=theme.Colors.text_primary,
        label_style=ft.TextStyle(color=theme.Colors.text_secondary),
        cursor_color=theme.Colors.primary,
    )
    password = ft.TextField(
        label="Password",
        password=True,
        can_reveal_password=True,
        bgcolor=theme.Colors.surface_2,
        border_color=theme.Colors.border,
        focused_border_color=theme.Colors.primary,
        color=theme.Colors.text_primary,
        label_style=ft.TextStyle(color=theme.Colors.text_secondary),
        cursor_color=theme.Colors.primary,
    )
    error_text = ft.Text("", color=theme.Colors.state_failed, size=13)

    progress = ft.ProgressRing(
        width=18, height=18, stroke_width=2, color=theme.Colors.primary, visible=False
    )

    def set_busy(busy: bool) -> None:
        progress.visible = busy
        login_btn.disabled = busy
        browser_btn.disabled = busy
        username.disabled = busy
        password.disabled = busy
        page.update()

    async def do_api_login(_: ft.ControlEvent) -> None:
        error_text.value = ""
        if not username.value or not password.value:
            error_text.value = "Email and password are required."
            page.update()
            return
        set_busy(True)
        try:
            token = await api_login(username.value.strip(), password.value)
            state.install_client(token)
            on_logged_in()
        except RunwayApiError as e:
            error_text.value = f"Login failed ({e.status})." if e.status > 0 else f"Network error: {e.body}"
            logger.warning("API login failed: {}", e)
        except Exception as e:  # noqa: BLE001
            error_text.value = f"Login error: {e}"
            logger.exception("API login error")
        finally:
            set_busy(False)

    async def do_browser_login(_: ft.ControlEvent) -> None:
        error_text.value = ""
        set_busy(True)
        try:
            token = await browser_login(headless=False)
            state.install_client(token)
            on_logged_in()
        except Exception as e:  # noqa: BLE001
            error_text.value = f"Browser login failed: {e}"
            logger.exception("Browser login error")
        finally:
            set_busy(False)

    login_btn = theme.primary_button("Sign in", on_click=do_api_login, width=320)
    browser_btn = theme.secondary_button(
        "Sign in via browser",
        on_click=do_browser_login,
        icon=ft.Icons.OPEN_IN_BROWSER,
    )

    # Submit on Enter
    def on_submit(_: ft.ControlEvent) -> None:
        page.run_task(do_api_login, None)

    username.on_submit = on_submit
    password.on_submit = on_submit

    card = theme.card_container(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.AUTO_AWESOME, color=theme.Colors.primary, size=28),
                        ft.Text(
                            "Runway Automation",
                            size=22,
                            weight=ft.FontWeight.W_700,
                            color=theme.Colors.text_primary,
                        ),
                    ],
                    spacing=10,
                ),
                ft.Container(height=4),
                theme.subheading("Sign in to your Runway account to continue."),
                ft.Container(height=20),
                username,
                ft.Container(height=10),
                password,
                ft.Container(height=8),
                ft.Row([progress, error_text], spacing=10),
                ft.Container(height=12),
                login_btn,
                ft.Container(height=10),
                ft.Row(
                    [
                        ft.Container(height=1, bgcolor=theme.Colors.border, expand=True),
                        ft.Text("OR", size=11, color=theme.Colors.text_dim),
                        ft.Container(height=1, bgcolor=theme.Colors.border, expand=True),
                    ],
                    spacing=12,
                ),
                ft.Container(height=10),
                browser_btn,
                ft.Container(height=8),
                theme.caption(
                    "Browser fallback opens Chromium so you can log in manually "
                    "(useful if email/password is rejected)."
                ),
            ],
            spacing=0,
        ),
        padding=ft.padding.all(28),
    )

    return ft.View(
        route="/login",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[
            ft.Container(
                content=ft.Column(
                    [
                        ft.Container(height=80),
                        ft.Container(content=card, width=420),
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
