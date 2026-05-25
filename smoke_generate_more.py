"""Submit two MORE tasks (different prompts) — testing if API accepts
multiple submissions even though UI doesn't register them.

Run:  python smoke_generate_more.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from app.runway.auth import load_token
from app.runway.client import ApiLogEntry, RunwayClient
from app.runway.models import SEEDANCE_2
from app.runway.tasks import ensure_session, submit_task


PROMPTS = [
    "A small wooden boat drifts on a calm lake at dawn, mist on the water, soft pink sky, peaceful",
    "An old typewriter sits on a wooden desk, sunlight streaming through a window, dust particles floating, slow zoom in",
]


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

    print(f"=== SUBMITTING {len(PROMPTS)} ADDITIONAL TASKS ===", flush=True)
    print(f"(Goal: test if API accepts multiple parallel submissions while UI is unaware)", flush=True)

    async with RunwayClient(token=stored.token, sinks=[print_log]) as client:
        ctx = await ensure_session(client)
        print(f"\nSession: {ctx.session_id}\n", flush=True)

        task_ids = []
        for i, prompt in enumerate(PROMPTS, start=1):
            name = f"smoketest_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}"
            print(f"\n--- Submitting task #{i}: {prompt[:60]}... ---", flush=True)
            tid = await submit_task(
                client,
                ctx=ctx,
                profile=SEEDANCE_2,
                name=name,
                prompt=prompt,
                duration=5,
                aspect_ratio="9:16",
                resolution="720p",
                audio=False,
            )
            task_ids.append(tid)
            print(f">>> TASK_ID #{i}: {tid}", flush=True)
            # Small human-like pause between submissions
            await asyncio.sleep(1.5)

        print(f"\n=== SUBMITTED {len(task_ids)} TASKS ===", flush=True)
        for i, tid in enumerate(task_ids, start=1):
            print(f"  #{i}: {tid}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
