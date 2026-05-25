"""Authentication: API login + JWT persistence + browser-login fallback.

Two paths:
  - api_login(username, password) → calls POST /v1/login, stores token.
  - browser_login() → opens Playwright Chromium, user logs in by hand,
    we sniff Authorization header from a request to api.runwayml.com.
"""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.paths import jwt_file, browser_profile_dir
from app.runway.client import RunwayClient


@dataclass(frozen=True)
class StoredToken:
    token: str
    user_id: int | None
    email: str | None
    issued_at: datetime
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def seconds_remaining(self) -> int:
        return max(0, int((self.expires_at - datetime.now(timezone.utc)).total_seconds()))


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    pad = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(pad))


def _stored_from_token(token: str) -> StoredToken:
    payload = _decode_jwt_payload(token)
    return StoredToken(
        token=token,
        user_id=payload.get("id"),
        email=payload.get("email"),
        issued_at=datetime.fromtimestamp(payload.get("iat", 0), tz=timezone.utc),
        expires_at=datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc),
    )


def load_token() -> StoredToken | None:
    p: Path = jwt_file()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        token = data.get("token")
        if not token:
            return None
        stored = _stored_from_token(token)
        if stored.is_expired:
            logger.info("Stored JWT is expired (since {}), discarding.", stored.expires_at)
            return None
        return stored
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not load stored JWT: {}", e)
        return None


def save_token(token: str) -> StoredToken:
    stored = _stored_from_token(token)
    p = jwt_file()
    p.write_text(
        json.dumps({
            "token": token,
            "user_id": stored.user_id,
            "email": stored.email,
            "issued_at": stored.issued_at.isoformat(),
            "expires_at": stored.expires_at.isoformat(),
        }, indent=2),
        encoding="utf-8",
    )
    return stored


def clear_token() -> None:
    p = jwt_file()
    if p.exists():
        p.unlink()


# ─── API login (default path) ────────────────────────────────────────────


async def api_login(
    username: str,
    password: str,
    *,
    client: RunwayClient | None = None,
) -> StoredToken:
    """POST /v1/login with username + password; returns stored token."""
    own = client is None
    c = client or RunwayClient()
    try:
        resp = await c.request(
            "POST",
            "/v1/login",
            json_body={
                "username": username,
                "password": password,
                "machineId": None,
            },
        )
    finally:
        if own:
            await c.aclose()

    token = resp.get("token")
    if not token:
        raise RuntimeError(f"Login response missing token: {resp}")

    stored = save_token(token)
    logger.info("API login successful for user_id={} (expires {})", stored.user_id, stored.expires_at)
    return stored


# ─── Browser login (fallback path) ───────────────────────────────────────


async def browser_login(
    *,
    headless: bool = False,
    timeout_seconds: int = 600,
) -> StoredToken:
    """Open Chromium; sniff Authorization header from any api.runwayml.com call.

    Workflow:
      1. Launch Chromium (persistent profile so cookies survive).
      2. Navigate to https://app.runwayml.com/login.
      3. User logs in (handles 2FA / captcha however the site presents them).
      4. As soon as we see an Authorization: Bearer ... on api.runwayml.com,
         capture the token and close.
    """
    from playwright.async_api import async_playwright  # lazy import — heavy

    captured: dict[str, str] = {}
    done = asyncio.Event()

    def _on_request(req: Any) -> None:
        if "api.runwayml.com" not in req.url:
            return
        auth = req.headers.get("authorization") or req.headers.get("Authorization")
        if auth and auth.startswith("Bearer ey") and "token" not in captured:
            captured["token"] = auth.removeprefix("Bearer ").strip()
            done.set()

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile_dir()),
            headless=headless,
            viewport={"width": 1280, "height": 820},
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.on("request", _on_request)
        for existing in ctx.pages:
            existing.on("request", _on_request)

        await page.goto("https://app.runwayml.com/login", wait_until="domcontentloaded")

        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            await ctx.close()
            raise RuntimeError(f"Browser login timed out after {timeout_seconds}s.")

        await ctx.close()

    token = captured["token"]
    stored = save_token(token)
    logger.info("Browser login successful for user_id={}", stored.user_id)
    return stored


# ─── Helper for all task code ────────────────────────────────────────────


async def get_active_token() -> StoredToken:
    """Return a valid token, or raise. Does NOT prompt — UI handles that."""
    stored = load_token()
    if stored is None:
        raise RuntimeError("No stored token. Login required.")
    return stored
