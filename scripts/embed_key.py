"""Embed keys/public.pem into app/security/license.py for the build.

Usage:
    python scripts/embed_key.py        # embed
    python scripts/embed_key.py --revert  # revert to empty (dev mode)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_KEY = PROJECT_ROOT / "keys" / "public.pem"
LICENSE_FILE = PROJECT_ROOT / "app" / "security" / "license.py"

# Pattern that matches both "empty" placeholder and "previously embedded" form.
# Anchored at start-of-line, no leading whitespace.
PATTERN = re.compile(
    r'^EMBEDDED_PUBLIC_KEY_PEM: bytes = b("[^"\n]*"|\'[^\'\n]*\'|""".*?"""|b?".*?"|.*?)$',
    re.MULTILINE,
)
# Above is over-broad. Use a simpler: replace one whole line.
SIMPLE_PATTERN = re.compile(
    r'^EMBEDDED_PUBLIC_KEY_PEM: bytes = .+$',
    re.MULTILINE,
)


def _replace_line(content: str, new_value: bytes) -> str:
    new_line = f"EMBEDDED_PUBLIC_KEY_PEM: bytes = {new_value!r}"
    # Use a lambda — re.sub() string replacements interpret backslash escapes
    # (\n, \1, \g<...>), and `repr(bytes-with-newlines)` produces literal `\n`
    # which would otherwise be re-converted to actual newlines in the output.
    new_content, count = SIMPLE_PATTERN.subn(lambda _m: new_line, content, count=1)
    if count != 1:
        raise SystemExit(
            "ERROR: could not find EMBEDDED_PUBLIC_KEY_PEM line in app/security/license.py"
        )
    return new_content


def embed() -> int:
    if not PUBLIC_KEY.exists():
        print(f"ERROR: {PUBLIC_KEY} not found. Run `python -m app.security.keygen init` first.")
        return 1
    pub = PUBLIC_KEY.read_bytes()
    if not LICENSE_FILE.exists():
        print(f"ERROR: {LICENSE_FILE} not found.")
        return 1
    txt = LICENSE_FILE.read_text(encoding="utf-8")
    new_txt = _replace_line(txt, pub)
    LICENSE_FILE.write_text(new_txt, encoding="utf-8")
    print(f"OK: embedded {len(pub)}-byte public key into license.py")
    return 0


def revert() -> int:
    if not LICENSE_FILE.exists():
        print(f"ERROR: {LICENSE_FILE} not found.")
        return 1
    txt = LICENSE_FILE.read_text(encoding="utf-8")
    new_txt = _replace_line(txt, b"")
    LICENSE_FILE.write_text(new_txt, encoding="utf-8")
    print("OK: reverted license.py to dev mode (empty key)")
    return 0


def main(argv: list[str]) -> int:
    if "--revert" in argv:
        return revert()
    return embed()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
