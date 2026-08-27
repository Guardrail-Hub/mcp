#!/bin/bash
# Worker self-registration + heartbeat sidecar.
#
#   Worker Start -> Read HOSTNAME -> Build Endpoint -> POST /workers/register
#   ... every ZAP_WORKER_HEARTBEAT_INTERVAL_SECONDS ... -> POST /workers/heartbeat
#
# There is no worker name, prefix, or index configured anywhere here — the
# worker_id is simply this container's own HOSTNAME (Docker assigns each
# `--scale` replica a distinct one), and the endpoint is built from that same
# HOSTNAME plus the port ZAP itself listens on. This is what lets the pool
# grow/shrink at runtime with zero mcp-server configuration changes.
set -uo pipefail

MCP_SERVER_INTERNAL_URL="${MCP_SERVER_INTERNAL_URL:-http://mcp-server:8787}"
ZAP_API_PORT="${ZAP_API_PORT:-8080}"
ZAP_API_KEY="${ZAP_API_KEY:-change-this-zap-key}"
HEARTBEAT_INTERVAL="${ZAP_WORKER_HEARTBEAT_INTERVAL_SECONDS:-20}"
READY_STABLE_SUCCESS_COUNT="${ZAP_READY_STABLE_SUCCESS_COUNT:-3}"

WORKER_ID="${HOSTNAME}"
ENDPOINT="http://${HOSTNAME}:${ZAP_API_PORT}"

log() { echo "[register-worker] $*"; }

# --- Wait until ZAP's own local API answers stably before registering it ---
stable=0
while [ "$stable" -lt "$READY_STABLE_SUCCESS_COUNT" ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${ZAP_API_PORT}/JSON/core/view/version/?apikey=${ZAP_API_KEY}" >/dev/null 2>&1; then
        stable=$((stable + 1))
    else
        stable=0
    fi
    sleep 2
done
log "ZAP is stably healthy (${READY_STABLE_SUCCESS_COUNT} consecutive checks) — registering as '${WORKER_ID}' (${ENDPOINT})"

register() {
    curl -fsS -m 5 -X POST "${MCP_SERVER_INTERNAL_URL}/workers/register" \
        -H "Content-Type: application/json" \
        -d "{\"worker_id\":\"${WORKER_ID}\",\"hostname\":\"${HOSTNAME}\",\"endpoint\":\"${ENDPOINT}\",\"port\":${ZAP_API_PORT}}"
}

heartbeat() {
    curl -fsS -m 5 -o /dev/null -w '%{http_code}' -X POST "${MCP_SERVER_INTERNAL_URL}/workers/heartbeat" \
        -H "Content-Type: application/json" \
        -d "{\"worker_id\":\"${WORKER_ID}\"}"
}

deregister() {
    log "Deregistering '${WORKER_ID}' (graceful shutdown)"
    curl -fsS -m 5 -X DELETE "${MCP_SERVER_INTERNAL_URL}/workers/${WORKER_ID}" >/dev/null 2>&1 || true
    exit 0
}
trap deregister TERM INT

# --- Register, retrying until the mcp-server is reachable. Never gives up:
# a worker with nothing to talk to yet should keep trying, not exit. ---
until register >/dev/null 2>&1; do
    log "Registration failed (mcp-server unreachable?) — retrying in 5s"
    sleep 5
done
log "Registered with mcp-server at ${MCP_SERVER_INTERNAL_URL}"

# --- Heartbeat loop. A 404 means the server doesn't know this worker_id
# anymore (server restarted, or this worker was reaped for a timeout) ->
# re-register instead of assuming the worker is somehow still known. ---
while true; do
    sleep "${HEARTBEAT_INTERVAL}"
    status="$(heartbeat)"
    if [ "$status" = "404" ]; then
        log "Heartbeat got 404 — re-registering"
        register >/dev/null 2>&1 || log "Re-registration failed, will retry on the next heartbeat tick"
    elif [ "$status" != "200" ]; then
        log "Heartbeat failed (HTTP ${status:-none}) — mcp-server may be unreachable, will retry"
    fi
done
