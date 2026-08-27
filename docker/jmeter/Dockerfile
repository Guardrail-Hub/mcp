# syntax=docker/dockerfile:1.7
#
# JMeter worker: Apache JMeter plus the agent that runs it.
#
# **This image is the single source of truth for the JMeter version.** The
# version is pinned once, here, and is never selectable by the server, by a
# setting, or by a caller — which is why nothing in the application records or
# transmits an engine version. Upgrading JMeter means changing JMETER_VERSION
# below and rebuilding; there is no runtime switch and no second place to look.

FROM ghcr.io/astral-sh/uv:latest AS uv_bin

FROM eclipse-temurin:21-jre-jammy

ARG JMETER_VERSION=5.6.3

# Where to fetch the distribution from. These say *where*, never *which* —
# JMETER_VERSION above remains the single source of truth for the version.
#
# `dlcdn.apache.org` is Apache's CDN and is tried first: it is globally mirrored
# and built for automated traffic. `archive.apache.org` is a single origin that
# throttles aggressively, but it keeps every release forever — so it is the
# fallback, and it is what keeps this build working after 5.6.3 is superseded
# and the CDN drops it.
#
# On a network that does not allow direct egress to Apache, point the first one
# at an internal mirror instead; nothing else needs to change:
#
#   docker compose build \
#     --build-arg JMETER_DIST_BASE_URL=https://nexus.internal/repo/apache-jmeter \
#     jmeter-worker
ARG JMETER_DIST_BASE_URL=https://dlcdn.apache.org/jmeter/binaries
ARG JMETER_DIST_FALLBACK_URL=https://archive.apache.org/dist/jmeter/binaries

WORKDIR /app

COPY --from=uv_bin /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Apache JMeter, verified against the published SHA-512 checksum. An unverified
# download of something this image then executes would be an obvious supply
# chain hole.
#
# The curl flags each fix a distinct way this step failed or can fail:
#   -4                  force IPv4. A build network that advertises IPv6 without
#                       a working route makes curl try AAAA first and report a
#                       refused connection, while the same URL works from the
#                       host. This is the single most common cause of that
#                       "works outside Docker" symptom.
#   --retry-all-errors  archive.apache.org throttles and returns transient 5xx
#                       under automated load; a single attempt turns that into a
#                       failed build.
#   --connect-timeout   fail over to the next mirror promptly instead of hanging.
#
# The three ARGs above are still in scope here (an ARG lives to the end of its
# build stage), so they are deliberately not re-declared — re-declaring one
# without its default is how a working default silently becomes an empty string.
RUN set -eux; \
    cd /opt; \
    tarball="apache-jmeter-${JMETER_VERSION}.tgz"; \
    downloaded=""; \
    for base in "${JMETER_DIST_BASE_URL}" "${JMETER_DIST_FALLBACK_URL}"; do \
        echo "Fetching ${tarball} from ${base}"; \
        if curl -4 -fsSL --retry 5 --retry-all-errors --connect-timeout 15 \
                -O "${base}/${tarball}" \
                -O "${base}/${tarball}.sha512"; then \
            downloaded="${base}"; \
            break; \
        fi; \
        echo "Could not fetch JMeter from ${base}"; \
    done; \
    if [ -z "${downloaded}" ]; then \
        echo "ERROR: JMeter ${JMETER_VERSION} could not be downloaded from any"; \
        echo "configured source. If this network has no direct egress to Apache,"; \
        echo "rebuild with --build-arg JMETER_DIST_BASE_URL=<internal mirror>."; \
        exit 1; \
    fi; \
    sha512sum -c "${tarball}.sha512"; \
    tar -xzf "${tarball}"; \
    rm "${tarball}" "${tarball}.sha512"; \
    ln -s "/opt/apache-jmeter-${JMETER_VERSION}/bin/jmeter" /usr/local/bin/jmeter

# JVM heap for the JMeter process, pinned here because the image is the single
# source of truth for everything about the engine — the same rule that governs
# JMETER_VERSION above. There is deliberately no setting for this: heap is a
# property of the host the load is generated from, not of the test being run,
# and a caller who could raise it could OOM the worker for every other caller.
#
# `bin/jmeter` reads $HEAP and falls back to its own default when unset. These
# are exactly that default's values as shipped in 5.6.3, so **nothing about this
# run changes** — what changes is where the number comes from:
#
#   * Today the effective heap is whatever the bundled shell script happens to
#     default to, which is invisible from this repository and can move under a
#     JMeter upgrade. Pinning it makes a heap change a reviewed edit to this
#     file instead of a side effect of bumping JMETER_VERSION.
#   * -Xms == -Xmx is the predictability part. An expanding heap makes the JVM
#     resize mid-run, and those pauses land inside the measurement window — a
#     load generator that stalls reports latency the target never caused.
#     Committing the full heap up front also fails at startup, loudly, if the
#     container's memory limit cannot cover it, rather than 20 minutes into a
#     run.
#   * MaxMetaspaceSize bounds class metadata separately; it is unrelated to
#     sample volume and does not need to grow with load.
#
# Raising this is a legitimate change for a worker driving very high thread
# counts — edit it here, and give the container a memory limit above it.
ENV HEAP="-Xms1g -Xmx1g -XX:MaxMetaspaceSize=256m"

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# The isolation boundary required by runtime-boundary RB5. Only the trees the
# agent imports — NOT `COPY app ./app`. The worker has no business holding the
# DAO, the dispatcher, OperationService or the ZAP integration, and shipping
# them would leave RB3 ("a runtime never reaches persistence or orchestration")
# resting on convention alone. `app/` is a namespace package, so copying
# subtrees needs no __init__.py.
# tests/integrations/jmeter/test_jmeter_worker_isolation.py fails if the agent's
# import closure ever grows past this list.
COPY app/runtime/jmeter ./app/runtime/jmeter
COPY app/core ./app/core
COPY app/schemas/tools/jmeter ./app/schemas/tools/jmeter
COPY app/integrations/jmeter ./app/integrations/jmeter
COPY config ./config

COPY docker/jmeter/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/jmeter/register-worker.sh /usr/local/bin/register-worker.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/register-worker.sh

EXPOSE 8090

# The entrypoint runs the agent alongside the registration/heartbeat sidecar,
# so a worker announces itself with no server-side configuration — the same
# pattern the ZAP worker uses.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn app.runtime.jmeter.main:app --host 0.0.0.0 --port ${JMETER_AGENT_PORT:-8090}"]
