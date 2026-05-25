"""High-level Runway operations: prepare a session, submit a task, poll, download.

Flow per generation:
  1. ensure_session() — create session + assetGroup once per batch run, cached.
  2. submit_task(profile, prompt, ...) → task_id
  3. poll_task(task_id) → yields TaskStatus updates until SUCCEEDED / FAILED.
  4. download_artifact(artifact_url, dest) → streams mp4 to disk.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from app.runway.client import RunwayClient
from app.runway.models.base import ModelProfile
from app.settings import settings


# ── data classes ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SessionContext:
    team_id: int
    session_id: str
    asset_group_id: str


@dataclass
class TaskStatus:
    task_id: str
    status: str          # PENDING / THROTTLED / RUNNING / SUCCEEDED / FAILED
    progress_ratio: float
    estimated_start_seconds: int | None
    error: str | None
    artifacts: list[dict[str, Any]]
    raw: dict[str, Any]

    @classmethod
    def from_response(cls, resp: dict[str, Any]) -> "TaskStatus":
        task = resp.get("task") or {}
        try:
            progress = float(task.get("progressRatio") or 0.0)
        except (TypeError, ValueError):
            progress = 0.0

        # error can be: None, str, or {"code":"...","message":"..."} — normalize to str
        raw_error = task.get("error")
        if raw_error is None:
            error_str: str | None = None
        elif isinstance(raw_error, str):
            error_str = raw_error
        elif isinstance(raw_error, dict):
            error_str = (
                raw_error.get("message")
                or raw_error.get("error")
                or raw_error.get("code")
                or json.dumps(raw_error, ensure_ascii=False)[:500]
            )
        else:
            error_str = str(raw_error)[:500]

        # estimatedTimeToStartSeconds usually int, but be defensive
        eta = task.get("estimatedTimeToStartSeconds")
        try:
            eta_int = int(eta) if eta is not None else None
        except (TypeError, ValueError):
            eta_int = None

        return cls(
            task_id=task.get("id", "<unknown>"),
            status=task.get("status", "UNKNOWN"),
            progress_ratio=progress,
            estimated_start_seconds=eta_int,
            error=error_str,
            artifacts=task.get("artifacts") or [],
            raw=task,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in ("SUCCEEDED", "FAILED", "CANCELLED")

    @property
    def is_success(self) -> bool:
        return self.status == "SUCCEEDED"


# ── session & team setup ────────────────────────────────────────────────


async def fetch_team_id(client: RunwayClient) -> int:
    """Get the user's primary team ID from /v1/profile."""
    resp = await client.request("GET", "/v1/profile")
    user = resp.get("user") or {}
    team_id = user.get("id")
    if team_id is None:
        raise RuntimeError(f"Could not derive team_id from profile: {resp}")
    return int(team_id)


async def create_session(client: RunwayClient, team_id: int) -> str:
    resp = await client.request(
        "POST",
        "/v1/sessions",
        json_body={"asTeamId": team_id, "taskIds": []},
    )
    sess = resp.get("session") or resp
    sid = sess.get("id") or resp.get("id")
    if not sid:
        raise RuntimeError(f"Could not parse session id: {resp}")
    return sid


async def create_asset_group(client: RunwayClient, session_id: str, team_id: int) -> str:
    resp = await client.request(
        "POST",
        f"/v1/sessions/{session_id}/assetGroup",
        json_body={"asTeamId": team_id},
    )
    ag = resp.get("assetGroup") or resp
    agid = ag.get("id") or resp.get("id")
    if not agid:
        raise RuntimeError(f"Could not parse assetGroup id: {resp}")
    return agid


async def ensure_session(client: RunwayClient) -> SessionContext:
    team_id = await fetch_team_id(client)
    sid = await create_session(client, team_id)
    agid = await create_asset_group(client, sid, team_id)
    logger.info("Session ready  team={} session={} assetGroup={}", team_id, sid, agid)
    return SessionContext(team_id=team_id, session_id=sid, asset_group_id=agid)


# ── task submission & polling ───────────────────────────────────────────


async def submit_task(
    client: RunwayClient,
    *,
    ctx: SessionContext,
    profile: ModelProfile,
    name: str,
    prompt: str,
    duration: int,
    aspect_ratio: str,
    resolution: str,
    audio: bool,
) -> str:
    profile.validate_prompt(prompt)
    profile.validate_duration(duration)
    profile.validate_aspect_ratio(aspect_ratio)

    options = profile.build_options(
        name=name,
        prompt=prompt,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        audio=audio,
        asset_group_id=ctx.asset_group_id,
    )
    body = {
        "taskType": profile.task_type,
        "options": options,
        "asTeamId": ctx.team_id,
        "sessionId": ctx.session_id,
    }
    resp = await client.request("POST", "/v1/tasks", json_body=body)
    task = resp.get("task") or resp
    task_id = task.get("id")
    if not task_id:
        raise RuntimeError(f"Could not parse task id: {resp}")
    logger.info("Task submitted  id={} model={}", task_id, profile.task_type)
    return task_id


async def register_task_in_ui(
    client: RunwayClient,
    *,
    ctx: SessionContext,
    runway_task_id: str,
    name: str,
    prompt: str,
    duration: int,
    audio: bool,
) -> None:
    """Optional follow-up call: makes the task visible in app.runwayml.com UI.

    Without this, the task is created and runs server-side but does not show
    up in the user's Generations panel. The browser does these calls after
    every POST /v1/tasks.

    Best-effort — if it fails, the generation still works; we just won't
    appear in UI. Errors are swallowed.
    """
    try:
        await client.request(
            "POST",
            "/v1/generations",
            json_body={
                "toolId": "generate",
                "prompt": "",
                "outputs": {"outputUrls": []},
                "settings": {
                    "duration": duration,
                    "numGenerations": 1,
                    "generateAudio": audio,
                    "exploreMode": True,
                    "recordingEnabled": True,
                    "name": name,
                    "textPrompt": prompt,
                },
                "asTeamId": ctx.team_id,
                "sessionId": ctx.session_id,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("UI generation registration failed: {}", e)
    try:
        await client.request(
            "POST",
            f"/v1/sessions/{ctx.session_id}/save",
            json_body={
                "taskId": runway_task_id,
                "asTeamId": ctx.team_id,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("UI session save failed: {}", e)


async def get_task_status(client: RunwayClient, task_id: str) -> TaskStatus:
    resp = await client.request("GET", f"/v1/tasks/{task_id}")
    return TaskStatus.from_response(resp)


async def poll_task(
    client: RunwayClient,
    task_id: str,
    *,
    interval: float | None = None,
    max_seconds: float | None = None,
) -> AsyncIterator[TaskStatus]:
    """Yields a TaskStatus on each poll until terminal state or timeout."""
    interval = interval or settings.poll_interval_seconds
    deadline = asyncio.get_event_loop().time() + (max_seconds or settings.poll_max_seconds)

    while True:
        status = await get_task_status(client, task_id)
        yield status
        if status.is_terminal:
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise TimeoutError(f"poll_task: {task_id} did not finish before deadline")
        await asyncio.sleep(interval)


# ── download ────────────────────────────────────────────────────────────


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._\- ]")


def safe_filename(name: str, *, fallback: str = "video", max_len: int = 120) -> str:
    cleaned = _FILENAME_SAFE_RE.sub("_", name).strip("._ ")
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned


async def download_artifact(
    client: RunwayClient,
    artifact: dict[str, Any],
    dest_dir: Path,
    *,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Stream-download a single artifact (mp4) to dest_dir.

    `artifact` is one entry from TaskStatus.artifacts, e.g.:
        {"url": "https://...mp4?_jwt=...", "filename": "...mp4", "fileSize": "7462818"}
    """
    url = artifact.get("url")
    if not url:
        raise ValueError(f"Artifact has no url: {artifact}")
    filename = artifact.get("filename") or "video.mp4"
    filename = safe_filename(filename)
    if not filename.lower().endswith(".mp4"):
        filename += ".mp4"

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    # avoid clobber
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{suffix}"
            n += 1

    total: int | None = None
    try:
        total = int(artifact.get("fileSize") or 0) or None
    except (TypeError, ValueError):
        total = None

    written = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with tmp.open("wb") as f:
            async for chunk in client.stream_download(url):
                f.write(chunk)
                written += len(chunk)
                if on_progress is not None:
                    on_progress(written, total)
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise

    logger.info("Downloaded {} ({} bytes)", dest, written)
    return dest
