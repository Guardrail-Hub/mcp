#!/bin/bash
# JMeter worker self-registration + heartbeat sidecar.
#
#   Worker Start -> Read HOSTNAME -> Build Endpoint -> POST /jmeter-workers/register
#   ... every JMETER_WORKER_HEARTBEAT_INTERVAL_SECONDS ... -> POST /jmeter-workers/heartbeat
#
# Mirrors docker/owasp_zap/register-worker.sh: no worker name, prefix or index
# is configured anywhere. The worker_id is this container's own HOSTNAME (Docker
# gives each `--scale` replica a distinct one) and the endpoint is built from
# that plus the agent's port, which is what lets the pool grow and shrink with
# zero mcp-server configuration.
#
# It posts to /jmeter-workers/*, never /workers/* — the two pools have separate
# surfaces so a worker cannot register into the wrong engine's registry.
set -uo pipefail

MCP_SERVER_INTERNAL_URL="${MCP_SERVER_INTERNAL_URL:-http://mcp-server:8787}"
JMETER_AGENT_PORT="${JMETER_AGENT_PORT:-8090}"
HEARTBEAT_INTERVAL="${JMETER_WORKER_HEARTBEAT_INTERVAL_SECONDS:-30}"

WORKER_ID="${HOSTNAME}"
ENDPOINT="http://${HOSTNAME}:${JMETER_AGENT_PORT}"

log() { echo "[register-jmeter-worker] $*"; }

# --- Wait until this container's own agent answers before announcing it.
# Registering earlier would advertise a worker the server could assign work to
# before it can accept any. ---
until curl -fsS -m 3 "http://127.0.0.1:${JMETER_AGENT_PORT}/health/live" >/dev/null 2>&1; do
    sleep 2
done
log "Agent is healthy — registering as '${WORKER_ID}' (${ENDPOINT})"

register() {
    curl -fsS -m 5 -X POST "${MCP_SERVER_INTERNAL_URL}/jmeter-workers/register" \
        -H "Content-Type: application/json" \
        -d "{\"worker_id\":\"${WORKER_ID}\",\"hostname\":\"${HOSTNAME}\",\"endpoint\":\"${ENDPOINT}\",\"port\":${JMETER_AGENT_PORT}}"
}

heartbeat() {
    curl -fsS -m 5 -o /dev/null -w '%{http_code}' -X POST "${MCP_SERVER_INTERNAL_URL}/jmeter-workers/heartbeat" \
        -H "Content-Type: application/json" \
        -d "{\"worker_id\":\"${WORKER_ID}\"}"
}

deregister() {
    log "Deregistering '${WORKER_ID}' (graceful shutdown)"
    curl -fsS -m 5 -X DELETE "${MCP_SERVER_INTERNAL_URL}/jmeter-workers/${WORKER_ID}" >/dev/null 2>&1 || true
    exit 0
}
trap deregister TERM INT

# --- Register, retrying until the mcp-server is reachable. Never gives up: a
# worker with nothing to talk to yet should keep trying, not exit. ---
until register >/dev/null 2>&1; do
    log "Registration failed (mcp-server unreachable?) — retrying in 5s"
    sleep 5
done
log "Registered with mcp-server at ${MCP_SERVER_INTERNAL_URL}"

# --- Heartbeat loop. A 404 means the server no longer knows this worker_id
# (it restarted, or this worker was reaped for a timeout) -> re-register. ---
while true; do
    sleep "${HEARTBEAT_INTERVAL}"
    status="$(heartbeat)"
    if [ "$status" = "404" ]; then
        log "Heartbeat got 404 — re-registering"
        register >/dev/null 2>&1 || log "Re-registration failed, will retry on the next tick"
    elif [ "$status" != "200" ]; then
        log "Heartbeat failed (HTTP ${status:-none}) — mcp-server may be unreachable, will retry"
    fi
done
