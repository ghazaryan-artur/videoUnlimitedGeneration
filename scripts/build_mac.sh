#!/usr/bin/env bash
# Build the macOS .app bundle for RunwayAutomation (arm64, Apple Silicon).
#
# Output:
#   dist/mac/RunwayAutomation.app
#
# Flow:
#   1. ensure a Python 3.12 .venv_mac with deps installed
#   2. bake keys/public.pem into license.py (if present)
#   3. run PyInstaller → dist/mac/RunwayAutomation.app
#   4. copy Playwright Chromium next to the binary (browser-login fallback)
#   5. revert license.py to dev mode
#
# Always wipes build/runway_app_mac/ and dist/mac/ before building.
#
# Usage:
#   ./scripts/build_mac.sh                   # full build
#   ./scripts/build_mac.sh --no-playwright   # skip ~250 MB browser bundle
set -euo pipefail

# ── locate project root (parent of scripts/) ─────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# ── parse flags ──────────────────────────────────────────────────────
WITH_PLAYWRIGHT=1
for arg in "$@"; do
  case "$arg" in
    --no-playwright) WITH_PLAYWRIGHT=0 ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# ── arch guard (we only build arm64) ─────────────────────────────────
HOST_ARCH="$(uname -m)"
if [[ "$HOST_ARCH" != "arm64" ]]; then
  echo "ERROR: this script targets arm64 (Apple Silicon). Detected: $HOST_ARCH" >&2
  echo "       Run on an M-series Mac, or modify --target-arch below." >&2
  exit 1
fi

# ── find Python 3.12 (PyInstaller works best on the same interpreter
#    family used by the Windows build) ────────────────────────────────
find_python() {
  for cand in \
      /opt/homebrew/bin/python3.12 \
      /opt/homebrew/opt/python@3.12/bin/python3.12 \
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
      "$(command -v python3.12 2>/dev/null || true)"; do
    if [[ -n "$cand" && -x "$cand" ]]; then
      echo "$cand"; return 0
    fi
  done
  # Fallback: any python3 >= 3.10 (will warn)
  if command -v python3 >/dev/null 2>&1; then
    local v
    v="$(python3 -c 'import sys; print(sys.version_info >= (3,10))')"
    if [[ "$v" == "True" ]]; then
      command -v python3; return 0
    fi
  fi
  return 1
}

PY="$(find_python)" || {
  echo "ERROR: Python 3.12 not found. Install it with:" >&2
  echo "       brew install python@3.12" >&2
  exit 1
}
echo "[build_mac] using interpreter: $PY ($("$PY" --version))"

# ── venv ─────────────────────────────────────────────────────────────
VENV="$ROOT/.venv_mac"
if [[ ! -d "$VENV" ]]; then
  echo "[build_mac] creating $VENV..."
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip wheel >/dev/null
echo "[build_mac] installing deps from requirements_mac.txt..."
python -m pip install -r requirements_mac.txt

# ── playwright browser cache ─────────────────────────────────────────
if [[ "$WITH_PLAYWRIGHT" == "1" ]]; then
  echo "[build_mac] ensuring Playwright Chromium is installed..."
  python -m playwright install chromium
fi

# ── clean previous build ─────────────────────────────────────────────
echo "[build_mac] cleaning build/runway_app_mac/ and dist/mac/..."
rm -rf "$ROOT/build/runway_app_mac" "$ROOT/dist/mac"
mkdir -p "$ROOT/dist/mac"

# ── embed public key (if available) so the license gate is active ────
KEY_EMBEDDED=0
if [[ -f "$ROOT/keys/public.pem" ]]; then
  echo "[build_mac] embedding keys/public.pem into license.py..."
  python "$ROOT/scripts/embed_key.py"
  KEY_EMBEDDED=1
else
  echo "[build_mac] WARNING: keys/public.pem not found — building in DEV MODE"
  echo "             (anyone can run the .app without a license.lic file)"
fi

# Always revert on exit so dev mode is restored even if PyInstaller fails.
cleanup() {
  if [[ "$KEY_EMBEDDED" == "1" ]]; then
    echo "[build_mac] reverting license.py to dev mode..."
    python "$ROOT/scripts/embed_key.py" --revert || true
  fi
}
trap cleanup EXIT

# ── PyInstaller ──────────────────────────────────────────────────────
echo "[build_mac] running PyInstaller..."
pyinstaller \
  --noconfirm \
  --clean \
  --name RunwayAutomation \
  --windowed \
  --osx-bundle-identifier com.runway.automation \
  --target-arch arm64 \
  --workpath "$ROOT/build/runway_app_mac" \
  --distpath "$ROOT/dist/mac" \
  --collect-all flet \
  --collect-all flet_runtime \
  --collect-data certifi \
  --hidden-import aiosqlite \
  --hidden-import sqlmodel \
  --hidden-import anthropic \
  --hidden-import playwright \
  "$ROOT/runway_app.py"

APP="$ROOT/dist/mac/RunwayAutomation.app"
if [[ ! -d "$APP" ]]; then
  echo "ERROR: PyInstaller did not produce $APP" >&2
  exit 1
fi

# ── bundle Playwright Chromium next to the binary ───────────────────
# app/__init__.py looks at  Path(sys.executable).parent / "ms-playwright"
# which on macOS resolves to  .../RunwayAutomation.app/Contents/MacOS/
MACOS_DIR="$APP/Contents/MacOS"

if [[ "$WITH_PLAYWRIGHT" == "1" ]]; then
  PW_CACHE="$HOME/Library/Caches/ms-playwright"
  if [[ -d "$PW_CACHE" ]]; then
    echo "[build_mac] copying Playwright Chromium into the .app..."
    rm -rf "$MACOS_DIR/ms-playwright"
    mkdir -p "$MACOS_DIR/ms-playwright"
    # Copy only chromium-* and (if present) ffmpeg-* — skip firefox/webkit.
    shopt -s nullglob
    for entry in "$PW_CACHE"/chromium-* "$PW_CACHE"/ffmpeg-*; do
      cp -R "$entry" "$MACOS_DIR/ms-playwright/"
    done
    shopt -u nullglob
  else
    echo "[build_mac] WARNING: $PW_CACHE missing — browser login fallback won't work"
  fi
fi

# ── result ───────────────────────────────────────────────────────────
SIZE="$(du -sh "$APP" | awk '{print $1}')"
echo
echo "[build_mac] done"
echo "[build_mac]   bundle: $APP"
echo "[build_mac]   size:   $SIZE"
echo
echo "Run locally:  open '$APP'"
echo "Distribute:   zip -r RunwayAutomation.zip '$APP'   (and ship the .zip)"
echo
echo "Note: the .app is NOT signed/notarized. First-launch on another Mac"
echo "      requires right-click → Open → confirm in Security & Privacy."
