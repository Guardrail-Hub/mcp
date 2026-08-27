#!/bin/bash
# Container entrypoint for a ZAP worker.
#
# Runs two processes side by side in the same container:
#   1. ZAP itself (the command passed by docker-compose / `docker run`).
#   2. register-worker.sh — waits for ZAP's local API to be healthy, then
#      POSTs /workers/register and sends periodic heartbeats.
#
# This is what makes `docker compose up --scale zap=N` work with no
# application config change: every replica runs this same entrypoint, reads
# its own HOSTNAME, and announces itself — the mcp-server never generates a
# worker address from a prefix + index.
set -euo pipefail

"$@" &
ZAP_PID=$!

/usr/local/bin/register-worker.sh &
REGISTER_PID=$!

_term() {
    echo "[entrypoint] Received termination signal — shutting down"
    kill -TERM "$REGISTER_PID" 2>/dev/null || true
    kill -TERM "$ZAP_PID" 2>/dev/null || true
    wait "$ZAP_PID" 2>/dev/null || true
    exit 0
}
trap _term TERM INT

# The container's lifecycle follows ZAP itself: if ZAP exits, tear down the
# registration sidecar and exit with ZAP's own exit code.
set +e
wait "$ZAP_PID"
EXIT_CODE=$?
set -e
kill -TERM "$REGISTER_PID" 2>/dev/null || true
exit "$EXIT_CODE"
