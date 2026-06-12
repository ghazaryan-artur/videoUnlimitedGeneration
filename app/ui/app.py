"""Flet app entry point — wiring + route navigation."""
from __future__ import annotations

import flet as ft
from loguru import logger

from app.core.storage import init_db, purge_old_api_logs
from app.paths import is_web_mode
from app.security.license import LicenseError, verify_or_die
from app.ui import theme
from app.ui.state import AppState
from app.ui.views.activation import build_activation_view
from app.ui.views.debug import build_debug_view
from app.ui.views.downloads import build_downloads_view
from app.ui.views.history import build_history_view
from app.ui.views.jobs import build_jobs_view
from app.ui.views.license_manager import build_license_manager_view
from app.ui.views.login import build_login_view
from app.ui.views.splash import build_splash_view


async def main(page: ft.Page) -> None:
    theme.apply(page)
    # In web mode, runtime.startup() (FastAPI lifespan) already initialized
    # the DB. Calling init_db() again here is harmless (idempotent) but the
    # purge below is per-session work that's fine to keep either way.
    await init_db()
    # Purge old api_log rows so the DB doesn't snowball
    try:
        removed = await purge_old_api_logs()
        if removed:
            logger.info("Purged {} old api_log rows", removed)
    except Exception as e:  # noqa: BLE001
        logger.warning("api_log purge skipped: {}", e)

    state = AppState(page)
    state.load_token_from_disk()

    # License gate — only enforced in packaged builds (where the public key is
    # embedded). In dev (empty key) verify_or_die() returns None silently.
    license_state: dict[str, object] = {"ok": True, "error_hint": None}
    try:
        verify_or_die()
    except LicenseError as e:
        license_state["ok"] = False
        license_state["error_hint"] = str(e)
        logger.info("License gate active: {}", e)

    def _resolve_initial() -> str:
        """Pick first screen based on license + auth state."""
        if not license_state["ok"]:
            return "/activate"
        if state.token and not state.token.is_expired:
            return "/jobs"
        return "/login"

    def _on_license_activated() -> None:
        license_state["ok"] = True
        license_state["error_hint"] = None
        page.go(_resolve_initial())

    async def navigate(route: str) -> None:
        page.views.clear()
        # Flet may fire the default "/" route before our explicit page.go() —
        # handle it the same way we handle the initial selection.
        if route in ("", "/"):
            page.go(_resolve_initial())
            return
        # If license gate is active, force /activate regardless of where the
        # user tries to navigate.
        if not license_state["ok"] and route != "/activate":
            page.go("/activate")
            return
        if route == "/activate":
            view = build_activation_view(
                page,
                error_hint=license_state.get("error_hint"),  # type: ignore[arg-type]
                on_activated=_on_license_activated,
            )
        elif route == "/login":
            view = build_login_view(page, state, on_logged_in=lambda: page.go("/jobs"))
        elif route == "/jobs":
            view = build_jobs_view(page, state)
        elif route == "/debug":
            view = build_debug_view(page, state)
        elif route == "/history":
            view = build_history_view(page, state)
        elif route == "/license-manager":
            view = build_license_manager_view(page, state)
        elif route == "/downloads":
            view = build_downloads_view(page, state)
        else:
            page.go(_resolve_initial())
            return
        page.views.append(view)
        page.update()

    async def on_route_change(_: ft.RouteChangeEvent) -> None:
        await navigate(page.route)

    page.on_route_change = on_route_change

    async def on_disconnect(_: ft.Event) -> None:
        await state.teardown()

    page.on_disconnect = on_disconnect

    page.go(_resolve_initial())


def run() -> None:
    """Entry point invoked by run.bat / `python -m app.ui.app`.

    Honours RUNWAY_UI_MODE env: "desktop" (default) or "web" (for headless dev).
    """
    logger.info("Launching Flet app")
    if is_web_mode():
        # Hosted build: own the HTTP layer (FastAPI + uvicorn) so we can
        # serve /downloads/<file>.mp4 with Content-Disposition: attachment.
        from app.ui.server import run_web
        run_web(main)
    else:
        ft.app(target=main, view=ft.AppView.FLET_APP)


if __name__ == "__main__":
    run()
