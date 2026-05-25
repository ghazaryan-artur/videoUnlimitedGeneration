"""License system — only people with a valid signed license file can run the app.

Architecture:
  - You (the developer) generate ONE master Ed25519 keypair, ONCE.
  - The PUBLIC key is embedded in this source file (built into the .exe).
  - The PRIVATE key stays on YOUR machine in the keys/ folder, NEVER bundled.
  - To grant access, you run `python -m app.security.keygen issue --to "name"`,
    which produces a signed `license.lic` file you give to the user.
  - At startup, the app calls `verify_or_die()`. Without a valid file → exit.

Use Ed25519 (RFC 8032) — small keys, fast verify, no parameter footguns.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)


# ── EMBEDDED PUBLIC KEY ────────────────────────────────────────────────
# After running keygen.py once, paste the printed public_key.pem here.
# The empty placeholder lets the app run in dev mode (license check skipped
# when this is empty); in a packaged build it MUST be filled in.

EMBEDDED_PUBLIC_KEY_PEM: bytes = b''
# Example after keygen:
# EMBEDDED_PUBLIC_KEY_PEM = b"-----BEGIN PUBLIC KEY-----\nMCowBQ...\n-----END PUBLIC KEY-----\n"


# ── data ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class License:
    issued_to: str
    issued_at: datetime
    expires_at: datetime | None  # None == perpetual
    license_id: str

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and datetime.now(timezone.utc) >= self.expires_at


class LicenseError(RuntimeError):
    pass


# ── PEM <-> bytes helpers ─────────────────────────────────────────────


def generate_keypair() -> tuple[bytes, bytes]:
    """Returns (private_pem, public_pem). Save private somewhere safe!"""
    sk = Ed25519PrivateKey.generate()
    private_pem = sk.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    public_pem = sk.public_key().public_bytes(
        encoding=Encoding.PEM,
        format=PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _load_private(pem: bytes) -> Ed25519PrivateKey:
    key = load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError("Private key is not Ed25519.")
    return key


def _load_public(pem: bytes) -> Ed25519PublicKey:
    key = load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("Public key is not Ed25519.")
    return key


# ── issue / verify ────────────────────────────────────────────────────


def issue_license(
    private_pem: bytes,
    *,
    issued_to: str,
    expires_at: datetime | None = None,
    license_id: str | None = None,
) -> str:
    """Returns a base64 string the user pastes into the app at activation."""
    import secrets

    payload = {
        "issued_to": issued_to,
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds") if expires_at else None,
        "license_id": license_id or secrets.token_hex(8),
        "version": 1,
    }
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    sk = _load_private(private_pem)
    signature = sk.sign(body)

    bundle = {
        "payload": base64.b64encode(body).decode("ascii"),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return base64.b64encode(json.dumps(bundle).encode("ascii")).decode("ascii")


def verify_license(license_string: str, public_pem: bytes) -> License:
    try:
        outer_bytes = base64.b64decode(license_string.strip())
        bundle = json.loads(outer_bytes)
        body = base64.b64decode(bundle["payload"])
        signature = base64.b64decode(bundle["signature"])
    except Exception as e:  # noqa: BLE001
        raise LicenseError(f"License is malformed: {e}") from e

    pk = _load_public(public_pem)
    try:
        pk.verify(signature, body)
    except InvalidSignature as e:
        raise LicenseError("License signature does not match public key.") from e

    payload = json.loads(body)
    expires_raw = payload.get("expires_at")
    expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None

    lic = License(
        issued_to=payload["issued_to"],
        issued_at=datetime.fromisoformat(payload["issued_at"]),
        expires_at=expires_at,
        license_id=payload["license_id"],
    )
    if lic.is_expired:
        raise LicenseError(f"License expired at {lic.expires_at.isoformat()}.")
    return lic


# ── runtime check ─────────────────────────────────────────────────────


def verify_or_die() -> License | None:
    """Called once at app startup.

    - In dev (no public key embedded) → returns None, app proceeds.
    - In packaged build → reads license file, verifies it, returns License.
      On failure, raises LicenseError that the UI converts into a friendly
      activation prompt.
    """
    if not EMBEDDED_PUBLIC_KEY_PEM:
        return None  # dev mode
    from app.paths import license_file

    p: Path = license_file()
    if not p.exists():
        raise LicenseError("License file is missing. The app needs to be activated.")
    return verify_license(p.read_text(encoding="utf-8"), EMBEDDED_PUBLIC_KEY_PEM)


def install_license(license_string: str) -> License:
    """Validate and persist a license string given by the user."""
    if not EMBEDDED_PUBLIC_KEY_PEM:
        raise LicenseError(
            "App was built without a public key — cannot install license. "
            "(This is dev mode.)"
        )
    from app.paths import license_file

    lic = verify_license(license_string, EMBEDDED_PUBLIC_KEY_PEM)
    license_file().write_text(license_string.strip(), encoding="utf-8")
    return lic
