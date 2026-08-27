#!/bin/bash
# Container entrypoint for a JMeter worker.
#
# Runs two processes side by side, the same shape as the ZAP worker's entrypoint:
#   1. The agent (the command passed by docker-compose) — accepts assignments
#      and owns the JMeter subprocess.
#   2. register-worker.sh — waits for the agent to be healthy, then registers
#      and heartbeats.
#
# This is what makes `docker compose up --scale jmeter-worker=N` work with no
# application config change: every replica runs this entrypoint, reads its own
# HOSTNAME, and announces itself.
set -euo pipefail

"$@" &
AGENT_PID=$!

/usr/local/bin/register-worker.sh &
REGISTER_PID=$!

_term() {
    echo "[entrypoint] Received termination signal — shutting down"
    # The sidecar first: its TERM trap deregisters this worker, so the server
    # stops assigning to it before the agent goes away.
    kill -TERM "$REGISTER_PID" 2>/dev/null || true
    kill -TERM "$AGENT_PID" 2>/dev/null || true
    wait "$AGENT_PID" 2>/dev/null || true
    exit 0
}
trap _term TERM INT

# The container's lifecycle follows the agent: if it exits, tear down the
# registration sidecar and exit with the agent's own code.
set +e
wait "$AGENT_PID"
EXIT_CODE=$?
set -e
kill -TERM "$REGISTER_PID" 2>/dev/null || true
exit "$EXIT_CODE"
