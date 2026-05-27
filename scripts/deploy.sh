#!/usr/bin/env bash
# Build the server bundle locally and push it to a remote host.
#
# Usage:
#   scripts/deploy.sh user@host [remote-dir]
#
# Defaults remote-dir to /opt/runway (matches where this project is
# already deployed). The remote host needs Python 3.11+ and write access
# to the target directory.
#
# What it does:
#   1. python3 scripts/build_server.py  → build/runway-server.tar.gz
#   2. scp the tarball to /tmp on the remote
#   3. ssh: stop service (if systemd), extract over remote-dir, start service
#
# Nothing is uploaded except the archive. Existing .venv and downloads/
# on the remote are preserved (tar only overwrites files it carries).

set -euo pipefail

HOST="${1:-}"
REMOTE_DIR="${2:-/opt/runway}"

if [ -z "$HOST" ]; then
  echo "Usage: $0 user@host [remote-dir]" >&2
  exit 1
fi

cd "$(dirname "$0")/.."

echo "[deploy] building locally…"
python3 scripts/build_server.py

ARCHIVE="build/runway-server.tar.gz"
[ -f "$ARCHIVE" ] || { echo "[deploy] build failed: $ARCHIVE missing" >&2; exit 1; }

echo "[deploy] uploading to ${HOST}:${REMOTE_DIR}…"
scp "$ARCHIVE" "${HOST}:/tmp/runway-server.tar.gz"

echo "[deploy] applying on remote…"
ssh "$HOST" REMOTE_DIR="$REMOTE_DIR" bash -se <<'REMOTE'
set -euo pipefail
: "${REMOTE_DIR:?REMOTE_DIR not set}"

# Stop the service if it's running under systemd. Ignore failure — the
# service might not exist on first deploy.
# Note: `list-unit-files` only shows enabled/disabled units; a unit that
# exists but isn't enabled won't show up. `cat` works on any installed
# unit file, which is what we actually want to detect.
if systemctl cat runway.service >/dev/null 2>&1; then
  systemctl stop runway || true
  HAD_SYSTEMD=1
else
  HAD_SYSTEMD=0
fi

mkdir -p "$REMOTE_DIR"
# --strip-components=1 drops the inner `runway-server/` directory so files
# land directly inside REMOTE_DIR.
tar xzf /tmp/runway-server.tar.gz --strip-components=1 -C "$REMOTE_DIR"
rm /tmp/runway-server.tar.gz

if [ "$HAD_SYSTEMD" = "1" ]; then
  systemctl start runway
  echo "[remote] restarted via systemd — tail logs: journalctl -u runway -f"
else
  echo "[remote] no systemd unit yet — start manually: cd $REMOTE_DIR && ./run.sh"
fi
REMOTE

echo "[deploy] done."
