"""Async HTTP client for api.runwayml.com.

Handles auth-header injection, JSON serialization, retry on transient
network errors, and emits structured log events for the Debug panel.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from loguru import logger

from app.settings import settings


@dataclass(frozen=True)
class ApiLogEntry:
    ts: str
    method: str
    url: str
    status: int | None
    duration_ms: float
    request_body: str | None
    response_body: str | None
    error: str | None


# Subscribers (e.g. Debug panel) register a callback to receive each entry live.
ApiLogSink = Callable[[ApiLogEntry], Awaitable[None] | None]


class RunwayClient:
    """Stateless async wrapper around api.runwayml.com.

    Token is provided per-call (or set via `set_token`) — the client itself
    does not own auth state. That lives in `app.runway.auth`.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        sinks: list[ApiLogSink] | None = None,
    ) -> None:
        self._base_url = (base_url or settings.api_base).rstrip("/")
        self._token = token
        self._sinks: list[ApiLogSink] = list(sinks or [])
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_seconds, connect=10.0),
            http2=True,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://app.runwayml.com",
                "Referer": "https://app.runwayml.com/",
            },
        )

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def is_closed(self) -> bool:
        """True after `aclose()` (or after a context-manager exit)."""
        return self._client.is_closed

    async def __aenter__(self) -> "RunwayClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ── token management ──────────────────────────────────────────────────

    def set_token(self, token: str | None) -> None:
        self._token = token

    @property
    def token(self) -> str | None:
        return self._token

    # ── debug log subscribers ─────────────────────────────────────────────

    def add_sink(self, sink: ApiLogSink) -> None:
        self._sinks.append(sink)

    async def _emit(self, entry: ApiLogEntry) -> None:
        for sink in self._sinks:
            try:
                result = sink(entry)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:  # noqa: BLE001
                logger.warning("api log sink raised: {}", e)

    # ── core request method ───────────────────────────────────────────────

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        retries: int = 2,
    ) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)

        request_body_str = json.dumps(json_body, ensure_ascii=False) if json_body is not None else None

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            t0 = asyncio.get_event_loop().time()
            try:
                resp = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                )
                duration_ms = (asyncio.get_event_loop().time() - t0) * 1000
                # Parse body
                resp_body_str: str | None = None
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        resp_body_str = resp.text
                    except Exception:
                        resp_body_str = None
                else:
                    resp_body_str = resp.text[:2000] if resp.text else None

                await self._emit(ApiLogEntry(
                    ts=datetime.now().isoformat(timespec="milliseconds"),
                    method=method,
                    url=url,
                    status=resp.status_code,
                    duration_ms=duration_ms,
                    request_body=request_body_str,
                    response_body=resp_body_str,
                    error=None,
                ))

                if resp.status_code >= 500 and attempt < retries:
                    # Transient — retry with backoff
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                if resp.status_code >= 400:
                    raise RunwayApiError(
                        method, url, resp.status_code, resp_body_str or ""
                    )
                if "json" in ct and resp.text:
                    return resp.json()
                return {"raw": resp.text}

            except httpx.RequestError as e:
                duration_ms = (asyncio.get_event_loop().time() - t0) * 1000
                last_err = e
                await self._emit(ApiLogEntry(
                    ts=datetime.now().isoformat(timespec="milliseconds"),
                    method=method,
                    url=url,
                    status=None,
                    duration_ms=duration_ms,
                    request_body=request_body_str,
                    response_body=None,
                    error=f"{type(e).__name__}: {e}",
                ))
                if attempt < retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise

        raise RunwayApiError(method, url, -1, str(last_err) if last_err else "unknown")

    # ── streaming download ────────────────────────────────────────────────

    async def stream_download(
        self,
        url: str,
        *,
        chunk_size: int = 256 * 1024,
    ) -> AsyncIterator[bytes]:
        """Stream bytes from a URL (e.g. CloudFront mp4). No auth header — the
        URLs are JWT-signed query params."""
        timeout = httpx.Timeout(settings.download_timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout, http2=True) as c:
            async with c.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                    yield chunk


class RunwayApiError(RuntimeError):
    """API returned a non-2xx response."""

    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        super().__init__(f"{method} {url} → {status}: {body[:300]}")
        self.method = method
        self.url = url
        self.status = status
        self.body = body
