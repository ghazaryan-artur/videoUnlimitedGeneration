"""Persistent ledger of issued licenses — `keys/issued.json`.

Each entry stores the raw license string plus its decoded metadata so the
License Manager UI can list them without re-verifying every row.

Filesystem layout:
    keys/
    ├── private.pem         (your signing key — never share)
    ├── public.pem          (embedded into the app at build time)
    ├── issued.json         (this ledger)
    └── issued/             (legacy per-file copies; auto-migrated on load)
        └── <id>_<name>.lic
"""
from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


# ── paths ─────────────────────────────────────────────────────────────


def _keys_dir() -> Path:
    p = Path(__file__).resolve().parent.parent.parent / "keys"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ledger_file() -> Path:
    return _keys_dir() / "issued.json"


def _legacy_dir() -> Path:
    return _keys_dir() / "issued"


# ── data class ────────────────────────────────────────────────────────


@dataclass
class IssuedLicense:
    license_id: str
    issued_to: str
    issued_at: str          # ISO 8601
    expires_at: str | None  # ISO 8601 or None for perpetual
    license_string: str     # the full base64-encoded token

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IssuedLicense":
        return cls(
            license_id=d["license_id"],
            issued_to=d["issued_to"],
            issued_at=d["issued_at"],
            expires_at=d.get("expires_at"),
            license_string=d["license_string"],
        )

    @property
    def expires_dt(self) -> datetime | None:
        if not self.expires_at:
            return None
        try:
            return datetime.fromisoformat(self.expires_at)
        except ValueError:
            return None

    @property
    def issued_dt(self) -> datetime:
        try:
            return datetime.fromisoformat(self.issued_at)
        except ValueError:
            return datetime.now(timezone.utc)

    @property
    def is_expired(self) -> bool:
        ed = self.expires_dt
        return ed is not None and datetime.now(timezone.utc) >= ed


# ── parsing helpers ───────────────────────────────────────────────────


def _decode_license_string(license_string: str) -> dict[str, Any]:
    """Returns the inner payload dict (or raises on malformed input)."""
    outer = base64.b64decode(license_string.strip())
    bundle = json.loads(outer)
    body = base64.b64decode(bundle["payload"])
    return json.loads(body)


def issued_license_from_string(license_string: str) -> IssuedLicense:
    payload = _decode_license_string(license_string)
    return IssuedLicense(
        license_id=payload["license_id"],
        issued_to=payload["issued_to"],
        issued_at=payload["issued_at"],
        expires_at=payload.get("expires_at"),
        license_string=license_string,
    )


# ── ledger CRUD ───────────────────────────────────────────────────────


def _read_ledger() -> list[IssuedLicense]:
    f = _ledger_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("issued.json read failed: {}", e)
        return []
    out: list[IssuedLicense] = []
    if isinstance(data, list):
        for d in data:
            try:
                out.append(IssuedLicense.from_dict(d))
            except (KeyError, TypeError):
                continue
    return out


def _write_ledger(licenses: list[IssuedLicense]) -> None:
    f = _ledger_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps([l.to_dict() for l in licenses], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _migrate_legacy_files(existing: list[IssuedLicense]) -> list[IssuedLicense]:
    """One-time migration: pick up any *.lic files in keys/issued/ that aren't
    already in the ledger and append them."""
    legacy = _legacy_dir()
    if not legacy.exists():
        return existing
    seen_ids = {l.license_id for l in existing}
    additions: list[IssuedLicense] = []
    for path in sorted(legacy.glob("*.lic")):
        try:
            license_string = path.read_text(encoding="utf-8").strip()
            entry = issued_license_from_string(license_string)
        except Exception as e:  # noqa: BLE001
            logger.warning("could not parse legacy {}: {}", path, e)
            continue
        if entry.license_id in seen_ids:
            continue
        additions.append(entry)
        seen_ids.add(entry.license_id)
    if additions:
        all_entries = existing + additions
        _write_ledger(all_entries)
        logger.info("Migrated {} legacy .lic files into ledger", len(additions))
        return all_entries
    return existing


def list_issued() -> list[IssuedLicense]:
    """Return every license ever issued (newest first)."""
    entries = _read_ledger()
    entries = _migrate_legacy_files(entries)
    entries.sort(key=lambda l: l.issued_at, reverse=True)
    return entries


def add_issued(license_string: str) -> IssuedLicense:
    """Parse + persist a new issued license. Returns the parsed entry."""
    entry = issued_license_from_string(license_string)
    existing = _read_ledger()
    # Replace by id if exists (e.g. re-issue)
    existing = [l for l in existing if l.license_id != entry.license_id]
    existing.append(entry)
    _write_ledger(existing)
    return entry


def delete_issued(license_id: str) -> bool:
    """Remove from ledger. Returns True if a row was removed.

    NOTE: this only removes from your local records. Ed25519 signatures are
    self-verifying; without an online server, you can't *revoke* a license
    that's already been delivered to a user. To prevent reuse, rotate the
    keypair and rebuild the .exe.
    """
    existing = _read_ledger()
    new = [l for l in existing if l.license_id != license_id]
    if len(new) == len(existing):
        return False
    _write_ledger(new)
    # Also remove any legacy .lic file
    for p in _legacy_dir().glob(f"{license_id}*.lic"):
        try:
            p.unlink()
        except OSError:
            pass
    return True


def has_private_key() -> bool:
    """True when keys/private.pem exists — i.e. this is a developer machine."""
    return (_keys_dir() / "private.pem").exists()
