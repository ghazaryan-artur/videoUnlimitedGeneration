"""License Manager — developer-only view for issuing and tracking licenses.

Visible only when keys/private.pem exists (i.e. on the dev machine).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import flet as ft
from loguru import logger

from app.security.license import LicenseError, issue_license
from app.security.license_ledger import (
    IssuedLicense,
    add_issued,
    delete_issued,
    has_private_key,
    list_issued,
)
from app.ui import theme
from app.ui.state import AppState


def _private_key_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "keys" / "private.pem"


def _format_date(s: str) -> str:
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return s


def _expires_text(lic: IssuedLicense) -> tuple[str, str]:
    """Returns (label, color)."""
    if lic.expires_at is None:
        return "Perpetual", theme.Colors.text_secondary
    if lic.is_expired:
        return f"Expired {_format_date(lic.expires_at)}", theme.Colors.state_failed
    ed = lic.expires_dt
    if ed is None:
        return lic.expires_at, theme.Colors.text_secondary
    days_left = (ed - datetime.now(timezone.utc)).days
    return f"In {days_left} days  ({_format_date(lic.expires_at)})", theme.Colors.state_done


def build_license_manager_view(page: ft.Page, state: AppState) -> ft.View:
    if not has_private_key():
        # Should never reach this in production builds, but render a friendly view
        return ft.View(
            route="/license-manager",
            bgcolor=theme.Colors.bg,
            padding=0,
            controls=[
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(ft.Icons.LOCK, size=48, color=theme.Colors.text_dim),
                            ft.Container(height=10),
                            ft.Text(
                                "License Manager is only available on the developer machine.",
                                size=14, color=theme.Colors.text_secondary,
                            ),
                            ft.Text(
                                "(no keys/private.pem found)",
                                size=12, color=theme.Colors.text_dim,
                            ),
                            ft.Container(height=20),
                            theme.secondary_button(
                                "Back",
                                on_click=lambda _: page.go("/jobs"),
                                icon=ft.Icons.ARROW_BACK_ROUNDED,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    alignment=ft.alignment.center,
                    expand=True,
                )
            ],
        )

    list_column = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
    summary_text = ft.Text("", size=12, color=theme.Colors.text_dim)

    issue_to_field = ft.TextField(
        label="Issue to (recipient label)",
        hint_text="e.g. \"Frunze laptop\" or \"client@example.com\"",
        autofocus=True,
        bgcolor=theme.Colors.surface_2,
        border_color=theme.Colors.border,
        focused_border_color=theme.Colors.primary,
        color=theme.Colors.text_primary,
        label_style=ft.TextStyle(color=theme.Colors.text_secondary),
        cursor_color=theme.Colors.primary,
        text_size=13,
        expand=True,
    )
    expiry_dd = ft.Dropdown(
        label="Expiry",
        value="0",
        options=[
            ft.dropdown.Option("0",  "Perpetual (no expiry)"),
            ft.dropdown.Option("7",  "7 days (trial)"),
            ft.dropdown.Option("30", "30 days"),
            ft.dropdown.Option("90", "90 days"),
            ft.dropdown.Option("180","180 days"),
            ft.dropdown.Option("365","365 days (1 year)"),
            ft.dropdown.Option("730","730 days (2 years)"),
        ],
        bgcolor=theme.Colors.surface_2,
        border_color=theme.Colors.border,
        focused_border_color=theme.Colors.primary,
        color=theme.Colors.text_primary,
        label_style=ft.TextStyle(color=theme.Colors.text_secondary),
        width=240,
    )
    issue_status = ft.Text("", size=12, color=theme.Colors.text_secondary, selectable=True)

    def show_license_string_dialog(license_str: str, label: str) -> None:
        """Modal showing the full license string with copy button."""
        def copy_to_clipboard(_: ft.ControlEvent) -> None:
            try:
                page.set_clipboard(license_str)
                page.snack_bar = ft.SnackBar(
                    ft.Text("License copied to clipboard."),
                    bgcolor=theme.Colors.surface_2,
                )
                page.snack_bar.open = True
                page.update()
            except Exception:
                pass

        modal = ft.AlertDialog(
            modal=True,
            bgcolor=theme.Colors.surface,
            title=ft.Text(
                f"License for: {label}",
                color=theme.Colors.text_primary, size=16, weight=ft.FontWeight.W_600,
            ),
            content=ft.Container(
                width=620,
                content=ft.Column(
                    [
                        ft.Text(
                            "Send this string to the user. They paste it into the app's "
                            "activation screen on first launch.",
                            size=12, color=theme.Colors.text_dim,
                        ),
                        ft.Container(height=10),
                        ft.Container(
                            content=ft.Text(
                                license_str,
                                size=11,
                                color=theme.Colors.text_primary,
                                font_family="monospace",
                                selectable=True,
                            ),
                            bgcolor=theme.Colors.surface_2,
                            border=ft.border.all(1, theme.Colors.border),
                            border_radius=6,
                            padding=ft.padding.all(10),
                            height=200,
                        ),
                    ],
                    spacing=0, tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.Row(
                    [
                        theme.secondary_button("Close", on_click=lambda _e: page.close(modal)),
                        ft.Container(expand=True),
                        theme.primary_button(
                            "Copy",
                            on_click=copy_to_clipboard,
                            icon=ft.Icons.CONTENT_COPY,
                        ),
                    ],
                ),
            ],
        )
        page.open(modal)

    def render_row(lic: IssuedLicense) -> ft.Control:
        exp_text, exp_color = _expires_text(lic)

        def on_view(_: ft.ControlEvent) -> None:
            show_license_string_dialog(lic.license_string, lic.issued_to)

        def on_copy(_: ft.ControlEvent) -> None:
            try:
                page.set_clipboard(lic.license_string)
                page.snack_bar = ft.SnackBar(
                    ft.Text(f"Copied license for: {lic.issued_to}"),
                    bgcolor=theme.Colors.surface_2,
                )
                page.snack_bar.open = True
                page.update()
            except Exception:
                pass

        def on_delete(_: ft.ControlEvent) -> None:
            confirm = ft.AlertDialog(
                modal=True,
                bgcolor=theme.Colors.surface,
                title=ft.Text(
                    "Remove from records?",
                    color=theme.Colors.text_primary, size=16, weight=ft.FontWeight.W_600,
                ),
                content=ft.Container(
                    content=ft.Text(
                        f"This removes the entry for '{lic.issued_to}' from your local "
                        f"ledger.\n\nNote: it does NOT revoke the license — without an "
                        f"online server, an Ed25519 license already in the user's hands "
                        f"keeps working until it expires. To force-invalidate, rotate the "
                        f"keypair and rebuild the .exe.",
                        size=12, color=theme.Colors.text_secondary,
                    ),
                    width=460,
                ),
                actions=[
                    theme.secondary_button("Cancel", on_click=lambda _e: page.close(confirm)),
                    ft.FilledButton(
                        text="Remove",
                        icon=ft.Icons.DELETE_OUTLINE,
                        on_click=lambda _e: (
                            delete_issued(lic.license_id),
                            page.close(confirm),
                            refresh(),
                        ),
                        style=ft.ButtonStyle(
                            color={"": theme.Colors.bg},
                            bgcolor={"": theme.Colors.state_failed},
                            shape=ft.RoundedRectangleBorder(radius=8),
                        ),
                    ),
                ],
            )
            page.open(confirm)

        return ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                lic.issued_to,
                                size=13, weight=ft.FontWeight.W_600,
                                color=theme.Colors.text_primary,
                                no_wrap=True,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.SCHEDULE, size=12, color=theme.Colors.text_dim),
                                    ft.Text(
                                        f"Issued {_format_date(lic.issued_at)}",
                                        size=11, color=theme.Colors.text_dim,
                                    ),
                                    ft.Container(width=10),
                                    ft.Icon(
                                        ft.Icons.EVENT,
                                        size=12,
                                        color=exp_color,
                                    ),
                                    ft.Text(
                                        exp_text,
                                        size=11, color=exp_color,
                                    ),
                                ],
                                spacing=4,
                            ),
                            ft.Text(
                                f"id: {lic.license_id}",
                                size=10, color=theme.Colors.text_dim,
                                font_family="monospace",
                                selectable=True,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.VISIBILITY,
                        icon_color=theme.Colors.text_secondary,
                        tooltip="View full license string",
                        on_click=on_view,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CONTENT_COPY,
                        icon_color=theme.Colors.text_secondary,
                        tooltip="Copy license string to clipboard",
                        on_click=on_copy,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color=theme.Colors.text_dim,
                        tooltip="Remove from records (does NOT revoke)",
                        on_click=on_delete,
                    ),
                ],
                spacing=4,
            ),
            bgcolor=theme.Colors.surface_2,
            border=ft.border.all(1, theme.Colors.border),
            border_radius=8,
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
        )

    def refresh() -> None:
        try:
            licenses = list_issued()
        except Exception as e:  # noqa: BLE001
            logger.warning("list_issued failed: {}", e)
            licenses = []
        list_column.controls.clear()
        if not licenses:
            list_column.controls.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(ft.Icons.KEY_OFF, size=44, color=theme.Colors.text_dim),
                            ft.Container(height=10),
                            ft.Text("No licenses issued yet.", size=14, color=theme.Colors.text_secondary),
                            ft.Text(
                                "Use the form above to issue your first license.",
                                size=12, color=theme.Colors.text_dim,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    alignment=ft.alignment.center,
                    padding=ft.padding.symmetric(vertical=60),
                )
            )
        else:
            for lic in licenses:
                list_column.controls.append(render_row(lic))

        active_count = sum(1 for l in licenses if not l.is_expired)
        expired_count = len(licenses) - active_count
        summary_text.value = (
            f"{len(licenses)} total  •  {active_count} active  •  {expired_count} expired"
        )
        try:
            list_column.update()
            summary_text.update()
        except Exception:
            pass

    async def do_issue(_: ft.ControlEvent) -> None:
        name = (issue_to_field.value or "").strip()
        if not name:
            issue_status.value = "Recipient label is required."
            issue_status.color = theme.Colors.state_failed
            issue_status.update()
            return
        try:
            days = int(expiry_dd.value or "0")
        except ValueError:
            days = 0
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=days)
            if days > 0
            else None
        )

        priv = _private_key_path()
        if not priv.exists():
            issue_status.value = "Private key not found at keys/private.pem."
            issue_status.color = theme.Colors.state_failed
            issue_status.update()
            return

        try:
            license_str = issue_license(
                priv.read_bytes(),
                issued_to=name,
                expires_at=expires_at,
            )
        except LicenseError as e:
            issue_status.value = f"Issue failed: {e}"
            issue_status.color = theme.Colors.state_failed
            issue_status.update()
            return

        try:
            entry = add_issued(license_str)
        except Exception as e:  # noqa: BLE001
            issue_status.value = f"Ledger write failed: {e}"
            issue_status.color = theme.Colors.state_failed
            issue_status.update()
            return

        issue_to_field.value = ""
        expiry_dd.value = "0"
        issue_status.value = f"Issued license for '{entry.issued_to}'  (id={entry.license_id})"
        issue_status.color = theme.Colors.state_done
        issue_status.update()
        issue_to_field.update()
        expiry_dd.update()
        refresh()
        # Auto-show the string so the dev can copy it right away
        show_license_string_dialog(license_str, entry.issued_to)

    issue_btn = theme.primary_button(
        "Issue license", on_click=do_issue, icon=ft.Icons.KEY,
    )

    header = ft.Container(
        content=ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.ARROW_BACK_ROUNDED,
                    icon_color=theme.Colors.text_secondary,
                    on_click=lambda _: page.go("/jobs"),
                    tooltip="Back",
                ),
                ft.Text(
                    "License Manager", size=18, weight=ft.FontWeight.W_700,
                    color=theme.Colors.text_primary,
                ),
                ft.Container(width=10),
                summary_text,
                ft.Container(expand=True),
                theme.secondary_button(
                    "Refresh", on_click=lambda _: refresh(), icon=ft.Icons.REFRESH,
                ),
            ],
            spacing=8,
        ),
        bgcolor=theme.Colors.surface,
        padding=ft.padding.symmetric(horizontal=24, vertical=14),
        border=ft.border.only(bottom=ft.BorderSide(1, theme.Colors.border)),
    )

    issue_form = ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    "ISSUE NEW LICENSE",
                    size=11, weight=ft.FontWeight.W_700, color=theme.Colors.text_dim,
                ),
                ft.Container(height=8),
                ft.Row([issue_to_field, expiry_dd], spacing=10),
                ft.Container(height=10),
                ft.Row([issue_btn, issue_status], spacing=12),
            ],
            spacing=0, tight=True,
        ),
        bgcolor=theme.Colors.surface,
        border=ft.border.all(1, theme.Colors.border),
        border_radius=10,
        padding=ft.padding.all(14),
    )

    body = ft.Container(
        content=ft.Column(
            [
                issue_form,
                ft.Container(height=14),
                ft.Text(
                    "ISSUED LICENSES",
                    size=11, weight=ft.FontWeight.W_700, color=theme.Colors.text_dim,
                ),
                ft.Container(height=8),
                list_column,
            ],
            spacing=0, expand=True,
        ),
        padding=ft.padding.symmetric(horizontal=24, vertical=18),
        expand=True,
    )

    refresh()

    return ft.View(
        route="/license-manager",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[ft.Column([header, body], spacing=0, expand=True)],
    )
