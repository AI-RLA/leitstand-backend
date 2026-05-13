#!/usr/bin/env bash
# Bring up zenohd and the leitstand-backend in one foreground shell.
# Ctrl-C reaps both.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROUTER_CONFIG="${LEITSTAND_ROUTER_CONFIG:-$REPO/config/zenoh_router.json5}"
PYTHON="${LEITSTAND_PYTHON:-$REPO/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

if ! command -v zenohd >/dev/null 2>&1; then
    echo "zenohd not found in PATH; install eclipse-zenoh's binary release" >&2
    exit 1
fi

echo "[start] zenohd config: $ROUTER_CONFIG"
echo "[start] python:        $PYTHON"

# Process substitution (not a pipe) so $! is zenohd, not sed.
zenohd --config "$ROUTER_CONFIG" > >(sed -u 's/^/[zenohd] /') 2>&1 &
ZENOHD_PID=$!

cleanup() {
    echo
    echo "[start] stopping zenohd (pid $ZENOHD_PID)"
    kill "$ZENOHD_PID" 2>/dev/null || true
    wait "$ZENOHD_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 0.5

cd "$REPO"
"$PYTHON" -m leitstand_backend
