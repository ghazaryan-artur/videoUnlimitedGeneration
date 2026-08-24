"""Disposable CLI for end-to-end smoke testing — to be replaced by the Flet UI.

Usage:
  python -m app.cli login
      Prompts for email + password, calls /v1/login, saves JWT.

  python -m app.cli login --browser
      Opens Chromium; user logs in by hand; we sniff the JWT.

  python -m app.cli whoami
      Shows the stored token's user, expiry.

  python -m app.cli generate "your prompt here"
      [--model seedance_2_5|seedance_2|kling_3_0_pro]
      [--duration 4..15  (model-dependent)]
      [--aspect 16:9|9:16|1:1|21:9|4:3|3:4  (model-dependent)]
      [--audio/--no-audio]
      [--name "custom name"]

  python -m app.cli logout
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from app.paths import output_dir
from app.runway.auth import (
    StoredToken,
    api_login,
    browser_login,
    clear_token,
    get_active_token,
    load_token,
)
from app.runway.client import ApiLogEntry, RunwayClient
from app.runway.models import all_profiles, by_task_type
from app.runway.tasks import (
    download_artifact,
    ensure_session,
    poll_task,
    submit_task,
)

console = Console()


def _print_token(stored: StoredToken) -> None:
    console.print(f"  user_id:  [cyan]{stored.user_id}[/cyan]")
    console.print(f"  email:    [cyan]{stored.email}[/cyan]")
    console.print(f"  expires:  [yellow]{stored.expires_at.isoformat()}[/yellow]  "
                  f"({stored.seconds_remaining // 86400} days remaining)")


# ── handlers ────────────────────────────────────────────────────────────


async def cmd_login(args: argparse.Namespace) -> int:
    if args.browser:
        console.print("[yellow]Opening browser for manual login…[/yellow]")
        stored = await browser_login(headless=False)
    else:
        username = args.username or input("Email or username: ").strip()
        password = args.password or getpass.getpass("Password: ")
        stored = await api_login(username, password)
    console.print("[green]✓ Login successful[/green]")
    _print_token(stored)
    return 0


async def cmd_whoami(_args: argparse.Namespace) -> int:
    stored = load_token()
    if stored is None:
        console.print("[red]No stored token. Run `login` first.[/red]")
        return 1
    if stored.is_expired:
        console.print("[yellow]Token is expired.[/yellow]")
        return 1
    console.print("[green]Logged in[/green]")
    _print_token(stored)
    return 0


async def cmd_logout(_args: argparse.Namespace) -> int:
    clear_token()
    console.print("[green]Token cleared.[/green]")
    return 0


async def cmd_models(_args: argparse.Namespace) -> int:
    table = Table(title="Available Models")
    table.add_column("taskType", style="cyan")
    table.add_column("Display Name")
    table.add_column("Max Prompt", justify="right")
    table.add_column("Durations")
    table.add_column("Aspects")
    for p in all_profiles():
        table.add_row(
            p.task_type,
            p.display_name,
            str(p.max_prompt_chars),
            "/".join(map(str, p.durations)),
            "/".join(p.aspect_ratios),
        )
    console.print(table)
    return 0


async def cmd_generate(args: argparse.Namespace) -> int:
    stored = await get_active_token()
    profile = by_task_type(args.model)

    name = args.name or f"{profile.display_name} - {args.prompt[:40]}"

    def log_sink(entry: ApiLogEntry) -> None:
        # Compact one-line debug print
        status = entry.status if entry.status is not None else "ERR"
        console.print(
            f"  [dim]{entry.ts.split('T')[1][:12]}[/dim]  "
            f"[bold]{entry.method:6}[/bold] {entry.url[-70:]:70}  "
            f"→ [yellow]{status}[/yellow]  {entry.duration_ms:.0f}ms"
        )

    async with RunwayClient(token=stored.token, sinks=[log_sink]) as client:
        console.rule("[bold cyan]Preparing session[/bold cyan]")
        ctx = await ensure_session(client)

        console.rule(f"[bold cyan]Submitting task ({profile.display_name})[/bold cyan]")
        task_id = await submit_task(
            client,
            ctx=ctx,
            profile=profile,
            name=name,
            prompt=args.prompt,
            duration=args.duration,
            aspect_ratio=args.aspect,
            resolution=args.resolution,
            audio=args.audio,
        )

        console.rule(f"[bold cyan]Polling task {task_id}[/bold cyan]")
        last_status: str | None = None
        async for status in poll_task(client, task_id):
            if status.status != last_status:
                console.print(
                    f"[bold]{datetime.now().strftime('%H:%M:%S')}[/bold]  "
                    f"status=[magenta]{status.status}[/magenta]  "
                    f"progress={status.progress_ratio:.0%}  "
                    f"eta_start={status.estimated_start_seconds}s"
                )
                last_status = status.status

        if not status.is_success:
            console.print(f"[red]Task did not succeed.  status={status.status}  error={status.error}[/red]")
            return 2

        console.rule("[bold cyan]Downloading[/bold cyan]")
        dest_root: Path = output_dir()
        for art in status.artifacts:
            def progress(written: int, total: int | None) -> None:
                if total:
                    pct = written / total * 100
                    sys.stdout.write(f"\r  {written/1024/1024:.1f}/{total/1024/1024:.1f} MB ({pct:.0f}%)")
                else:
                    sys.stdout.write(f"\r  {written/1024/1024:.1f} MB")
                sys.stdout.flush()
            path = await download_artifact(client, art, dest_root, on_progress=progress)
            sys.stdout.write("\n")
            console.print(f"[green]✓ Saved:[/green] {path}")

    return 0


# ── argparse wiring ─────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runway", description="Runway Automation CLI (smoke test)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="Log in to Runway")
    p_login.add_argument("--username", help="email/username")
    p_login.add_argument("--password", help="password (omit to be prompted)")
    p_login.add_argument("--browser", action="store_true", help="login via browser fallback")
    p_login.set_defaults(handler=cmd_login)

    p_who = sub.add_parser("whoami", help="Show stored token info")
    p_who.set_defaults(handler=cmd_whoami)

    p_out = sub.add_parser("logout", help="Clear stored token")
    p_out.set_defaults(handler=cmd_logout)

    p_mod = sub.add_parser("models", help="List available models")
    p_mod.set_defaults(handler=cmd_models)

    p_gen = sub.add_parser("generate", help="Submit a generation task and wait for the result")
    p_gen.add_argument("prompt", help="prompt text")
    p_gen.add_argument("--model", default="seedance_2", choices=[p.task_type for p in all_profiles()])
    p_gen.add_argument("--duration", type=int, default=5, choices=[5, 10, 15])
    p_gen.add_argument("--aspect", default="9:16", choices=["16:9", "9:16", "1:1"])
    p_gen.add_argument("--resolution", default="720p")
    p_gen.add_argument("--audio", dest="audio", action="store_true", default=True)
    p_gen.add_argument("--no-audio", dest="audio", action="store_false")
    p_gen.add_argument("--name", default=None)
    p_gen.set_defaults(handler=cmd_generate)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
