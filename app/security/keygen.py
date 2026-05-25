"""License key generator — runs ONLY on the developer's machine.

Workflow (one-time):
  python -m app.security.keygen init
      → creates keys/private.pem and keys/public.pem.
      → tells you to paste the public PEM into license.py.

Workflow (per recipient):
  python -m app.security.keygen issue --to "Frunze (laptop)"
      → prints a license string.
      → also writes keys/issued/<license_id>.lic for your records.

  python -m app.security.keygen issue --to "Tester" --days 30
      → time-limited license.

  python -m app.security.keygen verify --string "<base64>"
      → sanity-checks a license against the public key.

The keys/ folder is .gitignored. NEVER commit private.pem.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console

from app.security.license import (
    EMBEDDED_PUBLIC_KEY_PEM,
    LicenseError,
    generate_keypair,
    issue_license,
    verify_license,
)
from app.security.license_ledger import add_issued as _ledger_add

console = Console()

# Keys live in project root, NEVER inside the packaged app
KEYS_DIR = Path(__file__).resolve().parent.parent.parent / "keys"
PRIVATE_PEM = KEYS_DIR / "private.pem"
PUBLIC_PEM = KEYS_DIR / "public.pem"
ISSUED_DIR = KEYS_DIR / "issued"


def _ensure_dirs() -> None:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    ISSUED_DIR.mkdir(parents=True, exist_ok=True)


def cmd_init(_args: argparse.Namespace) -> int:
    _ensure_dirs()
    if PRIVATE_PEM.exists():
        console.print(f"[red]✗ {PRIVATE_PEM} already exists.[/red]  Refusing to overwrite.")
        console.print("[dim]Delete it manually if you really want to regenerate.[/dim]")
        return 1
    private_pem, public_pem = generate_keypair()
    PRIVATE_PEM.write_bytes(private_pem)
    PUBLIC_PEM.write_bytes(public_pem)

    console.print(f"[green]✓ Keypair generated.[/green]")
    console.print(f"  private: [yellow]{PRIVATE_PEM}[/yellow]  (NEVER share / commit)")
    console.print(f"  public:  [cyan]{PUBLIC_PEM}[/cyan]")
    console.print()
    console.print("[bold]Next step:[/bold] paste the public PEM below into  "
                  "[cyan]app/security/license.py[/cyan]  as  "
                  "[cyan]EMBEDDED_PUBLIC_KEY_PEM = b\"\"\"...\"\"\"[/cyan]")
    console.print()
    console.print(public_pem.decode("ascii"))
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    _ensure_dirs()
    if not PRIVATE_PEM.exists():
        console.print(f"[red]✗ {PRIVATE_PEM} not found.[/red]  Run `init` first.")
        return 1
    private_pem = PRIVATE_PEM.read_bytes()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=args.days) if args.days else None
    )
    license_str = issue_license(
        private_pem,
        issued_to=args.to,
        expires_at=expires_at,
    )

    # Sanity-verify against own public key, if it's set
    if PUBLIC_PEM.exists():
        try:
            verified = verify_license(license_str, PUBLIC_PEM.read_bytes())
            console.print(
                f"[green]✓ License issued and self-verified[/green]  id={verified.license_id}"
            )
        except LicenseError as e:
            console.print(f"[red]✗ self-verify failed: {e}[/red]")
            return 2

    # Persist for records — both the legacy .lic file AND the JSON ledger
    out = ISSUED_DIR / f"{verified.license_id}_{args.to.replace(' ', '_')}.lic"
    out.write_text(license_str, encoding="utf-8")
    try:
        _ledger_add(license_str)
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]warning: could not update ledger: {e}[/yellow]")

    console.print()
    console.print("[bold]License string (give this to the user):[/bold]")
    console.print(license_str)
    console.print()
    console.print(f"[dim]copy saved to {out}[/dim]")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if EMBEDDED_PUBLIC_KEY_PEM:
        pk = EMBEDDED_PUBLIC_KEY_PEM
    elif PUBLIC_PEM.exists():
        pk = PUBLIC_PEM.read_bytes()
    else:
        console.print("[red]No public key available (neither embedded nor on disk).[/red]")
        return 1
    try:
        lic = verify_license(args.string, pk)
    except LicenseError as e:
        console.print(f"[red]✗ Invalid: {e}[/red]")
        return 2
    console.print("[green]✓ Valid license[/green]")
    console.print(f"  issued_to:  {lic.issued_to}")
    console.print(f"  issued_at:  {lic.issued_at}")
    console.print(f"  expires_at: {lic.expires_at or 'never'}")
    console.print(f"  license_id: {lic.license_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="keygen", description="License key management")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Generate Ed25519 keypair (one-time)")
    p_init.set_defaults(handler=cmd_init)

    p_iss = sub.add_parser("issue", help="Issue a signed license for a user")
    p_iss.add_argument("--to", required=True, help="recipient label (free text, e.g. machine name)")
    p_iss.add_argument("--days", type=int, default=0, help="0 = perpetual; otherwise N days")
    p_iss.set_defaults(handler=cmd_issue)

    p_ver = sub.add_parser("verify", help="Verify a license string")
    p_ver.add_argument("--string", required=True)
    p_ver.set_defaults(handler=cmd_verify)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
