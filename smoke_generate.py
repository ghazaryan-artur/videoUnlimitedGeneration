"""Disposable smoke-test launcher: submits ONE task, prints id, then polls.

Run via:  python smoke_generate.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

from app.paths import output_dir
from app.runway.auth import load_token
from app.runway.client import ApiLogEntry, RunwayClient
from app.runway.models import SEEDANCE_2
from app.runway.tasks import (
    download_artifact,
    ensure_session,
    poll_task,
    submit_task,
)


PROMPT = "A calico cat slowly walks along a sandy beach at sunset, soft warm light, gentle ocean waves in the background, cinematic, calm, peaceful"
NAME = f"smoketest_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def print_log(entry: ApiLogEntry) -> None:
    s = entry.status if entry.status is not None else "ERR"
    short_url = entry.url.replace("https://api.runwayml.com", "")
    print(
        f"  [{entry.ts.split('T')[1][:8]}] {entry.method:5} {short_url[-60:]:60} "
        f"→ {s}  {entry.duration_ms:.0f}ms",
        flush=True,
    )


async def main() -> int:
    stored = load_token()
    if stored is None:
        print("ERROR: no stored token", file=sys.stderr)
        return 1

    print(f"=== SMOKE GENERATION — model={SEEDANCE_2.task_type} ===", flush=True)
    print(f"prompt: {PROMPT[:80]}...", flush=True)

    async with RunwayClient(token=stored.token, sinks=[print_log]) as client:
        print("\n--- preparing session ---", flush=True)
        ctx = await ensure_session(client)

        print("\n--- submitting task ---", flush=True)
        task_id = await submit_task(
            client,
            ctx=ctx,
            profile=SEEDANCE_2,
            name=NAME,
            prompt=PROMPT,
            duration=5,
            aspect_ratio="9:16",
            resolution="720p",
            audio=False,
        )

        print(f"\n>>> TASK_ID: {task_id}", flush=True)
        print(f">>> SESSION_ID: {ctx.session_id}", flush=True)
        print(">>> Open app.runwayml.com — the task should appear within 30s", flush=True)

        print("\n--- polling ---", flush=True)
        last_status: str | None = None
        async for status in poll_task(client, task_id):
            if status.status != last_status:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"status={status.status}  progress={status.progress_ratio:.0%}  "
                    f"eta_start={status.estimated_start_seconds}s",
                    flush=True,
                )
                last_status = status.status

        if not status.is_success:
            print(f"\n!!! TASK DID NOT SUCCEED: status={status.status} error={status.error}", flush=True)
            return 2

        print("\n--- downloading ---", flush=True)
        for art in status.artifacts:
            def progress(written: int, total: int | None) -> None:
                if total:
                    pct = written / total * 100
                    sys.stdout.write(f"\r  {written/1024/1024:.1f}/{total/1024/1024:.1f} MB ({pct:.0f}%)")
                else:
                    sys.stdout.write(f"\r  {written/1024/1024:.1f} MB")
                sys.stdout.flush()
            path = await download_artifact(client, art, output_dir(), on_progress=progress)
            sys.stdout.write("\n")
            print(f"SAVED: {path}", flush=True)

    print("\n=== SMOKE TEST COMPLETE ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
